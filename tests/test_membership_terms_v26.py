from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.db import TZ, connect, initialize
from app.members import Members
from app.membership_terms import MembershipTerms
from app.security import BotContext
from app.service import Service
from app.tournaments import Tournaments


class MembershipTermTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        initialize(self.root / 'business.sqlite3')
        self.moment = datetime(2026, 10, 13, 9, tzinfo=TZ)
        self.clock = lambda: self.moment
        engine_clock = patch('app.competitions._now', self.clock)
        engine_clock.start()
        self.addCleanup(engine_clock.stop)
        self.admin = BotContext('bot', self.root, 'group', 'admin', True, 'admin')
        self.user = replace(self.admin, actor='member-a', is_admin=False, source='callback')
        self.other = replace(self.user, actor='member-b')
        self.manager = Members(self.admin, self.clock)
        for context, name in [(self.user, '张三'), (self.other, '李四')]:
            Members(context, self.clock).apply(name, gender='male')
            self.manager.review(context.actor, 'approved')
        self.terms = MembershipTerms(self.admin, self.clock)
        self.mine = MembershipTerms(self.user, self.clock)
        self.service = Service(self.user, self.clock)

    def term(self, start='2026-10-01', end='2026-10-31', **extra):
        return self.terms.create(self.user.actor, {'start_date': start, 'end_date': end, **extra})

    def test_migration_keeps_registration_and_does_not_invent_renewal(self):
        with connect(self.admin.db_path) as db:
            before = [tuple(r) for r in db.execute('SELECT * FROM membership_applications')]
        initialize(self.admin.db_path)
        summary = self.mine.summary()
        self.assertEqual(summary['status'], 'not_configured')
        self.assertFalse(summary['configured'])
        self.assertTrue(summary['can_enroll'])
        self.assertEqual(self.mine.list(), [])
        self.assertEqual(self.manager.get(self.user.actor)['membership'], summary)
        with connect(self.admin.db_path) as db:
            self.assertEqual(before, [tuple(r) for r in db.execute('SELECT * FROM membership_applications')])
        self.assertEqual(self.mine.application_history()[0]['name'], '张三')
        self.assertNotIn('phone', self.mine.application_history()[0])
        self.assertTrue(self.service.enroll(self.user.actor, '张三')['registrations'])

    def test_period_history_gap_and_renewal_preserve_original(self):
        old = self.term('2025-01-01', '2025-12-31', amount='199.90', note='2025年登记')
        new = self.term('2026-10-20', '2027-10-19', amount='250')
        self.assertEqual(old['amount_cents'], 19990)
        self.assertEqual(old['amount'], '199.90')
        self.assertEqual(new['amount'], '250.00')
        self.assertEqual(self.mine.summary()['status'], 'gap')
        self.assertFalse(self.mine.summary()['can_enroll'])
        self.assertEqual(self.mine.summary()['next_term']['id'], new['id'])
        self.assertEqual(self.mine.summary()['last_end_date'], '2025-12-31')
        self.assertEqual(self.mine.list()[1], old)
        self.assertEqual(self.mine.summary(at_date='2026-10-20')['status'], 'active')
        self.assertEqual(self.mine.summary(at_date='2027-10-19')['status'], 'active')
        self.assertEqual(self.mine.summary(at_date='2027-10-20')['status'], 'expired')

    def test_future_only_period_and_inclusive_boundaries(self):
        self.term('2026-10-14', '2026-10-15')
        self.assertEqual(self.mine.summary()['status'], 'upcoming')
        self.assertEqual(self.mine.summary(at_date='2026-10-14')['status'], 'active')
        self.assertEqual(self.mine.summary(at_date='2026-10-15')['status'], 'active')
        self.assertEqual(self.mine.summary(at_date='2026-10-16')['status'], 'expired')

    def test_dates_use_shanghai_clock_at_utc_boundary(self):
        self.term('2026-10-14', '2026-10-14')
        self.moment = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(self.mine.summary()['as_of'], '2026-10-14')
        self.assertEqual(self.mine.summary()['status'], 'active')
        self.moment = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(self.mine.summary()['status'], 'expired')

    def test_adjacent_periods_allowed_but_overlap_rejected(self):
        self.term('2026-01-01', '2026-12-31')
        self.term('2027-01-01', '2027-12-31')
        for start, end in [('2026-12-31', '2027-01-02'), ('2026-03-01', '2026-04-01'),
                           ('2025-01-01', '2028-01-01')]:
            with self.subTest(start=start), self.assertRaisesRegex(ValueError, '重叠'):
                self.term(start, end)
        self.assertEqual(len(self.mine.list()), 2)

    def test_overlapping_concurrent_renewal_cannot_duplicate(self):
        def create():
            try:
                return self.term()['id']
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: create(), range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(self.mine.list()), 1)

    def test_admin_only_and_personal_read_boundary(self):
        term = self.term()
        for operation in [lambda: self.mine.create(self.user.actor, {'start_date': '2027-01-01', 'end_date': '2027-12-31'}),
                          lambda: self.mine.void(self.user.actor, term['id'], 1),
                          lambda: self.mine.summary(self.other.actor), lambda: self.mine.list(self.other.actor),
                          lambda: self.mine.application_history(self.other.actor)]:
            with self.assertRaises(PermissionError):
                operation()
        foreign = MembershipTerms(replace(self.admin, scope_id='another-club'), self.clock)
        with self.assertRaises(ValueError):
            foreign.void(self.user.actor, term['id'], 1)
        with self.assertRaises(ValueError):
            self.terms.void(self.other.actor, term['id'], 1)

    def test_void_preserves_amount_period_note_and_audit_then_can_replace(self):
        term = self.term(amount='120.50', note='原始登记')
        result = self.terms.void(self.user.actor, term['id'], 1, '录入日期有误')
        for key in ['start_date', 'end_date', 'amount', 'amount_cents', 'created_at', 'created_by', 'note']:
            self.assertEqual(result[key], term[key])
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['status'], 'void')
        self.assertEqual(self.mine.summary()['status'], 'inactive')
        self.assertTrue(self.mine.summary()['configured'])
        self.assertFalse(self.mine.summary()['can_enroll'])
        with self.assertRaises(ValueError):
            self.terms.void(self.user.actor, term['id'], 1)
        self.term(amount='120.50', note='更正后')
        self.assertEqual(len(self.mine.list()), 2)
        with connect(self.admin.db_path) as db:
            audit = list(db.execute('SELECT * FROM membership_term_audit ORDER BY id'))
        self.assertEqual([r['action'] for r in audit], ['term.add', 'term.void', 'term.add'])
        self.assertTrue(all(r['actor'] == self.admin.actor for r in audit))
        self.assertTrue(all(r['created_at'].startswith('2026-10-13T09:00:00') for r in audit))

    def test_invalid_dates_amounts_and_unknown_fields_are_rejected(self):
        for data in [{'start_date': '2026-02-30'}, {'end_date': '2026-01-01'}, {'start_date': True},
                     {'amount': -1}, {'amount': '12.345'}, {'amount': True}, {'amount': 'NaN'},
                     {'amount': 1.01}, {'amount': '1e3'}, {'note': []}, {'paid': True}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.term(**data) if not ('start_date' in data or 'end_date' in data) else self.terms.create(
                    self.user.actor, {'start_date': '2026-10-01', 'end_date': '2026-10-31', **data})
        self.assertEqual(self.mine.list(), [])
        self.assertEqual(self.term(amount='0')['amount_cents'], 0)

    def test_pending_members_cannot_get_periods_and_active_period_does_not_approve(self):
        self.manager.review(self.user.actor, 'pending')
        with self.assertRaisesRegex(ValueError, '审核'):
            self.term()
        self.manager.review(self.user.actor, 'approved')
        self.term()
        self.manager.review(self.user.actor, 'suspended')
        self.assertFalse(self.mine.summary()['can_enroll'])
        with self.assertRaises(PermissionError):
            self.service.enroll(self.user.actor, '张三')

    def test_ledger_and_audit_rollback_with_parent_transaction(self):
        with self.assertRaises(RuntimeError):
            with connect(self.admin.db_path, True) as db:
                self.terms.create(self.user.actor, {'start_date': '2026-10-01', 'end_date': '2026-10-31'}, db=db)
                raise RuntimeError('later admin operation failed')
        self.assertEqual(self.mine.list(), [])
        with connect(self.admin.db_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM membership_term_audit').fetchone()[0], 0)

    def test_expired_blocks_new_daily_and_admin_proxy_but_history_remains(self):
        self.term('2025-01-01', '2025-12-31')
        for service in [self.service, Service(self.admin, self.clock)]:
            with self.assertRaisesRegex(PermissionError, '已到期'):
                service.enroll(self.user.actor, '张三')
        self.assertEqual(Members(self.user, self.clock).get()['name'], '张三')
        self.assertEqual(self.service.stats(user_id=self.user.actor)['summary']['registrations'], 0)
        self.assertTrue(self.mine.application_history())

    def test_event_date_must_be_covered_and_future_renewal_cannot_skip_current_gap(self):
        self.term('2026-10-01', '2026-10-13')
        with self.assertRaisesRegex(PermissionError, '未覆盖活动日期'):
            self.service.enroll(self.user.actor, '张三', day='2026-10-14', number=1)
        self.term('2026-10-15', '2027-10-14')
        self.assertTrue(self.service.enroll(self.user.actor, '张三', day='2026-10-15', number=1)['registrations'])
        self.moment = datetime(2026, 10, 14, 9, tzinfo=TZ)
        with self.assertRaisesRegex(PermissionError, '续费间隔期'):
            self.service.enroll(self.user.actor, '张三', day='2026-10-16', number=1)

    def test_previous_booking_survives_expiry_but_new_guest_or_rejoining_does_not(self):
        self.term('2026-10-01', '2026-10-13')
        self.term('2026-10-15', '2026-10-31')
        self.service.enroll(self.user.actor, '张三', day='2026-10-15', guests=1, number=1)
        self.moment = datetime(2026, 10, 14, 9, tzinfo=TZ)
        self.assertEqual(len(self.service.enroll(self.user.actor, '张三', day='2026-10-15', number=1)['registrations']), 2)
        with self.assertRaises(PermissionError):
            self.service.enroll(self.user.actor, '张三', day='2026-10-15', guests=1, number=1)
        with self.assertRaises(PermissionError):
            self.service.enroll(self.user.actor, '张三', day='2026-10-15', guests=2, number=1)
        self.service.enroll(self.user.actor, '张三', day='2026-10-15', guests=0, number=1)
        self.assertEqual(self.service.cancel(self.user.actor, day='2026-10-15', number=1)['cancelled'], 1)
        with self.assertRaises(PermissionError):
            self.service.enroll(self.user.actor, '张三', day='2026-10-15', number=1)

    def event(self, **extra):
        return Tournaments(self.admin, self.clock).create({'kind': 'large', 'title': '秋季公开赛',
            'keyword': '秋季公开赛', 'date': '2026-10-15', 'format': 'round_robin',
            'registration_open': '2026-10-01T00:00:00+08:00', 'registration_close': '2026-10-15T15:00:00+08:00',
            'options': {'discipline': 'singles'}, **extra})

    def test_named_and_unnamed_guest_replacement_requires_validity_even_for_admin(self):
        term = self.term()
        self.service.enroll(self.user.actor, '张三', guests=2, guest_names=['原同行人', '不再参加'])
        self.terms.void(self.user.actor, term['id'], 1, '期限已失效')
        for service in [self.service, Service(self.admin, self.clock)]:
            with self.assertRaises(PermissionError):
                service.enroll(self.user.actor, '张三', guests=2, guest_names=['新同行人', '不再参加'])
        result = self.service.enroll(self.user.actor, '张三')
        self.assertEqual([r['guest_name'] for r in result['registrations'] if r['status'] == 'active'], ['', '原同行人', '不再参加'])
        self.assertEqual(self.service.cancel_guests(self.user.actor, ['不再参加'])['cancelled'], 1)
        self.assertEqual(self.service.cancel(self.user.actor)['cancelled'], 1)
        self.assertEqual(self.service.cancel_guests(self.user.actor, ['原同行人'])['cancelled'], 1)
        # Adding any new guest requires validity; existing guests can still cancel.
        self.terms.create(self.other.actor, {'start_date': '2026-10-01', 'end_date': '2026-10-31'})
        other_service = Service(self.other, self.clock)
        other_service.enroll(self.other.actor, '李四', guests=1)
        other_term = self.terms.list(self.other.actor)[0]
        self.terms.void(self.other.actor, other_term['id'], other_term['version'])
        with self.assertRaises(PermissionError):
            other_service.enroll(self.other.actor, '李四', guests=1, guest_names=['新补姓名'])
        self.assertEqual(other_service.cancel(self.other.actor)['cancelled'], 1)
        self.assertEqual(other_service.cancel_guests(self.other.actor, ['李四+1'])['cancelled'], 1)

    def test_large_tournament_new_enrollment_and_preloaded_participant_guard(self):
        self.term('2026-10-01', '2026-10-13')
        event = self.event()
        Tournaments(self.admin, self.clock).transition(event['id'], 'open_registration')
        for ctx in [self.user, self.admin]:
            with self.assertRaisesRegex(PermissionError, '未覆盖活动日期'):
                Tournaments(ctx, self.clock).enroll(event['id'], self.user.actor, '张三')
        with self.assertRaisesRegex(PermissionError, '未覆盖活动日期'):
            self.event(keyword='另一个秋季赛', participants=[{'id': 'member-a', 'user_id': 'member-a'}])
        self.term('2026-10-14', '2027-10-13')
        self.assertEqual(len(Tournaments(self.user, self.clock).enroll(event['id'], self.user.actor, '张三')['participants']), 1)

    def test_large_existing_registration_can_cancel_after_gap(self):
        self.term('2026-10-01', '2026-10-13')
        self.term('2026-10-15', '2027-10-14')
        event = self.event()
        events = Tournaments(self.admin, self.clock)
        events.transition(event['id'], 'open_registration')
        mine = Tournaments(self.user, self.clock)
        mine.enroll(event['id'], self.user.actor, '张三')
        self.moment = datetime(2026, 10, 14, 9, tzinfo=TZ)
        self.assertEqual(len(mine.get(event['id'])['participants']), 1)
        mine.enroll(event['id'], self.user.actor, '张三')
        with self.assertRaises(PermissionError):
            mine.enroll(event['id'], self.user.actor, '张三', guests=1)
        mine.cancel(event['id'], self.user.actor)
        with self.assertRaises(PermissionError):
            mine.enroll(event['id'], self.user.actor, '张三')


if __name__ == '__main__':
    unittest.main()
