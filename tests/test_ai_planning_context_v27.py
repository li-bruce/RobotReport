"""A previous planning session must not shrink a new plan's format catalogue."""
import asyncio
import re
from unittest.mock import patch

from app import ai
from app.ai_planner import validate_plan
from app.db import connect
from app.planning import DailyPlanning
from tests.test_ai import FakeRunnable
from tests.test_core import CoreFixture


class PlanningContextTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'local-fake-ai-key'}})
        self.clock_patch = patch('app.competitions._now', self.clock)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        for user in ('1001', '1002', '1003', '1004'):
            self.s.enroll(user, '球友' + user, day='2026-10-12', number=1)
        for user in ('1001', '1002', '1003', '1004', '1'):
            self.s.enroll(user, '球友' + user, day='2026-10-13', number=2)
        self.planner = DailyPlanning(self.member, self.clock)
        self.planner.begin({'day': '2026-10-12', 'venue_id': 1, 'number': 1}, title='旧四人策划')

    def test_all_formats_are_available_to_context_but_old_planning_keeps_eligibility(self):
        context = self.commands().ai_context(1)
        self.assertEqual({item['id'] for item in context['formats']}, set(ai.FORMAT_NAMES))
        self.assertNotIn('five_rotation', context['planning']['available_format_ids'])
        with self.assertRaisesRegex(ai._ClarificationRequired, '当前人数|不支持'):
            validate_plan({'steps': [{'operation': 'DailyPlan', 'arguments': {'format': 'five_rotation'}}]},
                          ai._trusted_context(context), '把当前策划按五人转生成对战表')

    def test_real_command_context_can_preview_and_confirm_new_five_person_plan(self):
        old = self.planner.get(1)
        response = FakeRunnable('BusinessPlan', {'steps': [
            {'operation': 'StartDailyPlan', 'arguments': {'date': '2026-10-13', 'number': 2,
                                                        'discipline': 'doubles', 'doubles_category': 'open',
                                                        'assignment_strategy': 'random',
                                                        'title': '明日五人转'}},
            {'operation': 'DailyPlan', 'arguments': {'format': 'five_rotation'}},
        ]})
        # Mock only the external provider. Real command context, normalization,
        # isolated preview, confirmation and tournament generation all execute.
        with patch.object(ai, '_make_runnable', return_value=response):
            reply = asyncio.run(self.commands().run('AI 用明天第二场的球友组织比赛叫明日五人转，再采用五人转生成对战表'))
        token = self.pending_match(reply)
        self.assertIsNotNone(token, reply)
        self.assertEqual(self.planner.get(1)['token'], old['token'])
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tour_events').fetchone()[0], 0)
        result = asyncio.run(self.commands().run('AI确认 ' + token[1]))
        self.assertIn('已完成', result)
        self.assertIn('明日五人转', result)
        created = self.planner.get(1)
        self.assertEqual(created['status'], 'created')
        self.assertEqual(created['payload']['date'], '2026-10-13')
        event = self.commands().tournaments().get(created['event_id'])
        self.assertEqual(event['format'], 'five_rotation')
        self.assertEqual(len(event['matches']), 5)
        self.assertEqual(event['options']['session_snapshot']['start'], '20:30')
        self.assertEqual(event['options']['session_id'], self.s.sessions('2026-10-13')[1]['id'])
