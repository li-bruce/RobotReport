import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import io
import json
import re
import tempfile
import unittest
from unittest.mock import patch
import httpx
from PIL import Image
from fastapi.testclient import TestClient
from app.db import TZ, connect, stamp
from app.security import Platform, validate_webhook
from app.service import Service
from app.members import Members
from app.commands import Commands
from app.messages import Outbox, chunks
from app.media import Media
from app.scheduler import Scheduler
from app.main import create_app
from app.bot_capabilities import ALL_CAPABILITIES

TOKEN = 'test-platform-token-with-at-least-32-characters'


class CoreFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 12, 16, 0, tzinfo=TZ)
        self.clock = lambda: self.now
        self.platform = Platform(Path(self.temp.name), TOKEN)
        self.admin = self.platform.authenticate(TOKEN)
        self.bots = [self.platform.create(self.admin, {'name': name, 'capabilities': list(ALL_CAPABILITIES), 'chat_id': '266382', 'robot_key': 'robot-test', 'callback_token': 'callback-secret-01234567890123456789', 'legacy': i == 0}) for i, name in enumerate(['一号', '二号'])]
        self.ctx = self.platform.context(self.admin, self.bots[0]['id'])
        self.s = Service(self.ctx, self.clock)
        self.member = replace(self.ctx, actor='1001', is_admin=False, source='callback')
        for user in ('0','1','2','3','4','5','6','9','1001','1002','1003','1004','leader','other'):
            self.approve(user, '小李' if user == '1001' else '球友' + user)

    def approve(self, user, name=None, ctx=None):
        manager = Members(ctx or self.ctx, self.clock)
        manager.touch(name or '球友' + user, user, source='admin')
        return manager.review(user, 'approved')

    def pending_match(self, reply, ctx=None):
        from app.ai_conversation import AiConversation
        self.assertIn('确认',reply)
        self.assertNotRegex(reply,r'确认\s+[a-f0-9]{12}')
        token=AiConversation(ctx or self.member,self.clock).current()['token']
        return re.fullmatch(r'([a-f0-9]{12})',token)

    def commands(self, member=None):
        return Commands(member or self.member, self.clock)


