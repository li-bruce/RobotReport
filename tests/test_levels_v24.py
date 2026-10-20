"""End-to-end grade bounds, review, imports and strength-based assignment."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from app.competitions import TournamentService
from app.db import TZ, initialize
from app.formats.common import assigned_groups, assigned_sides, level, player_name
from app.member_imports import MemberImports
from app.members import Members
from app.security import BotContext
from app.service import Service


class NineLevelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = lambda: datetime(2026, 10, 13, 9, 0, tzinfo=TZ)
        self.admin = BotContext('levels-bot', self.root, 'levels-chat', 'admin', True, 'admin')
        initialize(self.admin.db_path)
        self.manager = Members(self.admin, self.clock)

    def member(self, identity):
        return Members(replace(self.admin, actor=identity, is_admin=False, source='callback'), self.clock)

    def approved(self, identity, rating):
        person = self.member(identity)
        person.apply('球友' + identity, rating, gender='male')
        self.manager.review(identity, 'approved')
        self.manager.review_level(identity, True)
        return person

    def test_all_nine_self_assessments_need_review_and_persist_exact_grade(self):
        for rating in range(1, 10):
            with self.subTest(rating=rating):
                person = self.member('grade-' + str(rating))
                person.apply('球友' + str(rating), gender='male')
                self.manager.review(person.ctx.actor, 'approved')
                requested = person.request_level(rating)
                self.assertEqual(requested['requested_level'], rating)
                self.assertIsNone(requested['level'])
                self.assertEqual(requested['level_status'], 'pending')
                self.manager.review_level(person.ctx.actor, True)
                self.assertEqual(person.get()['level'], rating)
        # Reopening the existing database must never rescale historical grades.
        initialize(self.admin.db_path)
        self.assertEqual(self.member('grade-3').get()['level'], 3)
        self.assertEqual(self.member('grade-9').get()['level'], 9)

    def test_invalid_grades_do_not_change_approved_profile_or_constraints(self):
        person = self.approved('nine', 9)
        for value in (0, 10, -1, True, False, 1.0, 9.5, '9', None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    person.request_level(value)
                if value is not None:
                    with self.assertRaises(ValueError):
                        self.manager.review_level('nine', True, value)
                    with self.assertRaises(ValueError):
                        self.manager.validate_rules({'min_level': value})
                    with self.assertRaises(ValueError):
                        TournamentService._options('round_robin', {'eligibility': {'max_level': value}})
                self.assertEqual(person.get()['level'], 9)
        self.manager.review_level('nine', False)
        self.assertIsNone(person.get()['level'])
        self.assertEqual(person.get()['level_status'], 'rejected')

    def test_csv_one_four_nine_optional_and_phone_binding_preserve_grade(self):
        imports = MemberImports(self.admin, self.clock)
        rows = ['姓名,手机号,自评级别,组别,性别']
        for index, value in enumerate(('1', '4级', '9', ''), 1):
            rows.append(f'导入球友{index},1380000000{index},{value},,男')
        preview = imports.preview(('\n'.join(rows) + '\n').encode('utf-8-sig'))
        self.assertTrue(preview['can_commit'], preview)
        self.assertEqual([r['level'] for r in preview['rows']], [1, 4, 9, None])
        self.assertEqual(imports.commit(preview['token'])['imported'], 4)
        for index, value in enumerate((1, 4, 9, None), 1):
            person = self.member('imported-' + str(index))
            result = person.apply(f'导入球友{index}', phone=f'1380000000{index}', gender='male')
            self.assertEqual(result['status'], 'approved')
            self.assertEqual(result['level'], value)
        for value in ('0', '10', '-1', 'true', '9.5'):
            with self.subTest(value=value):
                preview = imports.preview(f'姓名,手机号,自评级别\n错误球友,13900000001,{value}\n'.encode())
                self.assertFalse(preview['can_commit'])
                self.assertIsNone(preview['token'])

    def test_activity_level_bounds_apply_to_seven_through_nine(self):
        person = self.approved('player', 8)
        admin_service = Service(self.admin, self.clock)
        user_service = Service(person.ctx, self.clock)
        session = admin_service.sessions()[0]
        admin_service.save_session_constraints(session['id'], {'min_level': 7, 'max_level': 9})
        self.assertEqual(user_service.enroll('player', '球友player')['registrations'][0]['level'], 8)
        self.manager.review_level('player', True, 6)
        with self.assertRaisesRegex(PermissionError, '最低7级'):
            user_service.enroll('player', '球友player')
        self.manager.review_level('player', True, 9)
        admin_service.save_session_constraints(session['id'], {'min_level': 4, 'max_level': 8})
        with self.assertRaisesRegex(PermissionError, '最高8级'):
            user_service.enroll('player', '球友player')
        self.manager.review_level('player', False)
        with self.assertRaisesRegex(PermissionError, '尚无已审核等级'):
            user_service.enroll('player', '球友player')

    def test_tournament_bounds_and_participant_snapshots_preserve_high_grades(self):
        service = TournamentService(self.root / 'standalone-tournament.sqlite3')
        players = [{'id': f'u{i}:0', 'user_id': f'u{i}', 'name': f'球友{i}', 'level': value}
                   for i, value in enumerate((1, 4, 7, 9))]
        event = service.create({'title': '九级验证赛', 'kind': 'daily', 'format': 'round_robin',
                                'venue_id': 1, 'chat_id': 'standalone', 'participants': players,
                                'options': {'assignment_strategy': 'high_low',
                                            'eligibility': {'min_level': 1, 'max_level': 9}}}, 'u0', False)
        self.assertEqual(sorted(p['level'] for p in event['participants']), [1, 4, 7, 9])
        self.assertIn('9级', service.render(event['id']))
        for limits in ({'min_level': 9}, {'max_level': 9}, {'min_level': 4, 'max_level': 9}):
            with self.subTest(limits=limits):
                self.assertEqual(TournamentService._options('round_robin', {'eligibility': limits})['eligibility'],
                                 {**limits, 'allowed_group_ids': []})
        with self.assertRaises(ValueError):
            TournamentService._options('round_robin', {'eligibility': {'min_level': 9, 'max_level': 8}})

    def test_strength_algorithms_use_real_nine_level_values_and_unknown_stays_zero(self):
        players = [{'id': f'p{value}', 'user_id': f'user{value}', 'name': f'球友{value}', 'level': value}
                   for value in (1, 2, 3, 4, 6, 7, 8, 9)]
        strengths = {p['id']: p['level'] for p in players}
        for strategy in ('seeded', 'balanced', 'high_low'):
            with self.subTest(strategy=strategy):
                teams = assigned_sides(players, {'assignment_strategy': strategy}, seed=42)
                self.assertEqual(sorted(m for t in teams for m in t['members']), sorted(strengths))
                self.assertEqual([t['level_total'] for t in teams], [10, 10, 10, 10])
                self.assertEqual(sorted(t['max_level'] for t in teams), [6, 7, 8, 9])
                self.assertTrue(all(sum(strengths[m] >= 6 for m in t['members']) == 1 for t in teams))
                groups = assigned_groups(teams, 2, strategy)
                if strategy == 'seeded':
                    self.assertEqual(sorted(max(t['max_level'] for t in g) for g in groups), [8, 9])
        for value in (0, None, True, False, -1, 10, '9', 9.0):
            with self.subTest(value=value):
                self.assertEqual(level({'level': value}), 0)
                self.assertIn('无级别', player_name({'name': '未知球友', 'level': value}))
        self.assertEqual(level({'level': 9}), 9)
        self.assertIn('9级', player_name({'name': '九级球友', 'level': 9}))
