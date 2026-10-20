"""Cross-module capability guards: pending AI, event targets and delivery."""
import asyncio
from datetime import timedelta
import json
import re
from unittest.mock import patch

from app.ai_operations import execute_step
from app.ai_plan_flow import Plans
from app.ai_plan_store import AiPlanStore
from app.bot_capabilities import ALL_CAPABILITIES
from app.db import connect, stamp
from app.messages import Outbox
from app.members import Members
from app.people import People
from app.identity_notifications import IdentityNotifications
from app.notifications import Notifications
from app.tournaments import Tournaments
from tests.test_core import CoreFixture


class BotCapabilityReviewTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'isolated-test-key'}})
        self.patcher = patch('app.competitions._now', self.clock)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def disable(self, *capabilities):
        self.platform.update(self.admin, self.ctx.id, {
            'capabilities': [key for key in ALL_CAPABILITIES if key not in capabilities]})

    def row_count(self, table):
        with connect(self.ctx.db_path) as db:
            return db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def preview(self):
        steps = [
            {'operation': 'Register', 'arguments': {'venue_id': 1, 'date': '2026-10-13',
                'number': 1, 'guests': 0, 'guest_names': [], 'chain': False}},
            {'operation': 'Roster', 'arguments': {'venue_id': 1, 'date': '2026-10-13', 'number': 1}},
        ]
        proposal = {'intent': 'BusinessPlan', 'steps': steps, 'requires_confirmation': True,
            'scope': {'bot_id': self.member.id, 'chat_id': self.member.chat_id, 'user_id': self.member.actor}}
        commands = self.commands()
        reply = asyncio.run(Plans(commands).propose(proposal))
        token = self.pending_match(reply)
        self.assertIsNotNone(token, reply)
        return commands, token[1]

    def test_multistep_plan_after_enrollment_revoke_never_starts_first_write(self):
        commands, token = self.preview()
        self.disable('enrollment')
        with self.assertRaises((PermissionError, ValueError)):
            asyncio.run(commands.run('AI确认 ' + token))
        self.assertEqual(self.row_count('registrations'), 0)
        record = AiPlanStore(commands.ctx, self.clock).get(token)
        self.assertEqual(record['status'], 'failed')
        self.assertTrue(all(item['status'] == 'skipped' for item in record['steps']))

    def test_multistep_plan_after_ai_revoke_never_starts_first_write(self):
        commands, token = self.preview()
        self.disable('ai')
        with self.assertRaisesRegex(PermissionError, 'AI'):
            asyncio.run(commands.run('AI确认 ' + token))
        self.assertEqual(self.row_count('registrations'), 0)

    def test_revoke_between_steps_retains_first_receipt_and_blocks_later_operation(self):
        commands, token = self.preview()
        from app import ai_operations
        original = ai_operations.execute_step

        async def revoke_after_first(router, step):
            result = await original(router, step)
            if step['operation'] == 'Register':
                self.disable('enrollment')
            return result

        with patch('app.ai_plan_flow.execute_step', side_effect=revoke_after_first):
            reply = asyncio.run(commands.run('AI确认 ' + token))
        self.assertIn('中止', reply)
        self.assertEqual(self.row_count('registrations'), 1)
        record = AiPlanStore(commands.ctx, self.clock).get(token)
        self.assertEqual(record['status'], 'partial')
        self.assertEqual([item['status'] for item in record['steps']], ['done', 'failed'])
        self.assertIn('活动报名', record['steps'][1]['error'])

    def test_model_wait_cannot_restore_revoked_enrollment(self):
        commands = self.commands()

        async def revoke_while_waiting(*args, **kwargs):
            self.disable('enrollment')
            return {'intent': 'Register', 'command': '场地1 2026-10-13 报名', 'arguments': {},
                'requires_confirmation': True, 'scope': {'bot_id': self.member.id,
                    'chat_id': self.member.chat_id, 'user_id': self.member.actor}}

        with patch('app.ai.interpret', side_effect=revoke_while_waiting):
            with self.assertRaisesRegex(PermissionError, '活动报名'):
                asyncio.run(commands.run('AI 明天帮我占一个位置'))
        self.assertEqual(self.row_count('registrations'), 0)
        with connect(self.ctx.transport_db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ai_pending').fetchone()[0], 0)

    def large_event(self, running=False):
        events = Tournaments(self.ctx, self.clock)
        event = events.create({'kind': 'large', 'title': '审核杯', 'keyword': '审核杯',
            'format': 'round_robin', 'date': self.now.date().isoformat(), 'venue_id': 1,
            'options': {'discipline': 'singles'},
            'registration_open': stamp(self.now - timedelta(hours=1)),
            'registration_close': stamp(self.now + timedelta(hours=1)),
            'participants': [{'id': uid, 'user_id': uid, 'name': '球友' + uid}
                             for uid in ('1001', '1002', '1003', '1004')],
            'staff': [{'user_id': '1001', 'role': 'organizer',
                       'starts_at': stamp(self.now - timedelta(minutes=1)),
                       'expires_at': stamp(self.now + timedelta(hours=2))}]})
        event = events.transition(event['id'], 'open_registration')
        if running:
            events.transition(event['id'], 'close_registration')
            event = events.transition(event['id'], 'start')
        return event

    def test_large_event_direct_name_id_keyword_and_staff_routes_remain_blocked(self):
        event = self.large_event()
        self.disable('large_events')
        before = json.dumps(Tournaments(self.platform.workspace_context(), self.clock).get(event['id']), sort_keys=True)
        inputs = [f"赛事 {event['id']} 报名", f"赛事 {event['id']} 名单", '审核杯报名',
                  '审核杯，报名，同行', '审核杯，取消报名', '审核杯，对战表',
                  '审核杯，公开说明 已变更', '审核杯，关闭比赛']
        for text in inputs:
            with self.subTest(text=text), self.assertRaisesRegex(PermissionError, '大型赛事'):
                asyncio.run(self.commands().run(text))
        self.assertEqual(json.dumps(Tournaments(self.platform.workspace_context(), self.clock).get(event['id']), sort_keys=True), before)
        self.assertNotIn('审核杯', asyncio.run(self.commands().run('对战表')))
        self.assertNotIn(event['id'], [item['id'] for item in self.commands().ai_context(1)['events']])

    def test_large_event_typed_ai_query_score_and_resume_cannot_bypass_hidden_catalog(self):
        event = self.large_event(running=True)
        self.disable('large_events')
        before = json.dumps(Tournaments(self.platform.workspace_context(), self.clock).get(event['id']), sort_keys=True)
        steps = [
            ('TournamentQuery', {'view': 'schedule'}),
            ('RecordScore', {'match_id': 1, 'scores': [[21, 19]]}),
            ('ResumeTournament', {}),
            ('EventAction', {'action': 'pause'}),
        ]
        for operation, arguments in steps:
            with self.subTest(operation=operation), self.assertRaisesRegex(PermissionError, '大型赛事'):
                asyncio.run(execute_step(self.commands(), {'operation': operation,
                    'arguments': {'tournament_id': event['id'], 'venue_id': 1, **arguments}}))
        self.assertEqual(json.dumps(Tournaments(self.platform.workspace_context(), self.clock).get(event['id']), sort_keys=True), before)

    def test_ai_resume_resolves_daily_event_without_hidden_large_candidate(self):
        for user in ('1001', '1002', '1003', '1004'):
            self.s.enroll(user, '球友' + user)
        daily = Tournaments(self.member, self.clock).create({
            'kind': 'daily', 'format': 'round_robin', 'title': '每日练习'})
        large = self.large_event(running=True)
        self.assertEqual({row['id'] for row in Tournaments(self.member, self.clock).resume_candidates()},
                         {daily['id'], large['id']})
        self.disable('large_events')
        reply = asyncio.run(execute_step(self.commands(), {'operation': 'ResumeTournament',
            'arguments': {'venue_id': 1}}))
        self.assertIn('每日练习', reply)
        self.assertNotIn('审核杯', reply)

    def test_queued_message_is_cancelled_after_capability_change_without_network(self):
        outbox = Outbox(self.platform.context_for_channel(self.ctx.channel_id))
        ids = outbox.enqueue('报名名单', 'capability-review')
        self.disable('enrollment')
        self.assertEqual(asyncio.run(outbox.deliver_one(self.platform, live=False)), 'failed')
        with connect(outbox.path) as db:
            row = db.execute('SELECT * FROM outbox WHERE id=?', (ids[0],)).fetchone()
        self.assertEqual(row['attempts'], 0)

    def test_daily_only_notice_is_allowed_but_large_event_notice_is_failed(self):
        for user in ('1001', '1002', '1003', '1004'):
            self.s.enroll(user, '球友' + user)
        daily = Tournaments(self.member, self.clock).create({'kind': 'daily', 'format': 'round_robin'})
        large = self.large_event()
        self.platform.update(self.admin, self.ctx.id, {'capabilities': ['daily_events']})
        notifications = Notifications(self.platform, self.clock)
        allowed = notifications.enqueue('每日对战表', [self.ctx.channel_id], tournament_id=daily['id'], venue_id=1)
        self.assertEqual(allowed['deliveries'][0]['status'], 'queued')
        blocked = notifications.enqueue('大型比赛报名', [self.ctx.channel_id], tournament_id=large['id'], venue_id=1)
        self.assertEqual(blocked['deliveries'][0]['status'], 'failed')
        self.assertEqual(blocked['deliveries'][0]['outbox_ids'], [])

    def test_management_robot_approves_but_registration_robot_notifies_applicant(self):
        self.platform.save_channel(self.admin, self.ctx.id, {'webhook_url':
            'https://xz.wps.cn/api/v1/webhook/send?key=review-management-notice'}, self.ctx.channel_id)
        applicant = self.platform.context_for_channel(self.ctx.channel_id, 'new-student', False, 'callback')
        Members(applicant, self.clock).touch('新会员')
        Members(applicant, self.clock).apply('新会员', gender='男')
        People(self.ctx, self.clock).set_roles('1001', ['member_manager'])
        self.platform.update(self.admin, self.ctx.id, {'capabilities': ['management']})
        self.platform.update(self.admin, self.bots[1]['id'], {'capabilities': ['registration']})
        second = self.platform.context(self.admin,self.bots[1]['id'])
        self.platform.save_channel(self.admin,second.id,{'webhook_url':
            'https://xz.wps.cn/api/v1/webhook/send?key=review-registration-notice'},second.channel_id)
        reply = asyncio.run(self.commands().run('审核通过 新会员'))
        self.assertIn('通过', reply)
        with connect(self.ctx.db_path) as db:
            event_id = db.execute("SELECT id FROM identity_notification_events WHERE user_id='new-student' AND kind='membership' ORDER BY rowid DESC").fetchone()[0]
        item = IdentityNotifications(self.platform, self.clock).dispatch(event_id)
        self.assertEqual(item['status'], 'queued')
        self.assertEqual(item['deliveries'][0]['channel_id'], second.channel_id)
        text = Outbox(second).list()[0]['payload']['markdown']['text']
        self.assertIn('<at user_id="new-student">新会员</at>', text)
        self.assertIn('注册审核已通过', text)

    def test_registration_only_robot_cannot_deliver_role_change_notice(self):
        self.platform.save_channel(self.admin, self.ctx.id, {'webhook_url':
            'https://xz.wps.cn/api/v1/webhook/send?key=review-registration-role'}, self.ctx.channel_id)
        Members(self.member, self.clock).touch('小李')
        self.platform.update(self.admin, self.ctx.id, {'capabilities': ['registration']})
        self.platform.update(self.admin, self.bots[1]['id'], {'capabilities': []})
        People(self.ctx, self.clock).set_roles('1001', ['member_manager'])
        with connect(self.ctx.db_path) as db:
            event_id = db.execute("SELECT id FROM identity_notification_events WHERE user_id='1001' AND kind='roles' ORDER BY rowid DESC").fetchone()[0]
        item = IdentityNotifications(self.platform, self.clock).dispatch(event_id)
        self.assertEqual(item['status'], 'blocked')
        self.assertEqual(item['deliveries'][0]['channel_id'], '')
        self.assertIn('群内管理', item['last_error'])
        self.assertEqual(Outbox(self.ctx).list(), [])
