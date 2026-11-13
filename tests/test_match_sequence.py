"""Daily authorization, real transaction integration, and sequence invariants."""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
from app.commands import Commands
from app.db import connect
from app.formats.match_sequence import build, contest_key, team_key
from app.formats.common import match, sides
from app.main import create_app
from app.score_parser import parse_score
from app.service import Service
from app.tournaments import Tournaments
from tests.test_core import CoreFixture, TOKEN


class SequenceAlgorithmTests(unittest.TestCase):
    def people(self, count):
        return [{'id': str(i), 'user_id': str(i), 'name': '球友'+str(i),
                 'gender': 'female' if i % 2 else 'male', 'level': i % 9+1} for i in range(count)]

    def test_stable_even_and_odd_rosters_cover_partnerships_without_repeating_contests(self):
        for count in (4, 5, 6, 7, 8, 9, 12):
            history, previous = [], None
            for _ in range(count*3):
                plan = build(self.people(count), {}, 199, history, previous)
                if not plan['matches']:
                    break
                teams = plan['sequence']['teams']
                existing = {contest_key(m) for m in history}
                needed = sum(tuple(sorted((team_key(a), team_key(b)))) not in existing for a, b in combinations(teams, 2))
                self.assertEqual(len(plan['matches']), needed)
                for game in plan['matches']:
                    members = game['side_a']['members']+game['side_b']['members']
                    self.assertEqual(len(set(members)), 4)
                    self.assertNotIn(contest_key(game), {contest_key(m) for m in history})
                    history.append({**game, 'status': 'completed'})
                previous = plan['sequence']
            else:
                self.fail('Rotation did not terminate')
            partners = {team_key(m[s]) for m in history for s in ('side_a', 'side_b')}
            self.assertEqual(partners, set(combinations(sorted(str(i) for i in range(count)), 2)))

    def test_singles_all_pairs_and_completed_history(self):
        people = self.people(7)
        plan = build(people, {'discipline': 'singles'}, 7)
        self.assertEqual(len(plan['matches']), 21)
        done = [{**m, 'status': 'completed'} for m in plan['matches'][:4]]
        again = build(people, {'discipline': 'singles'}, 7, done)
        self.assertEqual(len(again['matches']), 17)
        self.assertFalse({contest_key(m) for m in done} & {contest_key(m) for m in again['matches']})

    def test_large_roster_is_bounded_reproducible_and_balanced_per_batch(self):
        for count in (4, 7, 20, 40):
            plan = build(self.people(count), {}, 777)
            self.assertEqual(plan, build(self.people(count), {}, 777))
            loads = Counter(pid for m in plan['matches'] for s in ('side_a', 'side_b') for pid in m[s]['members'])
            self.assertEqual(len(set(loads.values())), 1)
            self.assertLessEqual(len(plan['matches']), 190)

    def test_outcome_syntax(self):
        for text, result in (('春季赛，第十二场 胜', 'a'), ('第２场负', 'b'),
                             ('第③场乙方胜', 'b'), ('3场甲负', 'b'), ('三场B输', 'a')):
            parsed = parse_score(text)
            self.assertEqual(parsed['outcome'], result)
            self.assertEqual(parsed['scores'], [])
        self.assertIsNone(parse_score('报名胜'))

    def completed_games(self, people, size=2):
        return [{**match(*sides(people[i:i+size*2], size)), 'status': 'completed'}
                for i in range(0, len(people), size*2)]

    def assert_unplayed_first(self, plan, history, people):
        played = Counter(pid for game in history for side in ('side_a', 'side_b')
                         for pid in game[side]['members'])
        waiting = {p['id'] for p in people if not played[p['id']]}
        for game in plan['matches']:
            members = set(game['side_a']['members']+game['side_b']['members'])
            if waiting:
                self.assertTrue(waiting & members, (waiting, members))
            waiting -= members
        self.assertFalse(waiting, 'A never-played member was left resting')

    def test_newcomer_never_rests_instead_of_someone_who_has_played(self):
        history = self.completed_games(self.people(8))
        for seed in range(24):
            with self.subTest(seed=seed):
                plan = build(self.people(9), {}, seed, history)
                self.assertNotIn('8', plan['sequence']['resting'])
                first = plan['matches'][0]
                self.assertIn('8', first['side_a']['members']+first['side_b']['members'])
                self.assert_unplayed_first(plan, history, self.people(9))

    def test_first_game_packs_unplayed_members_before_pairing_preferences(self):
        people = self.people(9)
        history = self.completed_games(people[:4])
        for seed in range(12):
            with self.subTest(seed=seed):
                plan = build(people, {}, seed, history)
                first = plan['matches'][0]
                members = set(first['side_a']['members']+first['side_b']['members'])
                if seed == 0:
                    self.assertEqual(len(members & set('45678')), 4)
                teams = plan['sequence']['teams']
                feasible = max(len(set(a['members']+b['members']) & set('45678'))
                               for a, b in combinations(teams, 2))
                self.assertEqual(len(members & set('45678')), feasible)
                self.assert_unplayed_first(plan, history, people)

    def test_singles_and_strict_mixed_serve_all_unplayed_before_old_only_games(self):
        for discipline, category, size in (('singles', 'open', 1), ('doubles', 'mixed', 2)):
            people = self.people(8)
            history = self.completed_games(people[:4], size)
            for seed in range(8):
                with self.subTest(discipline=discipline, seed=seed):
                    plan = build(people, {'discipline': discipline, 'doubles_category': category}, seed, history)
                    self.assert_unplayed_first(plan, history, people)
                    if category == 'mixed':
                        for game in plan['matches']:
                            for side in ('side_a', 'side_b'):
                                self.assertEqual({people[int(pid)]['gender'] for pid in game[side]['members']}, {'male', 'female'})

    def test_pending_games_are_not_first_appearances_and_history_is_not_mutated(self):
        from copy import deepcopy
        people = self.people(9)
        history = self.completed_games(people[:8])
        history.append({**match(*sides([people[i] for i in (8, 1, 2, 3)], 2)), 'status': 'ready'})
        before = deepcopy(history)
        plan = build(people, {}, 3, history)
        self.assert_unplayed_first(plan, history[:-1], people)
        self.assertEqual(history, before)

    def test_singles_newcomer_precedes_low_load_veterans_even_with_high_load_opponent(self):
        people = self.people(3)
        history = [{**match(*sides(people[1:])), 'status': 'completed'} for _ in range(6)]
        history += [{**match(*sides([people[0], people[1]])), 'status': 'completed'}]
        people.append(dict(id='3', name='新成员', gender='female', level=9))
        plan = build(people, {'discipline': 'singles'}, 2, history)
        self.assert_unplayed_first(plan, history, people)


class DailySequenceTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.channels = [bot['channels'][0]['id'] for bot in self.bots]
        self.contexts = [self.platform.context_for_channel(c, '1001', False, 'callback') for c in self.channels]
        self.events = Tournaments(self.contexts[0], self.clock)
        self.session = self.s.sessions()[0]
        self.s.save_session_constraints(self.session['id'], {'capacity': 20, 'substitute_capacity': 10})
        for user in ('1001', '1', '2', '3', '4', '5', '6', '9'):
            self.s.enroll(user, 'ignored', session_id=self.session['id'])
        p = patch('app.competitions._now', self.clock)
        p.start()
        self.addCleanup(p.stop)

    def send(self, text, user='1001', bot=0):
        ctx = self.platform.context_for_channel(self.channels[bot], user, False, 'callback')
        return asyncio.run(Commands(ctx, self.clock).run(text))

    def create(self, format='match_sequence', **options):
        return self.events.create({'title': '周一活动', 'format': format,
                                  'session_id': self.session['id'], 'options': options})

    def test_any_event_participant_scores_any_game_but_corrections_remain_restricted(self):
        event = self.create('round_robin', scoring_mode='leader', leader_user_ids=['1001'])
        game = event['matches'][0]
        playing = set(game['side_a']['members']+game['side_b']['members'])
        user = next(p['user_id'] for p in event['participants'] if p['id'] not in playing and p['user_id'] != '1001')
        reply = self.send('周一活动，第1场 甲方胜', user, 1)
        self.assertIn('胜 / 负', reply)
        with self.assertRaises(PermissionError):
            self.send('周一活动，第1场 乙方胜', user)
        with self.assertRaises(PermissionError):
            self.send('周一活动，第2场 甲方胜', '1002')
        self.send('周一活动，第1场乙方胜')
        final = self.events.get(event['id'])
        self.assertEqual(final['matches'][0]['scores'], [])
        self.assertEqual(final['matches'][0]['winner'], game['side_b']['id'])

    def test_compact_daily_table_and_result_history(self):
        event = self.create('round_robin')
        with connect(self.ctx.db_path, True) as db:
            db.execute('UPDATE tour_participants SET level=7 WHERE tournament_id=?', (event['id'],))
        reply = self.send('周一活动，对战表')
        self.assertIn('| 场次 | 甲方 | 状态 | 乙方 |', reply)
        self.assertNotIn('7级', reply)
        self.assertNotIn('时间 / 球场', reply)
        self.assertIn('未开始', reply)
        self.send('周一活动，第1场胜')
        from app.casual import CasualMatches
        with connect(self.ctx.db_path) as db:
            history = CasualMatches(self.contexts[0], self.clock)._formal(db)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['scores'], [])

    def test_cancel_activity_owner_or_admin_and_preserves_signups(self):
        event = self.create()
        before = self.s.registrations()['registrations']
        with self.assertRaises(PermissionError):
            self.send('取消活动', '1')
        self.assertIn('已取消', self.send('取消活动', bot=1))
        self.assertEqual(before, self.s.registrations()['registrations'])
        self.assertEqual(self.events.list(), [])
        self.assertNotEqual(self.create()['id'], event['id'])

    def test_exclude_by_name_and_web_id_without_cancelling_booking(self):
        self.send('组织比赛 双打 周一活动，不参与 球友1，球友2')
        self.send('对战序列')
        event = self.events.get(self.events.list()[0]['id'])
        self.assertEqual(event['participant_count'], 6)
        self.assertEqual(len(self.s.registrations()['registrations']), 8)
        self.assertFalse({'1', '2'} & set(event['participant_user_ids']))
        self.now = self.now.replace(hour=18)
        self.send('第一场报名', '1002', 1)
        self.assertFalse({'1', '2'} & set(self.events.get(event['id'])['participant_user_ids']))

    def test_dynamic_enroll_and_withdraw_keep_completed_rows_exactly(self):
        event = self.create()
        self.send('周一活动，第1场21:19')
        saved = self.events.get(event['id'])['matches'][0]
        stale = event['matches'][1]
        self.now = self.now.replace(hour=18)
        self.send('第一场报名', '1002', 1)
        joined = self.events.get(event['id'])
        self.assertEqual(joined['participant_count'], 9)
        self.assertEqual(next(m for m in joined['matches'] if m['id'] == saved['id']), saved)
        with self.assertRaises(ValueError):
            self.events.score(event['id'], stale['id'], [[21, 19]], stale['version'])
        self.send('球友1002退出')
        left = self.events.get(event['id'])
        self.assertEqual(left['participant_count'], 8)
        self.assertEqual(next(m for m in left['matches'] if m['id'] == saved['id']), saved)
        self.assertFalse(any('1002:0' in (m['side_a']['members']+m['side_b']['members']) for m in left['matches'] if m['status'] != 'completed'))
        self.send('第一场报名', '1002')
        self.assertEqual(self.events.get(event['id'])['participant_count'], 8)
        self.assertTrue(any(h['action'] == 'sequence.withdraw' for h in left['history']))

    def test_parallel_join_across_bots_and_explicit_self_exit(self):
        event = self.create()
        self.now = self.now.replace(hour=18)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda item: self.send('第一场报名', *item), [('1002', 0), ('1003', 1)]))
        self.assertEqual(len(results), 2)
        self.assertEqual(self.events.get(event['id'])['participant_count'], 10)
        with self.assertRaises(PermissionError):
            self.send('球友1002退出', '1003')
        self.send('我退出', '1003')
        self.assertEqual(self.events.get(event['id'])['participant_count'], 9)

    def test_live_roster_update_serves_unplayed_members_before_coverage(self):
        event = self.create()
        waiting = set(event['participant_user_ids'])
        while waiting:
            current = self.events.get(event['id'])
            game = next(m for m in current['matches'] if m['status'] == 'ready'
                        and waiting & {p['user_id'] for p in current['participants']
                                       if p['id'] in m['side_a']['members']+m['side_b']['members']})
            self.events.score(event['id'], game['id'], 'a', game['version'])
            waiting -= {p['user_id'] for p in current['participants']
                        if p['id'] in game['side_a']['members']+game['side_b']['members']}
        completed = [m for m in self.events.get(event['id'])['matches'] if m['status'] == 'completed']
        self.now = self.now.replace(hour=18)
        for user, bot in (('1002', 1), ('1003', 0)):
            self.send('第一场报名', user, bot)
        updated = self.events.get(event['id'])
        self.assertEqual([m for m in updated['matches'] if m['status'] == 'completed'], completed)
        ready = [m for m in updated['matches'] if m['status'] == 'ready']
        first_ids = set(ready[0]['side_a']['members']+ready[0]['side_b']['members'])
        self.assertTrue({'1002:0', '1003:0'} <= first_ids)
        self.send('球友1退出')
        updated = self.events.get(event['id'])
        self.assertEqual([m for m in updated['matches'] if m['status'] == 'completed'], completed)
        ready = [m for m in updated['matches'] if m['status'] == 'ready']
        self.assertTrue({'1002:0', '1003:0'} <= set(ready[0]['side_a']['members']+ready[0]['side_b']['members']))

    def test_regular_daily_draw_does_not_auto_add(self):
        event = self.create('round_robin')
        event = self.events.get(event['id'])
        self.now = self.now.replace(hour=18)
        self.send('第一场报名', '1002')
        self.assertEqual(self.events.get(event['id']), event)

    def test_waiting_after_departures_then_recovers_on_join(self):
        event = self.create(excluded_participant_ids=['4:0', '5:0', '6:0', '9:0'])
        self.send('球友1退出')
        waiting = self.events.get(event['id'])
        self.assertTrue(waiting['plan']['sequence']['waiting'])
        self.now = self.now.replace(hour=18)
        self.send('第一场报名', '1002')
        self.assertTrue(self.events.get(event['id'])['matches'])

    def test_http_outcomes_and_withdraw(self):
        event = self.create()
        with TestClient(create_app(self.temp.name, TOKEN, False, False, self.clock)) as client:
            headers = {'Authorization': 'Bearer '+TOKEN}
            result = client.post(f"/api/tournaments/{event['id']}/scores", headers=headers,
                                 json={'match_id': event['matches'][0]['id'], 'outcome': 'b', 'expected_version': 0})
            self.assertEqual(result.status_code, 200, result.text)
            result = client.post(f"/api/tournaments/{event['id']}/withdraw", headers=headers,
                                 json={'names': ['球友1'], 'expected_version': result.json()['version']})
            self.assertEqual(result.status_code, 200, result.text)

    def test_completed_batch_rotates_partners_and_finishes_without_fake_scores(self):
        event = self.create(excluded_participant_ids=['4:0', '5:0', '6:0', '9:0'])
        seen = set()
        for _ in range(20):
            current = self.events.get(event['id'])
            if current['status'] == 'completed':
                break
            game = next(m for m in current['matches'] if m['status'] == 'ready')
            self.assertNotIn(contest_key(game), seen)
            seen.add(contest_key(game))
            self.events.score(event['id'], game['id'], 'a', game['version'])
        else:
            self.fail('Daily sequence did not finish')
        self.assertEqual(len(seen), 3)
        self.assertTrue(all(m['scores'] == [] for m in current['matches']))
        self.assertEqual(sum(r['played'] for r in current['standings']), 12)

    def test_named_cancel_and_disabled_enrollment_capability(self):
        self.create()
        self.platform.update(self.admin, self.bots[0]['id'], {'capabilities': ['daily_events']})
        self.assertIn('已取消', self.send('周一活动，取消活动'))

    def test_ai_plans_exclude_score_and_withdraw_through_the_same_permissions(self):
        from app.ai_plan_flow import Plans
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'local-fake-ai-key'}})
        commands = Commands(self.contexts[0], self.clock)
        scope = {'bot_id': commands.ctx.id, 'chat_id': commands.ctx.chat_id, 'user_id': commands.ctx.actor}
        def execute(steps):
            reply = asyncio.run(Plans(commands).propose({'intent': 'BusinessPlan', 'steps': steps,
                                                       'requires_confirmation': True, 'scope': scope}))
            token = self.pending_match(reply, commands.ctx)[1]
            return asyncio.run(Plans(commands).confirm(token))
        execute([{'operation': 'StartDailyPlan', 'arguments': {'venue_id': 1, 'date': '2026-10-12', 'number': 1,
                    'discipline': 'doubles', 'doubles_category': 'open', 'assignment_strategy': 'balanced',
                    'title': '周一活动', 'excluded_names': ['球友1']}},
                 {'operation': 'DailyPlan', 'arguments': {'venue_id': 1, 'format': 'match_sequence'}}])
        event = self.events.get(self.events.list()[0]['id'])
        self.assertEqual(event['participant_count'], 7)
        execute([{'operation': 'RecordScore', 'arguments': {'venue_id': 1, 'tournament_name': '周一活动',
                    'tournament_id': event['id'], 'match_id': 1, 'scores': [], 'outcome': 'b'}}])
        self.assertEqual(self.events.get(event['id'])['matches'][0]['scores'], [])
        execute([{'operation': 'EventAction', 'arguments': {'venue_id': 1, 'tournament_name': '周一活动',
                    'tournament_id': event['id'], 'action': 'withdraw', 'names': ['球友2']}}])
        self.assertEqual(self.events.get(event['id'])['participant_count'], 6)

