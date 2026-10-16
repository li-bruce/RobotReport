"""Shared venue reservations; daily booking and event writes use the same DB lock."""
import json
import re
from datetime import date, datetime, time, timedelta

from .db import connect, dumps, stamp, now, TZ, audit


def ensure_schema(db):
    # No executescript: callers may already be in a business transaction.
    db.execute('''CREATE TABLE IF NOT EXISTS venue_reservations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,venue_id INTEGER NOT NULL,
        date TEXT NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL,title TEXT NOT NULL,
        kind TEXT NOT NULL,event_id INTEGER,status TEXT NOT NULL DEFAULT 'active',
        version INTEGER NOT NULL DEFAULT 1,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
        released_at TEXT,updated_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS venue_reservation_time ON venue_reservations(venue_id,date,status,start,end)')
    db.execute('''CREATE TABLE IF NOT EXISTS availability_notices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,reservation_id INTEGER NOT NULL,
        action TEXT NOT NULL,snapshot TEXT NOT NULL,created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending')''')


def interval(data):
    if not isinstance(data, dict) or set(data) - {'date', 'start', 'end'}:
        raise ValueError('占用时段需要日期、开始和结束时间')
    day = data.get('date')
    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise ValueError('占用日期格式为 YYYY-MM-DD')
    date.fromisoformat(day)
    for key in ('start', 'end'):
        value = data.get(key)
        if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value):
            raise ValueError('占用时间格式为 HH:MM')
        time.fromisoformat(value)
    if data['start'] >= data['end']:
        raise ValueError('结束时间须晚于开始时间，跨天占用请拆成多个日期')
    return {key: data[key] for key in ('date', 'start', 'end')}


def intervals(values):
    if not isinstance(values, list) or len(values) > 120:
        raise ValueError('赛事占用最多120段，每段指定日期、开始和结束时间')
    result = sorted([interval(value) for value in values], key=lambda row: (row['date'], row['start']))
    for a, b in zip(result, result[1:]):
        if a['date'] == b['date'] and a['end'] > b['start']:
            raise ValueError('同一赛事的占用时段不能重叠')
    return result


def overlapping(db, venue_id, value):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='venue_reservations'").fetchone():
        return []
    return [dict(row) for row in db.execute('''SELECT * FROM venue_reservations WHERE venue_id=? AND date=?
        AND start<? AND end>? AND (status='active' OR released_at>date||'T'||end||':00+08:00') ORDER BY start,id''',
        (venue_id, value['date'], value['end'], value['start']))]


def annotate(db, session):
    row = dict(session)
    blocks = overlapping(db, row['venue_id'], row)
    row['occupancies'] = [{k: block[k] for k in ('id', 'kind', 'event_id', 'title', 'date', 'start', 'end')} for block in blocks]
    row['booking_state'] = 'occupied' if blocks else 'available'
    row['booking_notice'] = '；'.join(f"{b['title']}（{b['start']}–{b['end']}）" for b in blocks)
    return row


def assert_bookable(db, session):
    row = annotate(db, session)
    if row['occupancies']:
        raise ValueError('该场次已占用，不可报名或组织每日比赛：' + row['booking_notice'])


def _conflicts(db, venue, value, ignore_event=None, ignore_id=None):
    conflicts = [r for r in overlapping(db, venue, value)
                 if r['status'] == 'active' and r['id'] != ignore_id
                 and (ignore_event is None or r['event_id'] != ignore_event)]
    if conflicts:
        block = conflicts[0]
        raise ValueError(f"场地已由「{block['title']}」占用：{block['date']} {block['start']}–{block['end']}，请调整时间或先释放原占用")


def _notice(db, row, action, at):
    # Historical-only edits must not send retrospective group announcements.
    end = datetime.fromisoformat(row['date'] + 'T' + row['end']).replace(tzinfo=TZ)
    if end > at:
        db.execute('INSERT INTO availability_notices(reservation_id,action,snapshot,created_at) VALUES(?,?,?,?)',
                   (row['id'], action, dumps(dict(row)), stamp(at)))


