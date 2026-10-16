import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.db import TZ, initialize
from app.event_management import EventManagement
from app.members import Members
from app.security import BotContext
from app.tournaments import Tournaments


class EventManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        initialize(self.root / 'business.sqlite3')
        self.now = datetime.now(TZ)
        self.clock = lambda: self.now
        self.admin = BotContext('bot-a', self.root, 'group-a', 'web-admin', True, 'admin')
        self.members = Members(self.admin, self.clock)
        for user in ('owner', 'second', 'score', 'coordinate', 'player', 'outside'):
            self.members.touch('球友' + user, user, source='admin')
            self.members.review(user, 'approved')
        self.events = Tournaments(self.admin, self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def context(self, user, bot='bot-a', chat='group-a'):
        return replace(self.admin, id=bot, chat_id=chat, actor=user, is_admin=False, source='callback')

    def staff(self, user, role, **times):
        return {'user_id': user, 'role': role,
                'starts_at': times.get('starts_at', (self.now - timedelta(days=1)).isoformat()),
                'expires_at': times.get('expires_at', (self.now + timedelta(days=1)).isoformat())}

    def create(self, start=True):
        event = self.events.create({'title': '联合赛事', 'kind': 'large', 'keyword': '联合杯',
                                    'organizer_user_id': 'owner', 'format': 'round_robin',
                                    'participants': [{'id': user, 'user_id': user} for user in ('owner', 'second', 'score', 'player')],
                                    'registration_open': (self.now - timedelta(days=1)).isoformat(),
                                    'registration_close': (self.now + timedelta(days=1)).isoformat(),
                                    'options': {'discipline': 'singles'}})
        if start:
            self.events.transition(event['id'], 'open_registration')
            self.events.transition(event['id'], 'close_registration')
            return self.events.transition(event['id'], 'start')
        return event

    def test_shared_event_is_available_from_another_bot_and_group(self):
        event = self.create()
        owner_other_group = Tournaments(self.context('owner', 'bot-b', 'group-b'), self.clock)
        viewer = Tournaments(self.context('player', 'bot-c', 'group-c'), self.clock)
        self.assertEqual(owner_other_group.get(event['id'])['chat_id'], 'club')
        self.assertEqual(viewer.list()[0]['id'], event['id'])
        match = event['matches'][0]
        owner_other_group.score(event['id'], match['id'], [[21, 19]])
        self.assertEqual(viewer.get(event['id'])['matches'][0]['scores'], [[21, 19]])
        with self.assertRaises(PermissionError):
            viewer.score(event['id'], match['id'], [[21, 18]], version=1)
        with self.assertRaises(PermissionError):
            viewer.transition(event['id'], 'pause')

    def test_creation_binds_only_explicit_trusted_origin_channel(self):
        backend_event = self.create(start=False)
        self.assertEqual(backend_event['channel_ids'], [])
        self.events = Tournaments(replace(self.admin, channel_id='trusted-source-channel'), self.clock)
        event = self.events.create({'title': '群内创建', 'kind': 'large', 'keyword': '第二届联合杯',
                                    'channel_ids': ['injected-destination'],
                                    'registration_open': (self.now - timedelta(days=1)).isoformat(),
                                    'registration_close': (self.now + timedelta(days=1)).isoformat()})
        self.assertEqual(event['channel_ids'], ['trusted-source-channel'])

    def test_coordinator_arranges_but_cannot_score_change_rules_or_appoint(self):
        event = self.create()
        management = EventManagement(self.context('owner'), self.clock)
        management.save_organization(event['id'], {'staff': [self.staff('coordinate', 'coordinator')]})
        coordinator = EventManagement(self.context('coordinate', 'bot-b', 'group-b'), self.clock)
        result = coordinator.save_organization(event['id'], {'description': '欢迎球友', 'meeting_instructions': '18点一号馆集合',
                                                            'coordination': '赛前十分钟热身', 'match_schedule': [
                                                                {'match_id': event['matches'][0]['id'], 'scheduled_at': '2099-01-01T18:00:00+08:00', 'court': '1号场'}]})
        self.assertTrue(result['permissions']['coordinate'])
        self.assertFalse(result['permissions']['score'])
        self.assertIn('18点一号馆集合', self.events.describe(event['id']))
        for data in ({'rules_text': '任意改规则'}, {'team_method': 'seeded'}, {'staff': []}, {'channel_ids': []}):
            with self.assertRaises(PermissionError):
                coordinator.save_organization(event['id'], data)
        for action in (lambda: Tournaments(self.context('coordinate'), self.clock).score(event['id'], event['matches'][0]['id'], [[21, 10]]),
                       lambda: Tournaments(self.context('coordinate'), self.clock).transition(event['id'], 'close'),
                       lambda: self.events.engine.score(event['id'], event['matches'][0]['id'], [[21, 10]], 'coordinate', False)):
            with self.assertRaises(PermissionError):
                action()

    def test_scorer_records_only_owner_corrects_and_admin_assigns_organizer(self):
        event = self.create()
        owner = EventManagement(self.context('owner'), self.clock)
        owner.save_organization(event['id'], {'staff': [self.staff('score', 'scorer')]})
        scorer = Tournaments(self.context('score'), self.clock)
        match = event['matches'][0]
        scorer.score(event['id'], match['id'], [[21, 18]])
        with self.assertRaises(PermissionError):
            scorer.score(event['id'], match['id'], [[21, 17]], version=1)
        Tournaments(self.context('owner'), self.clock).score(event['id'], match['id'], [[21, 17]], version=1)
        with self.assertRaises(PermissionError):
            owner.save_organization(event['id'], {'staff': [self.staff('second', 'organizer')]})
        EventManagement(self.admin, self.clock).save_organization(event['id'], {'staff': [self.staff('second', 'organizer')]})
        second = Tournaments(self.context('second'), self.clock)
        second.transition(event['id'], 'pause')
        second.transition(event['id'], 'resume')
        self.assertEqual(second.transition(event['id'], 'close')['status'], 'cancelled')

    def test_appointment_requires_approved_member_and_revocation_is_immediate(self):
        event = self.create()
        manager = EventManagement(self.admin, self.clock)
        self.members.review('outside', 'rejected')
        with self.assertRaises(PermissionError):
            manager.save_organization(event['id'], {'staff': [self.staff('outside', 'scorer')]})
        with self.assertRaises(PermissionError):
            self.events.engine.update(event['id'], {'staff': [self.staff('outside', 'scorer')]}, 'web-admin', True)
        self.assertEqual(manager.get_organization(event['id'])['staff'], [])
        manager.save_organization(event['id'], {'staff': [self.staff('score', 'scorer')]})
        self.members.review('score', 'rejected')
        with self.assertRaises(PermissionError):
            self.events.engine.score(event['id'], event['matches'][0]['id'], [[21, 19]], 'score', False)

    def test_expired_roles_do_not_grant_operations(self):
        event = self.create()
        expired = self.staff('second', 'organizer', starts_at=(self.now - timedelta(days=2)).isoformat(), expires_at=(self.now - timedelta(days=1)).isoformat())
        EventManagement(self.admin, self.clock).save_organization(event['id'], {'staff': [expired]})
        self.assertFalse(EventManagement(self.context('second'), self.clock).permissions(event['id'])['manage'])
        with self.assertRaises(PermissionError):
            self.events.engine.transition(event['id'], 'pause', 'second', False)

    def test_multichannel_binding_validates_atomically_and_keeps_scores(self):
        event = self.create()
        manager = EventManagement(self.admin, self.clock)
        calls = []
        def validator(ids, venue):
            calls.append((ids, venue))
            if any(x not in ('bot-a-group-1', 'bot-b-group-2') for x in ids):
                raise PermissionError('不属于当前赛事场地的通道')
        with self.assertRaises(ValueError):
            manager.save_channels(event['id'], ['bot-a-group-1'])
        manager.save_channels(event['id'], ['bot-a-group-1', 'bot-b-group-2'], validator)
        before = self.events.engine.get(event['id'])
        with self.assertRaises(PermissionError):
            manager.save_organization(event['id'], {'description': '不应保存', 'channel_ids': ['foreign']}, validator)
        current = manager.get_organization(event['id'])
        self.assertEqual(current['channel_ids'], ['bot-a-group-1', 'bot-b-group-2'])
        self.assertEqual(current['description'], '')
        after = self.events.engine.get(event['id'])
        self.assertEqual(before['matches'], after['matches'])
        self.assertEqual(before['seed'], after['seed'])
        self.assertEqual(calls[0][1], 1)

    def test_organization_version_rules_and_team_method_preserve_draw(self):
        event = self.create(start=False)
        manager = EventManagement(self.admin, self.clock)
        before = manager.get_organization(event['id'])
        result = manager.save_organization(event['id'], {'expected_version': before['version'], 'team_method': 'balanced',
                                                         'rules_text': '先到21分，20平需净胜2分', 'description': '夏季联赛'})
        self.assertEqual(result['team_method'], 'balanced')
        with self.assertRaises(ValueError):
            manager.save_organization(event['id'], {'expected_version': before['version'], 'description': '旧覆盖'})
        self.events.transition(event['id'], 'open_registration')
        self.events.transition(event['id'], 'close_registration')
        self.events.transition(event['id'], 'start')
        drawn = self.events.get(event['id'])
        manager.save_organization(event['id'], {'description': '欢迎参赛', 'team_method': 'balanced'})
        with self.assertRaises(ValueError):
            manager.save_organization(event['id'], {'team_method': 'random'})
        self.assertEqual(drawn['matches'], self.events.get(event['id'])['matches'])
        self.assertIn('欢迎参赛', self.events.engine.render(event['id'], 'rules'))
        self.assertIn('先到21分', self.events.engine.render(event['id'], 'rules'))

    def test_invalid_payload_and_closed_event_cannot_change_organization(self):
        event = self.create()
        manager = EventManagement(self.admin, self.clock)
        for data in ({'team_method': []}, {'channel_ids': [{}]}, {'staff': [{'user_id': 'score', 'role': 'club_admin'}]},
                     {'expected_version': True, 'description': '旧覆盖'}):
            with self.assertRaises(ValueError):
                manager.save_organization(event['id'], data)
        self.events.transition(event['id'], 'close')
        with self.assertRaises(ValueError):
            manager.save_organization(event['id'], {'description': '关赛后改动'})

    def test_coordinator_group_commands_save_without_republishing_internal_text(self):
        from app.commands import Commands
        event = self.create()
        EventManagement(self.admin, self.clock).save_organization(event['id'], {'staff': [self.staff('coordinate', 'coordinator')]})
        commands = Commands(self.context('coordinate', 'bot-b', 'group-b'), self.clock)
        messages = [('集合说明 18:30到一号馆集合', 'meeting_instructions', '18:30到一号馆集合'),
                    ('公开说明：欢迎参加\n周末联赛', 'description', '欢迎参加\n周末联赛'),
                    ('协调安排 裁判轮班内部备注XYZ', 'coordination', '裁判轮班内部备注XYZ')]
        for message, field, value in messages:
            reply = asyncio.run(commands.run('联合赛事，' + message))
            self.assertIn('已保存', reply)
            self.assertNotIn(value, reply)
            self.assertEqual(EventManagement(self.admin, self.clock).get_organization(event['id'])[field], value)
        public = asyncio.run(Commands(self.context('player'), self.clock).run('联合赛事，说明'))
        self.assertIn('18:30到一号馆集合', public)
        self.assertIn('欢迎参加', public)
        self.assertNotIn('裁判轮班内部备注XYZ', public)
        self.assertIn('赛事名，协调安排', asyncio.run(commands.run('帮助 大型赛事')))
        self.assertIn('没有群内管理权限', asyncio.run(commands.run('帮助 管理')))

    def test_organization_commands_reject_ordinary_members_scorers_and_expired_staff(self):
        from app.commands import Commands
        event = self.create()
        EventManagement(self.admin, self.clock).save_organization(event['id'], {'staff': [self.staff('score', 'scorer'),
            self.staff('coordinate', 'coordinator', starts_at=(self.now-timedelta(days=2)).isoformat(), expires_at=(self.now-timedelta(days=1)).isoformat())]})
        for actor in ('player', 'score', 'coordinate'):
            for content in ('联合赛事，公开说明 不应保存', '联合赛事，集合说明 不应保存', '联合赛事，协调安排 不应保存'):
                with self.assertRaises(PermissionError):
                    asyncio.run(Commands(self.context(actor), self.clock).run(content))
        self.assertEqual(EventManagement(self.admin, self.clock).get_organization(event['id'])['description'], '')

    def test_organization_command_rechecks_membership_inside_write_transaction(self):
        from app.commands import Commands
        event = self.create()
        EventManagement(self.admin, self.clock).save_organization(event['id'], {'staff': [self.staff('coordinate', 'coordinator')]})
        original = EventManagement.save_organization
        def revoke_before_write(manager, event_id, data, *args, **kwargs):
            self.members.review('coordinate', 'suspended')
            return original(manager, event_id, data, *args, **kwargs)
        with patch.object(EventManagement, 'save_organization', revoke_before_write):
            with self.assertRaises(PermissionError):
                asyncio.run(Commands(self.context('coordinate'), self.clock).run('联合赛事，协调安排 不应保存'))
        self.assertEqual(EventManagement(self.admin, self.clock).get_organization(event['id'])['coordination'], '')

    def test_organization_commands_reject_empty_text_ambiguous_titles_and_closed_events(self):
        from app.commands import Commands
        event = self.create()
        owner = Commands(self.context('owner'), self.clock)
        with self.assertRaises(ValueError):
            asyncio.run(owner.run('联合赛事，集合说明'))
        self.events.transition(event['id'], 'close')
        with self.assertRaises(ValueError):
            asyncio.run(owner.run('联合赛事，公开说明 不应保存'))
        duplicate = self.events.create({'title': '联合赛事', 'kind': 'large', 'keyword': '另一个联合杯',
            'registration_open': (self.now-timedelta(days=1)).isoformat(), 'registration_close': (self.now+timedelta(days=1)).isoformat()})
        with self.assertRaisesRegex(ValueError, '同名'):
            asyncio.run(owner.run('联合赛事，集合说明 不应猜测'))
        self.assertEqual(EventManagement(self.admin, self.clock).get_organization(duplicate['id'])['meeting_instructions'], '')


if __name__ == '__main__':
    unittest.main()
