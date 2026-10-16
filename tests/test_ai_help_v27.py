"""User help explains executable plans separately from form navigation."""
import asyncio
from unittest.mock import patch

from app.db import connect
from app.help import command_help
from tests.test_core import CoreFixture


class AIHelpV27Tests(CoreFixture):
    def test_ai_help_aliases_are_static_and_never_start_a_plan(self):
        with patch('app.ai.interpret') as model:
            replies = [asyncio.run(self.commands().run(command))
                       for command in ('AI帮助', '帮助 AI', 'AI')]
        model.assert_not_called()
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(replies[1], replies[2])
        help_text = replies[0]
        for phrase in ('8步', '整单预览', 'AI进度', '取消计划', '纯查询直接返回', '需要在页面提交'):
            self.assertIn(phrase, help_text)
        self.assertNotIn('一次说清一件事', help_text)
        with connect(self.member.transport_db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ai_pending').fetchone()[0], 0)
        self.assertEqual(self.s.registrations()['registrations'], [])

    def test_core_help_keeps_onboarding_and_offers_one_sentence_business(self):
        self.assertIn('发送 **注册**', command_help({'status': 'unregistered'}))
        approved = command_help({'status': 'approved'})
        self.assertIn('AI 明天第一场帮我占个位，再看那场名单', approved)
        self.assertIn('修改先预览再确认', approved)
        self.assertIn('同一群可接入多个机器人', command_help({'status': 'approved'}, category='管理', permissions=['bot.manage']))
