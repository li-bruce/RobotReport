"""End-to-end typed plans: isolated previews, commit guards and honest receipts."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import re
from unittest.mock import patch

from app.ai_plan_store import AiPlanStore
from app.db import connect
from app.members import Members
from app.people import People
from app.service import Service
from tests.test_core import CoreFixture


class AiPlanFlowTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'local-fake-ai-key'}})
        self.addCleanup(patch.stopall)
        patch('app.competitions._now', self.clock).start()

    def step(self, op, **args):
        common = {'venue_id': 1, 'date': '2026-10-13', 'number': 1}
        if op in ('Register', 'Roster', 'CancelRegistration', 'StartDailyPlan', 'UpdateSession'):
            args = common | args
        if op == 'Register':
            args = {'guests': 0, 'guest_names': [], 'chain': False} | args
        if op == 'StartDailyPlan':
            args = {'discipline': 'doubles', 'doubles_category': 'open',
                    'assignment_strategy': 'random', 'title': '周一练习赛'} | args
        return {'operation': op, 'arguments': args}

    def proposal(self, steps, ctx=None):
        ctx = ctx or self.member
        return {'intent': 'BusinessPlan', 'steps': steps, 'requires_confirmation': True,
                'scope': {'bot_id': ctx.id, 'chat_id': ctx.chat_id, 'user_id': ctx.actor}}

    def flow(self, ctx=None):
        from app.ai_plan_flow import Plans
        return Plans(self.commands(ctx or self.member))

    def preview(self, steps, ctx=None):
        reply = asyncio.run(self.flow(ctx).propose(self.proposal(steps, ctx)))
        match = self.pending_match(reply,ctx)
        self.assertIsNotNone(match, reply)
        return match[1], reply

    def confirm(self, token, ctx=None):
        return asyncio.run(self.flow(ctx).confirm(token))

    def count(self, table, ctx=None, transport=False):
        ctx = ctx or self.ctx
        with connect(ctx.transport_db_path if transport else ctx.db_path) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                return 0
            return db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def refused(self, action):
        try:
            result = action()
        except (ValueError, PermissionError):
            return
        self.assertRegex(result, '未执行|失败|变化|失效|过期|使用|权限|不属于|负责|未完成|停止|取消|不支持')

    def member_registrations(self, user='1001'):
        with connect(self.ctx.db_path) as db:
            return [dict(r) for r in db.execute('SELECT * FROM registrations WHERE user_id=?', (user,))]

    def grant_admin(self):
        People(self.ctx, self.clock).set_roles('1001', ['club_admin'])

    def test_register_then_roster_previews_without_writes_and_commits_once(self):
        sessions = self.count('sessions')
        token, preview = self.preview([self.step('Register'), self.step('Roster')])
        self.assertIn('确认', preview)
        self.assertEqual(self.count('registrations'), 0)
        self.assertEqual(self.count('sessions'), sessions, '隔离预览不能在生产库创建活动场次')
        self.assertEqual(self.count('outbox', transport=True), 0, '预览不能发送群消息')
        self.assertEqual(AiPlanStore(self.member, self.clock).get(token)['status'], 'pending')
        result = self.confirm(token)
        self.assertIn('小李', result)
        self.assertEqual(len(self.member_registrations()), 1)
        record = AiPlanStore(self.member, self.clock).get(token)
        self.assertEqual(record['status'], 'completed')
        self.assertEqual([s['status'] for s in record['steps']], ['done', 'done'])
        self.refused(lambda: self.confirm(token))
        self.assertEqual(len(self.member_registrations()), 1)

    def test_commands_route_business_plan_and_confirmation(self):
        proposal = self.proposal([self.step('Register'), self.step('Roster')])
        with patch('app.ai_gateway.interpret_limited', return_value=proposal) as interpret:
            reply = asyncio.run(self.commands().run('AI 明晚第一场帮我占个位，随后把名单给我看'))
        interpret.assert_called_once()
        token = self.pending_match(reply)
        self.assertIsNotNone(token, reply)
        self.assertEqual(self.count('registrations'), 0)
        result = asyncio.run(self.commands().run('AI确认 ' + token[1]))
        self.assertIn('小李', result)
        self.assertEqual(len(self.member_registrations()), 1)

    def test_update_session_then_register_and_roster_uses_ordered_new_settings(self):
        self.grant_admin()
        session = self.s.sessions('2026-10-13')[0]
        token, _ = self.preview([
            self.step('UpdateSession', capacity=12, substitute_capacity=6),
            self.step('Register'), self.step('Roster')])
        self.assertEqual(self.s.sessions('2026-10-13')[0]['capacity'], session['capacity'])
        self.assertEqual(self.count('registrations'), 0)
        self.confirm(token)
        final = self.s.sessions('2026-10-13')[0]
        self.assertEqual((final['capacity'], final['substitute_capacity']), (12, 6))
        self.assertEqual(len(self.member_registrations()), 1)
        self.assertEqual(AiPlanStore(self.member, self.clock).get(token)['status'], 'completed')

    def test_second_step_preview_failure_writes_nothing_real(self):
        self.refused(lambda: asyncio.run(self.flow().propose(self.proposal([
            self.step('Register'), self.step('Register', number=99)]))))
        self.assertEqual(self.count('registrations'), 0)
        self.assertEqual(self.count('outbox', transport=True), 0)
        self.assertEqual(self.count('ai_pending', transport=True), 0)

    def test_nonadmin_config_step_rejects_entire_plan_before_registration(self):
        self.refused(lambda: asyncio.run(self.flow().propose(self.proposal([
            self.step('Register'), self.step('UpdateSession', capacity=12)]))))
        self.assertEqual(self.count('registrations'), 0)
        self.assertEqual(self.count('ai_pending', transport=True), 0)

    def test_role_revocation_between_preview_and_confirm_rejects_every_step(self):
        self.grant_admin()
        original = self.s.sessions('2026-10-13')[0]['capacity']
        token, _ = self.preview([self.step('UpdateSession', capacity=12), self.step('Register')])
        People(self.ctx, self.clock).set_roles('1001', [])
        self.refused(lambda: self.confirm(token))
        self.assertEqual(self.s.sessions('2026-10-13')[0]['capacity'], original)
        self.assertEqual(self.count('registrations'), 0)

    def test_membership_suspension_between_preview_and_confirm_rejects_write(self):
        token, _ = self.preview([self.step('Register'), self.step('Roster')])
        Members(self.ctx, self.clock).review('1001', 'suspended')
        self.refused(lambda: self.confirm(token))
        self.assertEqual(self.count('registrations'), 0)

    def test_scope_change_between_preview_and_confirm_rejects_write(self):
        sessions = self.s.sessions('2026-10-13')
        token, _ = self.preview([self.step('Register'), self.step('Roster')])
        self.platform.update(self.admin, self.ctx.id, {'slot_keys': [sessions[1]['slot_key']]})
        self.refused(lambda: self.confirm(token))
        self.assertEqual(self.count('registrations'), 0)

    def test_restricted_robot_can_confirm_unchanged_allowed_slot(self):
        sessions = self.s.sessions('2026-10-13')
        self.platform.update(self.admin, self.ctx.id, {'slot_keys': [sessions[1]['slot_key']]})
        token, _ = self.preview([self.step('Register', number=2), self.step('Roster', number=2)])
        result = self.confirm(token)
        self.assertIn('已完成', result)
        self.assertEqual(self.member_registrations()[0]['session_id'], sessions[1]['id'])

    def test_registration_change_requires_repreview_instead_of_silent_retargeting(self):
        token, _ = self.preview([self.step('Register'), self.step('Roster')])
        self.s.enroll('1002', '球友1002', day='2026-10-13', number=1)
        self.refused(lambda: self.confirm(token))
        self.assertEqual(len(self.member_registrations('1002')), 1)
        self.assertEqual(len(self.member_registrations()), 0)

    def test_same_group_other_robot_and_other_user_cannot_confirm_or_read(self):
        token, _ = self.preview([self.step('Register'), self.step('Roster')])
        other_bot = replace(self.platform.context(self.admin, self.bots[1]['id']),
                            actor='1001', source='callback', is_admin=False)
        other_user = replace(self.member, actor='1002')
        for other in (other_bot, other_user):
            with self.subTest(bot=other.id, user=other.actor):
                self.refused(lambda: self.confirm(token, other))
                self.refused(lambda: self.flow(other).status(token))
        self.assertEqual(self.count('registrations'), 0)
        self.confirm(token)
        self.assertEqual(len(self.member_registrations()), 1)

    def test_start_daily_plan_then_generate_schedule_is_previewed_without_events(self):
        for user in ('1001', '1002', '1003', '1004'):
            self.s.enroll(user, '小李' if user == '1001' else '球友' + user,
                          day='2026-10-12', number=1)
        original_events = self.count('tour_events')
        token, _ = self.preview([
            self.step('StartDailyPlan', date='2026-10-12', title='周一练习赛'),
            self.step('DailyPlan', venue_id=1, format='round_robin')])
        self.assertEqual(self.count('tour_events'), original_events)
        self.assertEqual(self.count('daily_planning', transport=True), 0)
        result = self.confirm(token)
        self.assertIn('周一练习赛', result)
        self.assertEqual(self.count('tour_events'), original_events + 1)
        self.assertEqual(AiPlanStore(self.member, self.clock).get(token)['status'], 'completed')

    def test_partial_runtime_failure_keeps_success_and_progress_receipts(self):
        from app import ai_operations
        token, _ = self.preview([self.step('Register'), self.step('Roster')])
        original = ai_operations.execute_step
        async def fail_second(commands, step):
            if step['operation'] == 'Roster':
                raise ValueError('测试模拟：名单读取暂时失败')
            return await original(commands, step)
        # Flow imports the executor at module scope. Only actual confirmation is
        # patched; both preview operations have already passed normally.
        with patch('app.ai_plan_flow.execute_step', side_effect=fail_second):
            self.refused(lambda: self.confirm(token))
        self.assertEqual(len(self.member_registrations()), 1)
        record = AiPlanStore(self.member, self.clock).get(token)
        self.assertEqual(record['status'], 'partial')
        self.assertEqual([s['status'] for s in record['steps']], ['done', 'failed'])
        self.assertIn('报名', record['steps'][0]['result'])
        self.assertIn('名单读取', record['steps'][1]['error'])
        progress = self.flow().status(token)
        self.assertRegex(progress, '部分|失败|未完成')
        self.assertIn('报名', progress)
        self.refused(lambda: self.confirm(token))
        self.assertEqual(len(self.member_registrations()), 1)

    def test_changed_config_during_own_plan_does_not_invalidate_later_steps(self):
        self.grant_admin()
        token, _ = self.preview([
            self.step('UpdateReminders', open='10:00', daily_enabled=False),
            self.step('Register'), self.step('Roster')])
        self.assertTrue(self.s.config()['daily_enabled'])
        self.confirm(token)
        self.assertEqual(self.s.config()['reminders']['open'], '10:00')
        self.assertFalse(self.s.config()['daily_enabled'])
        self.assertEqual(len(self.member_registrations()), 1)
        self.assertEqual(AiPlanStore(self.member, self.clock).get(token)['status'], 'completed')

    def test_cancelled_preview_never_runs(self):
        token, _ = self.preview([self.step('Register'), self.step('Roster')])
        self.assertIn('取消', self.flow().cancel(token))
        self.refused(lambda: self.confirm(token))
        self.assertEqual(self.count('registrations'), 0)

    def test_callback_member_touch_timestamp_does_not_invalidate_preview(self):
        proposal = self.proposal([self.step('Register'), self.step('Roster')])
        with patch('app.ai_gateway.interpret_limited', return_value=proposal):
            reply = asyncio.run(self.commands().run('AI 明晚第一场帮我占个位，随后把名单给我看'))
        token = self.pending_match(reply)[1]
        self.now += timedelta(minutes=2)
        result = asyncio.run(self.commands().run('AI确认 ' + token))
        self.assertIn('已完成', result)
        self.assertEqual(len(self.member_registrations()), 1)

    def test_external_change_between_successful_steps_stops_remaining_writes(self):
        from app import ai_plan_flow
        token, _ = self.preview([self.step('Register'), self.step('Register', number=2), self.step('Roster')])
        original = ai_plan_flow.snapshot
        mutated = False
        def snapshot_then_external_change(commands):
            nonlocal mutated
            value = original(commands)
            if self.member_registrations() and not mutated:
                mutated = True
                # This happens after the previous step's new baseline was read,
                # modelling another request committing before the next step.
                Members(self.ctx, self.clock).review('1003', 'suspended')
            return value
        with patch('app.ai_plan_flow.snapshot', side_effect=snapshot_then_external_change):
            self.refused(lambda: self.confirm(token))
        self.assertTrue(mutated)
        self.assertEqual(len(self.member_registrations()), 1)
        result = AiPlanStore(self.member, self.clock).get(token)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([s['status'] for s in result['steps']], ['done', 'failed', 'skipped'])
        self.assertIn('变化', result['steps'][1]['error'])

    def test_default_session_preview_shows_target_and_never_drifts_after_it_ends(self):
        self.now = self.now.replace(hour=20, minute=28)
        registration = self.step('Register', date='2026-10-12')
        registration['arguments'].pop('number')
        token, preview = self.preview([registration])
        self.assertIn('第一场', preview)
        self.assertIn('17:30', preview)
        self.assertIn('20:30', preview)
        saved = AiPlanStore(self.member, self.clock).get(token)['steps'][0]['arguments']
        self.assertEqual(saved['_resolved_session']['number'], 1)
        self.now += timedelta(minutes=3)  # Confirmation is valid, but first activity ended.
        self.refused(lambda: self.confirm(token))
        self.assertEqual(self.count('registrations'), 0)
        record = AiPlanStore(self.member, self.clock).get(token)
        self.assertEqual(record['status'], 'failed')
        self.assertIn('结束', record['steps'][0]['error'])

    def test_daily_plan_non_success_message_cannot_be_marked_done(self):
        for user in ('1001', '1002', '1003', '1004'):
            self.s.enroll(user, '小李' if user == '1001' else '球友' + user,
                          day='2026-10-12', number=1)
        steps = [self.step('StartDailyPlan', date='2026-10-12'),
                 self.step('DailyPlan', venue_id=1, format='round_robin')]
        with patch('app.planning.DailyPlanning.create', return_value='本次策划已在处理中，请稍后查询'):
            self.refused(lambda: asyncio.run(self.flow().propose(self.proposal(steps))))
        self.assertEqual(self.count('tour_events'), 0)
        self.assertEqual(self.count('daily_planning', transport=True), 0)
        self.assertEqual(self.count('ai_pending', transport=True), 0)
