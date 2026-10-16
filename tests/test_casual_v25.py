from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json

from app.casual import CasualMatches, render_history, render_statistics
from app.casual_dialog import handle_casual
from app.competitions import TournamentService
from app.db import connect
from app.members import Members
from app.service import Service
from tests.test_core import CoreFixture


class CasualTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.casual = CasualMatches(self.member, self.clock)
        with connect(self.ctx.db_path, True) as db:
            cols = {r['name'] for r in db.execute('PRAGMA table_info(member_records)')}
            if 'gender' not in cols:
                db.execute("ALTER TABLE member_records ADD COLUMN gender TEXT NOT NULL DEFAULT 'unknown'")
            for user, gender in [('1001', 'male'), ('1002', 'female'), ('1003', 'male'), ('1004', 'female')]:
                db.execute('UPDATE member_records SET gender=? WHERE chat_id=? AND user_id=?', (gender, self.ctx.scope_id, user))

    def data(self, **changes):
        return {'discipline': 'singles', 'team_a': [{'user_id': '1001'}], 'team_b': [{'user_id': '1002'}], 'score_a': 21, 'score_b': 19, **changes}

    def doubles(self, **changes):
        return self.data(**{'discipline': 'doubles', 'team_a': [{'user_id': '1001'}, {'user_id': '1002'}], 'team_b': [{'user_id': '1003'}, {'user_id': '1004'}], **changes})

    def test_create_is_participant_owned_and_identity_from_database(self):
        row = self.casual.create(self.data(team_a=[{'user_id': '1001', 'name': '伪造姓名', 'gender': 'female'}]))
        self.assertEqual(row['team_a'][0]['name'], '小李')
        self.assertEqual(row['team_a'][0]['gender'], 'male')
        self.assertEqual(row['status'], 'completed')
        self.assertTrue(row['can_edit'])
        self.assertEqual(self.casual.statistics()['wins'], 1)
        with self.assertRaises(PermissionError):
            self.casual.create(self.data(team_a=[{'user_id': '1003'}]))
        with self.assertRaises(PermissionError):
            self.casual.create(self.data(team_a=[{'name': '小李'}]))
        with self.assertRaises(ValueError):
            self.casual.create(self.data(team_b=[{'user_id': '1001'}]))

    def test_unapproved_suspended_cannot_record_or_read(self):
        Members(self.ctx, self.clock).review('1001', 'suspended')
        with self.assertRaises(PermissionError):
            self.casual.create(self.data())
        with self.assertRaises(PermissionError):
            self.casual.history()
        with self.assertRaises(PermissionError):
            handle_casual(self.member, '记录', clock=self.clock)

    def test_gender_categories_enforce_every_team_and_trusted_profiles(self):
        row = self.casual.create(self.doubles(doubles_category='mixed'))
        self.assertEqual(row['doubles_category'], 'mixed')
        for category in ('mens', 'womens'):
            with self.assertRaises(ValueError):
                self.casual.create(self.doubles(doubles_category=category))
        with self.assertRaises(ValueError):
            self.casual.create(self.doubles(doubles_category='mixed', team_b=[{'user_id': '1003'}, {'name': '访客小红'}]))
        row = self.casual.create(self.doubles(doubles_category='mixed', team_b=[{'user_id': '1003'}, {'name': '访客小红', 'gender': 'female'}]))
        self.assertEqual(row['team_b'][1]['kind'], 'guest')
        row = self.casual.create(self.doubles(doubles_category='mens', team_a=[{'user_id': '1001'}, {'user_id': '1003'}], team_b=[{'name': '甲', 'gender': 'male'}, {'name': '乙', 'gender': 'male'}]))
        self.assertEqual(row['doubles_category'], 'mens')

    def test_guest_names_never_merge_with_members_or_each_other(self):
        row = self.casual.create(self.data(team_b=[{'name': '球友1002', 'gender': 'female'}]))
        self.assertEqual(row['team_b'][0]['kind'], 'guest')
        self.assertNotIn('user_id', row['team_b'][0])
        other = self.casual.create(self.data(team_b=[{'name': '球友1002', 'gender': 'female'}], allow_duplicate=True))
        self.assertNotEqual(row['team_b'][0]['guest_id'], other['team_b'][0]['guest_id'])
        self.assertEqual(len(self.casual.statistics()['opponents']), 2)
        self.assertEqual(CasualMatches(replace(self.member, actor='1002'), self.clock).history()['count'], 0)

    def test_deduplication_atomic_and_explicit_repeat_is_a_distinct_game(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            rows = list(pool.map(lambda _: CasualMatches(self.member, self.clock).create(self.data()), range(8)))
        self.assertEqual(len({row['id'] for row in rows}), 1)
        self.assertEqual(self.casual.history()['count'], 1)
        reversed_record = CasualMatches(replace(self.member, actor='1002'), self.clock).create(self.data(team_a=[{'user_id': '1002'}], team_b=[{'user_id': '1001'}], score_a=19, score_b=21))
        self.assertTrue(reversed_record['duplicate'])
        self.casual.create(self.data(allow_duplicate=True))
        self.assertEqual(self.casual.history()['count'], 2)

    def test_only_submitter_or_admin_may_correct_and_void(self):
        row = self.casual.create(self.data())
        opponent = CasualMatches(replace(self.member, actor='1002'), self.clock)
        self.assertFalse(opponent.get(row['id'])['can_edit'])
        with self.assertRaises(PermissionError):
            opponent.update(row['id'], {'score_a': 19, 'score_b': 21})
        with self.assertRaises(PermissionError):
            opponent.void(row['id'])
        update = self.casual.update(row['id'], {'score_a': 18, 'score_b': 21, 'expected_version': 1})
        self.assertEqual(update['version'], 2)
        self.assertEqual(self.casual.statistics()['losses'], 1)
        with self.assertRaises(ValueError):
            self.casual.update(row['id'], {'score_a': 21, 'score_b': 0, 'expected_version': 1})
        result = CasualMatches(self.ctx, self.clock).void(row['id'], expected_version=2)
        self.assertEqual(result['status'], 'void')
        self.assertEqual(self.casual.statistics()['played'], 0)
        self.assertEqual(self.casual.history(include_void=True)['count'], 1)
        with self.assertRaises(ValueError):
            self.casual.update(row['id'], {'score_a': 21, 'score_b': 19})
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM casual_audit WHERE match_id=?', (row['id'],)).fetchone()[0], 3)

    def test_privacy_and_cross_bot_shared_history(self):
        row = self.casual.create(self.data())
        stranger = CasualMatches(replace(self.member, actor='1003'), self.clock)
        self.assertEqual(stranger.history()['count'], 0)
        for operation in (lambda: stranger.get(row['id']), lambda: stranger.history('1001'), lambda: stranger.statistics('1001')):
            with self.assertRaises(PermissionError):
                operation()
        second = replace(self.platform.context(self.admin, self.bots[1]['id']), actor='1001', is_admin=False)
        self.assertEqual(CasualMatches(second, self.clock).history()['count'], 1)
        self.assertEqual(CasualMatches(self.ctx, self.clock).history()['count'], 1)

    def test_personal_statistics_partners_opponents_combination_and_filters(self):
        self.casual.create(self.doubles())
        self.casual.create(self.doubles(score_a=18, score_b=21))
        self.casual.create(self.data(score_a=12, score_b=21))
        result = self.casual.statistics()
        self.assertEqual((result['played'], result['wins'], result['losses'], result['win_rate']), (3, 1, 2, 33.3))
        self.assertEqual(result['partners'][0]['played'], 2)
        self.assertEqual(result['partners'][0]['win_rate'], 50.0)
        self.assertEqual(len(result['opponents']), 3)
        self.assertEqual(sorted(x['played'] for x in result['opponent_teams']), [1, 2])
        self.assertEqual({x['discipline']: x['played'] for x in result['by_discipline']}, {'singles': 1, 'doubles': 2})
        self.assertEqual(self.casual.history(date_from='2026-10-13')['count'], 0)
        self.assertIn('| 搭档组合 |', render_statistics(result))
        self.assertIn('小李', render_history(self.casual.history()))
        self.assertNotIn('member:', render_history(self.casual.history()))

    def test_date_validation_and_location_snapshot_survives_edits(self):
        first = self.s.sessions()[0]
        row = self.casual.create(self.data(session_id=first['id']))
        with self.assertRaises(ValueError):
            self.casual.create(self.data(date='2026-10-11'))
        with self.assertRaises(ValueError):
            self.casual.create(self.data(date='2026-10-13'))
        self.s.save_venue({'name': '改名场馆'}, 1)
        Members(self.ctx, self.clock).review('1002', 'approved', name='新的姓名')
        updated = self.casual.update(row['id'], {'score_b': 18})
        self.assertEqual(updated['team_b'][0]['name'], '球友1002')
        self.assertEqual(self.casual.get(row['id'])['venue_name'], '主场地')
        self.assertEqual(self.casual.get(row['id'])['session']['start'], '17:30')
        with self.assertRaises(ValueError):
            self.casual.update(row['id'], {'session_id': 100})

    def test_slot_scope_blocks_unbound_record_and_filters_history(self):
        first, second = self.s.sessions()
        a = self.casual.create(self.data(session_id=first['id']))
        self.casual.create(self.data(session_id=second['id']))
        self.casual.create(self.data())
        scoped = replace(self.member, platform_root=None, slot_keys=(first['slot_key'],))
        limited = CasualMatches(scoped, self.clock)
        self.assertEqual([r['id'] for r in limited.history()['matches']], [a['id']])
        with self.assertRaises(ValueError):
            limited.create(self.data())
        with self.assertRaises(ValueError):
            limited.create(self.data(session_id=second['id']))
        nobody = CasualMatches(replace(scoped, slot_keys=()), self.clock)
        self.assertEqual(nobody.history()['count'], 0)

    def test_formal_completed_games_merge_once_with_correct_winner(self):
        engine = TournamentService(self.ctx.db_path)
        event = engine.create({'title': '个人积分赛', 'kind': 'daily', 'format': 'round_robin', 'date': '2026-10-12',
            'venue_id': 1, 'chat_id': self.ctx.scope_id, 'options': {'discipline': 'singles', 'best_of': 3},
            'participants': [{'id': user + ':0', 'user_id': user, 'name': '小李' if user == '1001' else '球友1002'} for user in ('1001', '1002')]}, '1001', False)
        self.assertEqual(self.casual.history()['count'], 0)
        match = event['matches'][0]
        engine.score(event['id'], match['id'], [[21, 19], [0, 21], [21, 19]], '1001', False)
        results = self.casual.history()['matches']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source'], 'tournament')
        # Side A wins two sets despite fewer total points.
        expected = 1 if results[0]['team_a'][0]['user_id'] == '1001' else 0
        self.assertEqual(self.casual.statistics()['wins'], expected)
        self.assertEqual(self.casual.statistics()['sources'], {'casual': 0, 'tournament': 1})
        self.assertEqual(CasualMatches(replace(self.member, platform_root=None, slot_keys=()), self.clock).history()['count'], 0)

    def test_formal_history_keeps_venue_snapshot_after_venue_rename(self):
        from app.tournaments import Tournaments
        for user in ('1001', '1002'):
            self.s.enroll(user, '姓名')
        events = Tournaments(self.member, self.clock)
        event = events.create({'title': '历史场地核验', 'kind': 'daily', 'format': 'round_robin',
                               'options': {'discipline': 'singles'}})
        match = event['matches'][0]
        events.score(event['id'], match['id'], [[21, 19]], match['version'])
        before = self.casual.history()['matches'][0]
        self.s.save_venue({'name': '改名后的场馆'}, 1)
        after = self.casual.history()['matches'][0]
        self.assertEqual(after['venue_name'], before['venue_name'])
        self.assertEqual(after['venue_name'], event['options']['session_snapshot']['venue_name'])
        self.assertEqual(after['session'], before['session'])

    def test_score_type_and_size_validation(self):
        for change in ({'score_a': True}, {'score_b': -1}, {'score_a': 1000}, {'score_b': 21}, {'discipline': 'triples'}, {'doubles_category': 'mixed'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.casual.create(self.data(**change))

    def test_live_bot_scope_revocation_applies_to_existing_service(self):
        first, second = self.s.sessions()
        self.casual.create(self.data(session_id=first['id']))
        self.casual.create(self.data(session_id=second['id']))
        self.platform.update(self.admin, self.ctx.id, {'slot_keys': [first['slot_key']]})
        self.assertEqual(self.casual.history()['count'], 1)
        self.platform.update(self.admin, self.ctx.id, {'slot_keys': []})
        self.assertEqual(self.casual.history()['count'], 0)
        with self.assertRaises(ValueError):
            self.casual.create(self.data(session_id=first['id']))

    def test_formal_single_game_duplicate_needs_explicit_second_game(self):
        engine = TournamentService(self.ctx.db_path)
        event = engine.create({'title': '记分赛事', 'kind': 'daily', 'format': 'round_robin', 'date': '2026-10-12',
            'venue_id': 1, 'chat_id': self.ctx.scope_id, 'options': {'discipline': 'singles'},
            'participants': [{'id': user + ':0', 'user_id': user, 'name': user + '姓名'} for user in ('1001', '1002')]}, '1001', False)
        match = event['matches'][0]
        engine.score(event['id'], match['id'], [[21, 19]], '1001', False)
        official = self.casual.history()['matches'][0]
        data = self.data(team_a=[{'user_id': official['team_a'][0]['user_id']}], team_b=[{'user_id': official['team_b'][0]['user_id']}])
        duplicated = self.casual.create(data)
        self.assertEqual(duplicated['source'], 'tournament')
        self.assertTrue(duplicated['duplicate'])
        self.assertEqual(self.casual.history()['count'], 1)
        self.casual.create(data | {'allow_duplicate': True})
        self.assertEqual(self.casual.history()['count'], 2)


class CasualDialogFlowTests(CoreFixture):
    def run_command(self, text, **options):
        return handle_casual(self.member, text, clock=self.clock, **options)

    def test_guided_and_inline_variants_and_modify_void(self):
        self.assertIn('记录 单打', self.run_command('记录'))
        reply = self.run_command('记录单打我VS球友1002 21：19')
        self.assertIn('已记录', reply)
        self.assertIn('| 甲方 | 比分 | 乙方 |', reply)
        self.assertIn('已修正', self.run_command('修改记录 1，19-21'))
        self.assertIn('1** 场', self.run_command('我的对战统计'))
        self.assertIn('已作废', self.run_command('作废记录1'))
        self.assertIn('还没有', self.run_command('我的对战历史'))
        self.assertIsNone(self.run_command('报名'))

    def test_homonyms_require_choice_and_cannot_leak_contact_or_ids(self):
        self.approve('1002', '张三')
        self.approve('1003', '张三')
        reply = self.run_command('记录 单打 我 VS 张三 21-19')
        self.assertIn('请选择', reply)
        self.assertNotIn('1002', reply)
        self.assertNotIn('1003', reply)
        self.assertNotIn('手机号', reply)
        self.assertEqual(CasualMatches(self.member, self.clock).history()['count'], 0)
        self.assertIn('已记录', self.run_command('选择2'))
        self.assertEqual(CasualMatches(self.member, self.clock).history()['matches'][0]['team_b'][0]['user_id'], '1003')
        self.assertIsNone(self.run_command('选择1'))

    def test_duplicate_confirmation_and_expiration(self):
        self.run_command('记录 单打 我 VS 球友1002 21:19')
        self.assertIn('未重复记入', self.run_command('记录 单打 我 VS 球友1002 21:19'))
        self.assertIn('已记录', self.run_command('重复记录'))
        self.assertEqual(CasualMatches(self.member, self.clock).history()['count'], 2)
        self.run_command('记录 单打 我 VS 球友1002 21:19')
        self.now += timedelta(minutes=21)
        self.assertIn('没有待确认', self.run_command('重复记录'))

    def test_named_guest_explicit_and_personal_filters(self):
        self.run_command('记录 单打 我 VS 访客：球友1002（女） 21-19')
        record = CasualMatches(self.member, self.clock).history()['matches'][0]
        self.assertEqual(record['team_b'][0]['kind'], 'guest')
        self.assertEqual(record['team_b'][0]['gender'], 'female')
        self.assertIn('访客', self.run_command('我的对战历史 2026-10-12'))
        self.assertIn('还没有', self.run_command('我的对战历史 2026-10-13 至 2026-10-15'))
        self.assertIn('对战统计', self.run_command('对战统计'))

    def test_explicit_default_venue_filter_is_not_ignored(self):
        other = self.s.save_venue({'name': '二号场馆'})
        self.run_command('记录 单打 我 VS 球友1002 21:19')
        self.run_command('记录 单打 我 VS 球友1002 21:19', venue_id=other['id'])
        self.assertIn('共 2 场', self.run_command('我的对战历史'))
        self.assertIn('共 1 场', self.run_command('我的对战历史', venue_id=1, explicit_venue=True))

    def test_unfinished_dialog_cannot_cross_midnight(self):
        self.now = self.now.replace(hour=23, minute=55)
        self.approve('1002', '同名球友')
        self.approve('1003', '同名球友')
        self.assertIn('请选择', self.run_command('记录 单打 我 VS 同名球友 21:19'))
        self.now += timedelta(minutes=10)
        with self.assertRaisesRegex(ValueError, '当天'):
            self.run_command('选择1')
        self.assertEqual(CasualMatches(self.member, self.clock).history()['count'], 0)
