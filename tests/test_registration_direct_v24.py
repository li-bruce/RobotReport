from tests.legacy_registry import seed_legacy_channel
"""Identity-bound direct registration, durable retries and legacy links."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.db import TZ, connect, dumps
from app.main import create_app
from app.members import Members
from app.registration import create_form, form_status, submit_form, confirm_form
from app.security import digest
from app.service import Service


class DirectRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.time = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
        self.clock = lambda: self.time
        self.app = create_app(Path(self.tmp.name), 'direct-form-test-token-at-least-32-chars',
                              start_scheduler=False, live=False, clock=self.clock)
        self.platform = self.app.state.platform
        self.principal = self.platform.authenticate('direct-form-test-token-at-least-32-chars')
        self.bot = self.platform.create(self.principal, {'name': '活动助手', 'chat_id': 'one', 'robot_key': 'one-key'})
        self.first = self.bot['channels'][0]['id']
        self.second = seed_legacy_channel(self.platform, self.bot['id'],
            {'name': '二群', 'chat_id': 'two', 'robot_key': 'two-key'})['id']
        self.other_bot = self.platform.create(self.principal, {'name': '其他助手', 'chat_id': 'other', 'robot_key': 'other-key'})
        self.admin = self.platform.workspace_context()
        self.owner = self.platform.context_for_channel(self.second, 'owner', False, 'callback')
        # The public endpoint deliberately starts from the first group. The form
        # must recover its actual origin from trusted persisted data instead.
        self.public = self.platform.context_for_channel(self.first, 'public', False, 'public')
        Service(self.admin, self.clock).update_config({'public_base_url': 'http://127.0.0.1:9874'})
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def form(self, owner=None):
        reference = create_form(owner or self.owner, self.clock).split('#', 1)[1]
        return reference, reference.split('.', 1)[1]

    def count_applications(self):
        with connect(self.admin.db_path) as db:
            return db.execute('SELECT COUNT(*) FROM membership_applications').fetchone()[0]

    def legacy(self, token, code='abcdef1234', status='submitted'):
        with connect(self.owner.transport_db_path, True) as db:
            db.execute('UPDATE registration_forms SET status=?,channel_id=?,payload=?,code=? WHERE token_hash=?',
                (status, '', dumps({'gender': 'male', 'name': '旧资料', 'phone': '13800000001', 'level': 2}), code, digest(token)))
        return code

    def test_link_get_does_not_apply_and_post_binds_identity_ignoring_claims(self):
        reference, token = self.form()
        self.assertNotIn('owner', reference)
        self.assertEqual(self.client.get('/public/registration/' + reference).json(), {'status': 'draft'})
        self.assertIsNone(Members(self.admin).get('owner'))
        self.assertEqual(self.count_applications(), 0)
        response = self.client.post('/public/registration/' + reference, json={'gender': 'male',
            'name': '姓名甲', 'level': 9, 'phone': '13800000001',
            'user_id': 'forged', 'chat_id': 'other', 'is_admin': True, 'status': 'approved', 'roles': ['club_admin']})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual((result['status'], result['member_status'], result['registration_result']),
                         ('completed', 'pending', 'submitted'))
        self.assertEqual(result['level'], 9)
        self.assertNotIn('command', result)
        for hidden in ('owner', '13800000001', token):
            self.assertNotIn(hidden, json.dumps(result))
        member = Members(self.admin).get('owner')
        self.assertEqual((member['name'], member['status'], member['requested_level'], member['roles']),
                         ('姓名甲', 'pending', 9, []))
        self.assertIsNone(Members(self.admin).get('forged'))
        self.assertIsNone(Members(self.admin).get('public'))
        self.assertEqual(self.count_applications(), 1)
        self.assertEqual(self.client.get('/public/registration/' + reference).json(), result)
        self.assertEqual(submit_form(self.public, token, {'gender': 'male', 'name': '不能覆盖', 'level': 1}, self.clock), result)
        self.assertEqual(Members(self.admin).get('owner')['name'], '姓名甲')
        self.assertEqual(self.count_applications(), 1)
        with connect(self.owner.transport_db_path) as db:
            row = db.execute('SELECT token_hash,status,payload,code,channel_id FROM registration_forms').fetchone()
        self.assertEqual(tuple(row), (digest(token), 'confirmed', None, None, self.second))

    def test_parallel_requests_make_one_application(self):
        _, token = self.form()
        def submit(_):
            return submit_form(self.public, token, {'gender': 'male', 'name': '并发姓名', 'level': 6}, self.clock)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(submit, range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.count_applications(), 1)

    def test_validation_failure_rolls_back_and_link_can_retry(self):
        _, token = self.form()
        for invalid in (0, 10, True, '9'):
            with self.subTest(level=invalid), self.assertRaises(ValueError):
                submit_form(self.public, token, {'gender': 'male', 'name': '九级申请', 'level': invalid}, self.clock)
            self.assertEqual(form_status(self.public, token, self.clock), {'status': 'draft'})
            self.assertEqual(self.count_applications(), 0)
        result = submit_form(self.public, token, {'gender': 'male', 'name': '九级申请', 'level': 9}, self.clock)
        self.assertEqual(result['level'], 9)
        self.assertEqual(self.count_applications(), 1)

    def test_crash_after_business_commit_recovers_without_duplicate_application(self):
        _, token = self.form()
        with patch('app.registration._mark_confirmed', side_effect=RuntimeError('simulated transport commit failure')):
            with self.assertRaises(RuntimeError):
                submit_form(self.public, token, {'gender': 'male', 'name': '已写入', 'level': 8}, self.clock)
        self.assertEqual(self.count_applications(), 1)
        with connect(self.owner.transport_db_path) as db:
            self.assertEqual(db.execute('SELECT status FROM registration_forms').fetchone()[0], 'draft')
        receipt = form_status(self.public, token, self.clock)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(submit_form(self.public, token, {'gender': 'male', 'name': '不能再申请'}, self.clock), receipt)
        self.assertEqual(Members(self.admin).get('owner')['name'], '已写入')
        self.assertEqual(self.count_applications(), 1)

    def test_tokens_cannot_cross_bot_or_be_guessed_and_expire_or_supersede(self):
        reference, token = self.form()
        wrong = self.other_bot['id'] + '.' + token
        self.assertEqual(self.client.post('/public/registration/' + wrong, json={'gender': 'male', 'name': '伪造'}).status_code, 400)
        tampered = token[:-1] + ('A' if token[-1] != 'A' else 'B')
        with self.assertRaises(ValueError):
            submit_form(self.public, tampered, {'gender': 'male', 'name': '伪造'}, self.clock)
        _, next_token = self.form()
        with self.assertRaises(ValueError):
            submit_form(self.public, token, {'gender': 'male', 'name': '旧链接'}, self.clock)
        self.time += timedelta(minutes=20)
        with self.assertRaises(ValueError):
            submit_form(self.public, next_token, {'gender': 'male', 'name': '超时'}, self.clock)
        self.assertEqual(self.count_applications(), 0)

    def test_disabled_original_channel_cannot_use_another_active_channel(self):
        reference, token = self.form()
        self.platform.save_channel(self.principal, self.bot['id'], {'active': False}, self.second)
        response = self.client.post('/public/registration/' + reference, json={'gender': 'male', 'name': '停用不能提交'})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.count_applications(), 0)
        self.platform.save_channel(self.principal, self.bot['id'], {'active': True, 'receive_commands': False}, self.second)
        with self.assertRaises(PermissionError):
            submit_form(self.public, token, {'gender': 'male', 'name': '关闭业务'}, self.clock)
        self.assertEqual(self.count_applications(), 0)

    def test_channel_identity_change_and_bot_disable_reject(self):
        _, token = self.form()
        with self.assertRaises(ValueError):
            self.platform.save_channel(self.principal, self.bot['id'], {'chat_id': 'replacement'}, self.second)
        # Simulate an out-of-band legacy migration changing the mapping.
        with connect(self.platform.path, True) as db:
            db.execute('UPDATE bot_channels SET chat_id=? WHERE id=?', ('replacement', self.second))
        with self.assertRaises(PermissionError):
            submit_form(self.public, token, {'gender': 'male', 'name': '不能转群'}, self.clock)
        self.platform.update(self.principal, self.bot['id'], {'active': False})
        with self.assertRaises(PermissionError):
            form_status(self.public, token, self.clock)
        self.assertEqual(self.count_applications(), 0)

    def test_suspended_rejects_and_approved_existing_skips_without_rename(self):
        _, token = self.form()
        Members(self.owner, self.clock).apply('既有姓名', 3, gender='male')
        Members(self.admin, self.clock).review('owner', 'approved')
        before = self.count_applications()
        result = submit_form(self.public, token, {}, self.clock)
        self.assertEqual(result['registration_result'], 'already_registered')
        self.assertEqual(result['name'], '既有姓名')
        self.assertEqual(self.count_applications(), before)
        Members(self.admin, self.clock).review('owner', 'suspended')
        for operation in (lambda: form_status(self.public, token, self.clock),
                          lambda: submit_form(self.public, token, {'gender': 'male', 'name': '换名'}, self.clock)):
            with self.assertRaises(PermissionError):
                operation()
        self.assertEqual(Members(self.admin).get('owner')['status'], 'suspended')

    def test_phone_conflict_can_retry_without_consuming_form(self):
        other = replace(self.owner, actor='phone-owner')
        Members(other, self.clock).apply('号码本人', phone='13800000001', gender='male')
        Members(self.admin, self.clock).review('phone-owner', 'approved')
        _, token = self.form()
        with self.assertRaises(PermissionError):
            submit_form(self.public, token, {'gender': 'male', 'name': '另一姓名', 'phone': '13800000001'}, self.clock)
        self.assertEqual(form_status(self.public, token, self.clock), {'status': 'draft'})
        result = submit_form(self.public, token, {'gender': 'male', 'name': '另一姓名', 'phone': '13800000002'}, self.clock)
        self.assertEqual(result['registration_result'], 'submitted')
        self.assertEqual(self.count_applications(), 2)

    def test_legacy_submitted_link_can_submit_directly_and_code_is_cleared(self):
        _, token = self.form()
        code = self.legacy(token)
        self.assertEqual(form_status(self.public, token, self.clock), {'status': 'submitted'})
        result = submit_form(self.public, token, {'gender': 'male', 'name': '网页新资料', 'level': 7}, self.clock)
        self.assertEqual(result['registration_result'], 'submitted')
        self.assertEqual(Members(self.admin).get('owner')['name'], '网页新资料')
        with self.assertRaises(ValueError):
            confirm_form(self.owner, code, self.clock)
        self.assertEqual(self.count_applications(), 1)

    def test_legacy_code_requires_original_actor_bot_and_chat_and_remains_one_use(self):
        _, token = self.form()
        code = self.legacy(token)
        wrong_bot = self.platform.context_for_channel(self.other_bot['channels'][0]['id'], 'owner', False, 'callback')
        for context in (replace(self.owner, actor='someone'), replace(self.owner, chat_id='one'), wrong_bot):
            with self.assertRaises(ValueError):
                confirm_form(context, code, self.clock)
        member = confirm_form(self.owner, code, self.clock)
        self.assertEqual((member['name'], member['status']), ('旧资料', 'pending'))
        with self.assertRaises(ValueError):
            confirm_form(self.owner, code, self.clock)
        self.assertEqual(self.count_applications(), 1)

    def test_legacy_confirmed_link_does_not_reopen_application(self):
        _, token = self.form()
        Members(self.owner, self.clock).apply('历史申请', 2, gender='male')
        self.legacy(token, status='confirmed')
        old_status = form_status(self.public, token, self.clock)
        self.assertEqual((old_status['registration_result'], old_status['member_status']), ('submitted', 'pending'))
        self.assertIn('等待管理员审核', old_status['message'])
        self.assertNotIn('您已注册', old_status['message'])
        result = submit_form(self.public, token, {'gender': 'male', 'name': '不应改名'}, self.clock)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(Members(self.admin).get('owner')['name'], '历史申请')
        self.assertEqual(self.count_applications(), 1)


if __name__ == '__main__':
    unittest.main()
