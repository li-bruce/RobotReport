"""Durable fan-out: one business notice, one selected robot per physical group."""
import json
import uuid
from .db import connect, dumps, stamp, now, audit
from .messages import Outbox
from .service import Service
from .scope import snapshot
from .bot_capabilities import require_capability
from .bot_policy import require_event


class Notifications:
    def __init__(self, platform, clock=now):
        self.platform,self.clock=platform,clock
        self.ctx=platform.workspace_context()
        self.path=self.ctx.db_path
        with connect(self.path) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS notification_jobs(
                id TEXT PRIMARY KEY,dedupe_key TEXT NOT NULL UNIQUE,text TEXT NOT NULL,
                channel_ids TEXT NOT NULL,venue_id INTEGER,tournament_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',created_by TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notification_deliveries(
                job_id TEXT NOT NULL,channel_id TEXT NOT NULL,status TEXT NOT NULL,
                outbox_ids TEXT NOT NULL DEFAULT '[]',error TEXT,PRIMARY KEY(job_id,channel_id));''')

        with connect(self.path,True) as db:
            if 'session_ids' not in {r['name'] for r in db.execute('PRAGMA table_info(notification_jobs)')}:
                db.execute("ALTER TABLE notification_jobs ADD COLUMN session_ids TEXT NOT NULL DEFAULT 'null'")
            if 'session_snapshots' not in {r['name'] for r in db.execute('PRAGMA table_info(notification_jobs)')}:
                db.execute("ALTER TABLE notification_jobs ADD COLUMN session_snapshots TEXT NOT NULL DEFAULT 'null'")

    def destinations(self, channel_ids, venue_id=None, session_ids=None, tournament_id=None):
        if not isinstance(channel_ids,list) or not channel_ids or len(channel_ids)>100 or any(not isinstance(v,str) for v in channel_ids):
            raise ValueError('请明确选择至少一个通知群通道')
        destinations=[];groups=set()
        for cid in dict.fromkeys(channel_ids):
            ctx=self.platform.context_for_channel(cid)
            service=Service(ctx,self.clock)
            if venue_id is not None:
                service.venue(venue_id)
            if tournament_id is not None:
                from .competitions import TournamentService
                event = TournamentService(self.path).get(tournament_id)
                require_event(ctx, event)
                service.assert_event_scope(event)
            elif session_ids is not None or venue_id is not None:
                require_capability(ctx, 'enrollment')
            else:
                require_capability(ctx, 'messages')
            if session_ids is not None:
                if not isinstance(session_ids,list) or not session_ids or any(type(v) is not int for v in session_ids):
                    raise ValueError('活动通知必须关联有效场次')
                with connect(self.path) as db:
                    for sid in session_ids:
                        row=db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
                        if not row or (venue_id is not None and row['venue_id']!=venue_id):
                            raise ValueError('通知关联场次无效')
                        service.assert_session_scope(dict(row))
            elif venue_id is not None and tournament_id is None and service.scope_context().slot_keys is not None:
                raise PermissionError('该机器人仅负责指定时段，活动通知须选择相应场次')
            if ctx.chat_id not in groups:
                destinations.append(ctx)
                groups.add(ctx.chat_id)
        return destinations

    def select_channels(self, channel_ids, venue_id=None, session_ids=None, tournament_id=None):
        """Persist one eligible sender per group, retaining failed destinations.

        Multiple robots may serve and manage the same group. They are candidate
        senders for one notice, not multiple recipients. The selected channel is
        stored on the job so retries cannot switch robots and duplicate a notice.
        """
        if not isinstance(channel_ids,list) or not channel_ids or len(channel_ids)>100 or any(not isinstance(v,str) for v in channel_ids):
            raise ValueError('请明确选择至少一个通知群通道')
        groups = {}
        for cid in dict.fromkeys(channel_ids):
            channel = self.platform.channel(cid)
            groups.setdefault(channel['chat_id'], []).append(cid)
        selected = []
        for candidates in groups.values():
            chosen = eligible = None
            for cid in candidates:
                try:
                    self.destinations([cid], venue_id, session_ids, tournament_id)
                except (ValueError, PermissionError):
                    continue
                eligible = eligible or cid
                if self.platform.channel(cid)['webhook_url']:
                    chosen = cid
                    break
            # Simulation may deliberately omit outgoing credentials. When no
            # sender is configured, retain the first permitted candidate; if
            # all are invalid, persist an explicit failed destination as before.
            selected.append(chosen or eligible or candidates[0])
        return selected

    def enqueue(self,text,channel_ids,actor='scheduler',key=None,venue_id=None,tournament_id=None,session_ids=None,session_snapshots=None):
        key=key or 'manual:'+uuid.uuid4().hex
        with connect(self.path) as db:
            old=db.execute('SELECT id FROM notification_jobs WHERE dedupe_key=?',(key,)).fetchone()
            if old:return self.get(old[0],db)
        ids=self.select_channels(channel_ids,venue_id,session_ids,tournament_id)
        job_id=uuid.uuid4().hex
        with connect(self.path,True) as db:
            old=db.execute('SELECT id FROM notification_jobs WHERE dedupe_key=?',(key,)).fetchone()
            if old:return self.get(old[0],db)
            if session_ids is not None and session_snapshots is None:
                session_snapshots = [snapshot(row) for sid in session_ids if (row := db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone())]
            db.execute('INSERT INTO notification_jobs(id,dedupe_key,text,channel_ids,venue_id,tournament_id,created_by,created_at,session_ids,session_snapshots) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (job_id,key,text,dumps(ids),venue_id,tournament_id,actor,stamp(self.clock()),dumps(session_ids),dumps(session_snapshots)))
            for cid in ids:
                db.execute("INSERT INTO notification_deliveries(job_id,channel_id,status) VALUES(?,?,'pending')",(job_id,cid))
            audit(db,actor,'notification.create',{'id':job_id,'channel_ids':ids,'tournament_id':tournament_id})
        return self.dispatch(job_id)

    def get(self,job_id,db=None):
        if db is None:
            with connect(self.path) as db:return self.get(job_id,db)
        row=db.execute('SELECT * FROM notification_jobs WHERE id=?',(job_id,)).fetchone()
        if not row:raise ValueError('通知不存在')
        item=dict(row);item['channel_ids']=json.loads(item['channel_ids']);item['session_ids']=json.loads(item['session_ids']);item['session_snapshots']=json.loads(item['session_snapshots'])
        item['deliveries']=[dict(r)|{'outbox_ids':json.loads(r['outbox_ids'])} for r in db.execute('SELECT * FROM notification_deliveries WHERE job_id=?',(job_id,))]
        return item

    def dispatch(self,job_id):
        item=self.get(job_id)
        for destination in item['deliveries']:
            if destination['status']!='pending':continue
            cid=destination['channel_id']
            try:
                ctx=self.destinations([cid],item['venue_id'],item['session_ids'],item['tournament_id'])[0]
                ids=Outbox(ctx).enqueue(item['text'],'notice:'+item['dedupe_key'])
                state,error='queued',None
            except (ValueError,PermissionError):
                ids,state,error=[],'failed','目的群停用或场地/时段权限已变更，请检查机器人和群通道'
            with connect(self.path,True) as db:
                db.execute('UPDATE notification_deliveries SET status=?,outbox_ids=?,error=? WHERE job_id=? AND channel_id=?',
                    (state,dumps(ids),error,job_id,cid))
        with connect(self.path,True) as db:
            states=[r[0] for r in db.execute('SELECT status FROM notification_deliveries WHERE job_id=?',(job_id,))]
            status='pending' if 'pending' in states else 'partial' if 'failed' in states else 'queued'
            db.execute('UPDATE notification_jobs SET status=? WHERE id=?',(status,job_id))
        return self.get(job_id)

    def recover(self):
        with connect(self.path) as db:
            ids=[r[0] for r in db.execute("SELECT id FROM notification_jobs WHERE status='pending' ORDER BY created_at")]
        for job_id in ids:self.dispatch(job_id)
