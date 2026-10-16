"""Registration gender, lawful doubles teams and immutable participant snapshots."""
from dataclasses import replace
from datetime import timedelta
import unittest

from app.db import connect, initialize
from app.competitions import _now
from app.members import Members
from app.member_imports import MemberImports
from app.formats import get_format, metadata
from app.planning import DailyPlanning
from app.registration import create_form, submit_form
from app.tournaments import Tournaments
from tests.test_core import CoreFixture


def roster(men=4, women=4):
    return [{'id': str(i), 'user_id': 'user'+str(i), 'name': '球友'+str(i),
             'gender': 'male' if i < men else 'female', 'level': i % 9 + 1,
             'source_category': '正式'} for i in range(men + women)]


class GenderRulesTests(unittest.TestCase):
    def test_all_fixed_formats_and_strategies_make_legal_mixed_teams(self):
        people = roster()
        genders = {p['id']: p['gender'] for p in people}
        for fmt in ('round_robin', 'single_elimination', 'double_elimination', 'group_knockout', 'doubles_rotation'):
            for strategy in ('random', 'seeded', 'balanced', 'high_low'):
                with self.subTest(fmt=fmt, strategy=strategy):
                    plan = get_format(fmt).build(people, {'discipline': 'doubles', 'doubles_category': 'mixed',
                        'assignment_strategy': strategy, 'group_count': 2, 'advance_per_group': 1}, 42)
                    for match in plan['matches']:
                        for side in ('side_a', 'side_b'):
                            if match.get(side):
                                self.assertEqual(sorted(genders[uid] for uid in match[side]['members']), ['female', 'male'])

    def test_rotation_supports_unequal_gender_counts_without_illegal_pairs(self):
        people = roster(5, 2)
        plan = get_format('doubles_rotation').build(people, {'doubles_category': 'mixed', 'rounds': 20}, 9)
        genders = {p['id']: p['gender'] for p in people}
        appeared = set()
        for match in plan['matches']:
            for side in ('side_a', 'side_b'):
                ids = match[side]['members']; appeared.update(ids)
                self.assertEqual({genders[uid] for uid in ids}, {'male', 'female'})
        self.assertEqual(appeared, set(genders))

    def test_gender_specific_rules_reject_unknown_wrong_gender_or_impossible_format(self):
        for bad in ([], {}, True, 1):
            with self.subTest(category=bad), self.assertRaises(ValueError):
                metadata(options={'doubles_category': bad})
        for fmt in ('five_rotation', 'five_team'):
            with self.assertRaises(ValueError):
                get_format(fmt).build(roster(5, 5), {'doubles_category': 'mixed'}, 1)
        for fmt in ('round_robin', 'five_rotation', 'doubles_rotation'):
            with self.subTest(fmt=fmt):
                with self.assertRaises(ValueError):
                    get_format(fmt).build(roster(4, 1), {'doubles_category': 'mens'}, 1)
        with self.assertRaises(ValueError):
            get_format('round_robin').build(roster(3, 1), {'doubles_category': 'mixed'}, 1)
        with self.assertRaises(ValueError):
            get_format('round_robin').build([p | {'gender': 'unknown'} for p in roster(4, 0)], {'doubles_category': 'mens'}, 1)

    def test_candidate_selection_uses_correct_gender_substitutes_without_dropping_formal(self):
        people = roster(3, 3)
        formal = people[:3] + people[3:5]
        substitute = [people[5] | {'source_category': '替补'}]
        formats = metadata('doubles', 5, {'doubles_category': 'mixed', 'participants': formal, 'substitutes': substitute}, 1)
        fixed = next(f for f in formats if f['id'] == 'round_robin')
        self.assertTrue(fixed['available'], fixed)
        self.assertEqual(fixed['substitutes_needed'], 1)
        self.assertEqual(set(fixed['selected_ids']), {p['id'] for p in people})
        self.assertEqual(fixed['gender_counts'], {'male': 3, 'female': 3, 'unknown': 0})
        bad = next(f for f in metadata('doubles', 5, {'doubles_category': 'mixed', 'participants': formal}) if f['id'] == 'round_robin')
        self.assertFalse(bad['available'])
        self.assertTrue(bad['reason'])


