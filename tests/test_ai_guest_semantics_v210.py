"""Incremental guests and scoped cancellation: synthetic model, real domain writes."""
import asyncio
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app import ai
from app.ai_catalog import local_route
from app.ai_operations import describe_step
from app.ai_plan_flow import is_plan
from app.ai_plan_store import AiPlanStore
from app.db import connect, dumps, stamp
from app.membership_terms import MembershipTerms
from app.tournaments import Tournaments
from tests import test_ai_plan_flow_v27 as flow_helpers
from tests.test_ai import CONFIG, FakeRunnable
from tests.test_ai_planner_v27 import context, plan, step
from tests.test_core import CoreFixture


class GuestIntentTests(unittest.TestCase):
    def test_twenty_additions_are_typed_for_daily_and_named_events(self):
        for schema, extra in ((ai.Register, {}), (ai.EventRegistration, {'action': 'enroll'})):
            with self.subTest(schema=schema.__name__):
                self.assertEqual(schema(guests=20, **extra).guests, 20)
                self.assertEqual(len(schema(guests=20, guest_names=[f'球友{i}' for i in range(20)], **extra).guest_names), 20)
                for count in (21, -1, True, '2'):
                    with self.assertRaises(ValidationError): schema(guests=count, **extra)

    def test_cancel_schema_accepts_only_distinct_literal_names_without_identity(self):
        self.assertEqual(ai.CancelRegistration().guest_names, [])
        self.assertEqual(ai.CancelRegistration(guest_names=['张三', '小李+1']).guest_names, ['张三', '小李+1'])
        for values in ({'guest_names': ['张三', '张三']}, {'guest_names': ['<script>']},
                       {'guest_names': '张三'}, {'guest_names': [f'球友{i}' for i in range(21)]},
                       {'guest_names': ['张三'], 'user_id': 'another-account'}, {'guests': 2}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ai.CancelRegistration(**values)
        with self.assertRaises(ValidationError): ai.EventRegistration(action='cancel', guests=1)
        with self.assertRaises(ValidationError): ai.EventRegistration(action='enroll', guests=1, guest_names=['小李+1'])

    def test_semantic_proxy_cancel_preserves_natural_sentence_and_full_plan(self):
        result = plan(step('CancelRegistration', date='2026-10-14', number=2, guest_names=['张三', '李四']),
                      step('Roster', date='2026-10-14', number=2),
                      text='明天第二场取消我帮张三李四报的名，然后把那场名单给我看看')
        self.assertEqual(result['steps'][0]['arguments']['guest_names'], ['张三', '李四'])
        self.assertTrue(result['requires_confirmation'])
        self.assertIsNone(local_route('取消我帮张三李四代报名人的记录'))

    def test_proxy_cancel_never_silently_turns_into_self_or_invented_person(self):
        for text in ('取消我帮张三李四报的名', '取消张三', '取消张三，李四', '取消小李+1', '取消我的所有同行'):
            with self.subTest(text=text), self.assertRaises(ai._ClarificationRequired):
                plan(step('CancelRegistration'), text=text)
        with self.assertRaises(ai._ClarificationRequired):
            plan(step('CancelRegistration', guest_names=['王五']), text='取消我帮张三报的名')
        result = plan(step('CancelRegistration'), text='取消我明天第二场的报名')
        self.assertEqual(result['steps'][0]['arguments']['guest_names'], [])

    def test_self_and_named_guests_are_two_explicit_steps(self):
        result = plan(step('CancelRegistration'), step('CancelRegistration', guest_names=['张三', '李四']),
                      text='取消我的报名，也取消我帮张三李四报的名')
        self.assertEqual([x['arguments']['guest_names'] for x in result['steps']], [[], ['张三', '李四']])

    def cancellation_context(self):
        return context(events=[{'id': 7, 'title': '友谊杯', 'venue_id': 1, 'date': '2026-10-13',
                               'actions': ['enroll', 'cancel'], 'match_numbers': []}])

    def test_single_tool_rejects_explicitly_negated_guest_target(self):
        for phrase in ('不要取消张三', '别取消张三', '张三暂时不要取消', '不取消张三',
                       '无需取消我帮张三报的名', '取消李四，但不要取消张三', '不要取消李四、张三',
                       '不要取消李四，张三'):
            for operation in ('CancelRegistration', 'EventRegistration'):
                arguments = {'guest_names': ['张三']}
                if operation=='EventRegistration': arguments.update(tournament_name='友谊杯', action='cancel')
                with self.subTest(phrase=phrase, operation=operation), self.assertRaises(ai._ClarificationRequired):
                    ai._proposal(operation, arguments, self.cancellation_context(), '友谊杯，'+phrase if operation=='EventRegistration' else phrase)

    def test_single_tool_allows_cancel_other_guest_while_preserving_negated_guest(self):
        for phrase in ('取消李四，但不要取消张三', '不要取消张三，取消李四', '张三不要取消，李四取消',
                       '不要取消张三，只取消李四', '取消李四但不要取消张三'):
            for operation in ('CancelRegistration', 'EventRegistration'):
                arguments = {'guest_names': ['李四']}
                if operation=='EventRegistration': arguments.update(tournament_name='友谊杯', action='cancel')
                with self.subTest(phrase=phrase, operation=operation):
                    result = ai._proposal(operation, arguments, self.cancellation_context(), '友谊杯，'+phrase if operation=='EventRegistration' else phrase)
                    self.assertTrue(result['command'].endswith('取消李四'))
                    self.assertEqual(result['arguments']['guest_names'], ['李四'])

    def test_plan_rejects_negated_guest_in_any_cancel_step(self):
        for operation in ('CancelRegistration', 'EventRegistration'):
            extra = {'tournament_name': '友谊杯', 'action': 'cancel'} if operation=='EventRegistration' else {}
            for names in (['张三'], ['李四', '张三']):
                with self.subTest(operation=operation, names=names), self.assertRaises(ai._ClarificationRequired):
                    plan(step(operation, guest_names=names, **extra), trusted=self.cancellation_context(),
                         text='友谊杯取消李四，但不要取消张三')

    def test_plan_keeps_unrelated_negation_and_following_query(self):
        for operation in ('CancelRegistration', 'EventRegistration'):
            extra = {'tournament_name': '友谊杯', 'action': 'cancel'} if operation=='EventRegistration' else {}
            result = plan(step(operation, guest_names=['李四'], **extra), step('Roster'),
                          trusted=self.cancellation_context(), text='取消李四，但不要取消张三，然后查一下名单')
            self.assertEqual(result['steps'][0]['arguments']['guest_names'], ['李四'])
            self.assertEqual(result['steps'][1]['operation'], 'Roster')

    def test_name_with_conjunction_character_remains_one_negation_target(self):
        with self.assertRaises(ai._ClarificationRequired):
            plan(step('CancelRegistration', guest_names=['但志远']), text='不要取消但志远')
        result = plan(step('CancelRegistration', guest_names=['但志远']), text='取消但志远，但不要取消张三')
        self.assertEqual(result['steps'][0]['arguments']['guest_names'], ['但志远'])

    def test_single_tool_interpret_keeps_retention_constraint_but_rejects_missing_action(self):
        raw_context = {'bot_id': 'bot-a', 'chat_id': 'group-a', 'user_id': 'member-a',
                       'today': '2026-10-13', 'venue_id': 1, 'approved': True}
        for names, text, accepted in ((['李四'], '取消李四，但不要取消张三', True),
                                      (['张三'], '取消李四，但不要取消张三', False),
                                      (['李四'], '取消李四，但不要取消张三，然后报名第二场', False)):
            fake = FakeRunnable('CancelRegistration', {'guest_names': names})
            with self.subTest(names=names, text=text), patch.object(ai, '_make_runnable', return_value=fake):
                result = asyncio.run(ai.interpret(text, raw_context, CONFIG))
                if accepted:
                    self.assertTrue(result['command'].endswith('取消李四'))
                else:
                    self.assertEqual(result['error'], 'clarification_required')

    def test_self_cancel_then_append_elsewhere_is_not_a_proxy_cancellation(self):
        result = plan(step('CancelRegistration', number=1), step('Register', number=2, guests=2),
                      text='取消第一场我的报名，然后第二场带两位朋友报名')
        self.assertEqual([x['operation'] for x in result['steps']], ['CancelRegistration', 'Register'])

    def test_named_event_uses_same_guest_scope_and_visible_event_permission(self):
        trusted = context(events=[{'id': 7, 'title': '友谊杯', 'venue_id': 1, 'date': '2026-10-13',
                                  'actions': ['enroll', 'cancel'], 'match_numbers': []}])
        result = plan(step('EventRegistration', tournament_name='友谊杯', action='cancel', guest_names=['张三']),
                      trusted=trusted, text='友谊杯取消我帮张三报的名')
        self.assertEqual(result['steps'][0]['arguments']['tournament_id'], 7)
        with self.assertRaises(ai._ClarificationRequired):
            plan(step('EventRegistration', tournament_name='友谊杯', action='enroll', guests=1, guest_names=['张三', '李四']),
                 trusted=trusted, text='友谊杯帮张三李四报名')
        with self.assertRaises(PermissionError):
            plan(step('EventRegistration', tournament_name='其他杯', action='cancel', guest_names=['张三']),
                 trusted=trusted, text='其他杯取消我帮张三报的名')

    def test_preview_words_explain_append_self_and_named_cancellation(self):
        self.assertIn('本次新增2', describe_step(step('Register', guests=2))[1])
        self.assertIn('保留已有', describe_step(step('Register', guests=0))[1])
        self.assertIn('只取消本人，保留所有同行', describe_step(step('CancelRegistration'))[1])
        self.assertIn('保留本人及其他同行', describe_step(step('CancelRegistration', guest_names=['张三']))[1])
        self.assertNotIn('整组', describe_step(step('EventRegistration', action='cancel', guest_names=[]))[1])

    def test_legacy_typed_intents_ground_explicit_names_and_counts(self):
        trusted = context(events=[{'id': 7, 'title': '友谊杯', 'venue_id': 1, 'date': '2026-10-13',
                                  'actions': ['enroll', 'cancel'], 'match_numbers': []}])
        daily = ai._proposal('Register', {'guests': 20}, trusted, '报名+20')
        self.assertTrue(daily['command'].endswith('报名+20'))
        named = ai._proposal('CancelRegistration', {'guest_names': ['张三', '李四']}, trusted,
                             '取消我帮张三李四报的名')
        self.assertTrue(named['command'].endswith('取消张三，李四'))
        event = ai._proposal('EventRegistration', {'tournament_name': '友谊杯', 'action': 'enroll',
                            'guests': 2, 'guest_names': ['张三', '李四']}, trusted, '友谊杯，报名，张三，李四')
        self.assertTrue(event['command'].endswith('友谊杯，报名，张三，李四'))
        cancelled = ai._proposal('EventRegistration', {'tournament_name': '友谊杯', 'action': 'cancel',
                                'guest_names': ['张三']}, trusted, '友谊杯，取消张三')
        self.assertTrue(cancelled['command'].endswith('友谊杯，取消张三'))

    def test_model_interface_accepts_natural_cancellation_without_fixed_phrase(self):
        raw_context = {'bot_id': 'bot-a', 'chat_id': 'group-a', 'user_id': 'member-a',
                       'today': '2026-10-13', 'venue_id': 1, 'approved': True}
        fake = FakeRunnable('BusinessPlan', {'steps': [step('CancelRegistration', number=1, guest_names=['张三', '李四'])]})
        with patch.object(ai, '_make_runnable', return_value=fake):
            result = asyncio.run(ai.interpret('第一场取消我帮张三李四报的名', raw_context, CONFIG))
        self.assertEqual(result['intent'], 'BusinessPlan')
        self.assertEqual(result['steps'][0]['arguments']['guest_names'], ['张三', '李四'])


class GuestPlanFlowTests(CoreFixture):
    step = flow_helpers.AiPlanFlowTests.step
    proposal = flow_helpers.AiPlanFlowTests.proposal
    flow = flow_helpers.AiPlanFlowTests.flow
    preview = flow_helpers.AiPlanFlowTests.preview
    confirm = flow_helpers.AiPlanFlowTests.confirm
    refused = flow_helpers.AiPlanFlowTests.refused

    def setUp(self):
        super().setUp()
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'local-fake-ai-key'}})
        changing_clock = patch('app.competitions._now', self.clock)
        changing_clock.start()
        self.addCleanup(changing_clock.stop)

    def active(self):
        return [r for r in self.s.registrations('2026-10-13')['registrations'] if r['status']=='active']

    def seed(self, user='1001', guests=None):
        self.s.enroll(user, '小李' if user=='1001' else '球友'+user,
                      len(guests or []), day='2026-10-13', number=1, guest_names=guests)

    def test_repeated_plan_confirm_is_once_but_new_plan_appends(self):
        self.seed(guests=['张三'])
        token, preview = self.preview([self.step('Register', guests=2), self.step('Roster')])
        self.assertIn('本次新增2', preview)
        self.assertEqual(len(self.active()), 2)
        self.assertIn('已完成', self.confirm(token))
        self.assertEqual(len(self.active()), 4)
        self.refused(lambda: self.confirm(token))
        self.assertEqual(len(self.active()), 4)
        token, _ = self.preview([self.step('Register', guests=1)])
        self.confirm(token)
        self.assertEqual(len(self.active()), 5)
        token, _ = self.preview([self.step('Register', guests=0)])
        self.confirm(token)
        self.assertEqual(len(self.active()), 5)
        self.assertIn('张三', [r['name'] for r in self.active()])

    def test_self_cancel_preserves_all_guests_and_named_cancel_preserves_others(self):
        self.seed(guests=['张三', '李四', '王五'])
        token, _ = self.preview([self.step('CancelRegistration')])
        self.confirm(token)
        self.assertEqual([r['name'] for r in self.active()], ['张三', '李四', '王五'])
        token, _ = self.preview([self.step('CancelRegistration', guest_names=['张三', '李四'])])
        self.confirm(token)
        self.assertEqual([r['name'] for r in self.active()], ['王五'])

    def test_same_name_other_owners_guests_are_never_cancelled(self):
        self.seed(guests=['张三'])
        self.seed('1002', ['张三'])
        token, _ = self.preview([self.step('CancelRegistration', guest_names=['张三'])])
        self.confirm(token)
        rows = self.active()
        self.assertEqual([(r['user_id'], r['name']) for r in rows if r['is_proxy']], [('1002', '张三')])
        reply = asyncio.run(self.flow().propose(self.proposal([self.step('CancelRegistration', guest_names=['张三'])])))
        self.assertIn('未通过', reply)
        self.assertEqual(self.active(), rows)

    def test_missing_guest_stops_whole_plan_before_any_write(self):
        self.seed(guests=['张三'])
        before = self.active()
        reply = asyncio.run(self.flow().propose(self.proposal([
            self.step('CancelRegistration', guest_names=['张三', '不存在']), self.step('Register', guests=1)])))
        self.assertIn('未通过', reply)
        self.assertEqual(self.active(), before)

    def test_expired_membership_can_cancel_guests_but_cannot_add_them(self):
        self.seed(guests=['张三'])
        MembershipTerms(self.ctx, self.clock).create('1001', {'start_date': '2026-01-01', 'end_date': '2026-10-11'})
        denied = asyncio.run(self.flow().propose(self.proposal([self.step('Register', guests=1)])))
        self.assertIn('未通过', denied)
        token, _ = self.preview([self.step('CancelRegistration', guest_names=['张三']), self.step('CancelRegistration')])
        self.confirm(token)
        self.assertEqual(self.active(), [])

    def test_cancel_rechecks_scope_and_does_not_run_after_scope_change(self):
        self.seed(guests=['张三'])
        token, _ = self.preview([self.step('CancelRegistration', guest_names=['张三'])])
        sessions = self.s.sessions('2026-10-13')
        self.platform.update(self.admin, self.ctx.id, {'slot_keys': [sessions[1]['slot_key']]})
        self.refused(lambda: self.confirm(token))
        # The old session is intentionally hidden by the new bot scope; inspect
        # storage rather than mistaking a filtered roster for cancelled data.
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM registrations WHERE user_id='1001' AND status='active'").fetchone()[0], 2)

    def test_old_pending_plan_is_invalidated_even_without_config_change(self):
        token, _ = self.preview([self.step('Register', guests=1)])
        with connect(self.ctx.transport_db_path, True) as db:
            db.execute('UPDATE ai_pending SET command=? WHERE token=?', (dumps({'version': 4, 'plan_id': token}), token))
        self.assertTrue(is_plan(self.commands(), token))
        with self.assertRaisesRegex(ValueError, '规则已更新'):
            self.confirm(token)
        record = AiPlanStore(self.member, self.clock).get(token)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['steps'][0]['status'], 'skipped')
        self.assertEqual(self.active(), [])

    def test_expired_plan_format_is_refused_through_command_confirmation(self):
        self.seed(guests=['张三'])
        token, _ = self.preview([self.step('CancelRegistration')])
        with connect(self.ctx.transport_db_path, True) as db:
            db.execute('UPDATE ai_pending SET command=? WHERE token=?', (json.dumps({'version': 4, 'plan_id': token}), token))
        with self.assertRaisesRegex(ValueError, '规则已更新'):
            asyncio.run(self.commands().run('AI确认 ' + token))
        self.assertEqual(len(self.active()), 2)

    def open_event(self):
        events = Tournaments(self.ctx, self.clock)
        event = events.create({'kind': 'large', 'title': '追加杯', 'keyword': '追加杯', 'format': 'round_robin',
                              'date': self.now.date().isoformat(), 'venue_id': 1, 'options': {'discipline': 'singles'},
                              'registration_open': stamp(self.now - timedelta(hours=1)),
                              'registration_close': stamp(self.now + timedelta(hours=1))})
        events.transition(event['id'], 'open_registration')
        return event

    def event_step(self, action, **args):
        return self.step('EventRegistration', tournament_name='追加杯', venue_id=1, action=action,
                         **({'guests': 0, 'guest_names': []} | args))

    def event_active(self, event):
        return [p for p in Tournaments(self.ctx, self.clock).get(event['id'])['participants'] if p['status']=='active']

    def test_event_append_self_cancel_named_cancel_and_no_daily_side_effect(self):
        event = self.open_event()
        token, _ = self.preview([self.event_step('enroll', guests=2, guest_names=['张三', '李四']), self.event_step('enroll', guests=1)])
        self.confirm(token)
        self.assertEqual(len(self.event_active(event)), 4)
        self.refused(lambda: self.confirm(token))
        self.assertEqual(len(self.event_active(event)), 4)
        token, _ = self.preview([self.event_step('cancel'), self.event_step('cancel', guest_names=['张三'])])
        self.confirm(token)
        self.assertEqual([p['name'] for p in self.event_active(event)], ['李四', '小李+3'])
        self.assertEqual(self.active(), [])

    def test_event_closed_after_preview_prevents_named_cancellation(self):
        event = self.open_event()
        Tournaments(self.member, self.clock).enroll(event['id'], '1001', '小李', 1, guest_names=['张三'])
        token, _ = self.preview([self.event_step('cancel', guest_names=['张三'])])
        Tournaments(self.ctx, self.clock).transition(event['id'], 'close_registration')
        self.refused(lambda: self.confirm(token))
        self.assertEqual(len(self.event_active(event)), 2)

