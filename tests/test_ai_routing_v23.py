"""Whole-workspace AI routing uses existing identity and operation boundaries."""
import asyncio
from dataclasses import replace
import re
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import ai
from app.ai_catalog import features, model_features
from app.ai_routing import feature_descriptor
from app.db import connect
from app.main import create_app
from app.members import Members
from app.people import People
from tests.test_ai import FakeRunnable
from tests.test_core import CoreFixture, TOKEN


class AIRoutingV23Tests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'routing-local-fake-key'},
                              'public_base_url': 'http://127.0.0.1:9874'})

    def send(self, text, ctx=None):
        return asyncio.run(self.commands(ctx).run(text))

    def pending_count(self):
        with connect(self.ctx.transport_db_path) as db:
            return db.execute("SELECT COUNT(*) FROM ai_pending WHERE status='pending'").fetchone()[0]

    def unchanged_business(self):
        tables = ('member_records', 'member_groups', 'member_group_links', 'user_roles',
                  'config', 'venues', 'slot_rules', 'registrations', 'tour_events',
                  'tour_participants', 'tour_matches', 'tour_staff', 'tour_organization')
        with connect(self.ctx.db_path) as db:
            present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {table: sorted([tuple(row) for row in db.execute('SELECT * FROM ' + table)], key=repr)
                    if table in present else [] for table in tables}

    def client(self):
        return TestClient(create_app(self.temp.name, TOKEN, False, False, self.clock))

    def test_unregistered_ai_registration_creates_only_original_identity_form(self):
        newcomer = replace(self.member, actor='new-member')
        with patch.object(ai, '_make_runnable') as make:
            reply = self.send('AI 我要注册', newcomer)
            make.assert_not_called()
        self.assertIn('/register#', reply)
        self.assertIn('确认本人资料', reply)
        self.assertEqual(Members(newcomer, self.clock).get()['status'], 'unregistered')
        with connect(newcomer.transport_db_path) as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM registration_forms')]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['chat_id'], rows[0]['user_id']), (newcomer.chat_id, newcomer.actor))
        self.assertEqual(rows[0]['status'], 'draft')
        self.assertEqual(self.pending_count(), 0)

    def test_registered_ai_registration_skips_without_reopening_application(self):
        before = self.unchanged_business()
        with patch.object(ai, '_make_runnable') as make:
            reply = self.send('AI 我要注册')
            make.assert_not_called()
        self.assertIn('已注册', reply)
        self.assertEqual(self.unchanged_business(), before)

    def test_public_ai_profile_routes_work_for_pending_members_only_own_record(self):
        Members(self.ctx, self.clock).review('1001', 'pending')
        with patch.object(ai, '_make_runnable') as make:
            for text in ('AI 查看我的会员状态', 'AI 我是什么身份'):
                with self.subTest(text=text):
                    reply = self.send(text)
                    self.assertRegex(reply, '待审批|待审|小李|1001')
                    self.assertNotIn('球友1002', reply)
            make.assert_not_called()

    def test_public_self_service_does_not_allow_unapproved_business(self):
        Members(self.ctx, self.clock).review('1001', 'pending')
        with patch.object(ai, '_make_runnable') as make:
            with self.assertRaises(PermissionError):
                self.send('AI 帮我报名')
            make.assert_not_called()
        self.assertEqual(self.s.registrations()['registrations'], [])

    def test_suspended_ai_registration_cannot_restore_membership(self):
        Members(self.ctx, self.clock).review('1001', 'suspended')
        before = self.unchanged_business()
        with patch.object(ai, '_make_runnable') as make:
            reply = self.send('AI 我要注册')
            make.assert_not_called()
        self.assertRegex(reply, '停用|联系管理员')
        self.assertEqual(self.unchanged_business(), before)

    def test_ai_navigation_never_sends_phone_or_credentials_to_provider(self):
        samples = ('AI 我要注册，手机号13800138000',
                   'AI 使用这个手机号 +86 138 0013 8000 关联已有资料',
                   'AI 修改API key为sk-routingonlynotreal12345678',
                   'AI 登录密码是private-test-pass-123456',
                   'AI webhook地址https://xz.wps.cn/api/v1/webhook/send?key=private-example',
                   'AI Bearer very-private-synthetic-token')
        with patch.object(ai, '_make_runnable') as make:
            for text in samples:
                with self.subTest(text=text):
                    try:
                        reply = self.send(text)
                    except (PermissionError, ValueError):
                        continue
                    for secret in ('13800138000', '138 0013 8000', 'sk-routingonlynotreal12345678',
                                   'private-test-pass-123456', 'private-example', 'very-private-synthetic-token'):
                        self.assertNotIn(secret, reply)
            make.assert_not_called()
        self.assertEqual(self.pending_count(), 0)

    def test_management_ai_endpoints_require_platform_authentication(self):
        grant = self.platform.grant(self.admin, [self.ctx.id])
        with self.client() as client:
            for method, path, data in (('GET', '/api/ai/capabilities', None),
                                       ('POST', '/api/ai/navigate', {'content': '批量导入会员', 'is_admin': True})):
                with self.subTest(path=path):
                    response = client.request(method, path, json=data)
                    self.assertEqual(response.status_code, 401)
                    response = client.request(method, path, json=data,
                                              headers={'Authorization': 'Bearer ' + grant['token']})
                    self.assertEqual(response.status_code, 403)

    def test_backend_navigation_does_not_execute_commands_or_management_writes(self):
        before = self.unchanged_business()
        with self.client() as client, patch.object(ai, '_make_runnable') as make:
            for content in ('批量导入会员', '审核会员注册申请', '设置管理员角色', '设置每场正式人数',
                            '配置定时提醒', '发布消息和图片', '上传赛事照片', '新增机器人和群'):
                with self.subTest(content=content):
                    response = client.post('/api/ai/navigate', json={'content': content},
                                           headers={'Authorization': 'Bearer ' + TOKEN})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertNotRegex(response.text, 'AI确认 [a-f0-9]{12}')
            make.assert_not_called()
        self.assertEqual(self.unchanged_business(), before)
        self.assertEqual(self.pending_count(), 0)

    def test_catalog_covers_each_business_area_and_existing_admin_destinations(self):
        catalog = features()
        by_id = {item['feature_id']: item for item in catalog}
        self.assertEqual(len(by_id), len(catalog))
        required = ('membership.register', 'membership.profile', 'membership.import', 'membership.review',
                    'membership.groups', 'membership.level_review', 'membership.roles', 'membership.guests',
                    'registration.enroll', 'registration.cancel', 'registration.calendar',
                    'registration.constraints', 'registration.export', 'venue.manage', 'venue.slots',
                    'venue.special_dates', 'venue.reminders', 'large.staff', 'large.media',
                    'large.schedule', 'large.notify', 'messages.publish', 'messages.media',
                    'messages.history', 'bots.manage', 'bots.channels', 'bots.credentials',
                    'stats.personal', 'stats.club', 'stats.dashboard', 'system.ai', 'system.password',
                    'system.credentials', 'system.audit', 'help.commands', 'help.simulator')
        self.assertFalse(set(required) - set(by_id))
        routes = {'dashboard', 'calendar', 'members', 'applications', 'profile-requests', 'member-imports', 'guests',
                  'roles', 'daily', 'tournaments', 'sessions', 'messages', 'stats', 'venues',
                  'settings', 'ai', 'reminders', 'simulator', 'guide', 'audit', 'bots', 'casual', 'occupancies', 'holidays'}
        safe_commands = {'修改资料', '注册', '注册资料', '身份', '名单', '我的统计', '统计', '帮助', 'AI帮助', 'AI状态', '性别', '我的对战历史', '我的对战统计', '待审核', '我的会籍'}
        for item in catalog:
            with self.subTest(feature=item['feature_id']):
                self.assertTrue(item['name'] and item['description'] and item['steps'])
                self.assertIn(item['mode'], ('command', 'guide'))
                if item['admin_route']:
                    self.assertIn(item['admin_route'], {'/admin#' + route for route in routes})
                if item['command']:
                    self.assertIn(item['command'], safe_commands)
                    self.assertEqual(item['mode'], 'command')

    def test_catalog_given_to_model_contains_only_current_identity_capabilities(self):
        for approved, admin in ((False, False), (True, False), (True, True)):
            rows = model_features(admin, approved)
            with self.subTest(approved=approved, admin=admin):
                if not approved:
                    self.assertTrue(all(row['permission'] == 'public' for row in rows))
                elif not admin:
                    self.assertTrue(all(row['permission'] != 'admin' for row in rows))
                else:
                    self.assertEqual(len(rows), len(features()))

    def test_every_management_destination_is_only_a_guide_and_has_no_write_side_effect(self):
        before = self.unchanged_business()
        for item in features():
            if item['permission'] != 'admin' or item['mode'] != 'guide':
                continue
            with self.subTest(feature=item['feature_id']):
                descriptor = feature_descriptor(self.ctx, item['feature_id'], self.clock)
                self.assertTrue(descriptor['allowed'])
                self.assertIsNone(descriptor['command'])
                self.assertIn('尚未执行', descriptor['message'])
                if descriptor['url']:
                    self.assertTrue(descriptor['url'].startswith('http://127.0.0.1:9874/admin#'))
        self.assertEqual(self.unchanged_business(), before)

    def test_club_role_does_not_create_platform_login_and_refreshes_before_navigation(self):
        People(self.ctx, self.clock).set_roles('1001', ['club_admin'])
        router = self.commands(replace(self.member, is_admin=True))
        with patch.object(ai, '_make_runnable') as make:
            reply = asyncio.run(router.run('AI 批量导入会员'))
            self.assertIn('/admin#member-imports', reply)
            self.assertIn('登录', reply)
            People(self.ctx, self.clock).set_roles('1001', [])
            denied = asyncio.run(router.run('AI 批量导入会员'))
            self.assertIn('管理员', denied)
            self.assertNotIn('/admin#member-imports', denied)
            make.assert_not_called()
        with self.client() as client:
            self.assertEqual(client.get('/api/ai/capabilities').status_code, 401)

    def test_capabilities_api_never_exposes_secrets_or_private_identity_values(self):
        with self.client() as client:
            response = client.get('/api/ai/capabilities', headers={'Authorization': 'Bearer ' + TOKEN})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['features']), len(features()))
        for secret in ('routing-local-fake-key', 'callback-secret-01234567890123456789', 'robot-test', TOKEN):
            self.assertNotIn(secret, response.text)

    def test_forged_model_feature_route_cannot_supply_commands_or_external_links(self):
        before = self.unchanged_business()
        for arguments in ({'feature_id': 'membership.roles', 'command': '开启AI'},
                          {'feature_id': 'membership.roles', 'url': 'https://attacker.invalid'},
                          {'feature_id': 'invented.admin.reset'}):
            fake = FakeRunnable('FeatureRoute', arguments)
            with self.subTest(arguments=arguments), patch.object(ai, '_make_runnable', return_value=fake):
                reply = self.send('AI 帮我找到相应的业务入口')
                self.assertNotIn('attacker.invalid', reply)
                self.assertNotIn('已执行', reply)
                self.assertNotRegex(reply, 'AI确认 [a-f0-9]{12}')
        self.assertEqual(self.unchanged_business(), before)

    def test_model_feature_route_rechecks_role_revoked_while_waiting(self):
        People(self.ctx, self.clock).set_roles('1001', ['club_admin'])
        router = self.commands(replace(self.member, is_admin=True))
        fixture = self

        class RevokeRole(FakeRunnable):
            async def ainvoke(self, messages, config=None):
                People(fixture.ctx, fixture.clock).set_roles('1001', [])
                return await super().ainvoke(messages, config)

        fake = RevokeRole('FeatureRoute', {'feature_id': 'membership.import'})
        with patch.object(ai, '_make_runnable', return_value=fake) as make:
            reply = asyncio.run(router.run('AI 帮我找到相应的业务入口'))
            make.assert_called_once()
        self.assertIn('管理员', reply)
        self.assertNotIn('/admin#member-imports', reply)
        self.assertEqual(self.pending_count(), 0)

    def test_model_feature_route_cannot_expose_member_results_after_suspension(self):
        fixture = self

        class SuspendMember(FakeRunnable):
            async def ainvoke(self, messages, config=None):
                Members(fixture.ctx, fixture.clock).review('1001', 'suspended')
                return await super().ainvoke(messages, config)

        fake = SuspendMember('FeatureRoute', {'feature_id': 'registration.roster'})
        with patch.object(ai, '_make_runnable', return_value=fake):
            with self.assertRaises(PermissionError):
                self.send('AI 帮我找到相应的业务入口')
        self.assertEqual(self.pending_count(), 0)

    def test_backend_model_navigation_rejects_direct_write_tool(self):
        before = self.unchanged_business()
        fake = FakeRunnable('Register', {'guests': 0})
        with self.client() as client, patch.object(ai, '_make_runnable', return_value=fake) as make:
            response = client.post('/api/ai/navigate', json={'content': '帮我找到相应的业务入口'},
                                   headers={'Authorization': 'Bearer ' + TOKEN})
            make.assert_called_once()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['feature'])
        self.assertEqual(self.unchanged_business(), before)
        self.assertEqual(self.pending_count(), 0)

    def test_backend_model_navigation_does_not_execute_read_commands(self):
        fake = FakeRunnable('FeatureRoute', {'feature_id': 'stats.club'})
        with self.client() as client, patch.object(ai, '_make_runnable', return_value=fake) as make, \
                patch('app.service.Service.stats', side_effect=AssertionError('Navigator cannot execute stats')):
            response = client.post('/api/ai/navigate', json={'content': '帮我找到相应的业务入口'},
                                   headers={'Authorization': 'Bearer ' + TOKEN})
            make.assert_called_once()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['feature']['feature_id'], 'stats.club')
        self.assertEqual(response.json()['source'], 'model')

    def test_backend_model_navigation_uses_per_user_rate_budget(self):
        fake = FakeRunnable('FeatureRoute', {'feature_id': 'stats.club'})
        with self.client() as client, patch.object(ai, '_make_runnable', return_value=fake) as make:
            for _ in range(6):
                response = client.post('/api/ai/navigate', json={'content': '帮我找到相应的业务入口'},
                                       headers={'Authorization': 'Bearer ' + TOKEN})
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(make.call_count, 5)
        self.assertIsNone(response.json()['feature'])
        self.assertRegex(response.json()['message'], '频繁|稍后')

    def test_local_function_navigation_still_works_with_ai_model_disabled(self):
        self.s.update_config({'ai': {'enabled': False}})
        with self.client() as client, patch.object(ai, '_make_runnable') as make:
            local = client.post('/api/ai/navigate', json={'content': '批量导入会员'},
                                headers={'Authorization': 'Bearer ' + TOKEN})
            unknown = client.post('/api/ai/navigate', json={'content': '帮我找到相应的业务入口'},
                                  headers={'Authorization': 'Bearer ' + TOKEN})
            make.assert_not_called()
        self.assertEqual(local.status_code, 200, local.text)
        self.assertEqual(local.json()['feature']['feature_id'], 'membership.import')
        self.assertEqual(unknown.status_code, 200, unknown.text)
        self.assertIsNone(unknown.json()['feature'])
        self.assertRegex(unknown.json()['message'], '关闭|开启|停用')

    def test_self_level_requires_confirmation_and_can_be_cancelled(self):
        fake = FakeRunnable('SelfLevel', {'level': 2})
        with patch.object(ai, '_make_runnable', return_value=fake):
            reply = self.send('AI 我想申请自评2级')
        token = self.pending_match(reply)
        self.assertIsNotNone(token, reply)
        self.assertNotEqual(Members(self.member, self.clock).get()['level_status'], 'pending')
        self.send('AI取消 ' + token[1])
        with self.assertRaises((ValueError, PermissionError)):
            self.send('AI确认 ' + token[1])
        self.assertNotEqual(Members(self.member, self.clock).get()['level_status'], 'pending')

    def test_self_level_confirmation_rechecks_membership(self):
        with patch.object(ai, '_make_runnable', return_value=FakeRunnable('SelfLevel', {'level': 3})):
            reply = self.send('AI 我想申请自评3级')
        token = self.pending_match(reply)
        self.assertIsNotNone(token, reply)
        Members(self.ctx, self.clock).review('1001', 'suspended')
        with self.assertRaises(PermissionError):
            self.send('AI确认 ' + token[1])
        self.assertNotEqual(Members(self.member, self.clock).get()['level_status'], 'pending')
