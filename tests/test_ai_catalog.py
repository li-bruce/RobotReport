"""Capability routing is descriptive, scoped and cannot bypass write proposals."""
import unittest

from app.ai_catalog import features, get_feature, local_route, model_features


class CapabilityCatalogTests(unittest.TestCase):
    def test_catalog_covers_every_management_surface_and_has_actionable_examples(self):
        rows = features()
        self.assertGreaterEqual(len(rows), 50)
        self.assertEqual(len(rows), len({row['feature_id'] for row in rows}))
        expected_routes = {'dashboard', 'members', 'applications', 'member-imports',
                           'guests', 'roles', 'calendar', 'venues', 'sessions', 'occupancies', 'holidays',
                           'daily', 'stats', 'reminders', 'tournaments', 'messages',
                           'bots', 'settings', 'ai', 'audit', 'guide', 'simulator', 'casual'}
        self.assertEqual({r['admin_route'].split('#')[1] for r in rows if r['admin_route']},
                         expected_routes)
        for row in rows:
            with self.subTest(feature=row['feature_id']):
                self.assertRegex(row['feature_id'], r'^[a-z]+\.[a-z_]+$')
                self.assertIn(row['permission'], ('public', 'member', 'admin', 'event'))
                self.assertIn(row['mode'], ('guide', 'command'))
                self.assertTrue(row['name'] and row['category'] and row['description'])
                self.assertTrue(row['examples'] and all(type(v) is str and v for v in row['examples']))
                self.assertTrue(row['steps'] and all(type(v) is str and v for v in row['steps']))
                if row['admin_route']:
                    self.assertRegex(row['admin_route'], r'^/admin#[a-z-]+$')
                    self.assertNotIn('?', row['admin_route'])

    def test_business_area_coverage_is_not_limited_to_tournaments(self):
        ids = {row['feature_id'] for row in features()}
        for required in ('membership.register', 'membership.import', 'membership.review',
                         'membership.phone_link', 'membership.roles', 'membership.guests',
                         'membership.groups', 'membership.level_review', 'registration.enroll',
                         'registration.guests', 'registration.cancel', 'registration.calendar',
                         'registration.constraints', 'registration.export', 'venue.manage',
                         'venue.slots', 'venue.special_dates', 'venue.reminders', 'daily.start',
                         'daily.assignment', 'large.create', 'large.staff', 'large.schedule',
                         'large.media', 'large.notify', 'tournament.score', 'tournament.control',
                         'messages.publish', 'messages.history', 'messages.media', 'bots.manage',
                         'bots.channels', 'bots.credentials', 'stats.personal', 'stats.club',
                         'system.password', 'system.ai', 'system.audit', 'help.simulator'):
            self.assertIn(required, ids)

    def test_direct_commands_cannot_modify_business_or_roles(self):
        approved_commands = {'修改资料', '我的会籍', '注册', '注册资料', '身份', '名单', '我的统计',
                             '统计', '帮助', 'AI帮助', 'AI状态', '性别', '我的对战历史', '我的对战统计', '待审核'}
        for row in features():
            with self.subTest(feature=row['feature_id']):
                if row['mode'] == 'command':
                    self.assertIn(row['command'], approved_commands)
                else:
                    self.assertIsNone(row['command'])
        for feature_id in ('registration.enroll', 'registration.cancel', 'membership.self_rating',
                           'tournament.score', 'tournament.control', 'membership.roles',
                           'messages.publish', 'system.ai', 'large.draw'):
            self.assertEqual(get_feature(feature_id)['mode'], 'guide')

    def test_public_discovery_never_advertises_member_or_admin_operations(self):
        rows = model_features(is_admin=False, approved=False)
        self.assertEqual({row['permission'] for row in rows}, {'public'})
        self.assertIn('membership.register', {row['feature_id'] for row in rows})
        self.assertIn('membership.profile', {row['feature_id'] for row in rows})
        self.assertNotIn('registration.enroll', {row['feature_id'] for row in rows})

    def test_member_discovery_does_not_assume_platform_or_event_role(self):
        rows = model_features(is_admin=False, approved=True)
        self.assertNotIn('admin', {row['permission'] for row in rows})
        self.assertIn('tournament.score', {row['feature_id'] for row in rows})
        for row in rows:
            if row['permission'] == 'event':
                self.assertEqual(row['mode'], 'guide')
                self.assertTrue(any('权限' in step for step in row['steps']))

    def test_admin_sees_catalog_but_truthy_strings_are_not_permissions(self):
        self.assertEqual(model_features(is_admin=True, approved=True), features())
        self.assertEqual(model_features(is_admin='true', approved='true'),
                         model_features(is_admin=False, approved=False))

    def test_returned_catalog_is_independent_and_unknown_ids_do_not_resolve(self):
        rows = features()
        rows[0]['steps'].append('injected')
        rows[0]['command'] = '关闭AI'
        again = get_feature(rows[0]['feature_id'])
        self.assertNotIn('injected', again['steps'])
        self.assertNotEqual(again['command'], '关闭AI')
        selected = model_features(True, True)
        selected[0]['examples'].clear()
        self.assertTrue(features()[0]['examples'])
        for invalid in (None, [], {'feature_id': 'membership.register'}, '../secret',
                        'membership.register;delete', ''):
            self.assertIsNone(get_feature(invalid))

    def test_local_private_and_management_routes(self):
        cases = {
            '我想注册成为会员': 'membership.register',
            '我的注册审核到哪里了': 'membership.profile',
            '我的会员审核状态': 'membership.profile',
            '我的等级审核通过了吗': 'membership.profile',
            '我的身份': 'membership.identity',
            '我是谁': 'membership.identity',
            '查看我的用户ID': 'membership.identity',
            '管理员导入过我，怎么关联手机号': 'membership.phone_link',
            '手机号13912345678': 'membership.phone_link',
            '我要审批新会员注册': 'membership.review',
            '审核会员的自评级别': 'membership.level_review',
            '把某位会员设为管理员': 'membership.roles',
            '我要给会员设置组别': 'membership.groups',
            '查看代报名访客': 'membership.guests',
            '上传会员CSV': 'membership.import',
            '下载所有活动报名CSV': 'registration.export',
            '修改会员姓名': 'membership.directory',
            '正式人数设为12人': None,
            '新增第二块场地': 'venue.manage',
            '每周二增加中午12点的活动': None,
            '临时加一场明天中午的活动': 'venue.special_dates',
            '每天的报名提醒改为9点半': None,
            '筹办大型比赛': 'large.create',
            '给大型赛事随机分成四组': 'large.draw',
            '指定春季联赛的记分员': 'large.staff',
            '上传比赛照片存档': 'large.media',
            '修改联赛每一场比赛时间': 'large.schedule',
            '主动发布联赛报名通知': 'large.notify',
            '将已结束联赛归档': 'large.archive',
            '我要给群里发布一条公告': 'messages.publish',
            '查看通知有没有发送成功': 'messages.history',
            '上传通知附件': 'messages.media',
            '新增一个机器人': 'bots.manage',
            '让机器人再服务一个群': 'bots.channels',
            '修改机器人的webhook地址': 'bots.credentials',
            '机器人回调密钥在哪里设置': 'bots.credentials',
            '在哪里配置模型API密钥': 'system.ai',
            '修改管理后台登录密码': 'system.password',
            '后台的接口令牌在哪里管理': 'system.credentials',
            '打开操作日志': 'system.audit',
            '我想看报名趋势图': 'stats.dashboard',
            '打开完整使用说明书': 'help.manual',
            '我要在后台模拟机器人对话': 'help.simulator',
            'AI怎么用': 'help.ai',
            '现在AI开启了吗': 'help.ai_status',
            '机器人有哪些功能': 'help.commands',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(local_route(text), expected)

    def test_local_routing_leaves_parameterised_business_to_confirmed_intents(self):
        for text in ('报名', '帮我报名明天18点的活动', '取消我明天第二场的报名',
                     '报名，张三，李四', '我想查明天名单', '统计全部报名',
                     '我要自评2级', '自评三级', '组织比赛', '采用五人转',
                     '春季联赛，第十二场21：19', '暂停春季联赛', '关闭周二双打',
                     '恢复昨天的比赛', '周二双打晋级', 'AI 关闭AI', '开启AI',
                     '我的统计', '请帮我看看这个东西', '你好', '天气怎么样'):
            with self.subTest(text=text):
                self.assertIsNone(local_route(text))

    def test_local_routing_is_bounded_and_never_interprets_structures_as_text(self):
        for text in (None, [], {}, '', '   ', '注册' * 2001):
            self.assertIsNone(local_route(text))


if __name__ == '__main__':
    unittest.main()
