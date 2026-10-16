import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from app.competitions import TournamentService
from app.formats import available_formats, get_format
from app.formats.common import assigned_sides
from app.db import TZ
from app.members import Members
from app.security import Platform
from app.service import Service
from app.tournaments import Tournaments


def players(levels):
    return [{'id': f'private-id-{i}:0', 'user_id': f'private-id-{i}', 'name': f'球友{i}',
             'level': value, 'group_ids': [1]} for i, value in enumerate(levels)]


class TournamentV2Tests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parent / '.tmp-competitions'
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        self.engine = TournamentService(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, people, format_id='round_robin', **options):
        return self.engine.create({'kind': 'daily', 'format': format_id, 'venue_id': 1,
                                  'chat_id': 'chat', 'participants': people, 'options': options}, 'u', False)

    def test_default_doubles_and_explicit_singles_are_distinct(self):
        event = self.create(players([3, 2, 1, None]))
        self.assertEqual(event['options']['discipline'], 'doubles')
        self.assertEqual(event['options']['team_size'], 2)
        self.assertEqual(len(event['matches']), 1)
        self.assertTrue(all(len(side['members']) == 2 for side in event['groups'][0]['entrants']))
        singles = self.create(players([3, 2, 1, None]), discipline='singles')
        self.assertEqual(len(singles['matches']), 6)
        self.assertTrue(all(len(side['members']) == 1 for side in singles['groups'][0]['entrants']))
        with self.assertRaises(ValueError):
            self.create(players([3, 2, 1]))
        with self.assertRaises(ValueError):
            self.create(players([3, 2, 1, 1]), discipline='doubles', team_size=1)

    def test_available_formats_honor_count_discipline_and_group_size(self):
        three = {x['id']: x for x in available_formats(3)}
        self.assertFalse(any(x['available'] for x in three.values()))
        five = {x['id']: x for x in available_formats(5)}
        self.assertTrue(five['five_rotation']['available'])
        self.assertTrue(five['doubles_rotation']['available'])
        self.assertFalse(five['single_elimination']['available'])
        self.assertEqual(five['single_elimination']['player_step'], 2)
        two_singles = {x['id']: x for x in available_formats(2, 'singles')}
        self.assertTrue(two_singles['round_robin']['available'])
        self.assertFalse(two_singles['five_rotation']['available'])
        grouped = {x['id']: x for x in available_formats(12, 'doubles', {'group_count': 3, 'advance_per_group': 2})}
        self.assertEqual(grouped['group_knockout']['min_players'], 18)
        self.assertFalse(grouped['group_knockout']['available'])

    def test_availability_adds_minimum_substitutes_without_dropping_formal_players(self):
        choices = {item['id']: item for item in available_formats(3, 'doubles', substitute_count=4)}
        self.assertEqual(choices['round_robin']['selected_count'], 4)
        self.assertEqual(choices['round_robin']['substitutes_needed'], 1)
        self.assertEqual(choices['five_rotation']['selected_count'], 5)
        self.assertFalse(choices['group_knockout']['available'])
        self.assertFalse(choices['five_team']['available'])
        too_many = {item['id']: item for item in available_formats(6, 'doubles', substitute_count=10)}
        self.assertFalse(too_many['five_rotation']['available'])
        self.assertEqual(too_many['round_robin']['substitutes_needed'], 0)

    def test_proxy_name_preserved_and_level_not_inherited(self):
        people = players([3, 2, 1, None])
        people[1].update(name='王小明', user_id=people[0]['user_id'], guest_index=1, is_proxy=True, level=3)
        event = self.create(people)
        proxy = next(p for p in event['participants'] if p['name'] == '王小明')
        self.assertEqual(proxy['level'], 0)
        self.assertEqual(proxy['group_ids'], [])
        self.assertEqual(proxy['is_proxy'], 1)
        self.assertIn('王小明（代报·无级别）', self.engine.render(event['id']))

    def test_special_formats_reject_singles_in_engine_and_cli_builders(self):
        for key, count in [('five_rotation', 5), ('five_team', 10), ('doubles_rotation', 8)]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.create(players([1] * count), key, discipline='singles')
                with self.assertRaises(ValueError):
                    get_format(key).build(players([1] * count), {'discipline': 'singles'}, 1)

    def test_assignment_is_reproducible_and_never_loses_or_duplicates_players(self):
        people = players([3, 3, 3, 2, 2, 1, 1, None])
        expected = {p['id'] for p in people}
        for strategy in ('random', 'seeded', 'balanced', 'high_low'):
            options = {'assignment_strategy': strategy}
            first = assigned_sides(people, options, 12)
            self.assertEqual(first, assigned_sides(people, options, 12))
            members = [member for side in first for member in side['members']]
            self.assertEqual(len(members), len(set(members)))
            self.assertEqual(set(members), expected)
            self.assertTrue(all(len(side['members']) == 2 for side in first))

    def test_seeded_top_two_singles_are_in_opposite_bracket_halves(self):
        people = players([3, 3, 2, 2, 1, 1, None, None])
        plan = get_format('single_elimination').build(people, {'discipline': 'singles', 'assignment_strategy': 'seeded'}, 18)
        first_round = [m for m in plan['matches'] if m['round'] == 1]
        top = {p['id'] for p in people if p['level'] == 3}
        places = [index for index, m in enumerate(first_round) if any(top.intersection(side['members']) for side in [m['side_a'], m['side_b']])]
        self.assertEqual(len(places), 2)
        self.assertNotEqual(places[0] // 2, places[1] // 2)

    def test_seeded_groups_distribute_top_players(self):
        people = players([3, 3, 3, 3, 2, 2, 2, 2, 1, 1, None, None])
        plan = get_format('group_knockout').build(people, {'discipline': 'singles', 'assignment_strategy': 'seeded', 'group_count': 4}, 21)
        top = {p['id'] for p in people if p['level'] == 3}
        self.assertEqual([len(top.intersection(group['members'])) for group in plan['groups']], [1, 1, 1, 1])

    def test_balanced_teams_and_high_low_pairing_have_real_effect(self):
        people = players([3, 3, 3, 3, 1, 1, 1, 1])
        for strategy in ('balanced', 'high_low', 'seeded'):
            teams = assigned_sides(people, {'assignment_strategy': strategy}, 9)
            self.assertEqual({team['level_total'] for team in teams}, {4})
            self.assertEqual({team['max_level'] for team in teams}, {3})
        random = assigned_sides(people, {'assignment_strategy': 'random'}, 9)
        self.assertNotEqual({team['level_total'] for team in random}, {4})

    def test_rotations_keep_equal_appearances_under_all_strategies(self):
        for strategy in ('random', 'seeded', 'balanced', 'high_low'):
            plan = get_format('doubles_rotation').build(players([3, 3, 2, 2, 1, 1, None]), {'assignment_strategy': strategy}, 3)
            counts = Counter(pid for m in plan['matches'] for side in ('side_a', 'side_b') for pid in m[side]['members'])
            self.assertEqual(set(counts.values()), {4})

    def test_plan_and_approved_level_snapshot_survive_restart(self):
        event = self.create(players([3, 2, None, 1]), assignment_strategy='high_low')
        resumed = TournamentService(self.path).get(event['id'])
        self.assertEqual(event['matches'], resumed['matches'])
        self.assertEqual(event['plan'], resumed['plan'])
        self.assertEqual(event['plan']['assignment']['strategy'], 'high_low')
        self.assertEqual(event['plan']['assignment']['seed'], event['seed'])
        self.assertIn(0, event['plan']['assignment']['levels'].values())
        self.assertEqual(event['participants'][0]['group_ids'], [1])
        self.assertEqual(event['history'][-1]['after']['assignment'], event['plan']['assignment'])

    def test_all_rosters_are_tables_and_never_expose_id_as_name(self):
        people = players([3, None, 2, 1])
        people[0]['name'] = '张|三'
        people[1]['name'] = people[1]['user_id']
        event = self.create(people)
        for section in ('registration', 'groups', 'schedule', 'results'):
            result = self.engine.render(event['id'], section)
            self.assertIn('| --- |', result)
            self.assertNotIn('private-id-', result)
            self.assertIn(r'张\|三' if section == 'registration' else '张｜三', result)
            self.assertNotIn('\n\n| ---', result)
        self.assertIn('3级', self.engine.render(event['id']))
        self.assertIn('无级别', self.engine.render(event['id']))
        self.assertEqual(next(p for p in event['participants'] if p['user_id'] == people[1]['user_id'])['name'], '待完善姓名')

    def test_old_draw_and_scoring_options_are_preserved(self):
        event = self.create(players([3, 2, 1, 1]), discipline='singles')
        old_options = dict(event['options'])
        for key in ('discipline', 'assignment_strategy', 'eligibility'):
            old_options.pop(key)
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE tour_events SET options=? WHERE id=?', (json.dumps(old_options), event['id']))
        fresh = TournamentService(self.path)
        original = fresh.get(event['id'])
        changed = fresh.update(event['id'], {'options': {'notes': '补充说明'}}, 'admin', True)
        self.assertEqual(changed['options']['discipline'], 'singles')
        self.assertEqual(original['matches'], changed['matches'])
        self.assertEqual(len(changed['matches']), 6)

    def test_legacy_rotation_team_size_one_remains_doubles_and_editable(self):
        for format_id, count in [('five_rotation', 5), ('doubles_rotation', 6)]:
            event = self.create(players([1] * count), format_id)
            options = dict(event['options'])
            options.pop('discipline')
            options['team_size'] = 1
            with sqlite3.connect(self.path) as db:
                db.execute('UPDATE tour_events SET options=? WHERE id=?', (json.dumps(options), event['id']))
            updated = self.engine.update(event['id'], {'options': {'notes': '兼容升级'}}, 'admin', True)
            self.assertEqual(updated['options']['discipline'], 'doubles')
            self.assertEqual(updated['matches'], event['matches'])
            self.assertIn('双打', self.engine.render(event['id']))

    def test_planner_token_is_atomic_under_concurrency_and_retries(self):
        data = {'kind': 'daily', 'format': 'round_robin', 'chat_id': 'chat', 'venue_id': 1,
                'participants': players([3, 2, 1, None]), 'options': {'planner_token': 'a' * 32}}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.engine.create(data, 'owner', False), range(2)))
        self.assertEqual(results[0]['id'], results[1]['id'])
        self.assertEqual(results[0]['matches'], results[1]['matches'])
        self.assertEqual(len(self.engine.list()), 1)
        retry = TournamentService(self.path).create(data, 'owner', False)
        self.assertEqual(retry['seed'], results[0]['seed'])
        with self.assertRaises(ValueError):
            self.engine.create({**data, 'format': 'single_elimination'}, 'owner', False)
        with self.assertRaises(ValueError):
            self.engine.create({**data, 'options': {**data['options'], 'discipline': 'singles'}}, 'owner', False)

    def test_organizer_correction_close_and_personal_schedule(self):
        event = self.create(players([3, 2, 1, 1]), discipline='singles')
        match = event['matches'][0]
        for actor in ('private-id-0', 'spectator'):
            with self.assertRaises(PermissionError):
                self.engine.score(event['id'], match['id'], [[21, 19]], actor, False)
            with self.assertRaises(PermissionError):
                self.engine.transition(event['id'], 'pause', actor, False)
        self.engine.score(event['id'], match['id'], [[21, 19]], 'u', False)
        with self.assertRaises(ValueError):
            self.engine.score(event['id'], match['id'], [[21, 18]], 'u', False)
        changed = self.engine.score(event['id'], match['id'], [[21, 18]], 'u', False, expected_version=1)
        self.assertEqual(changed['matches'][0]['version'], 2)
        self.assertIn('（21） VS ', self.engine.render(event['id']))
        self.assertIn('（18）', self.engine.render(event['id']))
        mine = self.engine.render(event['id'], 'mine', actor='private-id-0')
        self.assertEqual(mine.count(' VS '), 3)
        self.engine.transition(event['id'], 'pause', 'u', False)
        closed = self.engine.transition(event['id'], 'close', 'u', False)
        self.assertEqual(closed['status'], 'cancelled')
        self.assertFalse(closed['champion'])
        self.assertEqual(closed['matches'][0]['scores'], [[21, 18]])
        with self.assertRaises(ValueError):
            self.engine.transition(event['id'], 'resume', 'u', False)
        with self.assertRaises(ValueError):
            self.engine.score(event['id'], match['id'], [[21, 17]], 'u', False, expected_version=2)

    def test_legacy_cached_id_names_project_verified_names_without_redraw(self):
        event = self.create(players([1, 2, 3, None]))
        event = self.engine.score(event['id'], event['matches'][0]['id'], [[21, 19]], 'u', False)
        old_groups, old_champion = event['groups'], event['champion']
        for group in old_groups:
            for side in group['entrants']:
                side['name'] = '+'.join(side['members'])
        for side in old_champion:
            side['name'] = '+'.join(side['members'])
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE tour_events SET groups_json=?,champion_json=? WHERE id=?', (json.dumps(old_groups), json.dumps(old_champion), event['id']))
            for item in event['matches']:
                a, b = item['side_a'], item['side_b']
                a['name'], b['name'] = '+'.join(a['members']), '+'.join(b['members'])
                db.execute('UPDATE tour_matches SET side_a=?,side_b=? WHERE id=?', (json.dumps(a), json.dumps(b), item['id']))
            db.execute('UPDATE tour_participants SET name=user_id WHERE tournament_id=?', (event['id'],))
        self.assertNotIn('private-id-', self.engine.render(event['id']))
        with sqlite3.connect(self.path) as db:
            for p in players([1, 2, 3, None]):
                db.execute('UPDATE tour_participants SET name=? WHERE tournament_id=? AND id=?', (p['name'], event['id'], p['id']))
            saved_cache = db.execute('SELECT side_a FROM tour_matches WHERE tournament_id=?', (event['id'],)).fetchone()[0]
        fresh = TournamentService(self.path).get(event['id'])
        self.assertEqual(fresh['matches'][0]['scores'], [[21, 19]])
        self.assertEqual(fresh['matches'][0]['side_a']['members'], event['matches'][0]['side_a']['members'])
        for section in ('schedule', 'groups', 'results', 'registration'):
            rendered = self.engine.render(event['id'], section)
            self.assertIn('球友', rendered)
            self.assertNotIn('private-id-', rendered)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT side_a FROM tour_matches WHERE tournament_id=?', (event['id'],)).fetchone()[0], saved_cache)


