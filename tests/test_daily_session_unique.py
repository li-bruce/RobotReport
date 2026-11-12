"""One shared daily competition per activity, with authorized explicit deletion."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.commands import Commands
from app.competitions import DailySessionConflict
from app.db import connect
from app.main import create_app
from app.planning import DailyPlanning
from app.service import Service
from app.tournaments import Tournaments
from tests.test_core import CoreFixture, TOKEN


class DailySessionUniqueTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.channels = [bot['channels'][0]['id'] for bot in self.bots]
        self.contexts = [self.platform.context_for_channel(channel, '1001', False, 'callback') for channel in self.channels]
        self.events = [Tournaments(ctx, self.clock) for ctx in self.contexts]
        for index, user in enumerate(('1001', '1', '2', '3', '4', '5', '6', '9')):
            ctx = self.platform.context_for_channel(self.channels[index % 2], user, False, 'callback')
            Service(ctx, self.clock).enroll(user, 'ignored')
        self.session = self.s.sessions()[0]
        patcher = patch('app.competitions._now', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, index, text, user='1001'):
        ctx = self.platform.context_for_channel(self.channels[index], user, False, 'callback')
        return asyncio.run(Commands(ctx, self.clock).run(text))

    def create(self, index=0, **changes):
        return self.events[index].create({'kind': 'daily', 'format': 'round_robin', 'title': '联合双打',
                                          'session_id': self.session['id'], **changes})

    def test_concurrent_bots_and_organizers_cannot_create_twice(self):
        other = self.platform.context_for_channel(self.channels[1], '1', False, 'callback')
        engines = [self.events[0], Tournaments(other, self.clock)]
        def attempt(i):
            try:
                return engines[i % 2].create({'title': '并发赛事' + str(i), 'session_id': self.session['id']})
            except DailySessionConflict as exc:
                return exc
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual(sum(isinstance(r, DailySessionConflict) for r in results), 11)
        self.assertEqual(len(self.events[0].list()), 1)

    def test_existing_event_blocks_every_robot_and_preserves_plan_and_scores(self):
        for index in (0, 1):
            self.send(index, '组织比赛 双打 联合双打')
        self.send(0, '积分赛')
        original = self.events[0].list()[0]
        self.send(0, '联合双打，第1场21:19')
        saved = self.events[0].get(original['id'])
        reply = self.send(1, '积分赛')
        self.assertIn('本场次已有', reply)
        self.assertIn('联合双打', reply)
        self.assertIn('对战表', reply)
        self.assertIn('本场次已有', self.send(1, '组织比赛 双打 替换赛事', '1'))
        self.assertEqual(self.events[1].get(original['id']), saved)
        with self.assertRaises(DailySessionConflict):
            self.create(1, title='不同赛制也不能替换', format='single_elimination')

    def test_paused_closed_completed_and_archived_all_keep_session_occupied(self):
        event = self.create()
        for action in ('pause', 'close', 'archive'):
            self.events[0].transition(event['id'], action)
            with self.assertRaises(DailySessionConflict):
                self.create(1)
        with connect(self.ctx.db_path, True) as db:
            db.execute("UPDATE tour_events SET status='completed' WHERE id=?", (event['id'],))
        with self.assertRaises(DailySessionConflict):
            self.create(1)

    def test_organizer_deletes_through_other_bot_then_rebuilds_without_losing_enrollments(self):
        event = self.create()
        registrations = self.s.registrations()['registrations']
        reply = self.send(1, '联合双打，删除比赛')
        self.assertIn('已删除', reply)
        self.assertEqual(self.events[0].list(), [])
        self.assertEqual(self.s.registrations()['registrations'], registrations)
        with self.assertRaisesRegex(ValueError, '已删除'):
            self.events[0].get(event['id'])
        for mutation in (lambda: self.events[0].engine.transition(event['id'], 'resume', '1001', False),
                         lambda: self.events[0].engine.update(event['id'], {'title': '恢复旧赛'}, '1001', False)):
            with self.assertRaisesRegex(ValueError, '已删除'):
                mutation()
        rebuilt = self.create(1)
        self.assertNotEqual(rebuilt['id'], event['id'])
        self.assertEqual(len(rebuilt['participants']), 8)
        with connect(self.ctx.db_path) as db:
            self.assertGreater(db.execute('SELECT count(*) FROM tour_matches WHERE tournament_id=?', (event['id'],)).fetchone()[0], 0)
            audit = db.execute("SELECT actor,after_json FROM tour_audit WHERE tournament_id=? AND action='delete'", (event['id'],)).fetchone()
            self.assertEqual(audit['actor'], '1001')
            self.assertIn('deleted', audit['after_json'])

    def test_participant_and_unrelated_leader_cannot_delete(self):
        event = self.create(options={'leader_user_ids': ['1'], 'scoring_mode': 'leader'})
        for user in ('1', '2', 'other'):
            with self.assertRaises(PermissionError):
                self.send(1, '联合双打，删除比赛', user)
        self.assertEqual(self.events[0].get(event['id'])['status'], 'running')

    def test_admin_delete_requires_current_version(self):
        event = self.create()
        manager = Tournaments(self.ctx, self.clock)
        with self.assertRaisesRegex(ValueError, '已更新'):
            manager.delete(event['id'], event['version'] - 1)
        with self.assertRaisesRegex(ValueError, '已更新'):
            manager.delete(event['id'], None)
        self.assertEqual(manager.delete(event['id'], event['version'])['status'], 'deleted')

    def test_late_enrollment_keeps_draw_and_scores_until_organizer_rebuilds(self):
        self.s.save_session_constraints(self.session['id'], {'capacity':12})
        event=self.create()
        self.send(0,'联合双打，第1场21:19')
        original=self.events[0].get(event['id'])
        self.now=self.now.replace(hour=18)
        for index,user in enumerate(('1002','1003')):
            reply=self.send(index,'第一场 报名',user)
            self.assertIn('报名成功',reply)
            self.assertIn('本场已有赛程，本次报名不加入现有比赛',reply)
        self.assertEqual(self.events[0].get(event['id']),original)
        with self.assertRaisesRegex(ValueError,'不在报名阶段'):
            self.events[0].engine.enroll(event['id'],'1002','球友1002',now=self.now)
        with self.assertRaises(PermissionError): self.send(1,'联合双打，删除比赛','1002')
        self.send(1,'联合双打，删除比赛')
        rebuilt=self.create(1)
        self.assertEqual(len(rebuilt['participants']),10)
        self.assertTrue({'1002','1003'} <= {p['user_id'] for p in rebuilt['participants']})
        self.assertNotIn('本场已有赛程',self.send(1,'第一场 报名','1002'))
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute('SELECT status FROM tour_events WHERE id=?',(event['id'],)).fetchone()[0],'deleted')
            self.assertGreater(db.execute('SELECT count(*) FROM tour_matches WHERE tournament_id=?',(event['id'],)).fetchone()[0],0)

    def test_different_sessions_and_dates_remain_independent(self):
        first = self.create()
        second = self.s.sessions()[1]
        for user in ('1001', '1', '2', '3'):
            self.s.enroll(user, 'ignored', session_id=second['id'])
            self.s.enroll(user, 'ignored', day='2026-10-13')
        next_session = self.events[1].create({'title': '第二场', 'session_id': second['id']})
        next_day = self.events[1].create({'title': '明天场', 'date': '2026-10-13'})
        self.assertEqual(len({first['id'], next_session['id'], next_day['id']}), 3)

    def test_stale_created_draft_cannot_resurrect_deleted_event(self):
        self.send(0, '组织比赛 双打 联合双打')
        self.send(0, '积分赛')
        self.send(1, '联合双打，删除比赛')
        self.assertIn('原比赛已删除', self.send(0, '积分赛'))
        self.assertEqual(self.events[0].list(), [])
        self.assertIn('每日策划', self.send(1, '组织比赛 双打 新比赛'))
        self.assertIn('VS', self.send(1, '积分赛'))

    def test_delete_scope_and_large_event_protection(self):
        event = self.create()
        self.platform.update(self.admin, self.bots[1]['id'], {'slot_keys': ['slot:2']})
        with self.assertRaises(PermissionError):
            self.events[1].delete(event['id'], event['version'])
        manager = Tournaments(self.ctx, self.clock)
        large = manager.create({'kind': 'large', 'title': '大型赛事', 'keyword': '联赛', 'date': '2026-10-14'})
        with self.assertRaises(PermissionError):
            manager.delete(large['id'], large['version'])

    def test_http_creation_and_delete_use_same_rules(self):
        with TestClient(create_app(self.temp.name, TOKEN, False, False, self.clock)) as client:
            headers = {'Authorization': 'Bearer ' + TOKEN}
            original = self.create()
            response = client.post('/api/tournaments', headers=headers, json={'title': '重复', 'session_id': self.session['id']})
            self.assertEqual(response.status_code, 400)
            self.assertIn('本场次已有', response.text)
            response = client.request('DELETE', '/api/tournaments/' + str(original['id']), headers=headers, json={'expected_version': original['version']})
            self.assertEqual(response.status_code, 200, response.text)
            response = client.post('/api/tournaments', headers=headers, json={'title': '重新组织', 'session_id': self.session['id']})
            self.assertEqual(response.status_code, 200, response.text)

    def test_same_planner_retry_returns_same_event(self):
        self.send(0, '组织比赛 双打 联合双打')
        self.send(0, '积分赛')
        self.assertIn('已生成赛事', self.send(0, '积分赛'))
        self.assertEqual(len(self.events[0].list()), 1)

    def test_deleted_scores_are_not_in_personal_history(self):
        from app.casual import CasualMatches
        event = self.create()
        self.send(0, '联合双打，第1场21:19')
        records = CasualMatches(self.contexts[0], self.clock)
        with connect(self.ctx.db_path) as db:
            self.assertEqual(len(records._formal(db)), 1)
        latest = self.events[0].get(event['id'])
        self.events[0].delete(event['id'], latest['version'])
        with connect(self.ctx.db_path) as db:
            self.assertEqual(records._formal(db), [])
