"""Execution receipts prove isolation, one-time confirmation and no crash replay."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from app.ai_plan_store import AiPlanStore
from app.db import TZ, connect
from app.security import BotContext


class AiPlanStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.at = datetime(2026, 10, 13, 16, tzinfo=TZ)
        self.clock = lambda: self.at
        self.ctx = BotContext('bot1', self.path, 'group1', 'member1', False, 'callback',
                              transport_path=self.path / 'transport.sqlite3')
        self.store = AiPlanStore(self.ctx, self.clock)

    def create(self, count=2, store=None):
        return (store or self.store).create(
            [{'operation': 'enroll', 'arguments': {'date': '2026-10-14', 'number': n + 1}} for n in range(count)],
            {'version': 3, 'member': 'approved'}, 7, self.at.astimezone(TZ).date().isoformat())

    def claim(self, plan):
        return self.store.claim(plan['token'], policy_epoch=7, request_day='2026-10-13',
                                snapshot={'version': 3, 'member': 'approved'})

    def pending_status(self, token):
        with connect(self.ctx.transport_db_path) as db:
            return db.execute('SELECT status FROM ai_pending WHERE token=?', (token,)).fetchone()[0]

    def test_create_is_scoped_durable_pending_and_v4_pointer(self):
        plan = self.create()
        self.assertRegex(plan['token'], r'^[a-f0-9]{12}$')
        self.assertEqual(plan['id'], plan['token'])
        self.assertEqual(plan['status'], 'pending')
        self.assertEqual(plan['expires_at'], '2026-10-13T16:05:00+08:00')
        self.assertEqual([s['status'] for s in plan['steps']], ['pending', 'pending'])
        with connect(self.ctx.transport_db_path) as db:
            pointer = json.loads(db.execute('SELECT command FROM ai_pending WHERE token=?', (plan['id'],)).fetchone()[0])
        self.assertEqual(pointer, {'version': 5, 'plan_id': plan['id']})
        reloaded = AiPlanStore(self.ctx, self.clock).get(plan['id'])
        self.assertEqual(reloaded, plan)
        plan['steps'][0]['arguments']['number'] = 99
        self.assertEqual(self.store.get(plan['id'])['steps'][0]['arguments']['number'], 1)

    def test_identity_scope_applies_to_every_store_method(self):
        plan = self.create()
        for override in ({'actor': 'other'}, {'chat_id': 'other'}, {'id': 'other'}, {'scope_id': 'other'}):
            other = AiPlanStore(replace(self.ctx, **override), self.clock)
            for call in (lambda: other.get(plan['id']), lambda: other.claim(plan['id']),
                         lambda: other.cancel(plan['id']), lambda: other.start_step(plan['id'], 0),
                         lambda: other.finish_step(plan['id'], 0, '完成'),
                         lambda: other.fail_step(plan['id'], 0, '拒绝'),
                         lambda: other.abort(plan['id'], '拒绝'), lambda: other.finish(plan['id'])):
                with self.subTest(override=override), self.assertRaisesRegex(ValueError, '不属于'):
                    call()
        self.assertEqual(self.store.get(plan['id'])['status'], 'pending')

    def test_confirmation_is_consumed_once_even_concurrently(self):
        plan = self.create()
        def claim():
            try:
                return self.store.claim(plan['id'])['status']
            except ValueError:
                return 'rejected'
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: claim(), range(12)))
        self.assertEqual(results.count('running'), 1)
        self.assertEqual(results.count('rejected'), 11)
        self.assertEqual(self.pending_status(plan['id']), 'consuming')

    def test_five_minute_boundary_expires_and_commits_refusal(self):
        plan = self.create()
        self.at += timedelta(minutes=5)
        with self.assertRaisesRegex(ValueError, '过期'):
            self.claim(plan)
        self.assertEqual(self.store.get(plan['id'])['status'], 'expired')
        self.assertEqual(self.pending_status(plan['id']), 'expired')
        self.assertTrue(all(s['status'] == 'skipped' for s in self.store.get(plan['id'])['steps']))

    def test_china_day_boundary_expires_before_five_minutes(self):
        self.at = datetime(2026, 10, 13, 15, 59, tzinfo=timezone.utc)
        plan = self.create()
        self.at += timedelta(minutes=1)
        self.assertEqual(self.store.get(plan['id'])['status'], 'expired')

    def test_expired_cancel_cannot_undo_or_keep_pending(self):
        plan = self.create()
        self.at += timedelta(minutes=6)
        with self.assertRaises(ValueError):
            self.store.cancel(plan['id'])
        self.assertEqual(self.store.get(plan['id'])['status'], 'expired')

    def test_confirmation_guards_change_is_terminal_and_never_starts(self):
        for guards in ({'policy_epoch': 8}, {'request_day': '2026-10-14'}, {'snapshot': {'version': 4}}):
            plan = self.create()
            with self.subTest(guards=guards), self.assertRaisesRegex(ValueError, '变化'):
                self.store.claim(plan['id'], **guards)
            failed = self.store.get(plan['id'])
            self.assertEqual(failed['status'], 'failed')
            self.assertIsNone(failed['started_at'])
            self.assertEqual(self.pending_status(plan['id']), 'failed')

    def test_cancel_is_single_use_and_never_changes_running_plan(self):
        plan = self.create()
        cancelled = self.store.cancel(plan['id'])
        self.assertEqual(cancelled['status'], 'cancelled')
        with self.assertRaises(ValueError):
            self.store.claim(plan['id'])
        with self.assertRaises(ValueError):
            self.store.cancel(plan['id'])
        active = self.create()
        self.claim(active)
        with self.assertRaises(ValueError):
            self.store.cancel(active['id'])
        self.assertEqual(self.store.get(active['id'])['status'], 'running')

    def test_new_proposal_supersedes_only_pending_same_identity(self):
        other_store = AiPlanStore(replace(self.ctx, actor='other'), self.clock)
        other = self.create(store=other_store)
        first = self.create()
        second = self.create()
        self.assertEqual(self.store.get(first['id'])['status'], 'cancelled')
        self.assertEqual(other_store.get(other['id'])['status'], 'pending')
        self.claim(second)
        third = self.create()
        self.assertEqual(self.store.get(second['id'])['status'], 'running')
        self.assertEqual(self.store.get(third['id'])['status'], 'pending')

    def test_legacy_single_step_superseding_is_respected(self):
        plan = self.create()
        with connect(self.ctx.transport_db_path, True) as db:
            db.execute("UPDATE ai_pending SET status='superseded' WHERE token=?", (plan['id'],))
        self.assertEqual(self.store.get(plan['id'])['status'], 'cancelled')
        with self.assertRaises(ValueError):
            self.claim(plan)

    def test_ordered_success_persists_each_receipt_and_completes_only_once(self):
        plan = self.create()
        token = plan['id']
        self.claim(plan)
        with self.assertRaisesRegex(ValueError, '順序|顺序'):
            self.store.start_step(token, 1)
        with self.assertRaises(ValueError):
            self.store.finish(token)
        for index in range(2):
            self.store.start_step(token, index)
            receipt = self.store.finish_step(token, index, f'第{index + 1}场报名成功')
            self.assertEqual(receipt['status'], 'done')
            self.assertIsNotNone(receipt['started_at'])
            self.assertIsNotNone(receipt['finished_at'])
        result = self.store.finish(token)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.pending_status(token), 'consumed')
        self.assertTrue(all(s['result'] for s in result['steps']))
        with self.assertRaises(ValueError):
            self.store.finish(token)
        with self.assertRaises(ValueError):
            self.store.finish_step(token, 0, '覆盖原结果')

    def test_crash_does_not_replay_or_invent_success(self):
        plan = self.create()
        token = plan['id']
        self.claim(plan)
        self.store.start_step(token, 0)
        # A real domain write may have committed at this point. Reopening the
        # transport store must not silently assume failure and replay it.
        restarted = AiPlanStore(self.ctx, self.clock)
        self.at += timedelta(minutes=10)
        self.assertEqual(restarted.get(token)['status'], 'running')
        self.assertEqual(restarted.get(token)['steps'][0]['status'], 'running')
        for call in (lambda: restarted.claim(token), lambda: restarted.start_step(token, 0),
                     lambda: restarted.start_step(token, 1), lambda: restarted.finish(token),
                     lambda: restarted.abort(token, '重启恢复')):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(restarted.get(token)['steps'][0]['result'], '')

    def test_first_step_failure_stops_remaining_without_false_success(self):
        plan = self.create(3)
        self.claim(plan)
        self.store.start_step(plan['id'], 0)
        result = self.store.fail_step(plan['id'], 0, '名额已满')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([s['status'] for s in result['steps']], ['failed', 'skipped', 'skipped'])
        self.assertEqual(result['steps'][0]['error'], '名额已满')
        self.assertFalse(any(s['result'] for s in result['steps']))
        with self.assertRaises(ValueError):
            self.store.start_step(plan['id'], 1)

    def test_partial_failure_preserves_done_receipts_and_skips_remainder(self):
        plan = self.create(3)
        token = plan['id']
        self.claim(plan)
        self.store.start_step(token, 0)
        self.store.finish_step(token, 0, '第一场已报名')
        # Revalidation may fail before the next step begins.
        result = self.store.fail_step(token, 1, '会员已到期，第二场未报名')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([s['status'] for s in result['steps']], ['done', 'failed', 'skipped'])
        self.assertEqual(result['steps'][0]['result'], '第一场已报名')
        self.assertEqual(self.pending_status(token), 'failed')
        with self.assertRaises(ValueError):
            self.store.finish(token)

    def test_abort_preflight_marks_remaining_steps_skipped(self):
        plan = self.create()
        self.claim(plan)
        result = self.store.abort(plan['id'], '审批权限已撤销')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([s['status'] for s in result['steps']], ['skipped', 'skipped'])

    def test_invalid_inputs_do_not_supersede_valid_pending_plan(self):
        plan = self.create()
        for steps in ([], [{}], ['任意执行代码'], [{'x': 1}] * 17):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                self.store.create(steps, {}, 7, '2026-10-13')
        self.assertEqual(self.store.get(plan['id'])['status'], 'pending')
        for token in ('', '../file', 'F' * 12, 123):
            with self.assertRaises(ValueError):
                self.store.get(token)

    def test_step_completion_requires_running_and_cannot_overwrite_receipt(self):
        plan = self.create()
        token = plan['id']
        with self.assertRaises(ValueError):
            self.store.start_step(token, 0)
        self.claim(plan)
        for index in (-1, True, '0', 99):
            with self.assertRaises(ValueError):
                self.store.start_step(token, index)
        with self.assertRaises(ValueError):
            self.store.finish_step(token, 0, '伪造成功')
        with self.assertRaises(ValueError):
            self.store.fail_step(token, 1, '越过第一步')
        self.store.start_step(token, 0)
        self.store.finish_step(token, 0, '真实结果')
        for call in (lambda: self.store.start_step(token, 0),
                     lambda: self.store.finish_step(token, 0, '更改结果'),
                     lambda: self.store.fail_step(token, 0, '更改结果')):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(self.store.get(token)['steps'][0]['result'], '真实结果')


if __name__ == '__main__':
    unittest.main()
