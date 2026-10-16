import unittest

from app.ai_catalog import get_feature, local_route, model_features
from app.help import command_help


class CasualCapabilityTests(unittest.TestCase):
    def test_personal_history_statistics_and_gender_guidance_are_read_only(self):
        for name, command in [('casual.history', '我的对战历史'), ('casual.statistics', '我的对战统计'), ('membership.gender', '性别')]:
            feature = get_feature(name)
            self.assertEqual(feature['mode'], 'command')
            self.assertEqual(feature['command'], command)
            self.assertEqual(feature['permission'], 'member')
        for name in ('casual.record', 'casual.correct', 'casual.void'):
            feature = get_feature(name)
            self.assertEqual(feature['mode'], 'guide')
            self.assertIsNone(feature['command'])
            self.assertEqual(feature['permission'], 'member')

    def test_public_users_do_not_discover_personal_match_mutations_or_history(self):
        ids = {r['feature_id'] for r in model_features(False, False)}
        self.assertFalse(any(name.startswith('casual.') for name in ids))
        self.assertNotIn('membership.gender', ids)
        for name in ('casual.manage', 'bots.scope'):
            self.assertEqual(get_feature(name)['permission'], 'admin')

    def test_local_intents_preserve_permissions_and_route_without_silent_writes(self):
        cases = {
            '我想记录刚打完的一局': 'casual.record',
            '怎么登记一次混双对局': 'casual.record',
            '记录 单打 我 VS 张三 21:19': 'casual.record',
            '我想查询我的对战历史': 'casual.history',
            '查询我的对局记录': 'casual.history',
            '我之前和谁打过球': 'casual.history',
            '统计我的对战胜率': 'casual.statistics',
            '和哪位搭档组合胜率最高': 'casual.statistics',
            '修正刚才记录的对局': 'casual.correct',
            '如何撤销我的对局记录': 'casual.void',
            '管理员修正个人对局记录': 'casual.manage',
            '后台查看所有零散对局': 'casual.manage',
            '我要补齐性别': 'membership.gender',
            '配置机器人负责的场地和时段': 'bots.scope',
            '让这个机器人只负责第一场': 'bots.scope',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(local_route(text), expected)

    def test_help_explains_required_gender_doubles_and_casual_steps(self):
        public = command_help(None)
        self.assertIn('姓名、性别必填', public)
        member = command_help({'status': 'approved'})
        self.assertIn('我的对战历史', member)
        self.assertIn('我的对战统计', member)
        register = command_help(None, category='注册')
        self.assertIn('性别 男', register)
        self.assertIn('1–9级', register)
        for word in ('男双', '女双', '混双', '每队一男一女'):
            self.assertIn(word, command_help({'status': 'approved'}, category='比赛'))
        casual = command_help({'status': 'approved'}, category='记录')
        for word in ('当天提交立即生效', '修改记录', '作废记录', '当前机器人负责范围', '胜率'):
            self.assertIn(word, casual)
        self.assertIn('历史报名仍保留', command_help(None, True, '管理'))
