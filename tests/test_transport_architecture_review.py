from tests.legacy_registry import seed_legacy_channel
"""Independent architecture boundary review. No live message delivery."""
import asyncio
import importlib
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.db import TZ, connect
from app.members import Members
from app.messages import Outbox
from app.notifications import Notifications
from app.registration import create_form, submit_form, confirm_form
from app.security import Platform
from app.service import Service


class TransportArchitectureReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.token = 'independent-review-token-32-characters'
        self.platform = Platform(self.root, self.token)
        self.admin = self.platform.authenticate(self.token)
        self.a = self.platform.create(self.admin, {'name': '入口甲', 'chat_id': 'group-one', 'robot_key': 'robot-key-one'})
        self.a1 = self.a['channels'][0]['id']
        self.a2 = seed_legacy_channel(self.platform, self.a['id'], {'name': '第二群', 'chat_id': 'group-two', 'robot_key': 'robot-key-two'})['id']
        self.b = self.platform.create(self.admin, {'name': '入口乙', 'chat_id': 'group-three', 'robot_key': 'robot-key-three'})
        self.b1 = self.b['channels'][0]['id']
        self.now = datetime(2026, 10, 13, 16, tzinfo=TZ)
        self.clock = lambda: self.now

    def tearDown(self):
        self.tmp.cleanup()

    def context(self, channel, actor='u1'):
        return self.platform.context_for_channel(channel, actor, False, 'callback')

    def test_registration_submission_uses_token_origin_not_public_context_group(self):
        admin_ctx = self.platform.workspace_context()
        Service(admin_ctx, self.clock).update_config({'public_base_url': 'http://127.0.0.1:9874'})
        origin = self.context(self.a2)
        url = create_form(origin, self.clock)
        token = url.split('#', 1)[1].split('.', 1)[1]
        public_ctx = replace(self.context(self.a1), actor='public', source='public')
        with self.assertRaises(ValueError):
            submit_form(self.context(self.b1), token, {'name': '错误机器人'}, self.clock)
        submitted = submit_form(public_ctx, token, {'name': '张三', 'gender': 'male', 'phone': '13800138000', 'level': 9}, self.clock)
        self.assertEqual(submitted['status'], 'completed')
        self.assertNotIn('command', submitted)
        member = Members(origin, self.clock).get()
        self.assertEqual((member['name'],member['status'],member['requested_level']), ('张三','pending',9))
        other_admin = replace(self.context(self.b1), is_admin=True)
        self.assertEqual(Members(other_admin, self.clock).get('u1')['name'], '张三')
        replay = submit_form(public_ctx, token, {'name': '不覆盖姓名'}, self.clock)
        self.assertEqual(replay['name'], '张三')
        with connect(origin.transport_db_path) as db:
            row = db.execute('SELECT payload,code FROM registration_forms').fetchone()
            self.assertEqual(tuple(row), (None, None))

    def test_legacy_multiple_groups_do_not_share_replies_or_delivery_claims(self):
        one, two = self.context(self.a1), self.context(self.a2)
        first = Outbox(one).enqueue('甲群回复', 'same-business-key')
        second = Outbox(two).enqueue('乙群回复', 'same-business-key')
        self.assertNotEqual(first, second)
        self.assertEqual(len(Outbox(one).list()), 1)
        self.assertIn('甲群回复', Outbox(one).list()[0]['text'])
        self.assertEqual(asyncio.run(Outbox(two).deliver_one(self.platform, live=False)), 'simulated')
        self.assertEqual(Outbox(one).list()[0]['status'], 'pending')
        self.assertEqual(Outbox(two).list()[0]['status'], 'simulated')

    def test_notification_recovery_does_not_enqueue_a_destination_twice(self):
        notices = Notifications(self.platform, self.clock)
        job = notices.enqueue('开赛通知', [self.a1, self.a2, self.b1], key='competition:one')
        original = {channel: [m['id'] for m in Outbox(self.context(channel)).list()] for channel in (self.a1, self.a2, self.b1)}
        with connect(notices.path, True) as db:
            db.execute("UPDATE notification_jobs SET status='pending' WHERE id=?", (job['id'],))
            db.execute("UPDATE notification_deliveries SET status='pending',outbox_ids='[]' WHERE job_id=?", (job['id'],))
        Notifications(self.platform, self.clock).recover()
        for channel, ids in original.items():
            self.assertEqual([m['id'] for m in Outbox(self.context(channel)).list()], ids)

    def test_queued_venue_notice_cannot_send_after_robot_loses_venue_permission(self):
        Service(self.platform.workspace_context(), self.clock).save_venue({'name': '第二场地'})
        self.platform.update(self.admin, self.a['id'], {'venue_ids': [1]})
        notices = Notifications(self.platform, self.clock)
        notices.enqueue('场地一的报名名单', [self.a1], key='venue-one-private-roster', venue_id=1)
        self.platform.update(self.admin, self.a['id'], {'venue_ids': [2]})
        result = asyncio.run(Outbox(self.context(self.a1)).deliver_one(self.platform, live=False))
        self.assertEqual(result, 'failed')
        self.assertEqual(Outbox(self.context(self.a1)).list()[0]['status'], 'failed')

    def test_daily_automatic_notice_selects_one_bot_per_physical_group(self):
        from app.scheduler import Scheduler
        duplicate = seed_legacy_channel(self.platform, self.b['id'], {'name': '同一目的群', 'chat_id': 'group-one', 'robot_key': 'other-bot-key'})['id']
        self.now = self.now.replace(hour=9, minute=29)
        scheduler = Scheduler(self.platform, live=False, clock=self.clock)
        self.now = self.now.replace(hour=9, minute=30)
        scheduler.tick()
        scheduler.tick()
        count = sum(len(Outbox(self.context(cid)).list()) for cid in (self.a1, self.a2, self.b1, duplicate))
        self.assertEqual(count, 3)

    def test_one_disabled_destination_does_not_block_valid_group_delivery(self):
        self.platform.save_channel(self.admin, self.a['id'], {'active': False}, self.a2)
        notices = Notifications(self.platform, self.clock)
        job = notices.enqueue('仍应通知有效群', [self.a1, self.a2], key='competition:partial')
        states = {item['channel_id']: item['status'] for item in job['deliveries']}
        self.assertEqual(states[self.a1], 'queued')
        self.assertEqual(states[self.a2], 'failed')
        self.assertEqual(len(Outbox(self.context(self.a1)).list()), 1)

    def test_outbox_dedupe_key_is_literal_not_sql_like_pattern(self):
        outbox = Outbox(self.context(self.a1))
        first = outbox.enqueue('消息一', 'source:alphaX1')
        second = outbox.enqueue('消息二', 'source:alpha_1')
        self.assertNotEqual(first, second)
        self.assertEqual(len(outbox.list()), 2)

    def test_same_physical_group_selects_one_sender_if_multiple_bots_are_bound(self):
        duplicate = seed_legacy_channel(self.platform, self.b['id'], {'name': '同一目的群', 'chat_id': 'group-one', 'robot_key': 'other-bot-key'})['id']
        notices = Notifications(self.platform, self.clock)
        job = notices.enqueue('群目的地去重', [self.a1, duplicate], key='competition:one-destination')
        self.assertEqual(job['channel_ids'], [self.a1])
        self.assertEqual(len(job['deliveries']), 1)
        count = len(Outbox(self.context(self.a1)).list()) + len(Outbox(self.context(duplicate)).list())
        self.assertEqual(count, 1)

    def test_bot_operations_token_cannot_read_or_mutate_shared_business(self):
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {'ROBORT_STATE_DIR': str(self.root / 'module-import-state'), 'ROBORT_LIVE_SEND': '0'}):
            main = importlib.import_module('app.main')
        app = main.create_app(self.root, self.token, start_scheduler=False, live=False, clock=self.clock)
        grant = self.platform.grant(self.admin, [self.a['id']])
        headers = {'Authorization': 'Bearer ' + grant['token']}
        with TestClient(app) as client:
            for path in ('/api/members', '/api/tournaments', '/api/workspace', '/api/roles', '/api/messages'):
                self.assertEqual(client.get(path, headers=headers).status_code, 403, path)
            self.assertEqual(client.post('/api/simulator', json={'user_id': 'u1', 'channel_id': self.a1, 'content': '注册 张三'}, headers=headers).status_code, 403)
            channels = client.get('/api/channels', headers=headers)
            self.assertEqual(channels.status_code, 200)
            self.assertEqual({row['bot_id'] for row in channels.json()}, {self.a['id']})


if __name__ == '__main__':
    unittest.main()