class GenderMemberTests(CoreFixture):
    def test_new_self_registration_requires_gender_and_application_persists_it(self):
        actor = replace(self.member, actor='gender-new')
        members = Members(actor, self.clock)
        for value in (None, '', 'unknown', 'other', 1, [], {}, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                members.apply('新会员', gender=value)
        self.assertIsNone(members.get())
        result = members.apply('新会员', 9, gender='女')
        self.assertEqual(result['gender'], 'female')
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute('SELECT gender FROM membership_applications WHERE user_id=?', (actor.actor,)).fetchone()[0], 'female')

    def test_existing_members_stay_unknown_and_self_cannot_edit_another(self):
        members = Members(self.member, self.clock)
        self.assertEqual(members.get()['gender'], 'unknown')
        initialize(self.ctx.db_path)
        self.assertEqual(members.get()['gender'], 'unknown')
        with self.assertRaises(PermissionError):
            members.set_gender('female', '1002')
        self.assertEqual(members.set_gender('男')['gender'], 'male')
        self.assertEqual(Members(self.ctx, self.clock).update_details('1001', gender='female')['gender'], 'female')

    def test_public_form_requires_gender_and_receipt_is_idempotent(self):
        self.s.update_config({'public_base_url': 'http://127.0.0.1:9874'})
        actor = replace(self.member, actor='form-gender')
        reference = create_form(actor, self.clock).split('#')[1]
        token = reference.split('.')[-1]
        from fastapi.testclient import TestClient
        from app.main import create_app
        from pathlib import Path
        from tests.test_core import TOKEN
        with TestClient(create_app(Path(self.temp.name), TOKEN, start_scheduler=False, live=False, clock=self.clock)) as client:
            for bad in (None, [], {}, True):
                reply = client.post('/public/registration/' + reference, json={'name': '表单会员', 'gender': bad})
                self.assertEqual(reply.status_code, 400, reply.text)
        with self.assertRaises(ValueError):
            submit_form(actor, token, {'name': '表单会员'}, self.clock)
        result = submit_form(actor, token, {'name': '表单会员', 'gender': 'female'}, self.clock)
        self.assertEqual(result['gender'], 'female')
        self.assertEqual(submit_form(actor, token, {'name': '变更', 'gender': 'male'}, self.clock), result)

    def test_csv_gender_is_optional_but_binding_fills_missing_gender(self):
        imports = MemberImports(self.ctx, self.clock)
        preview = imports.preview('姓名,手机号,性别\n预登记甲,13800000001,女\n预登记乙,13800000002,\n'.encode())
        self.assertTrue(preview['can_commit'], preview)
        imports.commit(preview['token'])
        for uid, phone, gender in [('csv-female', '13800000001', 'female'), ('csv-male', '13800000002', 'male')]:
            result = Members(replace(self.member, actor=uid), self.clock).apply('填写名', phone=phone, gender='male')
            self.assertEqual(result['gender'], gender)
        bad = imports.preview('姓名,手机号,性别\n错误,13800000003,猜测\n'.encode())
        self.assertFalse(bad['can_commit'])

    def test_daily_mixed_roster_snapshots_do_not_follow_later_profile_edits(self):
        for uid, gender in [('1001', 'male'), ('1002', 'male'), ('1003', 'female'), ('1004', 'female')]:
            Members(self.ctx, self.clock).set_gender(gender, uid)
            self.s.enroll(uid, '球友')
        planner = DailyPlanning(self.member, self.clock)
        description = planner.begin({'venue_id': 1}, doubles_category='mixed')
        self.assertIn('混双', description)
        self.assertIn('男 2 人', description)
        result = planner.create(1, 'round_robin')
        self.assertIn('混双', result)
        events = Tournaments(self.member, self.clock)
        event = events.get(events.list()[0]['id'])
        before = [(p['id'], p['gender']) for p in event['participants']]
        Members(self.ctx, self.clock).set_gender('female', '1001')
        reopened = events.get(event['id'])
        self.assertEqual(before, [(p['id'], p['gender']) for p in reopened['participants']])
        self.assertEqual(event['matches'], reopened['matches'])

    def test_large_gender_restrictions_apply_to_enrollment_and_recheck_before_draw(self):
        self.now = _now()
        events = Tournaments(self.ctx, self.clock)
        moment = _now()
        event = events.create({'kind': 'large', 'title': '男双公开赛', 'keyword': '男双杯', 'date': moment.date().isoformat(),
            'registration_open': (moment - timedelta(minutes=1)).isoformat(),
            'registration_close': (moment + timedelta(days=1)).isoformat(),
            'options': {'doubles_category': 'mens'}})
        events.transition(event['id'], 'open_registration')
        with self.assertRaisesRegex(ValueError, '补齐性别'):
            events.enroll(event['id'], '1001', '姓名')
        Members(self.ctx, self.clock).set_gender('female', '1001')
        with self.assertRaisesRegex(ValueError, '男双'):
            events.enroll(event['id'], '1001', '姓名')
        for uid in ('1001', '1002', '1003', '1004'):
            Members(self.ctx, self.clock).set_gender('male', uid)
            events.enroll(event['id'], uid, '姓名')
        with self.assertRaisesRegex(ValueError, '独立注册'):
            events.enroll(event['id'], '1001', '姓名', 1)
        events.transition(event['id'], 'close_registration')
        Members(self.ctx, self.clock).set_gender('female', '1004')
        with self.assertRaisesRegex(ValueError, '男双'):
            events.transition(event['id'], 'draw')
        self.assertFalse(events.get(event['id'])['matches'])

    def test_csv_conflicting_genders_are_not_silently_ignored(self):
        preview = MemberImports(self.ctx, self.clock).preview('姓名,手机号,性别\n同一人,13800000009,男\n同一人,13800000009,女\n'.encode())
        self.assertFalse(preview['can_commit'])

    def test_format_preview_uses_same_grade_group_and_gender_roster_as_creation(self):
        manager = Members(self.ctx, self.clock)
        for index, uid in enumerate(('1001', '1002', '1003', '1004', '1')):
            manager.set_gender('male' if index < 2 else 'female', uid)
            manager.review_level(uid, True, 8 if index < 4 else 2)
            self.s.enroll(uid, '球友')
        events = Tournaments(self.ctx, self.clock)
        options = {'doubles_category': 'mixed', 'eligibility': {'min_level': 8}}
        formats = events.planning_formats(events.planning_candidates(), 'doubles', options)
        selected = next(f for f in formats if f['id'] == 'round_robin')
        self.assertTrue(selected['available'], selected)
        self.assertEqual(selected['eligibility_excluded_count'], 1)
        self.assertEqual(selected['gender_counts'], {'male': 2, 'female': 2, 'unknown': 0})
        event = events.create({'kind': 'daily', 'options': options})
        self.assertEqual(set(selected['selected_ids']), {p['id'] for p in event['participants']})

    def test_large_session_binding_checks_date_and_venue_and_freezes_snapshot(self):
        events = Tournaments(self.ctx, self.clock)
        session = self.s.sessions()[0]
        event = events.create({'kind': 'large', 'title': '关联场次', 'keyword': '关联杯',
            'date': session['date'], 'registration_open': '2026-10-11T00:00:00+08:00',
            'registration_close': '2026-10-12T23:59:00+08:00',
            'options': {'session_id': session['id']}})
        self.assertEqual(event['options']['session_snapshot']['start'], session['start'])
        second = self.s.sessions()[1]
        updated = events.update(event['id'], {'options': {'session_id': second['id']}})
        self.assertEqual(updated['options']['session_snapshot']['start'], second['start'])
        tomorrow = self.s.sessions('2026-10-13')[0]
        with self.assertRaisesRegex(ValueError, '日期'):
            events.update(event['id'], {'options': {'session_id': tomorrow['id']}})
        with self.assertRaisesRegex(ValueError, '不可手动修改'):
            events.update(event['id'], {'options': {'session_snapshot': {'slot_key': 'forged'}}})
        unbound = events.update(event['id'], {'options': {'session_id': None}})
        self.assertNotIn('session_id', unbound['options'])
        self.assertNotIn('session_snapshot', unbound['options'])