class CoreTests(CoreFixture):
    def test_parallel_idempotent_registration_and_guests(self):
        member = Service(self.member, self.clock)
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda _: member.enroll('1001', '小李'), range(24)))
        member.enroll('1001', '小李', 2)
        rows = self.s.registrations()['registrations']
        self.assertEqual(len(rows), 3)
        member.enroll('1001', '小李', 1)
        rows = self.s.registrations()['registrations']
        self.assertEqual(sum(r['status'] == 'active' for r in rows), 4)
        self.assertEqual(member.cancel('1001')['cancelled'], 1)
        self.assertEqual(member.cancel('1001')['cancelled'], 0)
        self.assertEqual(sum(r['status'] == 'active' for r in member.registrations()['registrations']), 3)

    def test_substitute_and_waitlist_promote_in_order(self):
        self.s.save_slots(1, [{'name': '小场', 'weekdays': [0], 'start': '17:00', 'end': '22:00', 'capacity': 1, 'substitute_capacity': 1}])
        for user in ('1', '2', '3', '4'):
            self.s.enroll(user, user)
        rows = self.s.registrations()['registrations']
        self.assertEqual([r['category'] for r in rows], ['正式','替补','候补','候补'])
        self.s.cancel('1')
        active = [r for r in self.s.registrations()['registrations'] if r['status'] == 'active']
        self.assertEqual([r['user_id'] for r in active], ['2','3','4'])
        self.assertEqual([r['category'] for r in active], ['正式','替补','候补'])

    def test_admin_cancel_individual_keeps_group(self):
        self.s.enroll('1001', '甲', 2)
        rows = self.s.registrations()['registrations']
        self.s.cancel_person(rows[1]['id'])
        self.assertEqual(sum(r['status'] == 'active' for r in self.s.registrations()['registrations']), 2)

    def test_rule_changes_follow_future_bookings_and_keep_original_snapshot(self):
        self.s.enroll('1001', '甲', day='2026-10-13')
        original = self.s.sessions('2026-10-13')[0]
        rules = self.s.slots()
        rules[0].update(name='新场',start='18:00',capacity=20)
        self.s.save_slots(1,rules)
        preserved = self.s.sessions('2026-10-13')[0]
        self.assertEqual(preserved['id'], original['id'])
        self.assertEqual(preserved['capacity'], 20)
        self.assertEqual(preserved['start'], '18:00')
        self.assertEqual(self.s.registrations('2026-10-13')['registrations'][0]['session_snapshot']['start'],'17:30')
        self.assertEqual(self.s.sessions('2026-10-14')[0]['capacity'], 20)

    def test_special_dates_and_weekends(self):
        self.assertEqual(self.s.sessions('2026-10-17')[0]['start'], '18:00')
        self.s.save_slots(1, [], '2026-10-17')
        self.assertEqual(self.s.sessions('2026-10-17'), [])
        self.s.save_slots(1, [{'name':'午场','start':'12:00','end':'13:30'}], '2026-10-18')
        self.assertEqual(self.s.sessions('2026-10-18')[0]['start'], '12:00')

    def test_time_and_scope_validation(self):
        for kw in ({'day':'2026-02-30'}, {'day':'2026-10-11'}, {'day':'2028-10-12'}, {'at':'12:00'}, {'at':'25:00'}):
            with self.assertRaises(ValueError):
                self.s.enroll('1001', '甲', **kw)
        with self.assertRaises(ValueError):
            self.s.save_slots(1, [{'name':'跨天','weekdays':[0],'start':'22:00','end':'01:00'}])
        first = self.s.sessions()[0]
        self.now = self.now.replace(hour=20, minute=30)
        self.assertEqual(self.s.resolve_session()['number'], 2)
        with self.assertRaises(ValueError):
            self.s.enroll('1001','甲',session_id=first['id'])
        self.now = self.now.replace(hour=22)
        with self.assertRaises(ValueError):
            self.s.enroll('1001','甲')

    def test_command_variants_name_and_personal_permissions(self):
        router = self.commands()
        async def exercise():
            self.assertIn('已注册', await router.run('设置姓名 张三'))
            self.assertEqual(Members(self.member,self.clock).get()['name'], '小李')
            self.assertEqual(Members(self.member,self.clock).get()['status'], 'approved')
            Members(self.ctx,self.clock).review('1001','approved',name='张三')
            self.assertIn('张三', await router.run('报名+2 接龙'))
            self.assertIn('报名成功', await router.run('2026年10月13日20时报名'))
            self.assertIn('张三', await router.run('2026-10-13 名单'))
            self.assertIn('已取消', await router.run('取消报名'))
            with self.assertRaises(PermissionError):
                await router.run('统计')
            with self.assertRaises(PermissionError):
                await router.run('开启AI')
        asyncio.run(exercise())

    def test_bots_share_business_venues_separate_and_transport_grants_are_scoped(self):
        second = self.platform.context(self.admin, self.bots[1]['id'])
        self.s.enroll('1001','甲')
        self.assertEqual(Service(second, self.clock).registrations()['registrations'], self.s.registrations()['registrations'])
        Outbox(self.ctx).enqueue('只发一群')
        self.assertEqual(Outbox(second).list(), [])
        venue = self.s.save_venue({'name':'另一个场地'})
        self.assertEqual(self.s.registrations(venue_id=venue['id'])['registrations'], [])
        session_id = self.s.sessions()[0]['id']
        with self.assertRaises(ValueError):
            self.s.enroll('1','甲',venue_id=venue['id'],session_id=session_id)
        grant = self.platform.grant(self.admin, [self.ctx.id])
        user = self.platform.authenticate(grant['token'])
        self.assertEqual(self.platform.context(user).id, self.ctx.id)
        with self.assertRaises(PermissionError):
            self.platform.context(user, self.bots[1]['id'])
        self.assertEqual(self.platform.context(self.admin).scope_id, 'club')
        self.assertEqual(self.platform.context(self.admin).id, 'workspace')
        self.assertFalse(self.platform.context(user).is_admin)
        with self.assertRaises(PermissionError):
            Service(self.platform.context(user), self.clock).save_venue({'name':'越权'})
        with self.assertRaises(PermissionError):
            self.platform.create(user, {'name':'hack'})

    def test_callback_dedupe_keys_chat_and_legacy(self):
        app = create_app(self.temp.name, TOKEN, False, False, self.clock)
        path = self.bots[0]['callback_path']
        body = {'robot_key':'robot-test','chatid':266382,'creator':1001,'ctime':123456,'content':'报名+2'}
        with TestClient(app) as client:
            self.assertEqual(client.get(path).json(), {'result':'ok'})
            self.assertEqual(client.post(path, json=body).json(), {'result':'ok'})
            self.assertEqual(client.post(path, json=body).json(), {'result':'ok'})
            self.assertEqual(len(self.s.registrations()['registrations']), 3)
            self.assertEqual(len(Outbox(self.ctx).list()), 1)
            self.assertEqual(client.post(path, json=body | {'robot_key':'bad'}).status_code, 403)
            self.assertEqual(client.post(path, json=body | {'chatid':999}).status_code, 403)
            self.assertEqual(client.post('/kingsoft/callback/callback-secret-01234567890123456789', json=body).json(), {'result':'ok'})

    def test_admin_api_auth_secret_redaction_and_scope(self):
        app = create_app(self.temp.name, TOKEN, False, False, self.clock)
        grant = self.platform.grant(self.admin, [self.ctx.id])
        headers = {'Authorization':'Bearer ' + grant['token']}
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/config').status_code, 401)
            self.assertEqual(client.get('/api/config', headers=headers | {'X-Bot-ID':self.bots[1]['id']}).status_code, 403)
            self.assertEqual(client.get('/api/bots', headers=headers).json()['is_platform_admin'], False)
            self.assertEqual(client.patch('/api/config', headers=headers, json={'ai':{'api_key':'secret-test-only'}}).status_code, 403)
            platform_headers = {'Authorization':'Bearer ' + TOKEN}
            response = client.patch('/api/config', headers=platform_headers, json={'ai':{'api_key':'secret-test-only'}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('secret-test-only', response.text)
            self.assertTrue(response.json()['ai']['configured'])
            self.assertNotIn('secret-test-only', client.get('/api/audit',headers=platform_headers).text)
            self.assertEqual(client.post('/api/bots', headers=headers,json={'name':'X'}).status_code,403)

    def test_scheduler_no_catchup_and_durable_dedupe(self):
        # Both robots bind the same real group. Daily reminders are emitted
        # once per group; either configured bot may be the selected sender.
        def delivered():
            return [row for ctx in self.platform.system_contexts() for row in Outbox(ctx).list()]
        scheduler = Scheduler(self.platform, clock=self.clock)
        scheduler.tick()
        self.assertEqual(delivered(), [])
        self.now = self.now.replace(hour=17,minute=30)
        scheduler.tick()
        self.assertEqual(len(delivered()), 1)
        original_channel = delivered()[0]['channel_id']
        scheduler.tick()
        self.assertEqual(len(delivered()), 1)
        reboot = Scheduler(self.platform, clock=self.clock)
        reboot.tick()
        self.assertEqual(len(delivered()), 1)
        self.assertEqual(delivered()[0]['channel_id'], original_channel)
        self.assertIn('20:30', delivered()[0]['text'])

    def test_outbox_rate_limit_unknown_no_retry_and_chunking(self):
        out = Outbox(self.ctx)
        for i in range(25):
            out.enqueue('安全消息', f'test:{i}')
        async def exercise():
            for _ in range(20):
                self.assertEqual(await out.deliver_one(self.platform, at=self.now), 'simulated')
            self.assertIsNone(await out.deliver_one(self.platform, at=self.now))
        asyncio.run(exercise())
        self.assertEqual(sum(x['status']=='simulated' for x in out.list()),20)
        self.assertTrue(all(len(x)<5000 for x in chunks('张'*12000)))
        self.platform.update(self.admin,self.ctx.id,{'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=test'})
        async def failed():
            async def handler(request):
                raise httpx.ReadTimeout('secret URL should not be logged')
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                status = await out.deliver_one(self.platform,live=True,client=client,at=self.now+timedelta(minutes=2))
            self.assertEqual(status,'unknown')
        asyncio.run(failed())
        self.assertEqual(sum(x['status']=='unknown' for x in out.list()),1)
        out.recover()
        self.assertEqual(sum(x['status']=='unknown' for x in out.list()),1)

    def test_webhook_allowlist(self):
        validate_webhook('https://xz.wps.cn/api/v1/webhook/send?key=test')
        for value in ['http://xz.wps.cn/api/v1/webhook/send?key=a','https://127.0.0.1/a','https://xz.wps.cn@evil.test/api/v1/webhook/send?key=a','https://xz.wps.cn/api/v1/webhook/send?key=a&next=http://localhost']:
            with self.assertRaises(ValueError):
                validate_webhook(value)

    def test_upload_type_sanitization_and_expiry(self):
        manager = Media(self.ctx,self.clock)
        self.s.update_config({'public_base_url':'http://127.0.0.1:9874'})
        picture = io.BytesIO()
        Image.new('RGB',(8,8),'green').save(picture,format='PNG')
        row = manager.save('../../avatar.png',picture.getvalue()+b'<script>evil</script>')
        self.assertEqual(row['name'],'avatar.png')
        self.assertNotIn(b'<script>',manager.download_path(manager.get(row['id'])).read_bytes())
        for name, data in [('a.svg',b'<svg/>'),('a.png',b'<html>'),('a.txt',b'<script>alert(1)</script>')]:
            with self.assertRaises(ValueError):
                manager.save(name,data)
        link = manager.link(row['id'])
        token = link['url'].rsplit('.',1)[1]
        self.assertEqual(manager.public_download(token)['id'],row['id'])
        manager.revoke(row['id'])
        with self.assertRaises(PermissionError):
            manager.public_download(token)
        self.assertEqual(Media(self.platform.context(self.admin,self.bots[1]['id'])).get(row['id'])['id'], row['id'])
        foreign = Platform(Path(self.temp.name)/'separate-backend', TOKEN)
        with self.assertRaises(ValueError):
            Media(foreign.workspace_context()).get(row['id'])

    def test_ai_confirmation_once_and_cross_bot(self):
        self.s.update_config({'ai':{'enabled':True,'api_key':'test-value'}})
        router = self.commands()
        proposal = {'command':'场地1 2026-10-12 报名+1','explanation':'待确认','requires_confirmation':True,
                    'intent':'Register','arguments':{'guests':1},
                    'scope':{'bot_id':self.ctx.id,'chat_id':self.ctx.chat_id,'user_id':'1001'}}
        async def exercise():
            with patch('app.ai.interpret', return_value=proposal):
                text = await router.propose('帮我报名并带一人',1)
            token = self.pending_match(text)[1]
            self.assertEqual(self.s.registrations()['registrations'],[])
            other = replace(self.platform.context(self.admin,self.bots[1]['id']),actor='1001',is_admin=False)
            with self.assertRaises(ValueError):
                await self.commands(other).confirm(token,'甲')
            await router.confirm(token,'甲')
            with self.assertRaises(ValueError):
                await router.confirm(token,'甲')
        asyncio.run(exercise())
        self.assertEqual(len(self.s.registrations()['registrations']),2)


if __name__ == '__main__':
    unittest.main()
