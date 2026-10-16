from tests.legacy_registry import seed_legacy_channel
"""AI entry, live authorization and confirmation regressions; no provider calls."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import re
from unittest.mock import patch

from app import ai
from app.commands import Commands
from app.db import connect, stamp
from app.members import Members
from app.people import People
from app.service import Service
from app.tournaments import Tournaments
from tests.test_ai import FakeRunnable
from tests.test_core import CoreFixture


class AIWorkflowTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'local-fake-ai-key'}})
        self.patcher = patch('app.competitions._now', self.clock)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def run_command(self, text, ctx=None):
        return asyncio.run(self.commands(ctx).run(text))

    def count(self, table, where='', transport=False):
        path = self.ctx.transport_db_path if transport else self.ctx.db_path
        with connect(path) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                return 0
            return db.execute('SELECT COUNT(*) FROM ' + table + (' WHERE ' + where if where else '')).fetchone()[0]

    def proposal(self, command='场地1 2026-10-12 报名', ctx=None, intent='Register', arguments=None):
        ctx = ctx or self.member
        return {'command': command, 'requires_confirmation': True, 'intent': intent,
                'arguments': arguments or {},
                'scope': {'bot_id': ctx.id, 'chat_id': ctx.chat_id, 'user_id': ctx.actor}}

    def pending(self, command='场地1 2026-10-12 报名', router=None, intent='Register'):
        router = router or self.commands()
        with patch('app.ai.interpret', return_value=self.proposal(command, router.ctx, intent)):
            reply = asyncio.run(router.propose('请帮我办理', 1))
        token = self.pending_match(reply,router.ctx)
        self.assertIsNotNone(token, reply)
        return token[1], router

    def rejected(self, action):
        """Router may surface a deterministic refusal or a normal business error."""
        try:
            result = action()
        except (ValueError, PermissionError):
            return
        self.assertRegex(result, '关闭|未开启|已失效|过期|变化|权限|停用|取消|不支持|未识别|请重新|未执行')

    def test_fixed_commands_and_unprefixed_unknown_never_call_ai(self):
        with patch('app.ai.interpret') as interpret:
            for text in ('帮助', '名单', '我的统计', 'AI', 'AI帮助', 'AI状态',
                         '我想和朋友明天一起打球', 'AIX 报名', '请 AI 帮我报名'):
                with self.subTest(text=text):
                    self.run_command(text)
            interpret.assert_not_called()
        self.assertEqual(self.count('registrations'), 0)
        self.assertEqual(self.count('ai_pending', transport=True), 0)

    def test_explicit_prefixes_only_send_body_to_model(self):
        for prefix in ('AI ', 'ai ', 'AI:', 'AI：', '/ai '):
            with self.subTest(prefix=prefix), patch('app.ai.interpret', return_value={
                'command': None, 'explanation': '测试建议'}) as interpret:
                self.run_command(prefix + '帮我看看报名名单')
                interpret.assert_called_once()
                self.assertEqual(interpret.call_args.args[0], '帮我看看报名名单')
        self.assertEqual(self.count('registrations'), 0)

    def test_unapproved_and_suspended_members_cannot_call_ai(self):
        for status in ('pending', 'suspended'):
            Members(self.ctx, self.clock).review('1001', status)
            with self.subTest(status=status), patch('app.ai.interpret') as interpret:
                with self.assertRaises(PermissionError):
                    self.run_command('AI 帮我报名')
                interpret.assert_not_called()

    def test_pending_proposal_only_original_bot_group_and_user_can_confirm(self):
        token, router = self.pending()
        other_channel = seed_legacy_channel(self.platform, self.ctx.id, {
            'name': '另一群', 'chat_id': 'another-room', 'robot_key': 'another-key'})
        wrong_contexts = (
            replace(self.member, actor='1002'),
            self.platform.context_for_channel(other_channel['id'], '1001', False, 'callback'),
            replace(self.platform.context(self.admin, self.bots[1]['id']), actor='1001', is_admin=False, source='callback'),
        )
        for ctx in wrong_contexts:
            with self.subTest(bot=ctx.id, group=ctx.chat_id, actor=ctx.actor):
                with self.assertRaises((ValueError, PermissionError)):
                    asyncio.run(self.commands(ctx).confirm(token, '小李'))
                self.assertEqual(self.count('registrations'), 0)
        asyncio.run(router.run('AI确认 ' + token))
        self.assertEqual(self.count('registrations', "status='active'"), 1)
        self.rejected(lambda: asyncio.run(router.run('AI确认 ' + token)))
        self.assertEqual(self.count('registrations', "status='active'"), 1)

    def test_ai_cancel_consumes_only_current_identity_pending_token(self):
        token, router = self.pending()
        reply = asyncio.run(router.run('AI取消 ' + token))
        self.assertIn('取消', reply)
        self.rejected(lambda: asyncio.run(router.run('AI确认 ' + token)))
        self.assertEqual(self.count('registrations'), 0)

    def test_backend_ai_disable_invalidates_existing_confirmation(self):
        token, router = self.pending()
        self.s.update_config({'ai': {'enabled': False}})
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        self.assertEqual(self.count('registrations'), 0)
        self.assertEqual(self.count('ai_pending', "status='pending'", transport=True), 0)
        self.s.update_config({'ai': {'enabled': True}})
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        self.assertEqual(self.count('registrations'), 0)

    def test_stale_admin_context_rechecks_role_before_confirming(self):
        People(self.ctx, self.clock).set_roles('1001', ['club_admin'])
        stale = replace(self.member, is_admin=True)
        token, router = self.pending('关闭AI', self.commands(stale), 'ToggleAI')
        People(self.ctx, self.clock).set_roles('1001', [])
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        self.assertTrue(self.s.config()['ai']['enabled'])

    def test_stale_admin_context_cannot_bypass_member_suspension(self):
        People(self.ctx, self.clock).set_roles('1001', ['club_admin'])
        token, router = self.pending(router=self.commands(replace(self.member, is_admin=True)))
        Members(self.ctx, self.clock).review('1001', 'suspended')
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        self.assertEqual(self.count('registrations'), 0)

    def test_role_revoked_during_model_wait_cannot_read_all_member_statistics(self):
        People(self.ctx, self.clock).set_roles('1001', ['club_admin'])
        router = self.commands(replace(self.member, is_admin=True))
        async def revoke_then_propose(*args, **kwargs):
            People(self.ctx, self.clock).set_roles('1001', [])
            return self.proposal('场地1 统计', router.ctx, 'AllStats') | {'requires_confirmation': False}
        with patch('app.ai.interpret', side_effect=revoke_then_propose):
            self.rejected(lambda: asyncio.run(router.run('AI 查看全部会员统计')))

    def test_group_disabled_after_proposal_cannot_confirm(self):
        token, router = self.pending()
        self.platform.save_channel(self.admin, self.ctx.id, {'receive_commands': False}, self.ctx.channel_id)
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        self.assertEqual(self.count('registrations'), 0)

    def test_robot_venue_scope_reduction_rejects_cached_context(self):
        venue = self.s.save_venue({'name': '第二场地'})
        self.s.save_slots(venue['id'], [{'name': '晚场', 'weekdays': [0], 'start': '17:30', 'end': '22:00'}])
        self.platform.update(self.admin, self.ctx.id, {'venue_ids': [1, venue['id']]})
        ctx = self.platform.context_for_channel(self.ctx.channel_id, '1001', False, 'callback')
        token, router = self.pending(f"场地{venue['id']} 2026-10-12 报名", self.commands(ctx))
        self.platform.update(self.admin, self.ctx.id, {'venue_ids': [1]})
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        self.assertEqual(self.count('registrations'), 0)

    def seed_planning(self, *, capacity=8, substitutes=4):
        self.s.save_slots(1, [{'name': '晚场', 'weekdays': [0], 'start': '17:30', 'end': '22:00',
                               'capacity': capacity, 'substitute_capacity': substitutes}])
        for uid in ('1001', '1002', '1003', '1004'):
            self.s.enroll(uid, uid)
        self.run_command('组织比赛')

    def test_daily_plan_confirmation_rejects_changed_mode_and_strategy(self):
        self.seed_planning()
        for change in ('单打', '分配 高带低'):
            with self.subTest(change=change):
                token, router = self.pending('场地1 积分赛', intent='DailyPlan')
                self.run_command(change)
                self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
                self.assertEqual(self.count('tour_events'), 0)

    def test_daily_plan_confirmation_rejects_substitute_pool_change(self):
        self.seed_planning(capacity=3)
        token, router = self.pending('场地1 积分赛', intent='DailyPlan')
        self.s.cancel('1004')
        self.s.enroll('2', '球友2')
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        self.assertEqual(self.count('tour_events'), 0)

    def test_daily_plan_confirmation_uses_selected_session_instead_of_default(self):
        for uid in ('1001', '1002', '1003', '1004'):
            self.s.enroll(uid, uid, at='20:30')
        self.run_command('2026-10-12 20:30 组织比赛')
        token, router = self.pending('场地1 积分赛', intent='DailyPlan')
        asyncio.run(router.confirm(token, '小李'))
        event = Tournaments(self.ctx, self.clock).list()[0]
        event = Tournaments(self.ctx, self.clock).get(event['id'])
        expected = self.s.resolve_session(at='20:30')
        self.assertEqual(event['options']['session_id'], expected['id'])
        self.assertEqual(len(event['participants']), 4)

    def test_daily_paused_resume_only_offers_manage_permission(self):
        self.seed_planning()
        event = Tournaments(self.member, self.clock).create({'kind': 'daily', 'format': 'round_robin'})
        Tournaments(self.member, self.clock).transition(event['id'], 'pause')
        self.assertIn(event['id'], self.commands().ai_context(1)['resumable_tournament_ids'])
        participant = replace(self.member, actor='1002')
        self.assertNotIn(event['id'], self.commands(participant).ai_context(1)['resumable_tournament_ids'])

    def large_event(self, role='organizer'):
        events = Tournaments(self.ctx, self.clock)
        event = events.create({'kind': 'large', 'title': '权限测试杯', 'keyword': '权限测试杯', 'format': 'round_robin',
            'date': self.now.date().isoformat(), 'venue_id': 1, 'options': {'discipline': 'singles'},
            'registration_open': stamp(self.now - timedelta(hours=1)),
            'registration_close': stamp(self.now + timedelta(hours=1)),
            'participants': [{'id': uid, 'user_id': uid, 'name': uid} for uid in ('1001', '1002', '1003', '1004')],
            'staff': [{'user_id': '1001', 'role': role, 'starts_at': stamp(self.now - timedelta(minutes=1)),
                       'expires_at': stamp(self.now + timedelta(minutes=2))}]})
        events.transition(event['id'], 'open_registration')
        events.transition(event['id'], 'close_registration')
        return events.transition(event['id'], 'start')

    def test_large_temporary_organizer_resume_context_and_expiry(self):
        event = self.large_event()
        Tournaments(self.ctx, self.clock).transition(event['id'], 'pause')
        self.assertIn(event['id'], self.commands().ai_context(1)['resumable_tournament_ids'])
        token, router = self.pending(f"场地1 继续比赛 {event['id']}", intent='ResumeTournament')
        self.now += timedelta(minutes=3)
        self.assertNotIn(event['id'], self.commands().ai_context(1)['resumable_tournament_ids'])
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        self.assertEqual(Tournaments(self.ctx, self.clock).get(event['id'])['status'], 'paused')

    def test_score_confirmation_rechecks_temporary_role_expiry(self):
        event = self.large_event('scorer')
        match = next(m for m in event['matches'] if m['status'] == 'ready')
        token, router = self.pending(f"场地1 赛事 {event['id']} 第 {match['number']} 场 21:19", intent='RecordScore')
        self.now += timedelta(minutes=3)
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        current = Tournaments(self.ctx, self.clock).get(event['id'])
        self.assertFalse(next(m for m in current['matches'] if m['id'] == match['id'])['scores'])

    def test_score_confirmation_rejects_changed_event_even_if_same_match_untouched(self):
        self.seed_planning()
        event = Tournaments(self.member, self.clock).create({'kind': 'daily', 'format': 'round_robin',
                                                            'options': {'discipline': 'singles'}})
        match = next(m for m in event['matches'] if m['status'] == 'ready')
        token, router = self.pending(f"场地1 赛事 {event['id']} 第 {match['number']} 场 21:19", intent='RecordScore')
        Tournaments(self.ctx, self.clock).update(event['id'], {'title': '改名后的活动'})
        self.rejected(lambda: asyncio.run(router.confirm(token, '小李')))
        current = Tournaments(self.ctx, self.clock).get(event['id'])
        self.assertFalse(next(m for m in current['matches'] if m['id'] == match['id'])['scores'])

    def test_ai_disabled_during_model_wait_does_not_queue_write(self):
        router = self.commands()
        async def disable_then_propose(*args, **kwargs):
            self.s.update_config({'ai': {'enabled': False}})
            return self.proposal(ctx=router.ctx)
        with patch('app.ai.interpret', side_effect=disable_then_propose):
            self.rejected(lambda: asyncio.run(router.run('AI 帮我报名')))
        self.assertEqual(self.count('registrations'), 0)
        self.assertEqual(self.count('ai_pending', "status='pending'", transport=True), 0)

    def test_backend_classifies_write_even_if_model_omits_confirmation_flag(self):
        proposal = self.proposal() | {'requires_confirmation': False}
        with patch('app.ai.interpret', return_value=proposal):
            reply = self.run_command('AI 帮我报名')
        self.assertIn('待确认', reply)
        self.assertEqual(self.count('registrations'), 0)
        self.assertEqual(self.count('ai_pending', "status='pending'", transport=True), 1)

    def test_query_intent_cannot_smuggle_a_write_command(self):
        proposal = self.proposal(intent='Roster') | {'requires_confirmation': False}
        with patch('app.ai.interpret', return_value=proposal):
            self.rejected(lambda: self.run_command('AI 看一下名单'))
        self.assertEqual(self.count('registrations'), 0)
        self.assertEqual(self.count('ai_pending', transport=True), 0)

    def test_unsupported_tool_and_ambiguous_date_never_create_pending_or_business_rows(self):
        cases = (
            ('帮我创建大型比赛并授予管理员', FakeRunnable('CreateLargeTournament')),
            ('明天或者后天报名', FakeRunnable('Register')),
            ('帮我在场地999报名', FakeRunnable('Register', {'venue_id': 999})),
            ('帮我报名', FakeRunnable('Register', {'user_id': '1002'})),
        )
        for text, fake in cases:
            with self.subTest(text=text), patch.object(ai, '_make_runnable', return_value=fake):
                self.run_command('AI ' + text)
                self.assertEqual(self.count('registrations'), 0)
                self.assertEqual(self.count('tour_events'), 0)
                self.assertEqual(self.count('ai_pending', transport=True), 0)