class TournamentBoundaryV2Tests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parent / '.tmp-competitions'
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        token = 'test-platform-token-with-at-least-32-characters'
        platform = Platform(Path(self.tmp.name), token)
        principal = platform.authenticate(token)
        from app.bot_capabilities import ALL_CAPABILITIES
        bot = platform.create(principal, {'name': '测试', 'chat_id': 'chat', 'robot_key': 'robot-key', 'capabilities':list(ALL_CAPABILITIES)})
        self.ctx = platform.context(principal, bot['id'])
        self.clock = lambda: datetime(2026, 10, 13, 16, tzinfo=TZ)
        self.members = Members(self.ctx, self.clock)
        self.service = Service(self.ctx, self.clock)
        self.events = Tournaments(self.ctx, self.clock)
        for i in range(8):
            self.members.touch('球友' + str(i), 'p' + str(i), source='admin')
            self.members.review('p' + str(i), 'approved')

    def tearDown(self):
        self.tmp.cleanup()

    def test_formal_first_minimum_substitutes_and_waitlist_never_selected(self):
        self.service.save_slots(1, [{'name': '小场', 'weekdays': list(range(7)), 'start': '17:30', 'end': '22:00', 'capacity': 2, 'substitute_capacity': 3}])
        for i in range(6):
            self.service.enroll('p' + str(i), '错误姓名')
        candidates = self.events.planning_candidates()
        self.assertEqual([p['user_id'] for p in candidates['formal']], ['p0', 'p1'])
        self.assertEqual([p['user_id'] for p in candidates['substitutes']], ['p2', 'p3', 'p4'])
        event = self.events.create({'kind': 'daily', 'format': 'round_robin'})
        self.assertEqual({p['user_id'] for p in event['participants']}, {'p0', 'p1', 'p2', 'p3'})
        self.assertEqual(event['options']['selected_substitute_names'], ['球友2', '球友3'])
        self.assertEqual(sum(p['source_category'] == '替补' for p in event['participants']), 2)
        self.assertIn('替补补齐参赛', self.events.describe(event['id']))
        with self.assertRaises(ValueError):
            self.events.create({'kind': 'daily', 'format': 'group_knockout'})

    def test_rejected_member_cannot_read_create_or_score(self):
        for i in range(4):
            self.service.enroll('p' + str(i), '球友' + str(i))
        event = self.events.create({'kind': 'daily', 'format': 'round_robin'})
        self.members.review('p0', 'rejected')
        stranger = Tournaments(replace(self.ctx, actor='p0', is_admin=False), self.clock)
        for action in [lambda: stranger.list(), lambda: stranger.get(event['id']),
                       lambda: stranger.create({'kind': 'daily'}),
                       lambda: stranger.score(event['id'], event['matches'][0]['id'], [[21, 10]])]:
            with self.assertRaises(PermissionError):
                action()

    def test_large_enroll_uses_verified_name_level_and_rechecks_rules_at_draw(self):
        self.members.review_level('p0', True, level=3)
        self.members.review_level('p1', True, level=3)
        event = self.events.create({'kind': 'large', 'format': 'round_robin', 'keyword': '公开赛',
                                   'registration_open': '2000-01-01T00:00:00+08:00',
                                   'registration_close': '2099-01-01T00:00:00+08:00',
                                   'options': {'discipline': 'singles', 'eligibility': {'min_level': 3}}})
        self.events.transition(event['id'], 'open_registration')
        with self.assertRaises(PermissionError):
            self.events.enroll(event['id'], 'p2', '伪造姓名')
        with self.assertRaises(PermissionError):
            self.events.enroll(event['id'], 'p0', '伪造姓名', 1)
        self.events.enroll(event['id'], 'p0', '伪造姓名')
        result = self.events.enroll(event['id'], 'p1', '伪造姓名')
        self.assertEqual({p['name'] for p in result['participants']}, {'球友0', '球友1'})
        self.assertEqual({p['level'] for p in result['participants']}, {3})
        self.events.transition(event['id'], 'close_registration')
        self.members.review('p1', 'rejected')
        with self.assertRaises(PermissionError):
            self.events.transition(event['id'], 'draw')
        self.assertEqual(self.events.get(event['id'])['matches'], [])

    def test_creation_rechecks_registration_and_approval_inside_transaction(self):
        for i in range(4):
            self.service.enroll('p' + str(i), '球友' + str(i))
        original = self.events.engine.create
        def race(data, actor, is_admin, **kwargs):
            self.members.review('p0', 'rejected')
            return original(data, actor, is_admin, **kwargs)
        self.events.engine.create = race
        with self.assertRaises(ValueError):
            self.events.create({'kind': 'daily', 'format': 'round_robin'})
        self.assertEqual(self.events.engine.list(), [])

    def test_admin_can_assign_approved_organizer_but_other_members_cannot(self):
        for i in range(4):
            self.service.enroll('p' + str(i), '球友' + str(i))
        event = self.events.create({'kind': 'daily', 'organizer_user_id': 'p0'})
        self.assertEqual(event['creator'], 'p0')
        self.assertEqual(event['options']['organizer_name'], '球友0')
        owner = Tournaments(replace(self.ctx, actor='p0', is_admin=False), self.clock)
        other = Tournaments(replace(self.ctx, actor='p1', is_admin=False), self.clock)
        self.assertTrue(owner.can_score(event))
        self.assertFalse(other.can_score(event))
        with self.assertRaises(PermissionError):
            other.create({'kind': 'daily', 'organizer_user_id': 'p0'})
        with self.assertRaises(PermissionError):
            other.transition(event['id'], 'close')
        self.assertEqual(owner.transition(event['id'], 'close')['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
