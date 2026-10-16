from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app.db import TZ, connect, initialize, stamp
from app.member_imports import MemberImports, MAX_BYTES, normalize_phone, ensure_schema
from app.members import Members
from app.security import BotContext


class MemberImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.current = datetime(2026, 10, 13, 9, 0, tzinfo=TZ)
        self.clock = lambda: self.current
        self.admin = BotContext('bot-one', Path(self.tmp.name), 'one', 'admin', True, 'admin')
        initialize(self.admin.db_path)
        self.imports = MemberImports(self.admin, self.clock)
        self.manager = Members(self.admin, self.clock)

    def member(self, user='alice', **context):
        return Members(replace(self.admin, actor=user, is_admin=False, source='callback', **context), self.clock)

    def preview(self, rows, encoding='utf-8-sig', header='姓名,手机号,自评级别,组别'):
        return self.imports.preview((header+'\n'+rows).encode(encoding))

    def import_rows(self, rows):
        preview = self.preview(rows)
        self.assertTrue(preview['can_commit'], preview)
        return self.imports.commit(preview['token'])

    def application_count(self, user='alice'):
        with connect(self.admin.db_path) as db:
            return db.execute('SELECT count(*) FROM membership_applications WHERE user_id=?', (user,)).fetchone()[0]

    def test_template_is_header_only_and_three_encodings_are_supported(self):
        data = self.imports.template()
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(data.decode('utf-8-sig').splitlines(), ['姓名,手机号,性别,自评级别,组别'])
        for encoding in ('utf-8', 'utf-8-sig', 'gb18030'):
            with self.subTest(encoding=encoding):
                result = self.preview('张三,13800000001,2,周一组;进阶\n', encoding)
                self.assertTrue(result['can_commit'])
                self.assertEqual(result['rows'][0]['groups'], ['周一组', '进阶'])
                self.assertEqual(result['encoding'], 'GB18030' if encoding == 'gb18030' else 'UTF-8')
        self.assertEqual(self.imports.list(), [])
        self.assertEqual(self.manager.groups(), [])

    def test_phone_normalization_and_optional_registration_phone(self):
        for phone in ('13800000001', '+86 138-0000-0001', '0086(138)00000001', '8613800000001', '＋８６ １３８０００００００１'):
            with self.subTest(phone=phone):
                self.assertEqual(normalize_phone(phone), '13800000001')
        self.assertEqual(normalize_phone('0044 7911 123456'), '+447911123456')
        self.assertEqual(normalize_phone(''), '')
        for phone in ('123', '+0123456789', '138000000011', '1.3800000001E10', '+861234', 'abc'):
            with self.subTest(phone=phone), self.assertRaises(ValueError):
                normalize_phone(phone)
        with self.assertRaises(ValueError):
            normalize_phone('', required=True)
        self.assertEqual(self.member().apply('张三', gender='male')['registration_result'], 'submitted')

    def test_csv_limits_headers_and_invalid_rows_never_import(self):
        for content in (b'', b'x'*(MAX_BYTES+1)):
            with self.assertRaises(ValueError):
                self.imports.preview(content)
        for header in ('姓名,手机号,管理员', '姓名,phone,手机号', '姓名,级别', 'name,phone,user_id'):
            with self.subTest(header=header), self.assertRaises(ValueError):
                self.preview('张三,13800000001,club_admin\n', header=header)
        for row in ('张三,abc,,\n', ',13800000001,,\n', '张三,13800000001,10,\n', '张三,13800000001\n'):
            with self.subTest(row=row):
                result = self.preview(row+'李四,13900000002,,\n')
                self.assertFalse(result['can_commit'])
                self.assertIsNone(result['token'])
                self.assertEqual(result['rows'][0]['status'], 'error')
                self.assertTrue(result['errors'])
        with self.assertRaises(ValueError):
            self.preview('张三,13800000001,,\n'*1001)
        result = self.preview('张三,13800000001,,\n'*1000)
        self.assertEqual((result['total'], result['create_count'], result['skip_count']), (1000, 1, 999))
        self.assertEqual(self.imports.list(), [])
        self.assertEqual(self.manager.groups(), [])

    def test_duplicates_are_normalized_and_conflicts_reject_whole_batch(self):
        result = self.preview('张三,+86 13800000001,2,甲;乙\n张三,13800000001,2,乙;甲\n')
        self.assertEqual([r['status'] for r in result['rows']], ['create', 'skip'])
        self.assertEqual(self.imports.commit(result['token'])['imported'], 1)
        self.assertEqual(len(self.manager.groups()), 2)
        result = self.preview('李四,13900000002,,\n张三,13800000001,3,甲;乙\n')
        self.assertFalse(result['can_commit'])
        self.assertIsNone(result['token'])
        self.assertEqual(len(self.imports.list()), 1)
        result = self.preview('李四,+86 13900000002,,\n王五,13900000002,,\n')
        self.assertEqual([r['status'] for r in result['rows']], ['error', 'error'])

    def test_confirmation_token_is_hashed_admin_scoped_expiring_and_idempotent(self):
        result = self.preview('张三,13800000001,,\n')
        with connect(self.admin.db_path) as db:
            row = dict(db.execute('SELECT * FROM member_import_previews').fetchone())
        self.assertNotIn(result['token'], str(row))
        self.assertEqual(row['token_hash'], hashlib.sha256(result['token'].encode()).hexdigest())
        for context in (replace(self.admin, actor='other-admin'), replace(self.admin, scope_id='other-club')):
            with self.assertRaises(ValueError):
                MemberImports(context, self.clock).commit(result['token'])
        first = self.imports.commit(result['token'])
        self.assertEqual(first, {'imported': 1, 'skipped': 0, 'total': 1, 'already_committed': False})
        self.assertEqual(self.imports.commit(result['token']), first | {'already_committed': True})
        self.assertEqual(len(self.imports.list()), 1)
        with connect(self.admin.db_path) as db:
            self.assertIsNone(db.execute('SELECT payload FROM member_import_previews').fetchone()[0])
        self.current += timedelta(minutes=21)
        with self.assertRaises(ValueError):
            self.imports.commit(result['token'])

    def test_commit_rechecks_conflicts_without_partial_records_or_groups(self):
        first = self.preview('张三,13800000001,,新组\n李四,13900000002,2,新组\n')
        self.import_rows('另一个人,13900000002,3,\n')
        with self.assertRaisesRegex(ValueError, '未导入任何'):
            self.imports.commit(first['token'])
        self.assertEqual([r['name'] for r in self.imports.list()], ['另一个人'])
        self.assertEqual(self.manager.groups(), [])
        # A concurrent identical import remains harmless and is counted as skipped.
        same = self.preview('王五,13700000003,,\n')
        self.import_rows('王五,13700000003,,\n')
        self.assertEqual(self.imports.commit(same['token'])['skipped'], 1)

    def test_link_uses_only_phone_and_preserves_imported_approved_tags(self):
        self.import_rows('管理员实名,13800000001,2,周一组;进阶\n')
        result = self.member().apply('表单填写名', 3, '+86 138-0000-0001', gender='male')
        self.assertEqual((result['registration_result'],result['status'],result['name']), ('linked','approved','管理员实名'))
        self.assertEqual((result['level'],result['requested_level'],result['level_status']), (2,2,'approved'))
        self.assertEqual(sorted(g['name'] for g in result['groups']), ['周一组','进阶'])
        self.assertEqual(result['roles'], [])
        row = self.imports.list('linked')[0]
        self.assertEqual(row['linked_user_id'], 'alice')
        self.assertEqual(row['phone_masked'], '138****0001')
        self.assertEqual(self.imports.list('unbound'), [])
        self.assertNotIn('13800000001', json.dumps(row))
        with connect(self.admin.db_path) as db:
            application = dict(db.execute('SELECT * FROM membership_applications').fetchone())
            self.assertEqual((application['status'],application['reviewed_by']), ('approved','admin'))
            self.assertEqual(application['name'], '管理员实名')
            self.assertNotIn('13800000001', str([dict(r) for r in db.execute('SELECT * FROM audit')]))
            self.assertEqual(db.execute('SELECT count(*) FROM user_roles').fetchone()[0], 0)
        self.assertEqual(self.preview('管理员实名,+86 13800000001,2,进阶;周一组\n')['skip_count'], 1)

    def test_blank_import_level_is_not_overridden_by_self_rating(self):
        self.import_rows('张三,13800000001,,\n')
        result = self.member().apply('张三', 3, '13800000001', gender='male')
        self.assertEqual(result['registration_result'], 'linked')
        self.assertIsNone(result['level'])
        self.assertIsNone(result['requested_level'])
        self.assertEqual(result['level_status'], 'none')

    def test_same_name_never_links_and_unmatched_phone_requires_approval(self):
        self.import_rows('张三,13800000001,2,进阶\n')
        for user, phone in [('same-name', None), ('different-phone', '13900000002')]:
            with self.subTest(user=user):
                result = self.member(user).apply('张三', 1, phone, gender='male')
                self.assertEqual((result['registration_result'], result['status']), ('submitted','pending'))
                self.assertIsNone(result['level'])
                self.assertEqual(result['groups'], [])
        self.assertEqual(len(self.imports.list('unbound')), 1)

    def test_cross_group_repeated_registration_does_not_change_profile_or_history(self):
        self.import_rows('张三,13800000001,2,进阶\n')
        original = self.member().apply('表单名', 1, '13800000001', gender='male')
        member = self.member(chat_id='another-group', id='another-bot')
        result = member.apply('另一个姓名', 3, '13900000002', gender='male')
        self.assertEqual(result['registration_result'], 'already_registered')
        self.assertEqual({k:v for k,v in original.items() if k!='registration_result'},
                         {k:v for k,v in result.items() if k!='registration_result'})
        self.assertEqual(self.application_count(), 1)
        # Ordinary approved members get the same non-destructive treatment.
        ordinary = self.member('ordinary')
        ordinary.apply('李四', gender='male'); self.manager.review('ordinary','approved')
        before = ordinary.get()
        self.assertEqual(ordinary.apply(None, 'invalid', 'invalid', gender='male')['registration_result'], 'already_registered')
        self.assertEqual(ordinary.get(), before)
        self.assertEqual(self.application_count('ordinary'), 1)

    def test_bound_phone_cannot_be_taken_or_account_rebound(self):
        self.import_rows('张三,13800000001,,\n李四,13900000002,,\n')
        self.member().apply('张三', phone='13800000001', gender='male')
        with self.assertRaisesRegex(PermissionError, '已关联其他账号'):
            self.member('intruder').apply('另一个人', phone='+86 13800000001', gender='male')
        self.assertIsNone(self.member('intruder').get())
        self.manager.review('alice','rejected')
        with self.assertRaisesRegex(PermissionError, '不能换绑'):
            self.member().apply('李四',phone='13900000002', gender='male')
        self.assertEqual(self.member().get()['status'], 'rejected')
        self.assertEqual(len(self.imports.list('unbound')), 1)

    def test_concurrent_claims_have_exactly_one_owner(self):
        self.import_rows('张三,13800000001,,\n')
        barrier = threading.Barrier(2)
        def claim(user):
            barrier.wait()
            try:
                return (user,self.member(user).apply('填写姓名',phone='13800000001', gender='male')['registration_result'])
            except PermissionError:
                return (user,'denied')
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(claim,['alice','bob']))
        self.assertEqual(sorted(r[1] for r in outcomes), ['denied','linked'])
        owner = next(user for user,result in outcomes if result=='linked')
        self.assertEqual(self.imports.list('linked')[0]['linked_user_id'], owner)
        self.assertEqual(len(self.manager.list()), 1)
        self.assertEqual(self.application_count(owner), 1)

    def test_legacy_duplicate_phones_fail_closed_before_writing(self):
        self.member('first').apply('甲',phone='13800000001', gender='male')
        self.member('second').apply('乙', gender='male')
        with connect(self.admin.db_path, True) as db:
            db.execute('UPDATE member_records SET phone=? WHERE user_id=?', ('+86 13800000001','second'))
        with self.assertRaisesRegex(PermissionError, '重复账号'):
            self.member().apply('张三',phone='13800000001', gender='male')
        self.assertIsNone(self.member().get())
        self.assertFalse(self.preview('张三,13800000001,,\n')['can_commit'])

    def test_legacy_duplicate_pre_registration_formats_fail_closed(self):
        self.import_rows('张三,13800000001,,\n')
        with connect(self.admin.db_path, True) as db:
            db.execute('INSERT INTO pre_registered_members(scope_id,name,phone,created_by,created_at) VALUES(?,?,?,?,?)',
                       ('club','张三','+86 13800000001','admin',stamp(self.clock())))
        with self.assertRaisesRegex(PermissionError, '重复预登记'):
            self.member().apply('张三',phone='13800000001', gender='male')
        self.assertFalse(self.preview('张三,13800000001,,\n')['can_commit'])

    def test_link_is_atomic_with_outer_registration_token_transaction(self):
        self.import_rows('张三,13800000001,2,进阶\n')
        with self.assertRaisesRegex(RuntimeError, 'consume'):
            with connect(self.admin.db_path, True) as db:
                self.assertEqual(self.member().apply('填写名',phone='13800000001',db=db, gender='male')['registration_result'], 'linked')
                raise RuntimeError('consume failed')
        self.assertIsNone(self.member().get())
        self.assertEqual(self.application_count(), 0)
        self.assertEqual(len(self.imports.list('unbound')), 1)
        self.assertEqual(self.member().apply('填写名',phone='13800000001', gender='male')['registration_result'], 'linked')

    def test_pending_application_is_superseded_when_matching_import_links(self):
        self.member().apply('旧申请姓名', 1, gender='male')
        self.import_rows('张三,13800000001,2,进阶\n')
        self.member().apply('新申请姓名', 3, '13800000001', gender='male')
        with connect(self.admin.db_path) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT status FROM membership_applications ORDER BY id')], ['superseded','approved'])

    def test_suspension_and_rejection_are_not_bypassed_by_previous_import(self):
        self.import_rows('张三,13800000001,,\n')
        self.member().apply('张三',phone='13800000001', gender='male')
        self.manager.review('alice','suspended')
        with self.assertRaisesRegex(PermissionError, '停用'):
            self.member().apply('张三',phone='13800000001', gender='male')
        self.manager.review('alice','rejected')
        result = self.member().apply('张三',phone='13800000001', gender='male')
        self.assertEqual((result['registration_result'],result['status']), ('submitted','pending'))
        self.assertEqual(self.imports.list('linked')[0]['linked_user_id'], 'alice')

    def test_all_import_operations_require_admin_and_records_are_scope_isolated(self):
        ordinary = MemberImports(self.member().ctx,self.clock)
        for action in (ordinary.template, ordinary.list, lambda: ordinary.preview(b'name,phone\na,13800000001'), lambda: ordinary.commit('a'*43)):
            with self.assertRaises(PermissionError):
                action()
        self.import_rows('张三,13800000001,,\n')
        other = MemberImports(replace(self.admin,scope_id='other-club'),self.clock)
        self.assertEqual(other.list(), [])
        result = self.member(scope_id='other-club').apply('张三',phone='13800000001', gender='male')
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.imports.list()[0]['status'], 'unbound')

    def test_existing_account_csv_never_overwrites_or_grants_roles(self):
        self.member().apply('张三',phone='13800000001', gender='male')
        self.assertFalse(self.preview('张三,13800000001,,\n')['can_commit'])
        self.manager.review('alice','approved')
        self.assertEqual(self.preview('张三,13800000001,,\n')['skip_count'], 1)
        self.assertFalse(self.preview('张三,13800000001,3,\n')['can_commit'])
        self.assertEqual(self.member().get()['roles'], [])
        self.assertIsNone(self.member().get()['level'])
        self.assertEqual(self.imports.list(), [])

    def test_admin_profile_edit_cannot_occupy_unbound_pre_registration_phone(self):
        self.import_rows('预登记姓名,13800000001,2,进阶\n')
        self.member('ordinary').apply('普通会员',phone='13900000002', gender='male')
        self.manager.review('ordinary','approved')
        before = self.member('ordinary').get()
        with self.assertRaisesRegex(ValueError, '尚未关联的预登记档案'):
            self.manager.update_details('ordinary',phone='+86 138-0000-0001',requested_level=3)
        self.assertEqual(self.member('ordinary').get(), before)
        self.assertEqual(len(self.imports.list('unbound')), 1)
        # Rejection did not reserve the imported phone for the edited account.
        linked = self.member('owner').apply('报名表姓名',phone='13800000001', gender='male')
        self.assertEqual(linked['registration_result'], 'linked')
        self.assertEqual(linked['name'], '预登记姓名')
        with self.assertRaisesRegex(PermissionError, '已关联其他账号'):
            self.manager.update_details('ordinary',phone='13800000001')
        # Editing the already-linked owner's own phone is still safe.
        self.assertEqual(self.manager.update_details('owner',phone='+8613800000001')['phone'], '13800000001')
        self.assertEqual(self.imports.list('linked')[0]['linked_user_id'], 'owner')


if __name__ == '__main__':
    unittest.main()
