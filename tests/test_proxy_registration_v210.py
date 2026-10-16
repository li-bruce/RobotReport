"""Append-only guest identity and precise owner-only cancellation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from app.db import connect
from app.members import Members
from app.membership_terms import MembershipTerms
from app.people import People
from app.service import Service
from app.tournaments import Tournaments
from tests.test_core import CoreFixture


class ProxyRegistrationV210Tests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.mine = Service(self.member, self.clock)
        self.other_ctx = replace(self.member, actor='1002')
        self.other = Service(self.other_ctx, self.clock)
        Members(self.ctx, self.clock).update_details('1001', gender='female')
        self.engine_clock = patch('app.competitions._now', self.clock)
        self.engine_clock.start()
        self.addCleanup(self.engine_clock.stop)

    def rows(self):
        with connect(self.ctx.db_path) as db:
            return [dict(row) for row in db.execute('SELECT * FROM registrations ORDER BY id')]

    def active(self, result=None):
        return [row for row in (result or self.mine.registrations())['registrations'] if row['status'] == 'active']

    def event(self, **options):
        events = Tournaments(self.ctx, self.clock)
        event = events.create({'kind': 'large', 'format': 'round_robin', 'title': '追加代报名测试赛',
            'keyword': '追加杯', 'date': '2026-10-12', 'venue_id': 1,
            'registration_open': '2026-10-12T09:00:00+08:00', 'registration_close': '2026-10-12T23:00:00+08:00',
            'options': {'capacity': 40, 'discipline': 'singles', **options}})
        events.transition(event['id'], 'open_registration')
        return event['id'], Tournaments(self.member, self.clock), events

    def test_two_then_five_appends_seven_and_plain_registration_preserves_guests(self):
        self.mine.enroll('1001', 'ignored', 2)
        original = self.rows()
        result = self.mine.enroll('1001', 'ignored', 5)
        self.assertEqual(len(self.active(result)), 8)
        self.assertEqual([row['name'] for row in self.active(result)], ['小李'] + [f'小李+{i}' for i in range(1, 8)])
        self.assertEqual(self.rows()[:3], original)
        before = self.rows()
        self.mine.enroll('1001', 'ignored')
        self.assertEqual(self.rows(), before)
        self.assertEqual([row['gender'] for row in self.active(result)], ['female'] + ['unknown'] * 7)
        self.assertTrue(all(row['is_proxy'] and row['sponsor_name'] == '小李' and row['level'] is None and row['groups'] == [] for row in self.active(result)[1:]))

    def test_cancel_self_preserves_guests_and_rejoin_enters_back_of_queue(self):
        self.s.save_slots(1, [{'name': '小场', 'weekdays': [0], 'start': '17:00', 'end': '22:00', 'capacity': 1, 'substitute_capacity': 1}])
        self.mine.enroll('1001', 'ignored', 2)
        self.other.enroll('1002', 'ignored')
        old_guests = [row for row in self.rows() if row['guest_index']]
        result = self.mine.cancel('1001')
        self.assertEqual(result['cancelled'], 1)
        self.assertEqual([row['category'] for row in self.active(result)], ['正式', '替补', '候补'])
        self.assertEqual([row for row in self.rows() if row['guest_index']], old_guests)
        self.assertEqual(self.mine.cancel('1001')['cancelled'], 0)
        result = self.mine.enroll('1001', 'ignored')
        self.assertEqual(self.active(result)[-1]['guest_index'], 0)
        self.assertEqual(self.active(result)[-1]['user_id'], '1001')

    def test_named_append_duplicates_atomic_and_history_not_renamed(self):
        self.mine.enroll('1001', 'ignored', 2, guest_names=['张三', '李四'])
        before = self.rows()
        with self.assertRaisesRegex(ValueError, '重复代报'):
            self.mine.enroll('1001', 'ignored', 2, guest_names=['赵六', '张三'])
        self.assertEqual(self.rows(), before)
        self.mine.enroll('1001', 'ignored', 1)
        old_guests = [row for row in self.rows() if row['guest_index']]
        Members(self.ctx, self.clock).review('1001', 'approved', name='新会员姓名')
        result = self.mine.enroll('1001', 'ignored', 1, guest_names=['赵六'])
        self.assertEqual([row for row in self.rows() if 0 < row['guest_index'] <= 3], old_guests)
        self.assertEqual([row['name'] for row in self.active(result)], ['新会员姓名', '张三', '李四', '小李+3', '赵六'])

    def test_precise_multi_cancel_rejects_foreign_or_mixed_targets_atomically(self):
        self.mine.enroll('1001', 'ignored', 2, guest_names=['张三', '李四'])
        self.other.enroll('1002', 'ignored', 2, guest_names=['别人的同行', '张三'])
        before = self.rows()
        for names in (['别人的同行'], ['张三', '别人的同行'], ['张三', '未报名'], ['小李'], ['张三', '张三']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.mine.cancel_guests('1001', names)
            self.assertEqual(self.rows(), before)
        result = self.mine.cancel_guests('1001', ['张三', '李四'])
        self.assertEqual(result['cancelled'], 2)
        self.assertEqual([row['name'] for row in self.active(result)], ['小李', '球友1002', '别人的同行', '张三'])
        self.assertEqual(self.mine.stats(user_id='1001')['summary']['cancellation_actions'], 1)
        self.assertEqual(len(self.rows()), len(before))

    def test_cancelled_anonymous_number_not_reused_and_limit_counts_active_only(self):
        self.mine.enroll('1001', 'ignored', 20)
        before = self.rows()
        with self.assertRaisesRegex(ValueError, '最多20'):
            self.mine.enroll('1001', 'ignored', 1)
        self.assertEqual(self.rows(), before)
        self.mine.cancel_guests('1001', ['小李+1', '小李+2'])
        result = self.mine.enroll('1001', 'ignored', 2)
        self.assertEqual(len(self.active(result)), 21)
        self.assertEqual([row['guest_index'] for row in self.active(result)[-2:]], [21, 22])
        cancelled = [row for row in self.rows() if row['status'] == 'cancelled']
        self.assertEqual([row['name'] for row in cancelled], ['小李+1', '小李+2'])

    def test_parallel_guest_appends_keep_unique_sequence_and_twenty_limit(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda _: self.mine.enroll('1001', 'ignored', 2), range(10)))
        self.assertEqual([row['guest_index'] for row in self.rows()], list(range(21)))
        self.assertEqual(len({row['queue_seq'] for row in self.rows()}), 21)

    def test_group_admin_cannot_supply_someone_elses_identity(self):
        People(self.ctx, self.clock).set_roles('1001', ['club_admin'])
        self.other.enroll('1002', 'ignored', 1, guest_names=['他人同行'])
        for operation in (lambda: self.mine.enroll('1002', 'ignored'), lambda: self.mine.cancel('1002'),
                          lambda: self.mine.cancel_guests('1002', ['他人同行'])):
            with self.assertRaises(PermissionError): operation()
        self.assertEqual(len(self.active()), 2)
        self.s.cancel_person(self.active()[1]['id'])
        self.assertEqual(len(self.active()), 1)

    def test_guest_cancel_respects_slot_scope_and_approval_but_not_expiry(self):
        self.mine.enroll('1001', 'ignored', 1, guest_names=['张三'], number=1)
        second = self.mine.sessions()[1]
        self.platform.update(self.admin, self.ctx.id, {'slot_keys': [second['slot_key']]})
        before = self.rows()
        with self.assertRaises(ValueError): self.mine.cancel_guests('1001', ['张三'], number=1)
        self.assertEqual(self.rows(), before)
        self.platform.update(self.admin, self.ctx.id, {'slot_keys': None})
        MembershipTerms(self.ctx, self.clock).create('1001', {'start_date': '2025-01-01', 'end_date': '2025-12-31'})
        with self.assertRaises(PermissionError): self.mine.enroll('1001', 'ignored', 1)
        self.assertEqual(self.mine.cancel('1001')['cancelled'], 1)
        self.assertEqual(self.mine.cancel_guests('1001', ['张三'])['cancelled'], 1)
        Members(self.ctx, self.clock).review('1001', 'suspended')
        with self.assertRaises(PermissionError): self.mine.cancel_guests('1001', ['张三'])

    def test_ambiguous_legacy_guest_names_fail_without_partial_cancellation(self):
        self.mine.enroll('1001', 'ignored', 2, guest_names=['张三', '李四'])
        with connect(self.ctx.db_path, True) as db:
            db.execute("UPDATE registrations SET name='张三',guest_name='张三' WHERE guest_index=2")
        before = self.rows()
        with self.assertRaisesRegex(ValueError, '唯一有效'): self.mine.cancel_guests('1001', ['张三'])
        self.assertEqual(self.rows(), before)

    def test_long_anonymous_display_can_be_cancelled(self):
        name = '长' * 80
        Members(self.ctx, self.clock).review('1001', 'approved', name=name)
        self.mine.enroll('1001', 'ignored', 1)
        self.assertEqual(self.mine.cancel_guests('1001', [name + '+1'])['cancelled'], 1)

    def test_generated_display_collision_does_not_replace_named_guest(self):
        self.mine.enroll('1001', 'ignored', 1, guest_names=['小李+2'])
        before = self.rows()
        with self.assertRaisesRegex(ValueError, '重复代报'): self.mine.enroll('1001', 'ignored', 1)
        self.assertEqual(self.rows(), before)

    def test_large_twenty_active_guest_limit_survives_cancellation_and_renaming(self):
        event_id, mine, _ = self.event()
        result = mine.enroll(event_id, '1001', 'ignored', 20)
        old_guest = next(row for row in result['participants'] if row['guest_index'] == 1)
        with self.assertRaisesRegex(ValueError, '最多20'): mine.enroll(event_id, '1001', 'ignored', 1)
        mine.cancel_guests(event_id, '1001', ['小李+2'])
        Members(self.ctx, self.clock).review('1001', 'approved', name='会员新名')
        result = mine.enroll(event_id, '1001', 'ignored', 1)
        self.assertEqual(result['participant_count'], 21)
        self.assertEqual(next(row for row in result['participants'] if row['guest_index'] == 1), old_guest)
        self.assertEqual(next(row['name'] for row in result['participants'] if row['guest_index'] == 21), '会员新名+21')

    def test_daily_draw_preserves_high_guest_indices_and_original_long_names(self):
        name = '长' * 80
        Members(self.ctx, self.clock).review('1001', 'approved', name=name)
        for start in (1, 21, 41):
            self.mine.enroll('1001', 'ignored', 20)
            self.mine.cancel_guests('1001', [f'{name}+{index}' for index in range(start, start + 20)])
        self.mine.enroll('1001', 'ignored', 3)
        Members(self.ctx, self.clock).review('1001', 'approved', name='现在的会员名')
        session = self.mine.sessions()[0]
        self.s.save_session_constraints(session['id'], {'capacity': 2, 'substitute_capacity': 2})
        event = Tournaments(self.member, self.clock).create({'kind': 'daily', 'format': 'round_robin'})
        proxies = [row for row in event['participants'] if row['guest_index']]
        self.assertEqual({row['guest_index'] for row in proxies}, {61, 62, 63})
        self.assertEqual({row['name'] for row in proxies}, {f'{name}+{index}' for index in (61, 62, 63)})
        self.assertEqual(event['options']['selected_substitute_names'], [f'{name}+62', f'{name}+63'])

    def test_large_event_append_cancel_precise_and_preserve_history(self):
        event_id, mine, admin = self.event()
        mine.enroll(event_id, '1001', 'ignored', 2, guest_names=['张三', '李四'])
        result = mine.enroll(event_id, '1001', 'ignored', 5)
        self.assertEqual(result['participant_count'], 8)
        old = result['participants']
        self.assertEqual(mine.enroll(event_id, '1001', 'ignored')['participants'], old)
        self.assertEqual(mine.cancel(event_id, '1001')['participant_count'], 7)
        self.assertEqual(mine.cancel_guests(event_id, '1001', ['张三', '小李+3'])['participant_count'], 5)
        result = mine.enroll(event_id, '1001', 'ignored', 1, guest_names=['新同行'])
        self.assertEqual(result['participant_count'], 7)
        self.assertEqual(next(row['guest_index'] for row in result['participants'] if row['name'] == '新同行'), 8)
        self.assertEqual(len(result['participants']), 9)
        self.assertTrue(all(row['gender'] == 'unknown' and row['level'] == 0 and row['group_ids'] == [] and row['is_proxy'] for row in result['participants'] if row['guest_index']))

    def test_large_event_mixed_cancel_and_duplicates_atomic_capacity_preserved(self):
        event_id, mine, admin = self.event(capacity=5)
        mine.enroll(event_id, '1001', 'ignored', 2, guest_names=['张三', '李四'])
        other = Tournaments(self.other_ctx, self.clock)
        other.enroll(event_id, '1002', 'ignored', 1, guest_names=['他人同行'])
        before = mine.get(event_id)
        for names in (['张三', '他人同行'], ['小李'], ['不存在']):
            with self.subTest(names=names), self.assertRaises(ValueError): mine.cancel_guests(event_id, '1001', names)
            self.assertEqual(mine.get(event_id), before)
        with self.assertRaisesRegex(ValueError, '重复代报'): mine.enroll(event_id, '1001', 'ignored', 1, guest_names=['张三'])
        with self.assertRaisesRegex(ValueError, '名额不足'): mine.enroll(event_id, '1001', 'ignored', 1, guest_names=['赵六'])
        self.assertEqual(mine.get(event_id), before)
        People(self.ctx, self.clock).set_roles('1001', ['club_admin'])
        for operation in (lambda: mine.cancel(event_id, '1002'), lambda: mine.cancel_guests(event_id, '1002', ['他人同行']), lambda: mine.enroll(event_id, '1002', 'ignored')):
            with self.assertRaises(PermissionError): operation()

    def test_large_event_expired_cancel_allowed_but_closed_draw_is_immutable(self):
        event_id, mine, admin = self.event()
        mine.enroll(event_id, '1001', 'ignored', 3, guest_names=['张三', '李四', '王五'])
        MembershipTerms(self.ctx, self.clock).create('1001', {'start_date': '2025-01-01', 'end_date': '2025-12-31'})
        with self.assertRaises(PermissionError): mine.enroll(event_id, '1001', 'ignored', 1)
        self.assertEqual(mine.cancel_guests(event_id, '1001', ['王五'])['participant_count'], 3)
        admin.transition(event_id, 'close_registration')
        admin.transition(event_id, 'start')
        before = mine.get(event_id)
        self.assertTrue(before['matches'])
        for operation in (lambda: mine.cancel(event_id, '1001'), lambda: mine.cancel_guests(event_id, '1001', ['张三']), lambda: mine.enroll(event_id, '1001', 'ignored')):
            with self.assertRaises(ValueError): operation()
            self.assertEqual(mine.get(event_id), before)

    def test_large_event_gender_and_group_constraints_reject_uncertified_guests(self):
        event_id, mine, _ = self.event(discipline='doubles', doubles_category='womens')
        before = mine.get(event_id)
        with self.assertRaises(ValueError): mine.enroll(event_id, '1001', 'ignored', 1, guest_names=['另一位女士'])
        self.assertEqual(mine.get(event_id), before)

    def test_large_admin_precise_entry_removal_keeps_owner_and_other_guests(self):
        event_id, mine, admin = self.event()
        mine.enroll(event_id, '1001', 'ignored', 2, guest_names=['张三', '李四'])
        with self.assertRaises(PermissionError): mine.cancel_entry(event_id, '1001:1')
        People(self.ctx, self.clock).set_roles('1001', ['club_admin'])
        with self.assertRaises(PermissionError): mine.cancel_entry(event_id, '1001:1')
        result = admin.cancel_entry(event_id, '1001:1')
        self.assertEqual([row['name'] for row in result['participants'] if row['status'] == 'active'], ['小李', '李四'])
        self.assertEqual(admin.cancel_entry(event_id, '1001:1'), result)
        before = admin.get(event_id)
        with self.assertRaises(ValueError): admin.cancel_entry(event_id, '1001:999')
        self.assertEqual(admin.get(event_id), before)
        admin.transition(event_id, 'close_registration')
        with self.assertRaises(ValueError): admin.cancel_entry(event_id, '1001:2')
