"""Compound requests must reach semantic planning without exposing private text."""
import unittest

from app.ai_catalog import local_route


class CompoundCatalogRoutingTests(unittest.TestCase):
    def test_comma_or_newline_cannot_drop_additional_business_clause(self):
        for text in (
            '查询我的续费记录，报名明天第一场',
            '查看我的对战历史，取消明天报名',
            '明天第一场报名，查询我的续费记录',
            '把每天报名提醒改为10点，查看我的统计',
            '查询我的会员资料\n报名明天第一场',
            '查询我的续费记录,报名明天第一场',
            '查询我的对战历史，查看我的会员资料',
        ):
            with self.subTest(text=text):
                self.assertIsNone(local_route(text))

    def test_incidental_context_and_named_guests_remain_compatible(self):
        self.assertEqual(local_route('管理员导入过我，怎么关联手机号'), 'membership.phone_link')
        self.assertEqual(local_route('查询我的续费记录'), 'membership.periods')
        self.assertIsNone(local_route('报名，张三，李四'))

    def test_sensitive_compound_requests_are_still_local_only(self):
        for text in ('手机号13812345678，帮我报名明天第一场',
                     '先报名明天，再用手机号 +86 138 1234 5678 关联资料',
                     'api_key=sk-exampleonlyabcdefghijkl，查询今天名单'):
            with self.subTest(text=text):
                self.assertIsNotNone(local_route(text))
