"""Robot capability boundaries across HTTP, AI plans and durable delivery."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import ai
from app.ai_catalog import get_feature, model_features
from app.ai_operations import execute_step
from app.ai_planner import validate_plan
from app.ai_routing import feature_descriptor
from app.commands import Commands
from app.db import TZ, connect
from app.identity_notifications import IdentityNotifications, record_identity_change
from app.main import create_app
from app.members import Members
from app.messages import Outbox
from app.notifications import Notifications
from app.scheduler import Scheduler
from app.service import Service
from app.bot_capabilities import ALL_CAPABILITIES


class BotIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.at = datetime(2026, 10, 19, 9, 29, tzinfo=TZ)
        self.clock = lambda: self.at
        self.token = 'test-v211-capability-integration-token'
        self.app = create_app(Path(self.temp.name), self.token, False, False, self.clock)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer ' + self.token}
        self.platform = self.app.state.platform
        self.principal = self.platform.authenticate(self.token)
        self.admin = self.platform.workspace_context()
        self.bots = [self.platform.create(self.principal, {
            'name': '机器人' + str(i), 'capabilities': list(ALL_CAPABILITIES), 'chat_id': 'shared-group', 'robot_key': 'integration-key-' + str(i),
            'webhook_url': 'https://xz.wps.cn/api/v1/webhook/send?key=integration-' + str(i),
        }) for i in range(2)]
        self.channels = [b['channels'][0]['id'] for b in self.bots]
        Members(self.admin, self.clock).touch('张三', 'member-one', source='admin')
        Members(self.admin, self.clock).review('member-one', 'approved')

    def ctx(self, index=0):
        return self.platform.context_for_channel(self.channels[index], 'member-one', False, 'callback')

    def caps(self, values, index=0):
        return self.platform.update(self.principal, self.bots[index]['id'], {'capabilities': values})

    def trusted(self, capabilities, **updates):
        return ai._trusted_context({'bot_id': self.bots[0]['id'], 'chat_id': 'shared-group',
            'user_id': 'member-one', 'today': '2026-10-19', 'venue_id': 1,
            'is_admin': True, 'approved': True, 'bot_capabilities': capabilities, **updates})

    def test_catalog_help_preview_and_private_callback_access(self):
        self.caps(['registration'])
        catalog = self.client.get('/api/bot-capabilities', headers=self.headers).json()['capabilities']
        self.assertEqual([c['id'] for c in catalog if c['required']], [])
        preview = self.client.get(f"/api/bots/{self.bots[0]['id']}/help-preview", headers=self.headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertNotIn('组织比赛', preview.json()['reply'])
        self.assertNotIn('AI帮助', preview.json()['reply'])
        route = f"/api/bots/{self.bots[0]['id']}/callback"
        self.assertEqual(self.client.get(route).status_code, 401)
        callback = self.client.get(route, headers=self.headers)
        self.assertTrue(callback.json()['callback_url'].endswith(self.bots[0]['callback_path']))
        self.assertEqual(callback.headers['cache-control'], 'no-store')
        visible = self.client.get('/api/bots', headers=self.headers).text
        self.assertNotIn('integration-key-', visible)
        self.assertNotIn('key=integration-', visible)

    def test_model_schemas_and_catalog_only_offer_supported_business(self):
        trusted = self.trusted(['ai','daily_events'])
        names = {tool.__name__ for tool in ai._tool_schemas(trusted)}
        self.assertIn('StartDailyPlan', names)
        self.assertNotIn('Register', names)
        self.assertNotIn('SelfLevel', names)
        self.assertNotIn('PersonalStats', names)
        self.assertNotIn('ToggleAI', names)
        features = {f['feature_id'] for f in ai._feature_catalog(trusted)}
        self.assertIn('daily.start', features)
        for forbidden in ('membership.register', 'registration.enroll', 'large.create', 'casual.record', 'stats.personal', 'bots.manage'):
            self.assertNotIn(forbidden, features)
        # A model cannot reintroduce a hidden tool in a compound plan.
        for op, args in [('Register', {}), ('ReviewMember', {'decision':'approved','member_name':'张三'}),
                         ('FeatureRoute', {'feature_id':'membership.register'})]:
            with self.subTest(op=op), self.assertRaises((ValueError, PermissionError)):
                validate_plan({'steps':[{'operation':op,'arguments':args}]}, trusted, '报名并通过张三的注册')

    def test_disabled_registration_route_does_not_create_form(self):
        self.caps(['ai'])
        with self.assertRaisesRegex(PermissionError, '未开启'):
            feature_descriptor(self.ctx(), 'membership.register', self.clock)
        with connect(self.ctx().transport_db_path) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='registration_forms'").fetchone()
            self.assertFalse(exists and db.execute('SELECT COUNT(*) FROM registration_forms').fetchone()[0])

    def test_execute_step_rereads_capability_and_no_model_network_when_ai_off(self):
        old = self.ctx()
        self.caps([])
        command = Commands(old, self.clock)
        with self.assertRaisesRegex(PermissionError, '未开启'):
            asyncio.run(execute_step(command, {'operation':'Register','arguments':{}}))
        from app.ai_gateway import interpret_limited
        with patch('app.ai.interpret') as model:
            with self.assertRaisesRegex(PermissionError, '未开启'):
                asyncio.run(interpret_limited(old, '报名', {}, {'enabled':True}, self.clock))
            model.assert_not_called()
        with connect(self.admin.db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM registrations').fetchone()[0], 0)

    def test_daily_scheduler_uses_eligible_same_group_bot_and_omits_registration_hint(self):
        self.caps([])
        self.caps(['enrollment'], 1)
        scheduler = Scheduler(self.platform, live=False, clock=self.clock)
        self.at = self.at.replace(minute=30)
        scheduler.tick()
        self.assertEqual(Outbox(self.ctx()).list(), [])
        messages = Outbox(self.ctx(1)).list()
        self.assertEqual(len(messages), 1)
        self.assertIn('报名', messages[0]['text'])
        self.assertNotIn('发送 注册', messages[0]['text'])
        self.assertEqual(asyncio.run(Outbox(self.ctx(1)).deliver_one(self.platform,at=self.at)), 'simulated')

    def test_manual_publish_uses_message_capability_not_group_management(self):
        self.caps([])
        self.caps(['messages'], 1)
        response = self.client.post('/api/messages/send', headers=self.headers,
            json={'text':'通知', 'channel_ids':self.channels})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(Outbox(self.ctx(1)).list()), 1)
        self.assertEqual(Outbox(self.ctx()).list(), [])
        self.assertEqual(asyncio.run(Outbox(self.ctx(1)).deliver_one(self.platform)), 'simulated')
        self.caps([], 1)
        denied = self.client.post('/api/messages/send', headers=self.headers,
            json={'text':'禁止发布', 'channel_ids':[self.channels[1]]})
        self.assertEqual(denied.status_code, 403)

    def test_queued_reply_revoked_when_capabilities_change_legacy_queue_stays_compatible(self):
        ctx = self.ctx()
        Outbox(ctx).enqueue('旧回复', 'legacy-reply')
        with connect(ctx.transport_db_path, True) as db:
            db.execute('UPDATE outbox SET scope_json=?', (json.dumps({'venues':None,'slots':None}),))
        self.assertEqual(asyncio.run(Outbox(ctx).deliver_one(self.platform)), 'simulated')
        Outbox(ctx).enqueue('报名名单', 'stale-roster')
        self.caps(['registration'])
        self.assertEqual(asyncio.run(Outbox(ctx).deliver_one(self.platform)), 'failed')

    def test_registration_notice_chooses_applicant_registration_bot_once(self):
        self.caps(['registration'])
        self.caps(['management'], 1)
        Members(self.ctx(), self.clock).touch('张三')
        with connect(self.admin.db_path, True) as db:
            eid = record_identity_change(db, self.admin, 'member-one', 'membership',
                {'status':'pending'}, {'status':'approved','name':'张三'}, self.at)
        notices = IdentityNotifications(self.platform, self.clock)
        result = notices.dispatch(eid)
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(result['deliveries'][0]['channel_id'], self.channels[0])
        self.assertEqual(len(Outbox(self.ctx()).list()), 1)
        self.assertEqual(Outbox(self.ctx(1)).list(), [])
        notices.dispatch(eid)
        self.assertEqual(len(Outbox(self.ctx()).list()), 1)

    def test_backend_global_admin_remains_independent_of_bot_modules(self):
        for i in range(2): self.caps([], i)
        self.assertEqual(self.client.get('/api/members', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/ai/capabilities', headers=self.headers).status_code, 200)

