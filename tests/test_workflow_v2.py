import asyncio
from dataclasses import replace
from datetime import timedelta
import io
import re
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from app.db import connect
from app.main import create_app
from app.members import Members
from app.messages import Outbox, table, mention
from app.table_images import render_pages, table_blocks, cleanup, publish_tables
from app.planning import DailyPlanning
from app.tournaments import Tournaments
from tests.test_core import CoreFixture, TOKEN

class WorkflowV2Tests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.client=TestClient(create_app(self.temp.name,TOKEN,False,False,self.clock))
        self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer '+TOKEN,'X-Bot-ID':self.ctx.id}
        patcher=patch('app.competitions._now', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def organized(self, title='周二双打'):
        for user in ('1001','1','2','3','4','5'):
            self.s.enroll(user,'ignored')
        router=self.commands()
        self.assertIn('每日策划',asyncio.run(router.run('组织比赛 双打 '+title)))
        self.assertEqual(DailyPlanning(self.member,self.clock).get(1)['payload']['title'],title)
        self.assertIn('VS',asyncio.run(router.run('积分赛')))
        event=Tournaments(self.ctx,self.clock).list()[0]
        self.assertEqual(event['title'],title)
        return event
    def test_registration_required_name_optional_phone_level_and_review(self):
        user=replace(self.member,actor='new-person')
        router=self.commands(user)
        with self.assertRaises(PermissionError): asyncio.run(router.run('名单'))
        self.assertIn('姓名、性别必填',asyncio.run(router.run('注册')))
        self.assertIn('已提交',asyncio.run(router.run('注册 李明 男 2级')))
        m=Members(self.ctx,self.clock).get('new-person')
        self.assertEqual((m['name'],m['status'],m['level'],m['requested_level']),('李明','pending',None,2))
        with self.assertRaises(PermissionError): asyncio.run(router.run('报名'))
        Members(self.ctx,self.clock).review('new-person','approved')
        self.assertIn('李明',asyncio.run(router.run('报名')))
        self.assertNotIn('new-person',asyncio.run(router.run('名单')))
        self.assertIn('|',asyncio.run(router.run('我的统计')))
    def test_form_binds_chat_sender_is_one_time_and_does_not_post_phone(self):
        self.s.update_config({'public_base_url':'http://127.0.0.1:9874'})
        actor=replace(self.member,actor='form-user')
        reply=asyncio.run(self.commands(actor).run('注册'))
        ref=re.search(r'/register#([^)]*)',reply)[1]
        url='/public/registration/'+ref
        self.assertEqual(self.client.get(url).status_code,200)
        response=self.client.post(url,json={'name':'王晓','gender':'female','phone':'13812345678','level':3})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('13812345678',response.text)
        self.assertNotIn('command',response.json())
        self.assertEqual(response.json()['member_status'],'pending')
        again=self.client.post(url,json={'name':'改名不能覆盖','level':9})
        self.assertEqual(again.status_code,200,again.text)
        self.assertEqual(again.json()['name'],'王晓')
        record=Members(self.ctx,self.clock).get('form-user')
        self.assertEqual(record['phone'],'13812345678')
        self.assertNotIn('13812345678',asyncio.run(self.commands(actor).run('注册资料')))
        with connect(self.ctx.transport_db_path) as db:
            self.assertIsNone(db.execute('select payload from registration_forms').fetchone()[0])
    def test_planning_requires_activation_defaults_doubles_and_persists(self):
        for user in ('1','2','3','4'): self.s.enroll(user,'irrelevant')
        router=self.commands()
        self.assertIn('请先发送 组织比赛',asyncio.run(router.run('积分赛')))
        reply=asyncio.run(router.run('组织比赛'))
        self.assertIn('双打',reply)
        self.assertIn('4人',reply)
        self.assertIn('| 赛制',reply)
        reply=asyncio.run(self.commands().run('积分赛比赛'))
        self.assertIn('|',reply)
        from app.tournaments import Tournaments
        event=Tournaments(self.ctx,self.clock).get(1)
        self.assertEqual(event['options']['discipline'],'doubles')
        self.assertEqual(len(event['matches']),1)
        self.assertEqual(len(event['matches'][0]['side_a']['members']),2)
        self.assertIn('已生成赛事',asyncio.run(self.commands().run('积分赛')))
        self.assertEqual(len(Tournaments(self.ctx,self.clock).list()),1)
    def test_callback_without_name_never_enrolls_id_as_name(self):
        path=self.bots[0]['callback_path']
        base={'robot_key':'robot-test','chatid':266382,'creator':888,'ctime':8881,'content':'注册 赵明 男'}
        self.assertEqual(self.client.post(path,json=base).status_code,200)
        profile=Members(self.ctx,self.clock).get('888')
        self.assertEqual(profile['name'],'赵明')
        self.assertEqual(profile['status'],'pending')
        response=self.client.post(path,json=base|{'ctime':8882,'content':'报名'})
        self.assertEqual(response.status_code,200)
        self.assertFalse(any(r['user_id']=='888' for r in self.s.registrations()['registrations']))
    def test_tables_become_readable_png_and_expire_without_duplicate_files(self):
        self.s.update_config({'public_base_url':'http://127.0.0.1:9874'})
        content='# 报名名单\n\n'+table(['姓名','级别'],[['张三','2级'],['王晓 | 李四','无级别']])
        self.assertEqual(len(list(table_blocks(content))),1)
        # Creation and expiry must use the same fixture clock, not wall time.
        with patch('app.table_images.publish_tables', side_effect=lambda text, ctx, db: publish_tables(text, ctx, db, self.clock)):
            ids=Outbox(self.ctx).enqueue(content,'v2table')
        self.assertEqual(ids,Outbox(self.ctx).enqueue(content,'v2table'))
        payload=Outbox(self.ctx).list()[0]['text']
        url=re.search(r'http://127.0.0.1:9874(/public/tables/[^)]*)',payload)[1]
        response=self.client.get(url)
        self.assertEqual(response.status_code,200,response.text[:100] if response.status_code!=200 else '')
        with Image.open(io.BytesIO(response.content)) as image:
            self.assertGreater(image.width,100)
            self.assertGreater(image.height,100)
        with connect(self.ctx.db_path) as db: self.assertEqual(db.execute('select count(*) from generated_tables').fetchone()[0],1)
        self.now+=timedelta(days=2)
        cleanup(self.ctx,self.clock)
        self.assertEqual(self.client.get(url).status_code,403)
    def test_table_pagination_carries_every_row_and_at_is_not_bare_id(self):
        pages=list(render_pages(['姓名'],[[f'成员{i}'] for i in range(45)]))
        self.assertEqual(len(pages),3)
        self.assertEqual(mention('12345'),'<at user_id="12345"></at>')
        with self.assertRaises(ValueError): mention('1\" onclick=evil')
    def test_password_login_cookie_csrf_change_and_legacy_api(self):
        old='local-test-password-for-auth'
        new='replacement-test-password-for-auth'
        auth=self.client.app.state.passwords
        auth.set_initial(old)
        self.assertEqual(self.client.get('/api/bots').status_code,401)
        login=self.client.post('/auth/login',json={'password':old})
        self.assertEqual(login.status_code,200,login.text)
        self.assertIn('HttpOnly',login.headers['set-cookie'])
        self.assertIn('SameSite=strict',login.headers['set-cookie'])
        csrf=login.json()['csrf_token']
        self.assertNotIn('session_token',login.json())
        self.assertEqual(self.client.get('/api/bots').status_code,200)
        self.assertEqual(self.client.post('/api/bots',json={'name':'forbidden'}).status_code,403)
        self.assertEqual(self.client.post('/api/bots',json={'name':'allowed'},headers={'X-CSRF-Token':csrf}).status_code,200)
        self.assertEqual(self.client.post('/api/bots',json={'name':'cross'},headers={'X-CSRF-Token':csrf,'Origin':'https://evil.example'}).status_code,403)
        response=self.client.post('/auth/password',json={'current_password':old,'new_password':new},headers={'X-CSRF-Token':csrf})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/bots').status_code,401)
        self.assertEqual(self.client.post('/auth/login',json={'password':old}).status_code,403)
        self.assertEqual(self.client.post('/auth/login',json={'password':new}).status_code,200)
        self.assertEqual(self.client.get('/api/bots',headers=self.headers).status_code,200)
    def test_named_guests_and_substitute_planning_with_capacity_edit(self):
        reply=asyncio.run(self.commands().run('报名，AAA，BBB'))
        self.assertIn('AAA',reply)
        self.assertIn('BBB',reply)
        self.assertIn('代报',reply)
        records=self.s.registrations()['registrations']
        self.assertEqual([r['name'] for r in records],['小李','AAA','BBB'])
        self.assertTrue(all(r['is_proxy'] for r in records[1:]))
        self.assertTrue(all(r['level'] is None for r in records[1:]))
        session=self.s.sessions()[0]
        self.s.save_session_constraints(session['id'],{'capacity':2,'substitute_capacity':3})
        self.s.enroll('1','irrelevant')
        reply=asyncio.run(self.commands().run('组织比赛'))
        self.assertIn('替补',reply)
        reply=asyncio.run(self.commands().run('积分赛'))
        event=Tournaments(self.ctx,self.clock).get(1)
        self.assertEqual(len(event['participants']),4)
        self.assertEqual(len(event['options']['selected_substitute_names']),2)
        self.assertIn('代报',reply)

    def test_named_flexible_scoring_creator_corrections_have_versioned_history(self):
        summary=self.organized()
        router=self.commands()
        self.assertIn('（21）',asyncio.run(router.run('周二双打，一21—19')))
        event=Tournaments(self.ctx,self.clock).get(summary['id'])
        first=next(m for m in event['matches'] if m['number']==1)
        self.assertEqual((first['scores'],first['version']),([[21,19]],1))
        self.assertIn('（17）',asyncio.run(router.run('周二双打，1 21/17')))
        event=Tournaments(self.ctx,self.clock).get(summary['id'])
        first=next(m for m in event['matches'] if m['number']==1)
        self.assertEqual((first['scores'],first['version']),([[21,17]],2))
        with connect(self.ctx.db_path) as db:
            corrections=list(db.execute("SELECT actor,before_json,after_json FROM tour_audit WHERE action='score_correction' AND tournament_id=?",(summary['id'],)))
        self.assertEqual(len(corrections),1)
        self.assertEqual(corrections[0]['actor'],'1001')
        self.assertIn('19',corrections[0]['before_json'])
        self.assertIn('17',corrections[0]['after_json'])

    def test_non_creator_cannot_score_pause_resume_or_close(self):
        summary=self.organized()
        outsider=self.commands(replace(self.member,actor='1'))
        for command in ('周二双打，第1场21:19','周二双打，暂停比赛','周二双打，关闭比赛'):
            with self.subTest(command=command),self.assertRaises(PermissionError):
                asyncio.run(outsider.run(command))
        self.assertIn('已暂停',asyncio.run(self.commands().run('周二双打，暂停比赛')))
        with self.assertRaises(PermissionError):
            asyncio.run(outsider.run('周二双打，继续比赛'))
        with self.assertRaises(ValueError):
            asyncio.run(self.commands().run('周二双打，1场21:19'))
        self.assertIn('已恢复',asyncio.run(self.commands().run('周二双打，继续比赛')))
        self.assertIn('已关闭',asyncio.run(self.commands().run('周二双打，关闭比赛')))
        event=Tournaments(self.ctx,self.clock).get(summary['id'])
        self.assertEqual(event['status'],'cancelled')
        self.assertTrue(event['matches'])
        self.assertFalse(any(m['scores'] for m in event['matches']))

    def test_participant_personal_schedule_filters_matches_and_readonly_actions(self):
        summary=self.organized()
        participant=replace(self.member,actor='1')
        event=Tournaments(self.ctx,self.clock).get(summary['id'])
        participant_ids={p['id'] for p in event['participants'] if p['user_id']=='1'}
        expected={m['number'] for m in event['matches'] if any(participant_ids.intersection((m.get(side) or {}).get('members',[])) for side in ('side_a','side_b'))}
        text=asyncio.run(self.commands(participant).run('周二双打，我的赛程'))
        shown={int(value) for value in re.findall(r'\| 第(\d+)场 \|',text)}
        self.assertEqual(shown,expected)
        self.assertLess(len(shown),len(event['matches']))
        for command in ('我的赛程','周二双打，积分表','周二双打，对战表'):
            self.assertIn('|',asyncio.run(self.commands(participant).run(command)))

    def test_duplicate_event_names_and_ambiguous_compact_scores_never_write(self):
        self.organized('相同赛事')
        self.organized('相同赛事')
        for command in ('相同赛事，第1场21:19','相同赛事，1221-19'):
            with self.subTest(command=command),self.assertRaises(ValueError):
                asyncio.run(self.commands().run(command))
        for event in Tournaments(self.ctx,self.clock).list():
            self.assertFalse(any(m['scores'] for m in Tournaments(self.ctx,self.clock).get(event['id'])['matches']))

    def test_registration_without_name_does_not_treat_rating_or_phone_as_name(self):
        user=replace(self.member,actor='not-yet-registered')
        router=self.commands(user)
        for command in ('注册 2级','注册 自评3级','注册 13812345678','注册 2级 13812345678'):
            with self.subTest(command=command),self.assertRaises(ValueError):
                asyncio.run(router.run(command))
        member=Members(self.ctx,self.clock).get(user.actor)
        self.assertEqual(member['name'],'')
        self.assertEqual(member['status'],'unregistered')
        self.assertIsNone(member['requested_level'])
