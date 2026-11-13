"""Robot suggestions are local to the authenticated message's physical group."""
import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient

from app.commands import Commands
from app.dialogue import MembershipPrompt
from app.main import create_app
from app.members import Members
from app.messages import Outbox
from app.service import Service
from tests.test_core import CoreFixture, TOKEN


class CurrentGroupRoutingTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.platform.update(self.admin, self.ctx.id, {'user_policy': {'enabled': True, 'match': 'all',
            'rules': [{'field': 'level', 'op': 'gte', 'value': 9}]}})

    def foreign(self, name='其他群助手', chat_id='other-group'):
        bot = self.platform.create(self.admin, {'name': name, 'chat_id': chat_id,
                                              'capabilities': ['enrollment']})
        ctx = self.platform.context_for_channel(bot['channels'][0]['id'], '1001', False, 'callback')
        Members(ctx, self.clock).touch('小李')
        return bot, ctx

    def send(self, text, ctx=None):
        return asyncio.run(Commands(ctx or self.member, self.clock).run(text))

    def test_known_other_groups_never_appear_in_same_group_suggestions(self):
        self.foreign()
        # Observing one robot must not make a second robot in that foreign group
        # visible to the current conversation, either.
        self.platform.create(self.admin, {'name': '其他群第二助手', 'chat_id': 'other-group',
                                        'capabilities': ['enrollment']})
        for text in ('报名', '手动报名', '第二场 报名+2', '报名，张三',
                     '2026-10-12 第2场 报名 接龙', '**报名**'):
            with self.subTest(command=text):
                with self.assertRaises(MembershipPrompt) as error:
                    self.send(text)
                reply = str(error.exception)
                self.assertIn('@二号', reply)
                self.assertNotIn('其他群', reply)
        self.assertEqual(self.s.registrations()['registrations'], [])

    def test_single_robot_group_does_not_fall_back_to_known_other_groups(self):
        self.foreign()
        self.platform.update(self.admin, self.bots[1]['id'], {'active': False})
        with self.assertRaisesRegex(PermissionError, '使用条件'):
            self.send('报名')

    def test_ineligible_same_group_robot_does_not_enable_cross_group_fallback(self):
        self.foreign()
        second = self.bots[1]
        for changes in ({'capabilities': ['registration']},
                        {'capabilities': ['enrollment'], 'slot_keys': []}):
            with self.subTest(changes=changes):
                self.platform.update(self.admin, second['id'], changes)
                with self.assertRaisesRegex(PermissionError, '使用条件'):
                    self.send('报名')

    def test_rebound_robot_is_not_suggested_from_an_old_group_context(self):
        second = self.bots[1]
        channel = second['channels'][0]['id']
        # This receive-only fixture has no real outgoing connection to migrate.
        self.platform.save_channel(self.admin, second['id'], {'robot_key': ''}, channel)
        moved_channel = self.platform.save_channel(self.admin, second['id'], {'chat_id': 'moved-group'}, channel)
        channel = moved_channel['id']
        self.assertEqual(self.platform.channel(channel)['chat_id'], 'moved-group')
        moved = self.platform.context_for_channel(channel, '1001', False, 'callback')
        Members(moved, self.clock).touch('小李')
        with self.assertRaisesRegex(PermissionError, '使用条件'):
            self.send('报名')

    def test_callback_keeps_suggestion_and_deduplication_in_original_group(self):
        _, foreign = self.foreign()
        body = {'robot_key': 'robot-test', 'chatid': 266382, 'creator': 1001,
                'ctime': 127859, 'content': '**报名**'}
        with TestClient(create_app(self.temp.name, TOKEN, False, False, self.clock, env_file='-')) as client:
            for _ in range(2):
                self.assertEqual(client.post(self.bots[0]['callback_path'], json=body).json(), {'result': 'ok'})
        messages = Outbox(self.ctx).list()
        self.assertEqual(len(messages), 1)
        self.assertIn('@二号', messages[0]['text'])
        self.assertNotIn('其他群', messages[0]['text'])
        self.assertEqual(Outbox(foreign).list(), [])
        self.assertEqual(self.s.registrations()['registrations'], [])

    def test_direct_use_in_another_group_retains_shared_membership(self):
        _, foreign = self.foreign()
        self.send('报名', foreign)
        self.assertEqual(len(Service(foreign, self.clock).registrations()['registrations']), 1)

    def test_missing_group_never_generates_group_suggestions(self):
        from app.enrollment_routing import recommendation
        self.foreign()
        self.assertIsNone(recommendation(replace(self.member, chat_id=''), '报名', self.clock))
