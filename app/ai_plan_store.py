"""Scoped, single-use AI plans and durable per-step execution receipts.

This module never interprets or executes a business operation. The caller must
validate operations, produce an isolated preview, and recheck current permission
before every step. A committed ``running`` step is deliberately not replayable:
after a process interruption its effect may already have reached the business DB.
"""
from datetime import date, timedelta
from contextlib import nullcontext
import json
import re
import secrets

from .db import TZ, connect, dumps, now, stamp


_UNSET = object()
_TOKEN = re.compile(r'[a-f0-9]{12}')
_MAX_STEPS = 16
_MAX_DETAIL = 20000
_SCOPE_ERROR = '计划不存在或不属于当前机器人、群和用户'
PLAN_VERSION = 5


class AiPlanStore:
    """Persist normalized operations; indexes are zero-based, dates use China time."""

    def __init__(self, ctx, clock=now):
        self.ctx = ctx
        self.clock = clock
        with connect(ctx.transport_db_path, True) as db:
            # This compatible definition also supports an empty transport DB in
            # tools/tests. Existing ai_pending rows and older payloads are kept.
            db.execute('''CREATE TABLE IF NOT EXISTS ai_pending(
                token TEXT PRIMARY KEY,bot_id TEXT NOT NULL,chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,command TEXT NOT NULL,expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending')''')
            db.execute('''CREATE TABLE IF NOT EXISTS ai_plans(
                id TEXT PRIMARY KEY,bot_id TEXT NOT NULL,chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,scope_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',snapshot TEXT NOT NULL,
                policy_epoch TEXT NOT NULL,request_day TEXT NOT NULL,
                created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
                started_at TEXT,finished_at TEXT,error TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(id) REFERENCES ai_pending(token))''')
            db.execute('''CREATE INDEX IF NOT EXISTS ai_plan_owner
                ON ai_plans(bot_id,chat_id,user_id,status)''')
            db.execute('''CREATE TABLE IF NOT EXISTS ai_plan_steps(
                plan_id TEXT NOT NULL REFERENCES ai_plans(id),step_index INTEGER NOT NULL,
                operation TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
                result TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',
                started_at TEXT,finished_at TEXT,PRIMARY KEY(plan_id,step_index))''')

    def _at(self):
        return self.clock().astimezone(TZ)

    def _scope(self):
        return (self.ctx.id, self.ctx.chat_id, self.ctx.actor, self.ctx.scope_id)

    def _row(self, db, token):
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ValueError(_SCOPE_ERROR)
        row = db.execute('''SELECT * FROM ai_plans WHERE id=? AND bot_id=?
            AND chat_id=? AND user_id=? AND scope_id=?''', (token, *self._scope())).fetchone()
        if not row:
            raise ValueError(_SCOPE_ERROR)
        return row

    def _record(self, db, token):
        row = self._row(db, token)
        result = dict(row)
        result['token'] = result['id']
        result['snapshot'] = json.loads(result['snapshot'])
        result['policy_epoch'] = json.loads(result['policy_epoch'])
        result['steps'] = []
        for step in db.execute('SELECT * FROM ai_plan_steps WHERE plan_id=? ORDER BY step_index', (token,)):
            item = dict(step)
            item['index'] = item.pop('step_index')
            payload = json.loads(item['operation'])
            item['operation'] = payload['operation']
            item['arguments'] = payload['arguments']
            result['steps'].append(item)
        return result

    def _terminal(self, db, token, status, error, at):
        pending_status = {'completed': 'consumed', 'partial': 'failed'}.get(status, status)
        db.execute('UPDATE ai_plans SET status=?,error=?,finished_at=? WHERE id=?',
                   (status, error, stamp(at), token))
        db.execute('UPDATE ai_pending SET status=? WHERE token=?', (pending_status, token))
        if status != 'completed':
            db.execute("UPDATE ai_plan_steps SET status='skipped',finished_at=? WHERE plan_id=? AND status='pending'",
                       (stamp(at), token))

    def _sync_pending(self, db, token, at):
        row = self._row(db, token)
        if row['status'] != 'pending':
            return row
        pending = db.execute('SELECT status,command FROM ai_pending WHERE token=?', (token,)).fetchone()
        if pending['status'] != 'pending':
            status = 'expired' if pending['status'] == 'expired' else 'cancelled'
            self._terminal(db, token, status, '提案已被替换或取消，未执行任何步骤', at)
        elif json.loads(pending['command']) != {'version': PLAN_VERSION, 'plan_id': token}:
            self._terminal(db, token, 'failed', '报名与取消规则已更新，旧提案未执行，请重新提出操作', at)
        elif row['expires_at'] <= stamp(at) or row['request_day'] != at.date().isoformat():
            self._terminal(db, token, 'expired', '确认已过期或日期已变化，请重新提出操作', at)
        return self._row(db, token)

    @staticmethod
    def _detail(value):
        if not isinstance(value, str):
            raise ValueError('步骤结果必须为文字摘要')
        if len(value) > _MAX_DETAIL:
            return value[:_MAX_DETAIL] + '\n（其余内容请在业务记录中查看）'
        return value

    def create(self, steps, snapshot, policy_epoch, request_day, minutes=5, _connection=None):
        """Create a bounded confirmation; no operation has been executed."""
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise ValueError('确认有效期须为1至1440分钟')
        if not isinstance(steps, list) or not 1 <= len(steps) <= _MAX_STEPS:
            raise ValueError(f'计划需要包含1至{_MAX_STEPS}个步骤')
        if any(not isinstance(item, dict) or set(item) != {'operation', 'arguments'}
               or not isinstance(item['operation'], str) or not item['operation'].strip()
               or not isinstance(item['arguments'], dict) for item in steps):
            raise ValueError('计划步骤必须为已校验的业务操作')
        if not isinstance(snapshot, dict):
            raise ValueError('计划需要业务状态快照')
        at = self._at()
        if not isinstance(request_day, str) or date.fromisoformat(request_day).isoformat() != at.date().isoformat():
            raise ValueError('计划日期已变化，请重新提出操作')
        encoded_steps = [dumps(item) for item in steps]
        encoded_snapshot, encoded_epoch = dumps(snapshot), dumps(policy_epoch)
        if sum(map(len, encoded_steps)) + len(encoded_snapshot) > 250000:
            raise ValueError('计划内容过长，请缩小操作范围')
        token = secrets.token_hex(6)
        expires = stamp(at + timedelta(minutes=minutes))
        with nullcontext(_connection) if _connection is not None else connect(self.ctx.transport_db_path, True) as db:
            for previous in db.execute('''SELECT id FROM ai_plans WHERE bot_id=? AND chat_id=?
                AND user_id=? AND scope_id=? AND status='pending' ''', self._scope()).fetchall():
                self._terminal(db, previous['id'], 'cancelled', '已被新的待确认提案替换，未执行任何步骤', at)
            db.execute("UPDATE ai_pending SET status='superseded' WHERE bot_id=? AND chat_id=? AND user_id=? AND status='pending'",
                       self._scope()[:3])
            db.execute('INSERT INTO ai_pending(token,bot_id,chat_id,user_id,command,expires_at) VALUES(?,?,?,?,?,?)',
                       (token, *self._scope()[:3], dumps({'version': PLAN_VERSION, 'plan_id': token}), expires))
            db.execute('''INSERT INTO ai_plans(id,bot_id,chat_id,user_id,scope_id,snapshot,policy_epoch,
                request_day,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                       (token, *self._scope(), encoded_snapshot, encoded_epoch, request_day, stamp(at), expires))
            db.executemany('INSERT INTO ai_plan_steps(plan_id,step_index,operation) VALUES(?,?,?)',
                           [(token, index, operation) for index, operation in enumerate(encoded_steps)])
            result = self._record(db, token)
        return result

    def get(self, token):
        """Read only the invoking bot/chat/actor's plan; pending expiry is persisted."""
        with connect(self.ctx.transport_db_path, True) as db:
            self._sync_pending(db, token, self._at())
            return self._record(db, token)

    def claim(self, token, *, policy_epoch=_UNSET, request_day=_UNSET, snapshot=_UNSET):
        """Atomically consume confirmation. Callers may supply freshly checked guards."""
        error = None
        at = self._at()
        with connect(self.ctx.transport_db_path, True) as db:
            row = self._sync_pending(db, token, at)
            if row['status'] != 'pending':
                error = row['error'] or '确认已使用，计划不能重复执行；请查询执行进度'
            elif ((policy_epoch is not _UNSET and json.loads(row['policy_epoch']) != policy_epoch)
                  or (request_day is not _UNSET and row['request_day'] != request_day)
                  or (snapshot is not _UNSET and json.loads(row['snapshot']) != snapshot)):
                error = '业务配置、权限或数据状态已变化，请重新提出操作'
                self._terminal(db, token, 'failed', error, at)
            else:
                db.execute("UPDATE ai_plans SET status='running',started_at=? WHERE id=?", (stamp(at), token))
                db.execute("UPDATE ai_pending SET status='consuming' WHERE token=?", (token,))
            result = self._record(db, token)
        if error:
            raise ValueError(error)
        return result

    def _step(self, db, token, index):
        row = self._row(db, token)
        if row['status'] != 'running':
            raise ValueError('计划未在执行，不能执行或重复提交步骤')
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError('计划步骤编号无效')
        step = db.execute('SELECT * FROM ai_plan_steps WHERE plan_id=? AND step_index=?', (token, index)).fetchone()
        if not step:
            raise ValueError('计划步骤不存在')
        return step

    def start_step(self, plan_id, index):
        """Commit before calling domain code. A running or completed step never restarts."""
        with connect(self.ctx.transport_db_path, True) as db:
            step = self._step(db, plan_id, index)
            if step['status'] != 'pending':
                raise ValueError('该步骤已开始或已处理，不能重复执行；执行中断时请先核对业务记录')
            if db.execute("SELECT 1 FROM ai_plan_steps WHERE plan_id=? AND step_index<? AND status<>'done'", (plan_id, index)).fetchone():
                raise ValueError('必须按照计划顺序执行步骤')
            db.execute("UPDATE ai_plan_steps SET status='running',started_at=? WHERE plan_id=? AND step_index=?",
                       (stamp(self._at()), plan_id, index))
            return self._record(db, plan_id)['steps'][index]

    def finish_step(self, plan_id, index, result):
        result = self._detail(result)
        with connect(self.ctx.transport_db_path, True) as db:
            step = self._step(db, plan_id, index)
            if step['status'] != 'running':
                raise ValueError('只有执行中的步骤才能记录成功结果')
            db.execute("UPDATE ai_plan_steps SET status='done',result=?,finished_at=? WHERE plan_id=? AND step_index=?",
                       (result, stamp(self._at()), plan_id, index))
            return self._record(db, plan_id)['steps'][index]

    def fail_step(self, plan_id, index, error):
        error = self._detail(error)
        with connect(self.ctx.transport_db_path, True) as db:
            step = self._step(db, plan_id, index)
            if step['status'] not in ('pending', 'running'):
                raise ValueError('该步骤已处理，不能覆盖执行结果')
            if db.execute("SELECT 1 FROM ai_plan_steps WHERE plan_id=? AND step_index<? AND status<>'done'", (plan_id, index)).fetchone():
                raise ValueError('只能停止当前应执行的步骤')
            at = self._at()
            db.execute("UPDATE ai_plan_steps SET status='failed',error=?,finished_at=? WHERE plan_id=? AND step_index=?",
                       (error, stamp(at), plan_id, index))
            status = 'partial' if db.execute("SELECT 1 FROM ai_plan_steps WHERE plan_id=? AND status='done'", (plan_id,)).fetchone() else 'failed'
            self._terminal(db, plan_id, status, error, at)
            return self._record(db, plan_id)

    def abort(self, plan_id, error):
        """Stop a claimed plan before a step begins; never resolve an uncertain running step."""
        error = self._detail(error)
        with connect(self.ctx.transport_db_path, True) as db:
            row = self._row(db, plan_id)
            if row['status'] != 'running':
                raise ValueError('只能停止执行中的计划')
            if db.execute("SELECT 1 FROM ai_plan_steps WHERE plan_id=? AND status='running'", (plan_id,)).fetchone():
                raise ValueError('有步骤执行状态待核对，不能自动重试或覆盖结果')
            status = 'partial' if db.execute("SELECT 1 FROM ai_plan_steps WHERE plan_id=? AND status='done'", (plan_id,)).fetchone() else 'failed'
            self._terminal(db, plan_id, status, error, self._at())
            return self._record(db, plan_id)

    def finish(self, plan_id):
        with connect(self.ctx.transport_db_path, True) as db:
            row = self._row(db, plan_id)
            if row['status'] != 'running':
                raise ValueError('计划未在执行，不能重复完成')
            if db.execute("SELECT 1 FROM ai_plan_steps WHERE plan_id=? AND status<>'done'", (plan_id,)).fetchone():
                raise ValueError('尚有未成功的步骤，不能将整个计划标记为完成')
            self._terminal(db, plan_id, 'completed', '', self._at())
            return self._record(db, plan_id)

    def cancel(self, token):
        error = None
        with connect(self.ctx.transport_db_path, True) as db:
            row = self._sync_pending(db, token, self._at())
            if row['status'] != 'pending':
                error = '只有待确认的计划可以取消；已经开始的操作不能撤回'
            else:
                self._terminal(db, token, 'cancelled', '用户取消，未执行任何步骤', self._at())
            result = self._record(db, token)
        if error:
            raise ValueError(error)
        return result
