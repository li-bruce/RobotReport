"""Nine-level parsing must reach the same approval-controlled member service."""
import asyncio
from dataclasses import replace
import re
from unittest.mock import patch

from app import ai
from app.members import Members
from tests.test_ai import FakeRunnable
from tests.test_core import CoreFixture


class CommandLevelsV24Tests(CoreFixture):
    def test_static_self_rating_all_nine_levels_and_chinese(self):
        for value in range(1, 10):
            reply = asyncio.run(self.commands().run(f'自评{value}级'))
            self.assertIn(f'自评{value}级', reply)
            self.assertEqual(Members(self.member, self.clock).get()['requested_level'], value)
        reply = asyncio.run(self.commands().run('自评 九 级'))
        self.assertIn('自评9级', reply)
        self.assertEqual(Members(self.member, self.clock).get()['level_status'], 'pending')
        for value in (0, 10):
            with self.subTest(value=value), self.assertRaises(ValueError):
                asyncio.run(self.commands().run(f'自评{value}级'))

    def test_inline_registration_level_is_validated_instead_of_becoming_name(self):
        actor = replace(self.member, actor='level-newcomer')
        for suffix in ('0级', '10级'):
            with self.assertRaises(ValueError):
                asyncio.run(self.commands(actor).run('注册 测试球友 男 ' + suffix))
        reply = asyncio.run(self.commands(actor).run('注册 测试球友 男 九级'))
        self.assertIn('已提交', reply)
        member = Members(actor, self.clock).get()
        self.assertEqual((member['name'], member['requested_level'], member['status']), ('测试球友', 9, 'pending'))

    def test_ai_nine_requires_confirmation_and_does_not_approve_level(self):
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'local-test-fake-key'}})
        before = Members(self.member, self.clock).get()
        with patch.object(ai, '_make_runnable', return_value=FakeRunnable('SelfLevel', {'level': 9})):
            reply = asyncio.run(self.commands().run('AI 我想申请自评九级'))
        token = self.pending_match(reply)
        self.assertIsNotNone(token, reply)
        self.assertEqual(Members(self.member, self.clock).get()['requested_level'], before['requested_level'])
        result = asyncio.run(self.commands().run('AI确认 ' + token[1]))
        self.assertIn('自评9级', result)
        member = Members(self.member, self.clock).get()
        self.assertEqual((member['requested_level'], member['level_status']), (9, 'pending'))
        self.assertEqual(member['level'], before['level'])
