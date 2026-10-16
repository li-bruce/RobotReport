"""One durable, current AI draft per club user, independent of transport bots.

The pointer never grants authority: execution stays with the authenticated origin
and existing plan executor. Generations prevent slower model replies replacing a
newer request. Claiming is single-use, including across bots and worker restarts.
"""
from datetime import timedelta
from contextlib import contextmanager
import json
import secrets

from .db import connect, dumps, now, stamp


def recover_interrupted(db_path, clock=now):
    """Called once at single-worker startup; retain receipts, never replay writes."""
    with connect(db_path, True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_conversations'").fetchone():
            db.execute("UPDATE ai_conversations SET status='interrupted',updated_at=? WHERE status='running'",(stamp(clock()),))


class AiConversation:
    def __init__(self, ctx, clock=now):
        self.ctx, self.clock = ctx, clock
        with connect(ctx.db_path, True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ai_conversations(
                scope_id TEXT NOT NULL,user_id TEXT NOT NULL,generation TEXT NOT NULL,
                bot_id TEXT NOT NULL,chat_id TEXT NOT NULL,channel_id TEXT NOT NULL,
                token TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,
                steps TEXT NOT NULL DEFAULT '[]',expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,PRIMARY KEY(scope_id,user_id))''')

    def _row(self, db):
        return db.execute('SELECT * FROM ai_conversations WHERE scope_id=? AND user_id=?',
                          (self.ctx.scope_id, self.ctx.actor)).fetchone()

    def _origin(self, row):
        return (row['bot_id'], row['chat_id'], row['channel_id']) == (self.ctx.id, self.ctx.chat_id, self.ctx.channel_id)

    def begin(self, minutes):
        at = stamp(self.clock())
        generation = secrets.token_hex(16)
        with connect(self.ctx.db_path, True) as db:
            old = self._row(db)
            if old and old['status'] == 'running':
                raise ValueError('上一份方案正在执行，请先发送 AI进度 查看结果。')
            prior = json.loads(old['steps']) if old and self._origin(old) and old['status'] == 'pending' and old['expires_at'] > at else []
            db.execute('''INSERT INTO ai_conversations VALUES(?,?,?,?,?,?,?,'drafting',?,?,?)
                ON CONFLICT(scope_id,user_id) DO UPDATE SET generation=excluded.generation,
                bot_id=excluded.bot_id,chat_id=excluded.chat_id,channel_id=excluded.channel_id,
                token='',status='drafting',steps=excluded.steps,expires_at=excluded.expires_at,
                updated_at=excluded.updated_at''',
                (self.ctx.scope_id,self.ctx.actor,generation,self.ctx.id,self.ctx.chat_id,self.ctx.channel_id,
                 '',dumps(prior),stamp(self.clock()+timedelta(minutes=minutes)),at))
        references = []
        for step in prior:
            args = {k:v for k,v in step['arguments'].items() if not k.startswith('_')}
            saved = step['arguments'].get('_resolved_session')
            if saved:
                args.update(date=saved['date'],number=saved['number'],hour=None,minute=None)
            references.append({'operation':step['operation'],'arguments':args})
        return generation, references

    def ensure_generation(self, generation, db=None):
        if db is None:
            with connect(self.ctx.db_path) as connection:
                return self.ensure_generation(generation,connection)
        row = self._row(db)
        if not row or row['generation'] != generation or row['status'] != 'drafting':
            raise ValueError('已收到更新的 AI 需求，请以最新回复为准。')

    @contextmanager
    def publication(self, generation):
        # Serialize the pointer and transport-plan publication against a newer
        # generation; a slow reply must never supersede the newest transport row.
        with connect(self.ctx.db_path, True) as db:
            self.ensure_generation(generation,db)
            yield db

    def publish(self, generation, token, steps, expires_at, db=None):
        if db is None:
            with self.publication(generation) as connection:
                return self.publish(generation,token,steps,expires_at,connection)
        changed = db.execute('''UPDATE ai_conversations SET token=?,steps=?,expires_at=?,
                status='pending',updated_at=? WHERE scope_id=? AND user_id=? AND generation=? AND status='drafting' ''',
            (token,dumps(steps),expires_at,stamp(self.clock()),self.ctx.scope_id,self.ctx.actor,generation)).rowcount
        if not changed:
            raise ValueError('已收到更新的 AI 需求，请以最新回复为准。')

    def abandon(self, generation):
        with connect(self.ctx.db_path, True) as db:
            db.execute("UPDATE ai_conversations SET status='failed',updated_at=? WHERE scope_id=? AND user_id=? AND generation=? AND status='drafting'",
                       (stamp(self.clock()),self.ctx.scope_id,self.ctx.actor,generation))

    def current(self, pending=True):
        with connect(self.ctx.db_path, True) as db:
            row = self._row(db)
            if not row:
                raise ValueError('你当前没有待确认的 AI 方案，请先发送 AI 加业务需求。')
            if not self._origin(row):
                raise ValueError('请在最近一次提出 AI 需求的群中，@对应机器人确认或查看进度。')
            if row['status'] == 'pending' and row['expires_at'] <= stamp(self.clock()):
                db.execute("UPDATE ai_conversations SET status='expired' WHERE scope_id=? AND user_id=?",(self.ctx.scope_id,self.ctx.actor))
                row = self._row(db)
            result = dict(row)
        if pending and result['status'] != 'pending':
            labels = {'expired':'方案已过期，请重新发送 AI 加业务需求。',
                      'drafting':'正在整理你的新需求，请等新的步骤预览后再确认。',
                      'running':'方案正在执行，请发送 AI进度 查看。'}
            raise ValueError(labels.get(result['status'],'当前没有待确认方案；发送 AI进度 查看上次结果，或重新提出需求。'))
        return result

    def claim(self, token):
        """Compatibility tokens also must refer to the user's one current draft."""
        with connect(self.ctx.db_path, True) as db:
            row = self._row(db)
            if row is None:
                return False  # pre-upgrade pending records expire under their old guards
            if not self._origin(row) or row['token'] != token or row['status'] != 'pending' or row['expires_at'] <= stamp(self.clock()):
                raise ValueError('方案已被替换、过期或已确认，请查看最新预览。')
            db.execute("UPDATE ai_conversations SET status='running',updated_at=? WHERE scope_id=? AND user_id=?",(stamp(self.clock()),self.ctx.scope_id,self.ctx.actor))
        return True

    def finish(self, token, status='completed'):
        with connect(self.ctx.db_path, True) as db:
            db.execute('UPDATE ai_conversations SET status=?,updated_at=? WHERE scope_id=? AND user_id=? AND token=? AND bot_id=? AND chat_id=?',
                       (status,stamp(self.clock()),self.ctx.scope_id,self.ctx.actor,token,self.ctx.id,self.ctx.chat_id))


def preview_text(steps):
    from .ai_operations import describe_step
    from .messages import escape
    lines = []
    for index, step in enumerate(steps, 1):
        label, detail = describe_step(step)
        lines.append(f'{index}. **{escape(label)}**：{escape(detail)}')
    return '**AI 计划待确认 · 尚未执行**\n\n' + '\n\n'.join(lines) + '\n\n回复 **确认** 执行；修改请发 **AI + 修改意见**；放弃请发 **取消计划**。'
