"""Safe message composition and durable, per-bot delivery outbox."""
from datetime import timedelta
import html
import json
import re
import uuid
from urllib.parse import urlsplit
import httpx
from .db import connect, dumps, stamp, now, audit


def escape(value):
    value = html.escape(str(value), quote=True)
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', value)


def safe_markdown(value):
    """Allow authored Markdown while escaping raw HTML and rejecting active links."""
    for match in re.finditer(r'\]\(([^\s)]*)', value):
        url = urlsplit(html.unescape(match[1]))
        if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password:
            raise ValueError('消息链接仅支持正常的 HTTP/HTTPS 地址')
    return html.escape(value, quote=False)


def chunks(text, limit=4500):
    if len(text) > 200000:
        raise ValueError('消息过长')
    parts, current = [], ''
    for line in str(text).splitlines(keepends=True):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ''
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            parts.append(current)
            current = ''
        current += line
    if current:
        parts.append(current)
    return parts or ['（空消息）']


def table(headers, rows):
    def cell(value):
        return escape(value if value is not None else '—').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(map(cell, headers)) + ' |',
                      '| ' + ' | '.join('---' for _ in headers) + ' |',
                      *['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]])


def mention(user_id, name=''):
    # WPS resolves an empty at label to the actual platform display name.
    from .security import identifier
    return '<at user_id="' + html.escape(identifier(user_id), quote=True) + '">' + html.escape(name) + '</at>'


def registration_name(row, include_level=True):
    """Human-facing label shared by rosters and registration receipts, before escaping."""
    guest = bool(row.get('is_proxy')) or int(row.get('guest_index') or 0) > 0
    name = str(row.get('name') or '').strip()
    user_id = str(row.get('user_id') or '')
    if not name or (user_id and (name == user_id or re.fullmatch(re.escape(user_id) + r'\+\d+', name))):
        name = (f"同行{row['guest_index']}" if row.get('guest_index') else '同行') if guest else '待完善姓名'
    if guest:
        sponsor = str(row.get('sponsor_name') or '').strip()
        if not sponsor or sponsor in (user_id, str(row.get('sponsor_id') or '')):
            sponsor = '待补姓名'
        return f'{name}（{sponsor}代报名）'
    level = row.get('level')
    if include_level and isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 9:
        name += f'（{level}级）'
    return name


def registration_gender(row):
    if row.get('is_proxy') or int(row.get('guest_index') or 0) > 0:
        return '未填写'
    return {'male': '男', 'female': '女', '男': '男', '女': '女'}.get(row.get('gender'), '未填写')


def registration_category(row):
    return {'formal': '正式', 'substitute': '替补', 'waitlist': '候补',
            '正式': '正式', '替补': '替补', '候补': '候补'}.get(row.get('category'), '—')


def registration_table(rows):
    return table(['姓名', '性别', '名额'], [
        [registration_name(row), registration_gender(row), registration_category(row)] for row in rows
    ])


def roster(data, venue_id=1):
    lines = []
    for session in data['sessions']:
        rows = [r for r in data['registrations'] if r['session_id'] == session['id'] and r['status'] == 'active']
        label = f"{session['date']} · 场地{venue_id} · {escape(session['name'])} {session['start']}–{session['end']}"
        if not rows:
            state = '🔴 已占用：'+escape(session['booking_notice']) if session.get('occupancies') else '暂无报名'
            lines.append(f'{label}：{state}')
            continue
        lines.append(f"## {label}")
        if session.get('occupancies'):
            lines.append('🔴 **已占用 · 不可报名** '+escape(session['booking_notice']))
        lines.append(f"**{len(rows)} 人** · 正式 {session['capacity']} · 替补 {session['substitute_capacity']}")
        lines.append(registration_table(rows))
    return '\n\n'.join(lines) if lines else '暂无场次安排。'


def tournament_text(item, mode='schedule'):
    lines = [f"# 赛事 {item['id']} · {escape(item['title'])}", f"状态：{escape(item['status'])} · 场地{item['venue_id']}"]
    if mode == 'registration':
        lines.extend([f"报名口令：{escape(item.get('keyword') or ('赛事 ' + str(item['id']) + ' 报名'))}",
                      f"报名时间：{escape(item.get('registration_open', ''))} 至 {escape(item.get('registration_close', ''))}"])
    elif mode == 'closed':
        lines.append('报名已截止，后续分组和赛程由管理员公布。')
    else:
        for m in item.get('matches', []):
            left = m.get('side_a_name') or (m.get('side_a') or {}).get('name', '待定')
            right = m.get('side_b_name') or (m.get('side_b') or {}).get('name', '待定')
            score = m.get('scores') or []
            scores = ', '.join(f'{s[0]}:{s[1]}' for s in score) if isinstance(score, list) else str(score)
            lines.append(f"第{m.get('number', m['id'])}场　{escape(left)} vs {escape(right)}　{scores}　{escape(m.get('scheduled_at') or '')} {escape(m.get('court') or '')}")
        if item.get('standings'):
            lines.append('## 积分 / 排名')
            for s in item['standings'][:100]:
                lines.append(escape(s.get('name', s.get('team_id', s.get('id', '')))) + '　' +
                             ' · '.join(f'{k} {escape(s[k])}' for k in ('points', 'wins', 'losses', 'rank') if k in s))
        winner = item.get('champion') or item.get('winner') or item.get('winner_name')
        if winner:
            lines.append('**胜者：' + escape(winner.get('name', winner) if isinstance(winner, dict) else winner) + '**')
    return '\n\n'.join(lines)


class Outbox:
    def __init__(self, context):
        self.ctx = context
        self.path = context.transport_db_path
        with connect(self.path) as db:
            columns = {r['name'] for r in db.execute('PRAGMA table_info(outbox)')}
        if 'scope_json' not in columns:
            with connect(self.path,True) as db:
                if 'scope_json' not in {r['name'] for r in db.execute('PRAGMA table_info(outbox)')}:
                    db.execute("ALTER TABLE outbox ADD COLUMN scope_json TEXT NOT NULL DEFAULT '{}'")

    @staticmethod
    def scope_signature(ctx, refresh=True):
        from .scope import refresh_context
        from .bot_capabilities import normalize_capabilities
        if refresh:
            ctx=refresh_context(ctx)
        from .time_rules import effective_capabilities
        capabilities = sorted(effective_capabilities(ctx))
        return {'venues': sorted(ctx.venue_ids) if ctx.venue_restricted or ctx.venue_ids else None,
                'slots': sorted(ctx.slot_keys) if ctx.slot_keys is not None else None,
                'capabilities': None if capabilities == sorted(normalize_capabilities(None)) else capabilities}

    def enqueue(self, text, key=None, db=None):
        # Capture the scope under which the caller composed this text. A scope
        # change between composition and enqueue must invalidate the old reply.
        scope = self.scope_signature(self.ctx, refresh=False)
        if not self.ctx.chat_id:
            raise ValueError('机器人尚未绑定群')
        key = (self.ctx.channel_id or self.ctx.chat_id) + ':' + (key or 'manual:' + uuid.uuid4().hex)
        def insert(conn):
            prefix=key+':'
            previous = list(conn.execute('SELECT id FROM outbox WHERE substr(dedupe_key,1,?)=? ORDER BY id', (len(prefix),prefix)))
            if previous:
                return [r[0] for r in previous]
            from .table_images import publish_tables
            if self.path == self.ctx.db_path:
                themed = publish_tables(text, self.ctx, conn)
                row = conn.execute("SELECT value FROM config WHERE key='theme'").fetchone()
            else:
                with connect(self.ctx.db_path,True) as business:
                    themed = publish_tables(text,self.ctx,business)
                    row = business.execute("SELECT value FROM config WHERE key='theme'").fetchone()
            theme = json.loads(row[0]) if row else {}
            if theme.get('enabled', True) and themed.startswith('#'):
                title, separator, body = themed.partition('\n')
                title=title.lstrip('#').strip()
                themed = f"<font color='{theme.get('accent', '#197661')}'><b>{title}</b></font>" + separator + body
                if theme.get('banner_url'):
                    themed = '![](' + theme['banner_url'] + ')\n\n' + themed
            ids = []
            for i, part in enumerate(chunks(themed)):
                conn.execute('INSERT OR IGNORE INTO outbox(dedupe_key,chat_id,payload,created_at,channel_id,scope_json) VALUES(?,?,?,?,?,?)',
                             (f'{key}:{i}', self.ctx.chat_id, dumps({'msgtype': 'markdown', 'markdown': {'text': part}}), stamp(),self.ctx.channel_id,dumps(scope)))
                ids.append(conn.execute('SELECT id FROM outbox WHERE dedupe_key=?', (f'{key}:{i}',)).fetchone()[0])
            return ids
        if db is not None:
            return insert(db)
        with connect(self.path, True) as conn:
            return insert(conn)

    def list(self):
        with connect(self.path) as db:
            rows = [dict(x) for x in db.execute('SELECT * FROM outbox WHERE channel_id=? ORDER BY id DESC LIMIT 300',(self.ctx.channel_id,))]
        for row in rows:
            row['payload'] = json.loads(row['payload'])
            row['text'] = row['payload'].get('markdown', {}).get('text', '')
        return rows

    def recover(self):
        with connect(self.path, True) as db:
            db.execute("UPDATE outbox SET status='unknown',last_error='进程中断，需人工核实发送结果' WHERE status='sending'")
            db.execute("UPDATE inbox SET status='unknown',error='进程中断，业务可能已经执行，禁止自动重放' WHERE status='processing'")
            db.execute("UPDATE ai_pending SET status='unknown' WHERE status='consuming'")

    async def deliver_one(self, platform, live=False, client=None, at=None):
        at = at or now()
        robot = platform.bot(self.ctx.id, active=True)
        bot = platform.channel(self.ctx.channel_id,active=True)
        with connect(self.path, True) as db:
            recent = db.execute('SELECT COUNT(*) FROM outbox WHERE channel_id=? AND last_attempt_at>?', (self.ctx.channel_id,stamp(at - timedelta(seconds=60)))).fetchone()[0]
            if recent >= 20:
                return None
            row = db.execute("SELECT * FROM outbox WHERE channel_id=? AND status='pending' ORDER BY id LIMIT 1",(self.ctx.channel_id,)).fetchone()
            if not row:
                return None
            if row['chat_id'] != bot['chat_id']:
                db.execute("UPDATE outbox SET status='failed',last_error='消息群绑定不匹配' WHERE id=?", (row['id'],))
                return 'failed'
            from .time_rules import bind_clock
            current_ctx = bind_clock(platform.context_for_channel(self.ctx.channel_id),lambda:at)
            # Also covers legacy identity notices queued before management-only
            # routing, whose captured scope can still match the current robot.
            from .bot_capabilities import supports
            if (row['dedupe_key'].startswith((self.ctx.channel_id or self.ctx.chat_id) + ':identity:')
                    and not supports(current_ctx, 'management')):
                db.execute("UPDATE outbox SET status='failed',last_error='机器人未开启群内管理，管理通知未发送' WHERE id=?", (row['id'],))
                return 'failed'
            from .time_rules import broadcast_delivery_allowed
            from .service import Service
            if not broadcast_delivery_allowed(row['dedupe_key'],current_ctx,at,Service(current_ctx,lambda:at).config()):
                db.execute("UPDATE outbox SET status='failed',last_error='播报规则已变更或消息已过期' WHERE id=?",(row['id'],))
                return 'failed'
            current_scope=self.scope_signature(current_ctx)
            captured_scope=json.loads(row['scope_json'])
            if captured_scope:
                captured_scope.setdefault('capabilities', None)
            if (captured_scope and captured_scope!=current_scope) or (not captured_scope and any(v is not None for v in current_scope.values())):
                db.execute("UPDATE outbox SET status='failed',last_error='机器人负责范围已变更，旧消息已取消发送' WHERE id=?",(row['id'],))
                return 'failed'
            # Recheck the business notice against the destination's current scope.
            # A channel's venue permissions may change while a message is queued.
            with connect(self.ctx.db_path) as business:
                has_notices = business.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_jobs'").fetchone()
                if has_notices:
                    prefix = (self.ctx.channel_id or self.ctx.chat_id) + ':notice:'
                    if row['dedupe_key'].startswith(prefix):
                        notice_key = row['dedupe_key'][len(prefix):].rsplit(':',1)[0]
                        notice = business.execute('SELECT * FROM notification_jobs WHERE dedupe_key=?',(notice_key,)).fetchone()
                        if notice:
                            from .service import Service
                            scoped=Service(platform.context_for_channel(self.ctx.channel_id))
                            try:
                                if notice['venue_id'] is not None:
                                    scoped.venue(notice['venue_id'])
                                session_ids=json.loads(notice['session_ids']) if 'session_ids' in notice.keys() else None
                                if notice['tournament_id'] is not None:
                                    event=business.execute('SELECT * FROM tour_events WHERE id=?',(notice['tournament_id'],)).fetchone()
                                    if not event:raise PermissionError('赛事已不存在')
                                    scoped.assert_event_scope(dict(event))
                                if session_ids is not None:
                                    from .scope import snapshot
                                    captured_sessions=json.loads(notice['session_snapshots']) if 'session_snapshots' in notice.keys() else None
                                    for sid in session_ids:
                                        activity=business.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
                                        if not activity:raise PermissionError('活动已调整')
                                        scoped.assert_session_scope(dict(activity))
                                        if captured_sessions is not None and snapshot(activity) not in captured_sessions:
                                            raise PermissionError('活动时间已调整，旧通知不再发送')
                                elif notice['venue_id'] is not None and notice['tournament_id'] is None and scoped.scope_context().slot_keys is not None:
                                    raise PermissionError('活动通知缺少负责时段')
                            except (PermissionError,ValueError):
                                db.execute("UPDATE outbox SET status='failed',last_error='目的群的场地或时段权限已撤销，旧通知不再发送' WHERE id=?",(row['id'],))
                                return 'failed'
            if live and not bot['webhook_url']:
                db.execute("UPDATE outbox SET status='failed',last_error='未配置发送Webhook' WHERE id=?", (row['id'],))
                return 'failed'
            db.execute("UPDATE outbox SET status='sending',attempts=attempts+1,last_attempt_at=? WHERE id=?", (stamp(at), row['id']))
        status, error = 'simulated', None
        if live:
            owned = client is None
            client = client or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
            try:
                from .integrations import outbound_payload, accepted
                payload = outbound_payload(json.loads(row['payload']), bot['webhook_url'], platform.root,
                                           event_id=row['id'], group_id=bot['chat_id'])
                response = await client.post(bot['webhook_url'], json=payload, timeout=15)
                if response.status_code >= 500:
                    status, error = 'unknown', f'HTTP {response.status_code}，结果待核实'
                else:
                    status = 'sent' if accepted(response, bot['webhook_url'], platform.root) else 'failed'
                    error = None if status == 'sent' else f'HTTP {response.status_code}，平台未接受消息'
            except Exception:
                status, error = 'unknown', '发送中断或响应无法确认，请人工核实；不会自动重发'
            finally:
                if owned:
                    await client.aclose()
        with connect(self.path, True) as db:
            db.execute('UPDATE outbox SET status=?,last_error=? WHERE id=?', (status, error, row['id']))
        if status == 'sent':
            from .bot_connection import record_sent
            record_sent(platform,bot,robot,at)
        return status

    def cancel(self, message_id):
        if not self.ctx.is_admin:
            raise PermissionError('需要管理员权限')
        with connect(self.path, True) as db:
            changed = db.execute("UPDATE outbox SET status='cancelled' WHERE id=? AND channel_id=? AND status='pending'", (message_id,self.ctx.channel_id)).rowcount
            if not changed:
                raise ValueError('仅待发送消息可撤回')
        with connect(self.ctx.db_path,True) as db:
            audit(db, self.ctx.actor, 'message.cancel', {'message_id': message_id,'channel_id':self.ctx.channel_id})
        return {'ok': True}
