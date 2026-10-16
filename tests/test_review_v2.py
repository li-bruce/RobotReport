"""Independent boundary and replay checks for the member/platform revision."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.commands import Commands
from app.db import TZ, connect
from app.main import create_app
from app.members import Members
from app.messages import table
from app.planning import DailyPlanning
from app.registration import create_form, submit_form, form_status, confirm_form
from app.security import Platform
from app.service import Service
from app.table_images import publish_tables, public_table, cleanup


class BoundaryReviewTests(unittest.TestCase):
    TOKEN = 'review-test-only-platform-token-0123456789'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.time = datetime(2026, 10, 13, 9, 0, tzinfo=TZ)
        self.clock = lambda: self.time
        self.platform = Platform(Path(self.temp.name), self.TOKEN)
        self.principal = self.platform.authenticate(self.TOKEN)
        bots = [self.platform.create(self.principal, {'name': name, 'chat_id': 'same-chat', 'robot_key': 'test-key'}) for name in ('甲', '乙')]
        self.admin = self.platform.context(self.principal, bots[0]['id'])
        self.other_admin = self.platform.context(self.principal, bots[1]['id'])
        self.user = replace(self.admin, actor='owner', is_admin=False, source='callback')
        self.other_user = replace(self.other_admin, actor='owner', is_admin=False, source='callback')
        Service(self.admin, self.clock).update_config({'public_base_url': 'https://example.com'})

    def form(self):
        reference = create_form(self.user, self.clock).split('#')[1]
        return reference.split('.', 1)[1]

    def test_registration_link_direct_submit_binds_user_and_is_idempotent(self):
        token = self.form()
        public = replace(self.admin, actor='public', is_admin=False, source='public')
        with self.assertRaises(ValueError):
            submit_form(self.other_user, token, {'name': '错误机器人'}, self.clock)
        result = submit_form(public, token, {'name': '张三', 'gender': 'male', 'level': 9, 'phone': '13812345678'}, self.clock)
        self.assertNotIn('command', result)
        self.assertNotIn('13812345678', json.dumps(result))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['member_status'], 'pending')
        self.assertEqual(form_status(public, token, self.clock)['status'], 'completed')
        replay = submit_form(public, token, {'name': '被替换姓名'}, self.clock)
        self.assertEqual(replay['name'], '张三')
        member = Members(self.user, self.clock).get()
        self.assertEqual((member['name'],member['status'],member['requested_level']), ('张三','pending',9))
        self.assertIsNone(member['level'])
        with connect(self.user.transport_db_path) as db:
            row = db.execute('SELECT payload,code FROM registration_forms').fetchone()
        self.assertEqual(tuple(row), (None, None))

    def test_new_link_supersedes_old_and_forms_expire(self):
        first = self.form()
        second = self.form()
        with self.assertRaises(ValueError):
            submit_form(self.user, first, {'name': '张三'}, self.clock)
        self.time += timedelta(minutes=20)
        with self.assertRaises(ValueError):
            submit_form(self.user, second, {'name': '张三'}, self.clock)

    def test_chat_registration_reply_and_all_rosters_omit_phone(self):
        reply = asyncio.run(Commands(self.user, self.clock).run('注册 张三 男 2级 13812345678'))
        self.assertNotIn('13812345678', reply)
        with self.assertRaises(PermissionError):
            asyncio.run(Commands(self.user, self.clock).run('名单'))
        Members(self.admin, self.clock).review('owner', 'approved')
        replies = [asyncio.run(Commands(self.user, self.clock).run(command)) for command in ('注册资料', '报名', '名单', '我的统计')]
        self.assertNotIn('13812345678', '\n'.join(replies))
        self.assertNotIn('13812345678', json.dumps(Service(self.admin, self.clock).audit()))

    def test_cookie_login_mutations_require_csrf_and_same_origin(self):
        app = create_app(self.temp.name, self.TOKEN, False, False, self.clock)
        app.state.passwords.set_initial('review-test-only-password-1234')
        with TestClient(app) as client:
            response = client.post('/auth/login', json={'password': 'review-test-only-password-1234'}, headers={'origin': 'http://testserver'})
            self.assertEqual(response.status_code, 200)
            cookie = response.headers['set-cookie'].lower()
            self.assertIn('httponly', cookie)
            self.assertIn('samesite=strict', cookie)
            csrf = response.json()['csrf_token']
            headers = {'x-bot-id': self.admin.id}
            self.assertEqual(client.patch('/api/config', headers=headers, json={'capacity': 9}).status_code, 403)
            self.assertEqual(client.patch('/api/config', headers=headers | {'x-csrf-token': csrf, 'origin': 'https://unrelated.example'}, json={'capacity': 9}).status_code, 403)
            self.assertEqual(client.patch('/api/config', headers=headers | {'x-csrf-token': csrf, 'origin': 'http://testserver'}, json={'capacity': 9}).status_code, 200)
            self.assertEqual(client.post('/auth/logout', headers={'x-csrf-token': csrf}).status_code, 200)
            self.assertNotEqual(client.get('/api/bots').status_code, 200)

    def test_non_ascii_csrf_is_a_denial_not_server_error(self):
        app = create_app(self.temp.name, self.TOKEN, False, False, self.clock)
        app.state.passwords.set_initial('review-test-only-password-1234')
        with TestClient(app, raise_server_exceptions=False) as client:
            self.assertEqual(client.post('/auth/login', json={'password': 'review-test-only-password-1234'}).status_code, 200)
            response = client.patch('/api/config', headers=[(b'x-bot-id', self.admin.id.encode()), (b'x-csrf-token', b'\xff')], json={'capacity': 9})
            self.assertEqual(response.status_code, 403)

    def test_table_capability_is_workspace_scoped_expires_and_is_cleaned(self):
        with connect(self.admin.db_path, True) as db:
            text = publish_tables('# 报名\n\n' + table(['姓名', '等级'], [['张三', '2级']]), self.admin, db, self.clock)
        reference = text.split('/public/tables/')[1].split(')')[0]
        token = reference.split('.', 1)[1]
        image = public_table(self.user, token, self.clock)
        self.assertTrue(image.is_file())
        self.assertEqual(public_table(self.other_user, token, self.clock), image)
        foreign = Platform(Path(self.temp.name)/'separate-backend', self.TOKEN).workspace_context()
        with self.assertRaises(PermissionError):
            public_table(foreign, token, self.clock)
        self.time += timedelta(hours=24)
        with self.assertRaises(PermissionError):
            public_table(self.user, token, self.clock)
        cleanup(self.user, self.clock)
        self.assertFalse(image.exists())

    def test_pending_member_cannot_read_or_modify_other_bot_member_data(self):
        app = create_app(self.temp.name, self.TOKEN, False, False, self.clock)
        Members(self.user, self.clock).apply('张三', phone='13812345678', gender='male')
        grant = self.platform.grant(self.principal, [self.other_admin.id])
        headers = {'authorization': 'Bearer ' + grant['token'], 'x-bot-id': self.admin.id}
        with TestClient(app) as client:
            for path in ('/api/members', '/api/members/owner', '/api/registrations'):
                response = client.get(path, headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn('13812345678', response.text)

    def test_daily_planning_recovers_event_committed_before_status_update(self):
        manager = Members(self.admin, self.clock)
        for index in range(4):
            uid = 'owner' if index == 0 else f'p{index}'
            manager.review(uid, 'approved', name=f'选手{index}')
            Service(self.admin, self.clock).enroll(uid, 'unused')
        planner = DailyPlanning(self.user, self.clock)
        planner.begin({'venue_id': 1})
        record = planner.get(1)
        event = planner.events.create({'kind': 'daily', 'format': 'round_robin', 'venue_id': 1,
                                       'date': record['payload']['date'], 'session_id': record['payload']['session_id'],
                                       'options': {'discipline': 'doubles', 'planner_token': record['token']}})
        with connect(self.user.transport_db_path, True) as db:
            db.execute("UPDATE daily_planning SET status='creating' WHERE token=?", (record['token'],))
        text = planner.create(1, 'round_robin')
        self.assertNotIn('正在生成', text)
        self.assertEqual(planner.get(1)['status'], 'created')
        self.assertEqual(planner.get(1)['event_id'], event['id'])
        self.assertEqual(len(planner.events.list()), 1)


if __name__ == '__main__':
    unittest.main()
