"""Real AI schemas and router/service bridge, with only model I/O replaced."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import re
from unittest.mock import patch

from app import ai
from app.db import connect, stamp
from app.members import Members
from app.planning import DailyPlanning
from app.tournaments import Tournaments
from tests.test_ai import FakeRunnable
from tests.test_core import CoreFixture


class ChangingRunnable(FakeRunnable):
    def __init__(self, name, args, change):
        super().__init__(name, args)
        self.change = change

    async def ainvoke(self, messages, config=None):
        self.change()
        return await super().ainvoke(messages, config)


class AIBusinessBridgeTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'bridge-local-fake-key'}})
        self.patcher = patch('app.competitions._now', self.clock)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def send(self, text, ctx=None):
        return asyncio.run(self.commands(ctx).run(text))

    def model_reply(self, text, tool, arguments=None, ctx=None, change=None):
        fake = ChangingRunnable(tool, arguments or {}, change) if change else FakeRunnable(tool, arguments)
        with patch.object(ai, '_make_runnable', return_value=fake) as make:
            reply = self.send(text, ctx)
            make.assert_called_once()
        return reply

    def confirm(self, reply):
        match = self.pending_match(reply)
        self.assertIsNotNone(match, reply)
        return self.send('AI确认 ' + match[1])

    def participants(self):
        for uid in ('1001', '1002', '1003', '1004'):
            self.s.enroll(uid, uid)

    def event(self):
        self.participants()
        return Tournaments(self.member, self.clock).create({
            'kind': 'daily', 'title': '桥接友谊赛', 'format': 'round_robin'})

    def domain_rows(self):
        with connect(self.ctx.db_path) as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in ('registrations', 'tour_events', 'tour_participants', 'tour_matches')}

    def pending_count(self):
        with connect(self.ctx.transport_db_path) as db:
            return db.execute("SELECT COUNT(*) FROM ai_pending WHERE status='pending'").fetchone()[0]

    def test_two_stage_doubles_high_low_planner_to_named_score_without_admin_role(self):
        self.participants()
        reply = self.model_reply('AI 组织比赛 双打，采用高带低', 'StartDailyPlan', {
            'discipline': 'doubles', 'assignment_strategy': 'high_low'})
        self.assertIn('高带低', reply)
        self.assertIsNone(DailyPlanning(self.member, self.clock).get(1))
        self.confirm(reply)
        planner = DailyPlanning(self.member, self.clock).get(1)
        self.assertEqual(planner['status'], 'ready')
        self.assertEqual(planner['payload']['discipline'], 'doubles')
        self.assertEqual(planner['payload']['assignment_strategy'], 'high_low')
        reply = self.model_reply('AI 积分赛', 'DailyPlan', {'format': 'round_robin'})
        self.assertEqual(Tournaments(self.ctx, self.clock).list(), [])
        self.confirm(reply)
        event = Tournaments(self.member, self.clock).list()[0]
        event = Tournaments(self.member, self.clock).get(event['id'])
        self.assertEqual(event['creator'], '1001')
        self.assertEqual(event['options']['discipline'], 'doubles')
        self.assertEqual(event['options']['assignment_strategy'], 'high_low')
        self.assertEqual(Members(self.member, self.clock).get()['roles'], [])
        match = next(m for m in event['matches'] if m['status'] == 'ready')
        reply = self.model_reply(f"AI {event['title']}，第{match['number']}场21:19", 'RecordScore', {
            'tournament_name': event['title'], 'match_id': match['number'], 'scores': [[21, 19]]})
        current = Tournaments(self.member, self.clock).get(event['id'])
        self.assertFalse(next(m for m in current['matches'] if m['id'] == match['id'])['scores'])
        self.confirm(reply)
        current = Tournaments(self.member, self.clock).get(event['id'])
        self.assertEqual(next(m for m in current['matches'] if m['id'] == match['id'])['scores'], [[21, 19]])
        # The literal default title shown to users must also work when copied
        # into the fixed command route, without an extra date or event ID.
        self.send(f"{event['title']}，第{match['number']}场21:18")
        current = Tournaments(self.member, self.clock).get(event['id'])
        self.assertEqual(next(m for m in current['matches'] if m['id'] == match['id'])['scores'], [[21, 18]])
        # An actual short title on that date creates a real ambiguity with the
        # default date-prefixed title. Neither event may be selected silently.
        Tournaments(self.member, self.clock).create({'kind': 'daily', 'title': '积分赛', 'format': 'round_robin'})
        before = self.domain_rows()
        with self.assertRaisesRegex(ValueError, '同名|歧义'):
            self.send(f"{event['title']}，第{match['number']}场21:17")
        self.assertEqual(self.domain_rows(), before)

    def test_real_register_schema_preserves_named_guests_until_confirmed(self):
        reply = self.model_reply('AI 报名，甲，乙', 'Register', {'guests': 2, 'guest_names': ['甲', '乙']})
        self.assertEqual(self.s.registrations()['registrations'], [])
        self.confirm(reply)
        rows = self.s.registrations()['registrations']
        self.assertEqual([r['name'] for r in rows], ['小李', '甲', '乙'])
        self.assertEqual([r['is_proxy'] for r in rows], [False, True, True])

    def test_real_named_tournament_query_has_no_business_mutations_or_confirmation(self):
        event = self.event()
        before = self.domain_rows()
        reply = self.model_reply(f"AI {event['title']}，对战表", 'TournamentQuery', {
            'tournament_name': event['title'], 'view': 'schedule'}, replace(self.member, actor='1002'))
        self.assertIn('AI 查询', reply)
        self.assertIn(event['title'], reply)
        self.assertEqual(self.domain_rows(), before)
        self.assertEqual(self.pending_count(), 0)

    def test_real_proposal_remains_invalid_after_ai_disable_then_reenable(self):
        reply = self.model_reply('AI 帮我报名', 'Register')
        token = self.pending_match(reply)[1]
        self.s.update_config({'ai': {'enabled': False}})
        self.s.update_config({'ai': {'enabled': True}})
        with self.assertRaisesRegex(ValueError, '配置|变化|失效'):
            self.send('AI确认 ' + token)
        self.assertEqual(self.s.registrations()['registrations'], [])
        self.assertEqual(self.pending_count(), 0)

    def test_real_daily_plan_rejects_replaced_token_during_model_wait(self):
        self.participants()
        self.send('组织比赛')
        before = DailyPlanning(self.member, self.clock).get(1)['token']
        def replace_plan():
            DailyPlanning(self.member, self.clock).begin({'day': None, 'venue_id': 1, 'at': None, 'number': None})
        with self.assertRaisesRegex(ValueError, '策划|变化'):
            self.model_reply('AI 积分赛', 'DailyPlan', {'format': 'round_robin'}, change=replace_plan)
        self.assertNotEqual(DailyPlanning(self.member, self.clock).get(1)['token'], before)
        self.assertEqual(Tournaments(self.ctx, self.clock).list(), [])
        self.assertEqual(self.pending_count(), 0)

    def test_real_daily_plan_rejects_changed_discipline_during_model_wait(self):
        self.participants()
        self.send('组织比赛')
        def change_mode():
            DailyPlanning(self.member, self.clock).change(1, discipline='singles')
        with self.assertRaisesRegex(ValueError, '策划|变化'):
            self.model_reply('AI 积分赛', 'DailyPlan', {'format': 'round_robin'}, change=change_mode)
        self.assertEqual(Tournaments(self.ctx, self.clock).list(), [])
        self.assertEqual(self.pending_count(), 0)

    def test_admin_event_with_comma_title_does_not_block_ai_registration_queries(self):
        self.participants()
        event = Tournaments(self.ctx, self.clock).create({'kind': 'daily', 'title': '周末杯，A组', 'format': 'round_robin'})
        self.assertEqual(event['title'], '周末杯，A组')
        before = self.domain_rows()
        reply = self.model_reply('AI 看一下报名名单', 'Roster')
        self.assertIn('AI 查询', reply)
        self.assertEqual(self.domain_rows(), before)
        self.assertEqual(self.pending_count(), 0)

    def test_ended_planner_session_does_not_block_unrelated_ai_queries(self):
        self.now = self.now.replace(hour=20, minute=0)
        self.participants()
        self.send('组织比赛')
        self.now += timedelta(minutes=31)
        planner = DailyPlanning(self.member, self.clock).get(1)
        self.assertIsNotNone(planner)
        self.assertEqual(planner['status'], 'ready')
        self.assertIsNone(self.commands().ai_context(1)['planning'])
        self.assertIn('AI 查询', self.model_reply('AI 看一下报名名单', 'Roster'))
        self.assertIn('AI 查询', self.model_reply('AI 我的统计', 'PersonalStats'))
        reply = self.model_reply('AI 积分赛', 'DailyPlan', {'format': 'round_robin'})
        self.assertIn('组织比赛', reply)
        self.assertEqual(Tournaments(self.ctx, self.clock).list(), [])
        self.assertEqual(self.pending_count(), 0)

    def open_large_event(self, title='桥接报名杯', staff=None):
        events = Tournaments(self.ctx, self.clock)
        event = events.create({'kind': 'large', 'title': title, 'keyword': title,
            'format': 'round_robin', 'date': self.now.date().isoformat(), 'venue_id': 1,
            'options': {'discipline': 'singles'},
            'registration_open': stamp(self.now - timedelta(hours=1)),
            'registration_close': stamp(self.now + timedelta(hours=1)),
            'staff': staff or []})
        return events.transition(event['id'], 'open_registration')

    def test_real_named_event_registration_and_cancel_never_touch_daily_roster(self):
        event = self.open_large_event()
        reply = self.model_reply('AI 桥接报名杯报名+2', 'EventRegistration', {
            'tournament_name': event['title'], 'action': 'enroll', 'guests': 2})
        self.assertEqual(Tournaments(self.ctx, self.clock).get(event['id'])['participants'], [])
        self.assertEqual(self.s.registrations()['registrations'], [])
        self.confirm(reply)
        people = Tournaments(self.ctx, self.clock).get(event['id'])['participants']
        self.assertEqual([(p['user_id'], p['name'], p['guest_index']) for p in people],
                         [('1001', '小李', 0), ('1001', '小李+1', 1), ('1001', '小李+2', 2)])
        self.assertEqual(self.s.registrations()['registrations'], [])
        reply = self.model_reply('AI 取消桥接报名杯的报名', 'EventRegistration', {
            'tournament_name': event['title'], 'action': 'cancel'})
        self.assertEqual(sum(p['status'] == 'active' for p in people), 3)
        self.confirm(reply)
        people = Tournaments(self.ctx, self.clock).get(event['id'])['participants']
        self.assertEqual([p['guest_index'] for p in people if p['status'] == 'active'], [1, 2])
        self.assertEqual(self.s.registrations()['registrations'], [])

    def test_named_event_proposal_fails_when_registration_changes_or_closes(self):
        event = self.open_large_event()
        reply = self.model_reply('AI 桥接报名杯报名', 'EventRegistration', {
            'tournament_name': event['title'], 'action': 'enroll'})
        Tournaments(self.ctx, self.clock).enroll(event['id'], '1002', '球友1002')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.confirm(reply)
        people = Tournaments(self.ctx, self.clock).get(event['id'])['participants']
        self.assertEqual([p['user_id'] for p in people], ['1002'])
        reply = self.model_reply('AI 桥接报名杯报名', 'EventRegistration', {
            'tournament_name': event['title'], 'action': 'enroll'})
        Tournaments(self.ctx, self.clock).transition(event['id'], 'close_registration')
        with self.assertRaisesRegex(ValueError, '报名阶段|变化'):
            self.confirm(reply)
        self.assertFalse(any(p['user_id'] == '1001' for p in Tournaments(self.ctx, self.clock).get(event['id'])['participants']))

    def test_real_event_pause_resume_close_preserves_recorded_history(self):
        event = self.event()
        before = self.domain_rows()
        reply = self.model_reply('AI 暂停桥接友谊赛', 'EventAction', {
            'tournament_name': event['title'], 'action': 'pause'})
        self.assertEqual(Tournaments(self.ctx, self.clock).get(event['id'])['status'], 'running')
        self.confirm(reply)
        self.assertEqual(Tournaments(self.ctx, self.clock).get(event['id'])['status'], 'paused')
        reply = self.model_reply('AI 继续桥接友谊赛', 'ResumeTournament', {'tournament_name': event['title']})
        self.confirm(reply)
        self.assertEqual(Tournaments(self.ctx, self.clock).get(event['id'])['status'], 'running')
        reply = self.model_reply('AI 关闭桥接友谊赛', 'EventAction', {
            'tournament_name': event['title'], 'action': 'close'})
        self.confirm(reply)
        self.assertEqual(Tournaments(self.ctx, self.clock).get(event['id'])['status'], 'cancelled')
        self.assertEqual(self.domain_rows()['tour_matches'], before['tour_matches'])
        self.assertEqual(self.domain_rows()['tour_participants'], before['tour_participants'])

    def test_event_action_never_grants_participant_management_or_revoked_temporary_role(self):
        event = self.event()
        participant = replace(self.member, actor='1002')
        reply = self.model_reply('AI 暂停桥接友谊赛', 'EventAction', {
            'tournament_name': event['title'], 'action': 'pause'}, ctx=participant)
        self.assertIn('权限', reply)
        self.assertEqual(self.pending_count(), 0)
        staff = [{'user_id': '1001', 'role': 'organizer', 'starts_at': stamp(self.now - timedelta(minutes=1)),
                  'expires_at': stamp(self.now + timedelta(minutes=2))}]
        large = self.open_large_event('临时组织杯', staff)
        for uid in ('1001', '1002'):
            Tournaments(self.ctx, self.clock).enroll(large['id'], uid, uid)
        Tournaments(self.ctx, self.clock).transition(large['id'], 'close_registration')
        Tournaments(self.ctx, self.clock).transition(large['id'], 'start')
        reply = self.model_reply('AI 暂停临时组织杯', 'EventAction', {
            'tournament_name': large['title'], 'action': 'pause'})
        self.now += timedelta(minutes=3)
        with self.assertRaisesRegex(PermissionError, '负责人|管理员|权限'):
            self.confirm(reply)
        self.assertEqual(Tournaments(self.ctx, self.clock).get(large['id'])['status'], 'running')

    def test_event_state_changed_while_model_waits_cannot_create_stale_action_proposal(self):
        event = self.event()
        def close_event():
            Tournaments(self.ctx, self.clock).transition(event['id'], 'close')
        with self.assertRaisesRegex((ValueError, PermissionError), '状态|暂停|关闭'):
            self.model_reply('AI 暂停桥接友谊赛', 'EventAction', {
                'tournament_name': event['title'], 'action': 'pause'}, change=close_event)
        self.assertEqual(self.pending_count(), 0)

    def test_self_level_confirmation_detects_newer_admin_review(self):
        reply = self.model_reply('AI 我想自评2级', 'SelfLevel', {'level': 2})
        self.assertIsNone(Members(self.member, self.clock).get()['requested_level'])
        Members(self.member, self.clock).request_level(3)
        with self.assertRaisesRegex(ValueError, '变化'):
            self.confirm(reply)
        member = Members(self.member, self.clock).get()
        self.assertEqual(member['requested_level'], 3)
