import asyncio
from dataclasses import replace
from datetime import timedelta
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.db import connect
from app.main import create_app
from app.members import Members
from app.people import People
from app.profile_changes import ProfileChanges
from app.registration import create_form, form_status, submit_form
from tests.test_core import CoreFixture, TOKEN


class ProfileRequestTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.requests = ProfileChanges(self.member, self.clock)
        self.admin_requests = ProfileChanges(self.ctx, self.clock)
        self.group = Members(self.ctx).save_group('提高组')
        Members(self.ctx).review_level('1001', True, 3)
        self.s.update_config({'public_base_url': 'http://127.0.0.1:9874'})

    def run_text(self, text, ctx=None):
        return asyncio.run(self.commands(ctx).run(text))

    def test_combined_request_preserves_current_until_approved_and_notifies_once(self):
        row = self.requests.submit({'group_ids': [self.group['id']], 'level': 6, 'name': '李明'})
        before = Members(self.member).get()
        self.assertEqual((before['name'], before['level'], before['group_ids']), ('小李', 3, []))
        self.assertEqual(before['level_status'], 'approved')
        self.admin_requests.review(row['id'], True)
        after = Members(self.member).get()
        self.assertEqual((after['name'], after['level'], after['group_ids']), ('李明', 6, [self.group['id']]))
        self.assertEqual(after['roles'], [])
        self.assertTrue(self.admin_requests.review(row['id'], True)['already_reviewed'])
        with connect(self.ctx.db_path) as db:
            events = db.execute("SELECT * FROM identity_notification_events WHERE kind='profile_request'").fetchall()
        self.assertEqual(len(events), 1)
        from app.identity_notifications import event_text
        self.assertIn('资料调整已通过', event_text(events[0]))

    def test_reject_and_cancel_leave_original_labels(self):
        row = self.requests.submit({'level': 8})
        self.admin_requests.review(row['id'], False)
        self.assertEqual(Members(self.member).get()['level'], 3)
        row = self.requests.submit({'level': 9})
        self.assertIn('撤回', self.requests.cancel())
        with self.assertRaises(ValueError):
            self.admin_requests.review(row['id'], True)
        self.assertEqual(Members(self.member).get()['level'], 3)

    def test_replace_preserves_other_requested_fields_and_old_request_cannot_be_approved(self):
        first = self.requests.submit({'level': 5})
        second = self.requests.submit({'group_ids': [self.group['id']]})
        self.assertEqual(second['changes'], {'level': 5, 'group_ids': [self.group['id']]})
        with self.assertRaises(ValueError):
            self.admin_requests.review(first['id'], True)
        self.assertEqual(self.requests.submit({'level': 5})['id'], second['id'])
        self.assertEqual(len(self.requests.list(status='pending')), 1)

    def test_stale_approval_rejects_changed_field_but_not_unrelated_field(self):
        row = self.requests.submit({'level': 5})
        Members(self.ctx).review_level('1001', True, 4)
        with self.assertRaisesRegex(ValueError, '已被修改'):
            self.admin_requests.review(row['id'], True)
        self.admin_requests.review(row['id'], False)
        row = self.requests.submit({'level': 6})
        Members(self.ctx).set_groups('1001', [self.group['id']])
        self.admin_requests.review(row['id'], True)
        self.assertEqual(Members(self.member).get()['level'], 6)

    def test_privileged_fields_and_invalid_labels_cannot_be_requested(self):
        for data in ({'roles': ['club_admin']}, {'status': 'approved'}, {'user_id': '1002'},
                     {'level': 0}, {'level': 10}, {'level': True}, {'gender': 'unknown'},
                     {'group_ids': [999999]}, {'group_ids': ['1']}, {'name': '1001'}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.requests.submit(data)
        self.assertEqual(self.requests.list(), [])

    def test_ordinary_manager_revoked_manager_and_self_approval_blocked(self):
        row = self.requests.submit({'level': 5})
        with self.assertRaises(PermissionError):
            self.requests.review(row['id'], True)
        People(self.ctx, self.clock).set_roles('1001', ['member_manager'])
        with self.assertRaisesRegex(PermissionError, '本人'):
            self.requests.review(row['id'], True)
        other = replace(self.member, actor='1002')
        People(self.ctx, self.clock).set_roles('1002', ['member_manager'])
        other = self.platform.context_for_channel(self.member.channel_id, '1002', False, 'callback')
        stale = ProfileChanges(other, self.clock)
        People(self.ctx, self.clock).set_roles('1002', [])
        with self.assertRaises(PermissionError):
            stale.review(row['id'], True)

    def test_own_history_only_and_unapproved_cannot_apply(self):
        self.requests.submit({'level': 5})
        other = ProfileChanges(replace(self.member, actor='1002'), self.clock)
        self.assertEqual(other.list(), [])
        with self.assertRaises(PermissionError):
            other.list(all_users=True)
        applicant = replace(self.member, actor='pending-user')
        Members(applicant).apply('待审用户', gender='female')
        with self.assertRaises(PermissionError):
            ProfileChanges(applicant).submit({'level': 5})

    def test_phone_is_masked_and_collision_rechecked_at_approval(self):
        row = self.requests.submit({'phone': '13800000001'})
        self.assertNotIn('13800000001', json.dumps(self.requests.list()))
        self.assertNotIn('13800000001', self.requests.describe(row))
        Members(self.ctx).update_details('1002', phone='13800000001')
        with self.assertRaises(PermissionError):
            self.admin_requests.review(row['id'], True)
        self.assertFalse(Members(self.member).get()['phone'])

    def test_commands_help_approval_and_registration_capability(self):
        self.assertIn('提高组', self.run_text('可选分组'))
        self.assertIn('待审核', self.run_text('申请分组 提高组'))
        self.assertIn('5级', self.run_text('申请等级 五级'))
        self.assertIn('资料申请', self.run_text('我的资料申请'))
        self.assertIn('修改资料', self.run_text('帮助 注册'))
        self.platform.update(self.admin, self.member.id, {'capabilities': ['registration']})
        self.assertIn('撤回', self.run_text('取消资料申请'))
        with self.assertRaises(PermissionError):
            self.run_text('资料待审核')
        self.platform.update(self.admin, self.member.id, {'capabilities': ['enrollment']})
        with self.assertRaises(PermissionError):
            self.run_text('申请等级 5级')

    def test_personal_form_purpose_and_receipt_prevent_identity_forgery_or_replay(self):
        link = create_form(self.member, self.clock, purpose='profile')
        token = link.rsplit('.', 1)[1]
        status = form_status(self.member, token, self.clock)
        self.assertEqual(status['purpose'], 'profile')
        self.assertEqual(status['profile']['level'], 3)
        with self.assertRaises(ValueError):
            submit_form(self.member, token, {'level': 8, 'user_id': '1002'}, self.clock)
        data = {'level': 5, 'gender': 'male', 'group_ids': [self.group['id']]}
        first = submit_form(self.member, token, data, self.clock)
        self.assertEqual(first, submit_form(self.member, token, {'level': 9}, self.clock))
        self.assertEqual(first, form_status(self.member, token, self.clock))
        self.assertEqual(len(self.requests.list()), 1)
        self.assertEqual(Members(self.member).get()['level'], 3)
        with self.assertRaises(ValueError):
            form_status(self.member, token, lambda: self.now + timedelta(minutes=21))

    def test_form_channel_revocation_and_cross_bot_rejected(self):
        link = create_form(self.member, self.clock, purpose='profile')
        token = link.rsplit('.', 1)[1]
        other = self.platform.context(self.admin, self.bots[1]['id'])
        with self.assertRaises(ValueError):
            submit_form(other, token, {'level': 5}, self.clock)
        self.platform.update(self.admin, self.member.id, {'capabilities': ['enrollment']})
        with self.assertRaises(PermissionError):
            submit_form(self.member, token, {'level': 5}, self.clock)

    def test_atomic_review_rolls_back_on_notification_failure(self):
        row = self.requests.submit({'level': 5, 'name': '新姓名'})
        with patch('app.identity_notifications.record_identity_change', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                self.admin_requests.review(row['id'], True)
        current = Members(self.member).get()
        self.assertEqual((current['name'], current['level']), ('小李', 3))
        self.assertEqual(self.requests.list()[0]['status'], 'pending')

    def test_http_review_requires_authentication_and_ai_opens_own_form(self):
        row = self.requests.submit({'level': 5})
        app = create_app(self.platform.root, start_scheduler=False, clock=self.clock)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/profile-requests').status_code, 401)
            headers = {'Authorization': 'Bearer ' + TOKEN}
            self.assertEqual(client.get('/api/profile-requests?status=pending', headers=headers).status_code, 200)
            response = client.post(f"/api/profile-requests/{row['id']}/review", json={'approved': True}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('/register#', self.run_text('AI 我想修改个人资料'))
