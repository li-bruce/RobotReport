from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import json
import tempfile
import threading
import unittest

from app.db import TZ, connect, initialize, stamp
from app.members import Members
from app.security import Platform
from app.service import Service


class MembershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = lambda: datetime(2026, 10, 13, 9, 0, tzinfo=TZ)
        self.platform = Platform(Path(self.temp.name), 'membership-test-bootstrap-token-0123456789')
        self.principal = self.platform.authenticate('membership-test-bootstrap-token-0123456789')
        self.bot = self.platform.create(self.principal, {'name': '测试机器人', 'chat_id': 'group-1'})
        self.admin = self.platform.context(self.principal, self.bot['id'])
        self.user = replace(self.admin, actor='101', is_admin=False, source='callback')
        self.members = Members(self.user, self.clock)
        self.manager = Members(self.admin, self.clock)
        self.service = Service(self.user, self.clock)
        self.admin_service = Service(self.admin, self.clock)

    def approve(self, level=None, groups=None):
        self.members.apply('张三', gender='male')
        self.manager.review('101', 'approved')
        if level is not None:
            self.members.request_level(level)
            self.manager.review_level('101', True)
        if groups is not None:
            self.manager.set_groups('101', groups)

    def test_unregistered_pending_rejected_and_admin_proxy_cannot_enroll(self):
        for state in ('unregistered', 'pending', 'rejected'):
            with self.subTest(state=state):
                if state == 'pending':
                    self.members.apply('张三', gender='male')
                elif state == 'rejected':
                    self.manager.review('101', 'rejected')
                with self.assertRaises(PermissionError):
                    self.service.enroll('101', '张三')
                with self.assertRaises(PermissionError):
                    self.admin_service.enroll('101', '张三')
        self.manager.review('101', 'approved')
        rows = self.service.enroll('101', 'forged name')['registrations']
        self.assertEqual(rows[0]['name'], '张三')
        with self.assertRaises(PermissionError):
            self.service.enroll('another-user', '张三')

    def test_callbacks_do_not_override_reviewed_or_submitted_names(self):
        self.members.touch('旧昵称')
        self.members.apply('张三', gender='male')
        self.members.touch('回调旧昵称')
        self.assertEqual(self.members.get()['name'], '张三')
        self.manager.review('101', 'approved')
        self.members.touch('外部变更')
        self.assertEqual(self.members.get()['name'], '张三')
        result = self.members.apply('张四', gender='male')
        self.assertEqual(result['registration_result'], 'already_registered')
        self.assertEqual(self.members.get()['status'], 'approved')
        self.assertEqual(self.members.get()['name'], '张三')
        self.assertEqual(self.service.enroll('101', '张四')['registrations'][0]['name'], '张三')
        self.manager.review('101', 'approved', name='张四')
        self.assertEqual(self.service.enroll('101', 'other')['registrations'][0]['name'], '张四')

    def test_id_cannot_be_display_name_and_registration_requires_name(self):
        for name in ('101', '101+1', '', None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.members.apply(name, gender='male')
        self.members.touch()
        self.assertEqual(self.members.get()['name'], '')
        with self.assertRaises(ValueError):
            self.manager.review('101', 'approved')

    def test_level_is_reviewed_and_rejection_removes_approved_level(self):
        self.approve()
        self.members.request_level(3)
        self.assertIsNone(self.members.get()['level'])
        self.assertEqual(self.members.get()['level_status'], 'pending')
        self.manager.review_level('101', True)
        self.assertEqual(self.members.get()['level'], 3)
        self.members.request_level(2)
        self.manager.review_level('101', False)
        self.assertIsNone(self.members.get()['level'])
        for level in (0, 10, True, '2'):
            with self.subTest(level=level), self.assertRaises(ValueError):
                self.members.request_level(level)

    def test_admin_only_actions_and_member_private_data(self):
        self.approve()
        actions = [lambda: self.members.list(), lambda: self.members.review('101', 'approved'),
                   lambda: self.members.review_level('101', True, 2), lambda: self.members.save_group('精英'),
                   lambda: self.members.set_groups('101', []), lambda: self.members.update_details('101', phone='13812345678'),
                   lambda: self.members.get('another'), lambda: self.members.touch('另一个人', 'another')]
        for action in actions:
            with self.subTest(action=action), self.assertRaises(PermissionError):
                action()

    def test_phone_is_validated_masked_and_not_in_audit(self):
        self.members.apply('张三', level=2, phone='13812345678', gender='male')
        self.assertEqual(self.members.get()['phone'], '13812345678')
        self.assertIsNone(self.members.get()['level'])
        listed = self.manager.list()[0]
        self.assertNotIn('phone', listed)
        self.assertEqual(listed['phone_masked'], '138****5678')
        self.assertNotIn('13812345678', json.dumps(self.admin_service.audit()))
        for number in ('1234', '138123456789', 'script:123', '+0123456789'):
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.members.apply('张三', phone=number, gender='male')
        self.manager.update_details('101', phone='+447911123456', requested_level=3)
        self.assertEqual(self.manager.get('101')['requested_level'], 3)
        self.assertIsNone(self.manager.get('101')['level'])

    def test_registration_can_share_atomic_token_transaction(self):
        with self.assertRaises(RuntimeError):
            with connect(self.user.db_path, True) as db:
                self.members.apply('张三', 2, '13812345678', db=db, gender='male')
                raise RuntimeError('token consume failed')
        self.assertIsNone(self.members.get())

    def test_groups_or_and_level_range_and_guests(self):
        group_a = self.manager.save_group('进阶')['id']
        group_b = self.manager.save_group('社员')['id']
        self.approve(level=2, groups=[group_b])
        session = self.admin_service.sessions()[0]
        self.admin_service.save_session_constraints(session['id'], {'min_level': 2, 'max_level': 3, 'allowed_group_ids': [group_a, group_b]})
        self.assertEqual(len(self.service.enroll('101', '张三')['registrations']), 1)
        with self.assertRaisesRegex(PermissionError, '同行'):
            self.service.enroll('101', '张三', 1)
        self.manager.set_groups('101', [])
        with self.assertRaisesRegex(PermissionError, '组别'):
            self.service.enroll('101', '张三')
        self.manager.set_groups('101', [group_b])
        self.manager.review_level('101', True, 1)
        with self.assertRaisesRegex(PermissionError, '最低2级'):
            self.service.enroll('101', '张三')
        self.manager.review_level('101', True, 3)
        self.admin_service.save_session_constraints(session['id'], {'max_level': 2})
        with self.assertRaisesRegex(PermissionError, '最高2级'):
            self.service.enroll('101', '张三')
        self.manager.review_level('101', False)
        with self.assertRaisesRegex(PermissionError, '尚无已审核等级'):
            self.service.enroll('101', '张三')

    def test_ordinary_guests_have_no_certified_tags(self):
        group = self.manager.save_group('普通组')['id']
        self.approve(3, [group])
        records = self.service.enroll('101', 'fake', 2)['registrations']
        self.assertEqual([r['name'] for r in records], ['张三', '张三+1', '张三+2'])
        self.assertEqual([r['level'] for r in records], [3, None, None])
        self.assertEqual([r['group_ids'] for r in records], [[group], [], []])

    def test_constraints_invalidate_existing_eligibility_without_rewriting_history(self):
        self.approve(1)
        records = self.service.enroll('101', '张三', 1)['registrations']
        session_id = records[0]['session_id']
        result = self.admin_service.save_session_constraints(session_id, {'min_level': 2})
        self.assertEqual(result['ineligible_registrations'], [r['id'] for r in records])
        records = self.admin_service.registrations()['registrations']
        self.assertTrue(all(r['status'] == 'active' and not r['eligible'] for r in records))
        self.manager.review_level('101', True, 2)
        self.assertTrue(self.admin_service.registrations()['registrations'][0]['eligible'])
        self.assertFalse(self.admin_service.registrations()['registrations'][1]['eligible'])

    def test_membership_and_groups_shared_across_bots_not_backends(self):
        self.approve()
        other_bot = self.platform.create(self.principal, {'name': '其他机器人', 'chat_id': 'group-2'})
        other = self.platform.context(self.principal, other_bot['id'])
        self.assertEqual(Members(other).get('101')['status'], 'approved')
        self.assertEqual(Service(other, self.clock).enroll('101', '张三')['registrations'][0]['name'], '张三')
        shared_group = Members(other).save_group('另一群创建的共同组')['id']
        self.manager.set_groups('101', [shared_group])
        self.admin_service.save_session_constraints(self.admin_service.sessions()[0]['id'], {'allowed_group_ids': [shared_group]})
        self.assertEqual(Members(other).get('101')['group_ids'], [shared_group])
        isolated = Platform(Path(self.temp.name)/'separate-backend', 'separate-backend-test-token-0123456789')
        isolated_ctx = isolated.workspace_context()
        self.assertEqual(Members(isolated_ctx).list(), [])
        with self.assertRaises(PermissionError):
            Service(isolated_ctx,self.clock).enroll('101','张三')
        with self.assertRaises(PermissionError):
            isolated.authenticate('membership-test-bootstrap-token-0123456789')

    def test_migration_is_idempotent_pending_and_only_repairs_fallback_names(self):
        session_id = self.admin_service.sessions()[0]['id']
        with connect(self.admin.db_path, True) as db:
            for uid, name in [('old-1', 'old-1'), ('old-2', '老王')]:
                db.execute('INSERT INTO profiles VALUES(?,?,?)', (self.admin.scope_id, uid, name))
                for guest in (0, 1):
                    db.execute('INSERT INTO registrations(session_id,chat_id,user_id,name,guest_index,queue_seq,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                               (session_id, self.admin.scope_id, uid, name + (f'+{guest}' if guest else ''), guest, guest, stamp(), stamp()))
            db.execute('INSERT INTO legacy_rows VALUES(?,?,?)', ('legacy-evidence', '{"name":"old-1"}', stamp()))
        initialize(self.admin.db_path)
        initialize(self.admin.db_path)
        self.assertEqual(self.manager.get('old-1')['name'], '')
        self.assertEqual(self.manager.get('old-2')['status'], 'pending')
        self.manager.review('old-1', 'approved', name='张老师')
        self.manager.review('old-2', 'approved', name='王老师')
        initialize(self.admin.db_path)
        with connect(self.admin.db_path) as db:
            names = [r[0] for r in db.execute('SELECT name FROM registrations WHERE user_id=? ORDER BY guest_index', ('old-1',))]
            self.assertEqual(names, ['张老师', '张老师+1'])
            self.assertEqual(db.execute('SELECT name FROM registrations WHERE user_id=? AND guest_index=0', ('old-2',)).fetchone()[0], '老王')
            self.assertEqual(db.execute('SELECT row_json FROM legacy_rows').fetchone()[0], '{"name":"old-1"}')
        self.assertEqual(self.manager.get('old-1')['status'], 'approved')

    def test_revocation_waiting_on_write_lock_cannot_race_enrollment(self):
        group = self.manager.save_group('社员')['id']
        self.approve(2, [group])
        session = self.admin_service.sessions()[0]
        self.admin_service.save_session_constraints(session['id'], {'allowed_group_ids': [group]})
        started = threading.Event()
        def attempt():
            started.set()
            return self.service.enroll('101', '张三')
        with ThreadPoolExecutor(max_workers=1) as pool:
            with connect(self.user.db_path, True) as db:
                db.execute('DELETE FROM member_group_links WHERE chat_id=? AND user_id=?', (self.admin.scope_id, '101'))
                future = pool.submit(attempt)
                self.assertTrue(started.wait(2))
                self.assertFalse(future.done())
            with self.assertRaises(PermissionError):
                future.result(timeout=5)
        self.assertEqual(self.admin_service.registrations()['registrations'], [])

    def test_tournament_only_legacy_members_and_name_repairs_are_business_scoped(self):
        with connect(self.admin.db_path, True) as db:
            db.executescript('''CREATE TABLE tour_events(id INTEGER PRIMARY KEY,chat_id TEXT NOT NULL);
                               CREATE TABLE tour_participants(tournament_id INTEGER,id TEXT,user_id TEXT,name TEXT,guest_index INTEGER,registered_at TEXT);
                               INSERT INTO tour_events VALUES(1,'club');
                               INSERT INTO tour_events VALUES(2,'external-scope');''')
            for event_id in (1, 2):
                db.execute('INSERT INTO tour_participants VALUES(?,?,?,?,?,?)', (event_id, 'stable-player-1', 'legacy', 'legacy', 0, stamp()))
        initialize(self.admin.db_path)
        self.assertEqual(self.manager.get('legacy')['status'], 'pending')
        self.assertEqual(self.manager.get('legacy')['name'], '')
        self.manager.review('legacy', 'approved', name='李先生')
        with connect(self.admin.db_path) as db:
            self.assertEqual(tuple(db.execute('SELECT id,name FROM tour_participants WHERE tournament_id=1').fetchone()), ('stable-player-1', '李先生'))
            self.assertEqual(db.execute('SELECT name FROM tour_participants WHERE tournament_id=2').fetchone()[0], 'legacy')

    def test_approved_level_resubmission_is_idempotent_and_constraints_validated(self):
        self.approve(2)
        self.members.request_level(2)
        self.manager.update_details('101', requested_level=2)
        self.members.apply('张三', level=2, gender='male')
        self.assertEqual(self.members.get()['level_status'], 'approved')
        session = self.admin_service.sessions()[0]
        for rules in ({'min_level': 3, 'max_level': 1}, {'min_level': 0}, {'allowed_group_ids': [True]}, {'other': 1}):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                self.admin_service.save_session_constraints(session['id'], rules)

    def test_activity_capacity_update_reclassifies_without_losing_policy_or_queue(self):
        self.approve()
        rows = self.service.enroll('101', '张三', 3)['registrations']
        session_id = rows[0]['session_id']
        original_ids = [r['id'] for r in rows]
        self.admin_service.save_session_constraints(session_id, {'capacity': 1, 'substitute_capacity': 1})
        rows = self.admin_service.registrations()['registrations']
        self.assertEqual([r['category'] for r in rows], ['正式', '替补', '候补', '候补'])
        self.assertEqual([r['id'] for r in rows], original_ids)
        self.admin_service.save_session_constraints(session_id, {'min_level': 2})
        updated = self.admin_service.save_session_constraints(session_id, {'capacity': 2})
        self.assertEqual(updated['min_level'], 2)
        self.assertEqual(updated['substitute_capacity'], 1)
        with self.assertRaises(ValueError):
            self.admin_service.save_session_constraints(session_id, {'capacity': 0})

    def test_named_proxy_registrations_survive_owner_renames_and_numeric_updates(self):
        self.approve(2)
        records = self.service.enroll('101', 'ignored', 2, guest_names=['王五', '李四'])['registrations']
        self.assertEqual([r['name'] for r in records], ['张三', '王五', '李四'])
        self.assertEqual([r['is_proxy'] for r in records], [False, True, True])
        self.assertEqual([r['sponsor_name'] for r in records], ['张三'] * 3)
        self.assertEqual([r['level'] for r in records], [2, None, None])
        self.manager.review('101', 'approved', name='张四')
        records = self.service.enroll('101', 'ignored', 2)['registrations']
        self.assertEqual([r['name'] for r in records], ['张四', '王五', '李四', '张四+3', '张四+4'])
        self.assertEqual([r['guest_name'] for r in records], ['', '王五', '李四', '', ''])
        records = self.service.enroll('101', 'ignored', 1, guest_names=['赵六'])['registrations']
        self.assertEqual([r['name'] for r in records if r['status'] == 'active'], ['张四', '王五', '李四', '张四+3', '张四+4', '赵六'])
        for names in (['张四'], ['重复', '重复'], [123], ['']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.service.enroll('101', 'ignored', len(names), guest_names=names)
        with self.assertRaises(ValueError):
            self.service.enroll('101', 'ignored', 2, guest_names=['王五'])


if __name__ == '__main__':
    unittest.main()
