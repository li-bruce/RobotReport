"""Domain permissions remain separate from membership and channel scope."""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.db import TZ, connect
from app.event_management import EventManagement
from app.member_imports import MemberImports
from app.members import Members
from app.membership_terms import MembershipTerms
from app.people import People
from app.security import ALL_PERMISSIONS, Platform, permissions_for_roles, refresh_authority
from app.service import Service
from app.tournaments import Tournaments
from app.bot_capabilities import ALL_CAPABILITIES


class PermissionV28Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = lambda: datetime(2026, 10, 13, 9, 0, tzinfo=TZ)
        # The competition engine uses its module clock for registration deadlines.
        clock_patch = patch('app.competitions._now', self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.token = 'permissions-v28-platform-bootstrap-0123456789'
        self.platform = Platform(Path(self.temp.name), self.token)
        self.principal = self.platform.authenticate(self.token)
        self.bot = self.platform.create(self.principal, {'name': '群机器人', 'chat_id': 'group1', 'robot_key': 'robot-secret', 'capabilities':list(ALL_CAPABILITIES)})
        self.admin = self.platform.context(self.principal, self.bot['id'])
        self.people = People(self.admin, self.clock)
        for user in ('manager', 'owner', 'player', 'ordinary'):
            member = Members(self.context(user), self.clock)
            member.apply('球友' + user, gender='male')
            self.people.review(user, 'approved')
        Members(self.context('pending'), self.clock).apply('待审用户', level=4, gender='female')

    def context(self, user):
        return replace(self.admin, actor=user, is_admin=False, source='callback', permissions=())

    def grant(self, roles, user='manager'):
        self.people.set_roles(user, roles)
        return refresh_authority(self.context(user))

    def test_club_admin_gets_all_and_children_are_independent(self):
        ctx = self.grant(['club_admin'])
        self.assertTrue(ctx.is_admin)
        self.assertEqual(set(ctx.permissions), set(ALL_PERMISSIONS))
        self.assertTrue(all(ctx.can(p) for p in ALL_PERMISSIONS))
        ctx = self.grant(['member_manager', 'event_manager'])
        self.assertFalse(ctx.is_admin)
        self.assertEqual(set(ctx.permissions), {'member.manage', 'event.manage'})
        self.assertFalse(ctx.can('roles.manage'))
        self.assertFalse(ctx.can('unknown'))
        self.assertEqual(permissions_for_roles(['unknown']), ())

    def test_callback_context_calculates_subpermissions_and_web_login_is_unchanged(self):
        self.grant(['member_manager'])
        callback_token = self.bot['callback_path'].rsplit('/', 1)[1]
        ctx = self.platform.callback_context(self.bot['id'], callback_token,
                                             {'chatid': 'group1', 'creator': 'manager', 'robot_key': 'robot-secret'})
        self.assertFalse(ctx.is_admin)
        self.assertEqual(ctx.permissions, ('member.manage',))
        self.assertTrue(self.admin.is_admin)
        self.assertTrue(self.admin.can('roles.manage'))

    def test_forged_or_stale_snapshot_does_not_grant_permissions(self):
        forged = replace(self.context('ordinary'), is_admin=True, permissions=ALL_PERMISSIONS)
        self.assertFalse(forged.can('member.manage'))
        with self.assertRaises(PermissionError):
            Members(forged, self.clock).review('pending', 'approved')
        ctx = self.grant(['member_manager'])
        self.people.set_roles('manager', [])
        self.assertFalse(ctx.can('member.manage'))
        with self.assertRaises(PermissionError):
            Members(ctx, self.clock).review('pending', 'approved')

    def test_membership_manager_reviews_profiles_and_terms_but_cannot_grant_roles(self):
        ctx = self.grant(['member_manager'])
        members = People(ctx, self.clock)
        result = members.review('pending', 'approved')
        self.assertEqual(result['status'], 'approved')
        members.review_level('pending', True, level=4)
        group = members.save_group('周三队')
        self.assertEqual(members.set_groups('pending', [group['id']])['group_ids'], [group['id']])
        term = MembershipTerms(ctx, self.clock).create('pending', {'start_date': '2026-10-13', 'end_date': '2027-10-12'})
        self.assertEqual(term['status'], 'valid')
        self.assertIn('姓名'.encode(), MemberImports(ctx, self.clock).template())
        self.assertTrue(members.applications())
        with self.assertRaises(PermissionError):
            members.set_roles('manager', ['club_admin'])
        with self.assertRaises(PermissionError):
            Service(ctx, self.clock).save_venue({'name': '越权场地'})
        with self.assertRaises(PermissionError):
            Service(ctx, self.clock).update_config({'ai': {'enabled': True}})

    def test_member_manager_cannot_revoke_management_roles_indirectly(self):
        ctx = self.grant(['member_manager'])
        self.people.set_roles('owner', ['club_admin'])
        for status in ('suspended', 'rejected', 'pending'):
            with self.subTest(status=status), self.assertRaises(PermissionError):
                Members(ctx, self.clock).review('owner', status)
        self.assertEqual(self.people.get('owner')['status'], 'approved')
        self.assertEqual(self.people.get('owner')['roles'], ['club_admin'])

    def test_roles_require_approved_member_and_suspension_revokes_immediately(self):
        with self.assertRaises(PermissionError):
            self.people.set_roles('pending', ['member_manager'])
        ctx = self.grant(['club_admin'])
        self.people.review('manager', 'suspended')
        self.assertFalse(ctx.can('member.manage'))
        self.assertEqual(self.people.get('manager')['roles'], [])
        with self.assertRaises(PermissionError):
            People(ctx, self.clock).set_roles('ordinary', ['club_admin'])

    def test_activity_manager_can_manage_activity_not_members_or_system(self):
        ctx = self.grant(['activity_manager'])
        service = Service(ctx, self.clock)
        session = service.sessions('2026-10-14')[0]
        changed = service.save_session_constraints(session['id'], {'capacity': 12, 'substitute_capacity': 6})
        self.assertEqual(changed['capacity'], 12)
        service.update_config({'reminders': {'open': '10:00'}})
        with self.assertRaises(PermissionError):
            service.enroll('player', 'ignored', day='2026-10-14', number=1)
        with self.assertRaises(PermissionError):
            service.cancel('player', day='2026-10-14', number=1)
        service.enroll(ctx.actor, 'ignored', day='2026-10-14', number=1)
        service.cancel(ctx.actor, day='2026-10-14', number=1)
        self.assertIn('summary', service.stats())
        self.assertIn('counts', service.dashboard())
        with self.assertRaises(PermissionError):
            Members(ctx, self.clock).review('pending', 'approved')
        with self.assertRaises(PermissionError):
            service.update_config({'reminders': {'open': '11:00'}, 'ai': {'enabled': True}})
        self.assertEqual(service.config()['reminders']['open'], '10:00')
        with self.assertRaises(PermissionError):
            service.audit()

    def test_system_manager_only_updates_system_fields(self):
        ctx = self.grant(['system_manager'])
        service = Service(ctx, self.clock)
        service.update_config({'ai': {'enabled': True}})
        self.assertTrue(service.config()['ai']['enabled'])
        with self.assertRaises(PermissionError):
            service.update_config({'daily_enabled': False})
        with self.assertRaises(PermissionError):
            People(ctx, self.clock).set_roles('ordinary', ['member_manager'])

    def event(self, ctx, title='权限赛事'):
        return Tournaments(ctx, self.clock).create({'title': title, 'kind': 'large', 'keyword': title,
            'organizer_user_id': 'owner', 'format': 'round_robin',
            'participants': [{'id': user, 'user_id': user} for user in ('owner', 'player')],
            'registration_open': (self.clock() - timedelta(days=1)).isoformat(),
            'registration_close': (self.clock() + timedelta(days=1)).isoformat(),
            'options': {'discipline': 'singles'}})

    def test_event_manager_organizes_scores_without_membership_privilege(self):
        ctx = self.grant(['event_manager'])
        event = self.event(ctx)
        tournaments = Tournaments(ctx, self.clock)
        manager = EventManagement(ctx, self.clock)
        manager.save_organization(event['id'], {'rules_text': '21分', 'coordination': '提前集合'})
        for action in ('open_registration', 'close_registration', 'start'):
            event = tournaments.transition(event['id'], action)
        match = event['matches'][0]
        tournaments.score(event['id'], match['id'], [[21, 19]])
        self.assertEqual(tournaments.get(event['id'])['matches'][0]['scores'], [[21, 19]])
        with self.assertRaises(PermissionError):
            Members(ctx, self.clock).review('pending', 'approved')
        with self.assertRaises(PermissionError):
            Service(ctx, self.clock).save_slots(1, Service(self.admin, self.clock).slots(1))
        self.people.set_roles('manager', [])
        self.assertFalse(tournaments.get(event['id'])['permissions']['manage'])
        with self.assertRaises(PermissionError):
            manager.save_organization(event['id'], {'rules_text': '30分'})

    def test_event_subpermission_keeps_scope_restrictions_and_organizer_authority(self):
        event = self.event(self.admin)
        ctx = self.grant(['event_manager'])
        venue = Service(self.admin, self.clock).save_venue({'name': '另一场地'})
        restricted = replace(ctx, platform_root=None, venue_ids=(venue['id'],), venue_restricted=True)
        with self.assertRaises(PermissionError):
            Tournaments(restricted, self.clock).get(event['id'])
        self.assertTrue(Tournaments(self.context('owner'), self.clock).get(event['id'])['permissions']['manage'])
        with self.assertRaises(PermissionError):
            self.event(self.context('ordinary'), '会员越权')

    def test_message_manager_media_and_event_archive_do_not_cross_capabilities(self):
        from app.media import Media
        ctx = self.grant(['message_manager'])
        media = Media(ctx, self.clock)
        record = media.save('通知.txt', b'Welcome')
        self.assertEqual(media.list()[0]['id'], record['id'])
        self.assertIn('通知', media.compose('通知', '欢迎报名', [record['id']]))
        self.assertEqual(media.revoke(record['id'])['revoked'], 0)
        with self.assertRaises(PermissionError):
            Members(ctx, self.clock).review('pending', 'approved')
        event = self.event(self.admin)
        event_ctx = self.grant(['event_manager'])
        event_media = Media(event_ctx, self.clock)
        archived = event_media.save('赛程.txt', b'Round one', event['id'])
        self.assertEqual(event_media.list(event['id'])[0]['id'], archived['id'])
        with self.assertRaises(PermissionError):
            event_media.save('群通知.txt', b'Welcome')
        with self.assertRaises(PermissionError):
            event_media.compose('群通知', '越权', [])

    def test_event_manager_can_correct_casual_matches_without_owning_record(self):
        from app.casual import CasualMatches
        own = CasualMatches(self.context('owner'), self.clock)
        result = own.create({'discipline': 'singles', 'doubles_category': 'open',
            'team_a': [{'user_id': 'owner'}], 'team_b': [{'user_id': 'player'}],
            'score_a': 21, 'score_b': 19, 'venue_id': 1})
        ctx = self.grant(['event_manager'])
        manager = CasualMatches(ctx, self.clock)
        corrected = manager.update(result['id'], {'score_a': 21, 'score_b': 18, 'expected_version': result['version']})
        self.assertEqual(corrected['score_b'], 18)
        self.assertTrue(manager.history(user_id='owner')['matches'])
        self.people.set_roles('manager', ['member_manager'])
        with self.assertRaises(PermissionError):
            manager.update(result['id'], {'score_a': 21, 'score_b': 17, 'expected_version': corrected['version']})

    def test_directory_lists_only_reviewed_roles_and_permissions(self):
        self.grant(['member_manager'])
        directory = self.people.role_directory()
        self.assertEqual(len(directory['definitions']), 7)
        self.assertEqual(len(directory['permissions']), 7)
        self.assertEqual(next(row for row in directory['assignments'] if row['user_id'] == 'manager')['roles'], ['member_manager'])
        with self.assertRaises(ValueError):
            self.people.set_roles('manager', ['roles.manage'])


if __name__ == '__main__':
    unittest.main()
