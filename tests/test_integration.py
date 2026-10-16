import asyncio
import csv
from dataclasses import replace
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
import httpx
from PIL import Image
from fastapi.testclient import TestClient
from app.db import connect, dumps, stamp
from app.main import create_app
from app.messages import Outbox
from app.scheduler import Scheduler
from app.service import Service
from app.people import People
from app.security import Platform
from app.tournaments import Tournaments
from app.ai_gateway import interpret_limited
from scripts.migrate_legacy import migrate
from tests.test_core import CoreFixture, TOKEN


class IntegrationTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.app = create_app(self.temp.name, TOKEN, False, False, self.clock)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization':'Bearer '+TOKEN,'X-Bot-ID':self.ctx.id}
        self.patcher = patch('app.competitions._now', self.clock)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def call(self, method, path, data=None):
        result = self.client.request(method,path,json=data,headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def daily(self, form='round_robin', via_api=False):
        for i in range(1,5):
            self.s.enroll(str(i),'球友'+str(i))
        data={'title':'每日积分','kind':'daily','format':form,'options':{'discipline':'singles'},'venue_id':1,'date':'2026-10-12','registration_open':None,'registration_close':None}
        if via_api:
            item=self.call('POST','/api/tournaments',data)
            self.call('PUT',f"/api/tournaments/{item['id']}/organization",{'channel_ids':[self.ctx.channel_id]})
            return self.call('GET',f"/api/tournaments/{item['id']}")
        return Tournaments(self.member,self.clock).create(data)

    def large(self):
        item=self.call('POST','/api/tournaments',{'title':'秋季杯','kind':'large','format':'single_elimination','venue_id':1,'date':'2026-10-12','options':{'discipline':'singles'},'keyword':'秋季杯','registration_open':stamp(self.now-timedelta(hours=1)),'registration_close':stamp(self.now+timedelta(hours=1))})
        self.call('PUT',f"/api/tournaments/{item['id']}/organization",{'channel_ids':[self.ctx.channel_id]})
        return self.call('GET',f"/api/tournaments/{item['id']}")

    def test_full_daily_api_scores_restart_and_results(self):
        item=self.daily('single_elimination', via_api=True)
        event_id=item['id']
        for _ in range(10):
            if item['status']=='completed':
                break
            match=next(m for m in item['matches'] if m['status']=='ready')
            item=self.call('POST',f'/api/tournaments/{event_id}/scores',{'match_id':match['id'],'scores':[[21,10]],'expected_version':match['version']})
        self.assertEqual(item['status'],'completed')
        self.assertTrue(item['champion'])
        self.assertEqual(Tournaments(self.ctx,self.clock).get(event_id)['champion'],item['champion'])
        self.call('POST',f'/api/tournaments/{event_id}/notify',{'type':'results'})
        self.assertIn('获胜者',Outbox(self.ctx).list()[0]['text'])
        self.assertEqual(self.client.get(f'/api/tournaments/{event_id}?venue_id=2',headers=self.headers).status_code,403)

    def test_large_keyword_temporary_scorer_and_auto_close(self):
        item=self.large()
        event_id=item['id']
        self.call('POST',f'/api/tournaments/{event_id}/actions',{'action':'open_registration'})
        for i in range(4):
            member=replace(self.member,actor=str(1001+i))
            self.assertIn('已更新',asyncio.run(self.commands(member).run('秋季杯报名')))
        scheduler=Scheduler(self.platform,clock=self.clock)
        self.now += timedelta(hours=1,seconds=1)
        scheduler.tick()
        scheduler.tick()
        notices=[x for x in Outbox(self.ctx).list() if 'tournament:' in x['dedupe_key']]
        self.assertEqual(len(notices),1)
        item=self.call('POST',f'/api/tournaments/{event_id}/actions',{'action':'start'})
        match=next(m for m in item['matches'] if m['status']=='ready')
        with self.assertRaises(PermissionError):
            asyncio.run(self.commands().run(f"赛事 {event_id} 第 {match['number']} 场 21:19"))
        self.call('PATCH',f'/api/tournaments/{event_id}',{'staff':[{'user_id':'1001','role':'scorer','starts_at':stamp(self.now-timedelta(minutes=1)),'expires_at':stamp(self.now+timedelta(minutes=1))}]})
        self.assertIn('（21）',asyncio.run(self.commands().run(f"赛事 {event_id} 第 {match['number']} 场 21:19")))
        saved = Tournaments(self.ctx,self.clock).get(event_id)
        self.assertEqual(next(m for m in saved['matches'] if m['id']==match['id'])['scores'], [[21,19]])
        self.now += timedelta(minutes=2)
        item=self.call('GET',f'/api/tournaments/{event_id}')
        next_match=next(m for m in item['matches'] if m['status']=='ready')
        with self.assertRaises(PermissionError):
            asyncio.run(self.commands().run(f"赛事 {event_id} 第 {next_match['number']} 场 21:19"))

    def test_daily_creator_and_admin_only_despite_legacy_leader_configuration(self):
        self.s.update_config({'scoring_mode':'leader','leader_user_ids':['leader']})
        for user in ('1001','2'):
            self.s.enroll(user,user)
        member_event=Tournaments(self.member,self.clock).create({'kind':'daily','format':'round_robin','options':{'scoring_mode':'members','discipline':'singles'}})
        self.assertEqual(member_event['options']['scoring_mode'],'leader')
        admin_event=Tournaments(self.ctx,self.clock).create({'kind':'daily','format':'round_robin','options':{'scoring_mode':'members','leader_user_ids':['other'],'discipline':'singles'}})
        self.assertEqual(admin_event['options']['scoring_mode'],'members')
        with self.assertRaises(PermissionError):
            Tournaments(replace(self.member,actor='leader'),self.clock).score(member_event['id'],member_event['matches'][0]['id'],[[21,19]])
        self.assertTrue(Tournaments(self.member,self.clock).score(member_event['id'],member_event['matches'][0]['id'],[[21,19]])['matches'][0]['scores'])
        with self.assertRaises(PermissionError):
            Tournaments(self.member,self.clock).score(admin_event['id'],admin_event['matches'][0]['id'],[[21,19]])
        self.assertTrue(Tournaments(self.ctx,self.clock).score(admin_event['id'],admin_event['matches'][0]['id'],[[21,19]])['matches'][0]['scores'])

    def test_resume_multiple_candidates_and_wrong_venue(self):
        self.daily()
        self.daily('single_elimination')
        response=asyncio.run(self.commands(replace(self.member,actor='1')).run('继续比赛'))
        self.assertIn('存在多场',response)
        venue=self.s.save_venue({'name':'第二场地'})
        with self.assertRaises(PermissionError):
            asyncio.run(self.commands().run(f"场地{venue['id']} 继续比赛 1"))

    def test_short_score_only_resolves_one_running_tournament(self):
        event = self.daily()
        reply = asyncio.run(self.commands().run('第1场 21:19'))
        self.assertIn('（21）', reply)
        saved = Tournaments(self.ctx, self.clock).get(event['id'])
        self.assertEqual(sum(bool(m['scores']) for m in saved['matches']), 1)
        second = self.daily('single_elimination')
        reply = asyncio.run(self.commands().run('第2场 21:18'))
        self.assertIn('本次没有记分', reply)
        for item, count in [(event, 1), (second, 0)]:
            saved = Tournaments(self.ctx, self.clock).get(item['id'])
            self.assertEqual(sum(bool(m['scores']) for m in saved['matches']), count)
        venue = self.s.save_venue({'name':'隔离场地'})
        reply = asyncio.run(self.commands().run(f"场地{venue['id']} 第1场 21:19"))
        self.assertIn('没有进行中的赛事', reply)

    def test_short_score_keeps_large_event_staff_permission(self):
        item = self.large()
        event_id = item['id']
        self.call('POST', f'/api/tournaments/{event_id}/actions', {'action':'open_registration'})
        for i in range(4):
            self.call('POST', f'/api/tournaments/{event_id}/participants', {'user_id':str(i),'name':str(i)})
        self.call('POST', f'/api/tournaments/{event_id}/actions', {'action':'close_registration'})
        self.call('POST', f'/api/tournaments/{event_id}/actions', {'action':'start'})
        with self.assertRaises(PermissionError):
            asyncio.run(self.commands().run('第1场 21:19'))
        item = Tournaments(self.ctx, self.clock).get(event_id)
        self.assertFalse(any(m['scores'] for m in item['matches']))

    def test_roster_with_a_name_never_registers_people(self):
        asyncio.run(self.commands().run('名单 小李+1'))
        self.assertEqual(self.s.registrations()['registrations'], [])

    def test_upload_download_shared_media_revoke_and_transport_grant_denial(self):
        picture=io.BytesIO()
        Image.new('RGB',(8,8)).save(picture,format='PNG')
        self.s.update_config({'public_base_url':'http://127.0.0.1:9874'})
        for name,content in [('note.txt',b'hello'),('court.png',picture.getvalue())]:
            response=self.client.post('/api/media',headers=self.headers,files={'file':(name,content)})
            self.assertEqual(response.status_code,200,response.text)
            row=response.json()
            self.assertEqual(self.client.get(row['url']).status_code,401)
            self.assertEqual(self.client.get(row['url'],headers=self.headers).status_code,200)
            foreign=self.headers|{'X-Bot-ID':self.bots[1]['id']}
            self.assertEqual(self.client.get(row['url'],headers=foreign).status_code,200)
            limited=self.platform.grant(self.admin,[self.bots[1]['id']])
            self.assertEqual(self.client.get(row['url'],headers={'Authorization':'Bearer '+limited['token']}).status_code,403)
            link=self.call('POST',f"/api/media/{row['id']}/link")['url']
            path='/public/media/'+link.split('/public/media/')[1]
            self.assertEqual(self.client.get(path).status_code,200)
            self.assertEqual(self.client.get(path.replace('workspace',self.bots[1]['id'])).status_code,200)
            with TestClient(create_app(Path(self.temp.name)/'separate-backend',TOKEN,False,False,self.clock)) as foreign_client:
                self.assertEqual(foreign_client.get(path).status_code,403)
            self.call('POST',f"/api/media/{row['id']}/revoke")
            self.assertEqual(self.client.get(path).status_code,403)

    def test_streaming_limits_stop_consumption_and_auth_first(self):
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(self.app),base_url='http://testserver') as client:
                for headers,path,expected_chunks in [({},'/api/media',0),(self.headers,'/api/config',2),({},self.bots[0]['callback_path'],2)]:
                    seen=[]
                    async def body():
                        for i in range(4):
                            seen.append(i)
                            yield b'x'*40000
                    response=await client.request('PATCH' if path=='/api/config' else 'POST',path,headers=headers|{'Content-Type':'application/json'},content=body())
                    self.assertIn(response.status_code,(400,401,413))
                    self.assertEqual(len(seen),expected_chunks)
                seen=[]
                async def multipart():
                    yield b'--BOUND\r\nContent-Disposition: form-data; name="file"; filename="x.txt"\r\n\r\n'
                    for i in range(50):
                        seen.append(i)
                        yield b'x'*(256*1024)
                    yield b'\r\n--BOUND--\r\n'
                response=await client.post('/api/media',headers=self.headers|{'Content-Type':'multipart/form-data; boundary=BOUND'},content=multipart())
                self.assertIn(response.status_code,(400,413))
                self.assertLess(len(seen),50)
        asyncio.run(exercise())

    def test_migration_preserves_rows_dedupe_history_and_is_idempotent(self):
        source=Path(self.temp.name)/'old'
        source.mkdir()
        config={'target_chat_id':'266382','callback_token':'migrate-callback-long-01234567890123','admin_token':'migrate-admin-token-long-01234567890123','webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=not-real','robot_key':'not-real','admin_user_ids':['9']}
        (source/'config.json').write_text(dumps(config),encoding='utf-8')
        rows=[{'date':'2026-10-11','chat_id':'266382','user_id':'9','name':'甲','status':'cancelled','registered_at':'2026-10-11T10:00:00+08:00','cancelled_at':'2026-10-11T11:00:00+08:00'},
              {'date':'2026-10-11','chat_id':'266382','user_id':'9','name':'甲','status':'active','registered_at':'2026-10-11T12:00:00+08:00','cancelled_at':''}]
        with (source/'registrations.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        with connect(source/'delivery.sqlite3',True) as db:
            db.executescript('CREATE TABLE events(event_id TEXT,received_at TEXT); CREATE TABLE outbox(id TEXT,payload TEXT,status TEXT,created_at TEXT,attempts INTEGER,last_error TEXT);')
            db.execute('INSERT INTO events VALUES(?,?)',('legacy-id',stamp(self.now)))
            db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?)',('old1','{"msgtype":"markdown","markdown":{"text":"old"}}','sent',stamp(self.now),1,None))
            db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?)',('old2','{"msgtype":"markdown","markdown":{"text":"pending"}}','pending',stamp(self.now),0,None))
        args=(source/'config.json',source/'registrations.csv',source/'delivery.sqlite3',Path(self.temp.name)/'new',TOKEN)
        before=hashlib.sha256((source/'registrations.csv').read_bytes()).hexdigest()
        result=migrate(*args)
        rerun=migrate(*args)
        self.assertEqual(result['preserved_source_rows'],2)
        self.assertEqual(result['current_status_counts'],{'active':1})
        self.assertEqual(rerun['newly_imported'],0)
        self.assertEqual(before,hashlib.sha256((source/'registrations.csv').read_bytes()).hexdigest())
        path=Path(self.temp.name)/'new'/'workspace'/'business.sqlite3'
        with connect(path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM legacy_rows').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_roles').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM workspace_migration_issues').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT chat_id FROM registrations').fetchone()[0],'club')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM membership_applications').fetchone()[0],1)
        transport=Path(self.temp.name)/'new'/'bots'/result['bot_id']/'transport.sqlite3'
        with connect(transport) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT status FROM outbox ORDER BY id')],['sent','unknown'])
            self.assertTrue(all(r[0] for r in db.execute('SELECT channel_id FROM outbox')))
            self.assertEqual(db.execute("SELECT status FROM inbox WHERE event_id='legacy-id'").fetchone()[0],'imported')

    def test_ai_rate_and_confirmation_snapshot_changes(self):
        config={'enabled':True,'api_key':'mock','model':'deepseek-ai/DeepSeek-V3.2'}
        async def exercise():
            with patch('app.ai.interpret',return_value={'command':None,'explanation':'mock'}):
                for _ in range(5):
                    await interpret_limited(self.member,'hi',{},config,self.clock)
                result=await interpret_limited(self.member,'hi',{},config,self.clock)
                self.assertEqual(result['error'],'rate_limit')
        asyncio.run(exercise())

    def test_bot_form_and_global_export(self):
        self.assertEqual(self.client.post('/api/bots',headers=self.headers,json={'name':'拒绝隐式角色','chat_id':'42','admin_user_ids':['7']}).status_code,400)
        result=self.call('POST','/api/bots',{'name':'表单创建','chat_id':'42','active':True})
        self.assertIn('callback_url',result)
        self.assertEqual(result['admin_user_ids'],[])
        self.approve('7','角色测试会员')
        People(self.ctx,self.clock).set_roles('7',['club_admin'])
        self.assertEqual(People(self.platform.context(self.admin,self.bots[1]['id']),self.clock).get('7')['roles'],['club_admin'])
        self.call('PATCH',f"/api/bots/{result['id']}",{'active':False})
        self.approve('1','=FORMULA')
        self.s.enroll('1','=FORMULA',day='2026-10-13')
        response=self.client.get('/api/registrations/export?venue_id=1',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertIn("'=FORMULA",response.text)