def _insert(db, venue, value, title, actor, at, event_id=None):
    rid = db.execute('''INSERT INTO venue_reservations(venue_id,date,start,end,title,kind,event_id,created_by,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (venue, value['date'], value['start'], value['end'], title, 'event' if event_id else 'manual',
                     event_id, actor, stamp(at), stamp(at))).lastrowid
    row = dict(db.execute('SELECT * FROM venue_reservations WHERE id=?', (rid,)).fetchone())
    _notice(db, row, 'reserved', at)
    return row


def _release(db, row, actor, at):
    db.execute("UPDATE venue_reservations SET status='released',released_at=?,updated_at=?,version=version+1 WHERE id=?",
               (stamp(at), stamp(at), row['id']))
    _notice(db, row, 'released', at)


def sync_event(db, event, actor, at):
    """Called within create/update/close transaction; unconfigured legacy events stay unchanged."""
    ensure_schema(db)
    values = intervals(event['options'].get('reservations', []))
    if event['kind'] != 'large':
        if values:
            raise ValueError('优先预留仅用于大型赛事，每日比赛使用报名场次')
        return
    closed = event['status'] in ('archived', 'cancelled')
    old = [dict(r) for r in db.execute("SELECT * FROM venue_reservations WHERE event_id=? AND status='active'", (event['id'],))]
    if not closed and (values or old):
        for match in db.execute("SELECT number,scheduled_at FROM tour_matches WHERE tournament_id=? AND scheduled_at IS NOT NULL AND status NOT IN ('completed','bye')", (event['id'],)):
            moment = datetime.fromisoformat(match['scheduled_at']).astimezone(TZ)
            if moment >= at and not any(v['date'] == moment.date().isoformat() and v['start'] <= moment.strftime('%H:%M') < v['end'] for v in values):
                raise ValueError(f"第{match['number']}场的比赛时间不在新预留范围内，请先调整赛程或同时提交新的赛程")
    keep = {(v['date'], v['start'], v['end']) for v in values} if not closed else set()
    for value in values if not closed else []:
        key = (value['date'], value['start'], value['end'])
        exists = any((r['date'], r['start'], r['end']) == key for r in old)
        if not exists:
            if datetime.fromisoformat(value['date']+'T'+value['start']).replace(tzinfo=TZ) <= at:
                raise ValueError('新增赛事占用须从未来时间开始')
            _conflicts(db, event['venue_id'], value, ignore_event=event['id'])
    for row in old:
        key = (row['date'], row['start'], row['end'])
        if key not in keep:
            if not closed and datetime.fromisoformat(row['date']+'T'+row['start']).replace(tzinfo=TZ) <= at:
                raise ValueError('已开始的占用时段保留历史，不能修改或删除；可新增后续时段')
            _release(db, row, actor, at)
        elif row['title'] != event['title']:
            db.execute('UPDATE venue_reservations SET title=?,version=version+1,updated_at=? WHERE id=?',
                       (event['title'], stamp(at), row['id']))
    existing = {(r['date'], r['start'], r['end']) for r in old}
    for value in values if not closed else []:
        if (value['date'], value['start'], value['end']) not in existing:
            _insert(db, event['venue_id'], value, event['title'], actor, at, event['id'])


class Availability:
    def __init__(self, ctx, clock=now):
        from .service import Service
        self.ctx, self.clock = ctx, clock
        self.service = Service(ctx, clock)
        self.path = ctx.db_path

    def _scope(self, venue, value, write=False):
        self.service.venue(venue, active=write)
        if self.service.scope_context().slot_keys is not None:
            rows = self.service.sessions(value['date'], venue)
            relevant = [s for s in rows if s['start'] < value['end'] and value['start'] < s['end']]
            if not relevant:
                raise PermissionError('本机器人未负责该占用时段')
            if write:
                cursor = value['start']
                for row in sorted(relevant, key=lambda s: s['start']):
                    if row['start'] > cursor:
                        break
                    cursor = max(cursor, row['end'])
                if cursor < value['end']:
                    raise PermissionError('占用范围超出本机器人负责时段，请在后台配置')

    def list(self, venue_id, start_date=None, end_date=None, history=False):
        from .service import day_value
        self.service.venue(venue_id)
        first = day_value(start_date or self.clock().date().isoformat())
        last = day_value(end_date or first.isoformat())
        if not first <= last <= first + timedelta(days=366):
            raise ValueError('查询日期范围最多366天')
        with connect(self.path) as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM venue_reservations WHERE venue_id=? AND date BETWEEN ? AND ? ORDER BY date,start,id',
                                             (venue_id, first.isoformat(), last.isoformat()))]
        result = []
        for row in rows:
            if not history and row['status'] != 'active':
                continue
            try:
                self._scope(venue_id, row)
            except PermissionError:
                continue
            result.append(row)
        return result

    def save(self, data, reservation_id=None):
        self.ctx.require('activity.manage')
        if not isinstance(data, dict) or set(data) - {'venue_id','date','start','end','title','expected_version'}:
            raise ValueError('占用字段无效')
        from .service import integer, clean_name
        venue = integer(data.get('venue_id', 1), 1, 2**31-1, '场地编号')
        value = interval({key:data.get(key) for key in ('date','start','end')})
        title = clean_name(data.get('title', ''))
        at = self.clock()
        if datetime.fromisoformat(value['date']+'T'+value['end']).replace(tzinfo=TZ) <= at:
            raise ValueError('不能新增已结束的场地占用')
        self._scope(venue, value, True)
        if reservation_id is not None:
            with connect(self.path) as db:
                previous = db.execute('SELECT * FROM venue_reservations WHERE id=?',(reservation_id,)).fetchone()
            if previous:
                self._scope(previous['venue_id'],dict(previous),True)
        with connect(self.path, True) as db:
            old = None
            if reservation_id is not None:
                old = db.execute('SELECT * FROM venue_reservations WHERE id=?', (reservation_id,)).fetchone()
                if not old or old['status'] != 'active' or old['kind'] != 'manual' or old['venue_id'] != venue:
                    raise ValueError('占用不存在或不能在此处修改；赛事占用请到赛事页面调整')
                if data.get('expected_version') != old['version']:
                    raise ValueError('占用已变化，请刷新后重试')
                if datetime.fromisoformat(old['date']+'T'+old['start']).replace(tzinfo=TZ) <= at:
                    raise ValueError('已开始的占用请先释放，再配置新的时段')
            _conflicts(db, venue, value, ignore_id=reservation_id)
            if old:
                _release(db, dict(old), self.ctx.actor, at)
            result = _insert(db, venue, value, title, self.ctx.actor, at)
            audit(db, self.ctx.actor, 'availability.save', {'id':result['id'], 'replaces':reservation_id})
        result['affected_sessions'] = [s for s in self.service.sessions(value['date'],venue) if s['start']<value['end'] and value['start']<s['end']]
        return result

    def release(self, reservation_id, expected_version=None):
        self.ctx.require('activity.manage')
        with connect(self.path) as db:
            found = db.execute('SELECT * FROM venue_reservations WHERE id=?', (reservation_id,)).fetchone()
        if not found:
            raise ValueError('占用不存在')
        row = dict(found)
        self._scope(row['venue_id'], row, True)
        with connect(self.path, True) as db:
            row = dict(db.execute('SELECT * FROM venue_reservations WHERE id=?', (reservation_id,)).fetchone())
            if row['kind'] != 'manual':
                raise ValueError('赛事占用请在赛事页面调整，或关闭赛事后释放')
            if expected_version is not None and row['version'] != expected_version:
                raise ValueError('占用已变化，请刷新后重试')
            if row['status'] == 'active':
                _release(db, row, self.ctx.actor, self.clock())
                audit(db, self.ctx.actor, 'availability.release', {'id':reservation_id})
        return {'id':reservation_id,'status':'released'}

    def describe(self, venue, day=None):
        from .messages import escape
        day = day or self.clock().date().isoformat()
        rows = self.list(venue, day, day)
        return f'**场地{venue} · {day} 占用**\n\n' + ('\n'.join(f"{i}. {r['start']}–{r['end']} {escape(r['title'])}（占用{r['id']}）" for i,r in enumerate(rows,1)) if rows else '当日没有已登记占用。')


def dispatch_notices(platform, clock=now):
    """Deduplicate overlapping bot scopes while covering every affected group/session."""
    from .service import Service
    from .notifications import Notifications
    from .bot_capabilities import supports
    from .messages import escape
    ctx = platform.workspace_context()
    with connect(ctx.db_path) as db:
        notices = [dict(r) for r in db.execute("SELECT * FROM availability_notices WHERE status='pending' ORDER BY id LIMIT 50")]
    sender = Notifications(platform, clock)
    channels = platform.system_contexts()
    channels.sort(key=lambda c: not bool(platform.channel(c.channel_id)['webhook_url']))
    for notice in notices:
        block = json.loads(notice['snapshot'])
        groups = {}
        if datetime.fromisoformat(block['date']+'T'+block['end']).replace(tzinfo=TZ) > clock():
            for channel in channels:
                if not channel.daily_notifications or not supports(channel,'enrollment'):
                    continue
                try:
                    sessions = [s for s in Service(channel,clock).sessions(block['date'],block['venue_id'])
                                if s['start'] < block['end'] and block['start'] < s['end'] and s['id'] not in groups.get(channel.chat_id,set())]
                    if not sessions:
                        continue
                    change = '已占用' if notice['action']=='reserved' else '占用已释放'
                    lines = [f"**场地{block['venue_id']} · {change}**",f"{block['date']} {block['start']}–{block['end']} · {escape(block['title'])}"]
                    for session in sessions:
                        state = '不可报名：'+escape(session['booking_notice']) if session['occupancies'] else '可报名'
                        lines.append(f"{escape(session['name'])} {session['start']}–{session['end']}：{state}")
                    if any(s['count'] for s in sessions):
                        lines.append('已有报名保留，请留意活动调整；需要退出可发送取消报名。')
                    session_key = ','.join(str(s['id']) for s in sessions)
                    sender.enqueue('\n\n'.join(lines),[channel.channel_id],key=f"availability:{notice['id']}:{channel.chat_id}:{session_key}",
                                   venue_id=block['venue_id'],session_ids=[s['id'] for s in sessions])
                    groups.setdefault(channel.chat_id,set()).update(s['id'] for s in sessions)
                except (ValueError, PermissionError):
                    continue
        with connect(ctx.db_path,True) as db:
            db.execute("UPDATE availability_notices SET status='dispatched' WHERE id=?",(notice['id'],))
