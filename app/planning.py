"""Persisted, sender-scoped daily competition selection."""
from datetime import timedelta
import json
import secrets
from .db import connect, stamp, now, dumps
from .service import Service
from .tournaments import Tournaments
from .messages import table, escape
from .dialogue import render, command, Markup
from .gender import category, CATEGORIES, eligible

STRATEGIES = {'随机': 'random', '种子分布': 'seeded', '平均分布': 'balanced', '高带低': 'high_low'}


class DailyPlanning:
    def __init__(self, ctx, clock=now):
        from .bot_capabilities import require_capability
        from .scope import refresh_context
        require_capability(refresh_context(ctx), 'daily_events')
        self.ctx, self.clock = ctx, clock
        self.events, self.service = Tournaments(ctx, clock), Service(ctx, clock)
        with connect(ctx.transport_db_path) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS daily_planning(
                chat_id TEXT NOT NULL,user_id TEXT NOT NULL,venue_id INTEGER NOT NULL,
                token TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,
                expires_at TEXT NOT NULL,event_id INTEGER,
                PRIMARY KEY(chat_id,user_id,venue_id))''')

    def get(self, venue_id):
        with connect(self.ctx.transport_db_path) as db:
            row = db.execute('SELECT * FROM daily_planning WHERE chat_id=? AND user_id=? AND venue_id=? AND expires_at>?',
                             (self.ctx.chat_id, self.ctx.actor, venue_id, stamp(self.clock()))).fetchone()
        if not row:
            return None
        return dict(row) | {'payload': json.loads(row['payload'])}

    def begin(self, selection, discipline='doubles', strategy='random', title=None, doubles_category='open'):
        if discipline not in ('singles', 'doubles') or strategy not in STRATEGIES.values():
            raise ValueError('比赛模式或分配方式无效')
        if title is not None:
            title = str(title).strip()
            if not title or len(title) > 100 or any(c in title for c in '\n\r,，'):
                raise ValueError('赛事名称限1至100字，不能包含逗号或换行')
        category({'discipline': discipline, 'doubles_category': doubles_category})
        session = self.service.resolve_session(**selection)
        payload = {'session_id': session['id'], 'date': session['date'], 'discipline': discipline, 'doubles_category': doubles_category, 'assignment_strategy': strategy, 'title': title}
        with connect(self.ctx.transport_db_path, True) as db:
            db.execute('INSERT OR REPLACE INTO daily_planning VALUES(?,?,?,?,?,?,?,NULL)',
                       (self.ctx.chat_id, self.ctx.actor, selection['venue_id'], secrets.token_hex(16), dumps(payload), 'ready', stamp(self.clock() + timedelta(hours=2))))
        return self.describe(selection['venue_id'])

    def change(self, venue_id, **options):
        record = self.get(venue_id)
        if not record or record['status'] != 'ready':
            return render(self.ctx,'planning.begin_first')
        self.info(record)  # Recheck current bot/channel scope before mutating.
        payload = record['payload'] | options
        if payload.get('discipline') == 'singles':
            payload['doubles_category'] = 'open'
        category(payload)
        if payload.get('discipline') not in ('singles', 'doubles') or payload.get('assignment_strategy') not in STRATEGIES.values():
            raise ValueError('比赛模式或分配方式无效')
        with connect(self.ctx.transport_db_path, True) as db:
            db.execute("UPDATE daily_planning SET payload=? WHERE chat_id=? AND user_id=? AND venue_id=? AND token=? AND status='ready'",
                       (dumps(payload), self.ctx.chat_id, self.ctx.actor, venue_id, record['token']))
        return self.describe(venue_id)

    def info(self, record):
        payload = record['payload']
        candidates = self.events.planning_candidates(payload['date'], record['venue_id'], payload['session_id'])
        kind = category(payload)
        players = [p for p in candidates['formal'] if eligible(p, kind)]
        formats = self.events.planning_formats(candidates, payload['discipline'], payload)
        all_players = candidates['formal'] + candidates['substitutes']
        counts = {g: sum(p['gender'] == g for p in all_players) for g in ('male', 'female', 'unknown')}
        session = dict(candidates['session'], substitute_count=len([p for p in candidates['substitutes'] if eligible(p, kind)]), gender_counts=counts, excluded_count=sum(not eligible(p, kind) for p in all_players))
        return players, session, formats

    def describe(self, venue_id):
        record = self.get(venue_id)
        if not record:
            return render(self.ctx,'planning.expired')
        players, session, formats = self.info(record)
        mode = CATEGORIES[category(record['payload'])] if record['payload']['discipline'] == 'doubles' else '单打'
        rows = [[f['name'], ('可组织 · 补入' + str(f.get('substitutes_needed', 0)) + '名替补') if f['available'] and f.get('substitutes_needed') else ('可组织' if f['available'] else f.get('reason', '人数不符合')), f['min_players']] for f in formats if record['payload']['discipline'] in f.get('disciplines', [record['payload']['discipline']])]
        content = (f"# 每日策划 · {mode}\n\n赛事名称：{escape(record['payload'].get('title') or '选择赛制后自动命名')}\n\n{session['date']} {escape(session['name'])} · 场地{venue_id}\n\n"
                f"当前符合条件：正式 **{len(players)}人** · 替补 **{session['substitute_count']}人**\n\n男 {session['gender_counts']['male']} 人 · 女 {session['gender_counts']['female']} 人 · 性别待补齐 {session['gender_counts']['unknown']} 人；{session['excluded_count']} 人不符合当前类型，原报名保留。\n\n" + table(['赛制', '当前状态', '最低人数'], rows)
                )
        return render(self.ctx,'planning.options',content=Markup(content))

    def create(self, venue_id, format_id):
        record = self.get(venue_id)
        if not record:
            return render(self.ctx,'planning.begin_first')
        if record['status'] == 'created':
            self.events.get(record['event_id'])
            return render(self.ctx,'planning.ready',resume_command=command(self.ctx,f"继续比赛 {record['event_id']}"))
        if record['status'] == 'creating':
            # The engine commits the unique planner token with the tournament.
            for summary in self.events.list(venue_id):
                existing = self.events.get(summary['id'])
                if existing.get('options', {}).get('planner_token') == record['token']:
                    with connect(self.ctx.transport_db_path, True) as db:
                        db.execute("UPDATE daily_planning SET status='created',event_id=? WHERE token=?", (existing['id'], record['token']))
                    return self.events.describe(existing['id'])
            with connect(self.ctx.transport_db_path, True) as db:
                db.execute("UPDATE daily_planning SET status='ready' WHERE token=? AND status='creating'", (record['token'],))
        elif record['status'] != 'ready':
            return render(self.ctx,'planning.closed')
        players, session, formats = self.info(record)
        selected = next((f for f in formats if f['id'] == format_id), None)
        if not selected or not selected['available']:
            return (selected.get('reason', '当前人数或模式不支持该赛制') if selected else '不支持该赛制') + '\n\n' + self.describe(venue_id)
        with connect(self.ctx.transport_db_path, True) as db:
            changed = db.execute("UPDATE daily_planning SET status='creating' WHERE chat_id=? AND user_id=? AND venue_id=? AND token=? AND status='ready'", (self.ctx.chat_id, self.ctx.actor, venue_id, record['token'])).rowcount
            if not changed:
                return render(self.ctx,'planning.busy')
        try:
            event = self.events.create({'kind': 'daily', 'format': format_id, 'title': record['payload'].get('title') or f"{session['date']} {selected['name']}",
                                        'venue_id': venue_id, 'date': session['date'], 'session_id': session['id'],
                                        'options': {'discipline': record['payload']['discipline'], 'doubles_category': record['payload'].get('doubles_category', 'open'), 'assignment_strategy': record['payload']['assignment_strategy'], 'planner_token': record['token']}})
        except Exception:
            with connect(self.ctx.transport_db_path, True) as db:
                db.execute("UPDATE daily_planning SET status='ready' WHERE token=?", (record['token'],))
            raise
        with connect(self.ctx.transport_db_path, True) as db:
            db.execute("UPDATE daily_planning SET status='created',event_id=? WHERE token=?", (event['id'], record['token']))
        return self.events.describe(event['id'])
