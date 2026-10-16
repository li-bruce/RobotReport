"""Transactional identity events, with one durable mention per related group.

Business mutations only call ``record_identity_change`` on their own connection.
The scheduler later creates transport outbox rows, using an event-derived key.
An uncertain send is never retried: it may already have reached the group.
"""
from datetime import timedelta
import json
import uuid

from .db import audit, connect, dumps, now, stamp
from .messages import Outbox, escape, mention
from .bot_capabilities import supports


def ensure_schema(db):
    # Do not use executescript here: hooks run inside the business transaction.
    db.execute('''CREATE TABLE IF NOT EXISTS identity_notification_events(
        id TEXT PRIMARY KEY,scope_id TEXT NOT NULL,user_id TEXT NOT NULL,
        member_name TEXT NOT NULL,kind TEXT NOT NULL,before_json TEXT NOT NULL,
        after_json TEXT NOT NULL,created_by TEXT NOT NULL,source TEXT NOT NULL,
        preferred_channel_id TEXT NOT NULL DEFAULT '',preferred_chat_id TEXT NOT NULL DEFAULT '',
        recipient_chats TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,sent_at TEXT,last_error TEXT,next_attempt_at TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS identity_notification_deliveries(
        event_id TEXT NOT NULL REFERENCES identity_notification_events(id),
        chat_id TEXT NOT NULL,channel_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',outbox_ids TEXT NOT NULL DEFAULT '[]',
        last_error TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(event_id,chat_id))''')
    db.execute('''CREATE INDEX IF NOT EXISTS identity_notification_pending
        ON identity_notification_events(status,created_at)''')


def _snapshot(kind, value):
    value = value or {}
    if kind == 'membership':
        return {'status': value.get('status')}
    if kind == 'roles':
        return {'roles': sorted(set(str(v) for v in value.get('roles', [])))}
    if kind == 'level':
        return {'level': value.get('level'), 'level_status': value.get('level_status')}
    if kind == 'groups':
        groups = value.get('groups', [])
        return {'groups': sorted([{'id': g['id'], 'name': str(g['name'])}
                                  for g in groups], key=lambda g: str(g['id']))}
    raise ValueError('身份通知类型无效')


def record_identity_change(db, ctx, user_id, kind, before, after, at=None):
    """Record one committed change; no network, new connection or historical replay.

    Only whitelisted business fields enter the event. Phone numbers, internal
    review notes, raw contexts and registration credentials are never copied.
    A simulator still changes business data, but its notices remain suppressed.
    """
    old, new = _snapshot(kind, before), _snapshot(kind, after)
    if old == new:
        return None
    ensure_schema(db)
    row = db.execute('SELECT name FROM profiles WHERE chat_id=? AND user_id=?',
                     (ctx.scope_id, user_id)).fetchone()
    name = str((after or {}).get('name') or (row['name'] if row else '') or '会员')
    if name == str(user_id):
        name = '会员'
    chats = sorted({r[0] for r in db.execute(
        'SELECT chat_id FROM identity_observations WHERE scope_id=? AND user_id=?',
        (ctx.scope_id, user_id)) if r[0]})
    preferred = ctx.channel_id if ctx.source in ('callback', 'registration_form') and ctx.chat_id in chats else ''
    event_id = uuid.uuid4().hex
    status = 'suppressed' if ctx.source in ('simulator', 'preview', 'dry_run') else 'pending'
    db.execute('''INSERT INTO identity_notification_events(
        id,scope_id,user_id,member_name,kind,before_json,after_json,created_by,source,
        preferred_channel_id,preferred_chat_id,recipient_chats,status,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (event_id,ctx.scope_id,user_id,name,kind,dumps(old),dumps(new),ctx.actor,ctx.source,
         preferred,ctx.chat_id if preferred else '',dumps(chats),status,stamp(at)))
    return event_id


ROLE_LABELS = {
    'club_admin': '后台管理员', 'member_manager': '会员管理员',
    'competition_manager': '赛事管理员', 'tournament_manager': '赛事管理员',
    'activity_manager': '活动管理员', 'message_manager': '消息管理员',
    'robot_manager': '机器人管理员', 'settings_manager': '设置管理员',
    'role_manager': '角色管理员',
}


def _role_name(role):
    # The role registry is authoritative when installed, with a safe fallback
    # for legacy/custom names. Import lazily to avoid domain-hook cycles.
    try:
        from .security import ROLE_DEFINITIONS
        if isinstance(ROLE_DEFINITIONS, dict):
            item = ROLE_DEFINITIONS.get(role, {})
            if isinstance(item, dict) and item.get('name'):
                return item['name']
        elif isinstance(ROLE_DEFINITIONS, (list, tuple)):
            found = next((r.get('name') for r in ROLE_DEFINITIONS if r.get('id') == role), None)
            if found:
                return found
    except (ImportError, AttributeError):
        pass
    return ROLE_LABELS.get(role, '管理角色')


def event_text(event):
    old, new = json.loads(event['before_json']), json.loads(event['after_json'])
    title, detail = '会员资料已更新', ''
    if event['kind'] == 'membership':
        state = new['status']
        if state == 'approved':
            title = '会员资格已恢复' if old['status'] == 'suspended' else '注册审核已通过'
            detail = '会员身份已生效。发送 **帮助** 查看可用功能；报名还需符合有效会籍及活动要求。'
        elif state == 'rejected':
            title, detail = '注册审核未通过', '请联系会员管理员核对资料，再重新提交注册申请。'
        elif state == 'suspended':
            title, detail = '会员资格已停用', '会员业务权限已暂停。如需恢复，请联系会员管理员。'
        elif state == 'pending':
            title, detail = '会员资料等待审核', '资料已进入审核流程，审核通过后会收到通知。'
        else:
            title, detail = '会员资格已变更', '请发送 **注册** 查看当前注册状态。'
    elif event['kind'] == 'roles':
        added = set(new['roles']) - set(old['roles'])
        removed = set(old['roles']) - set(new['roles'])
        title = '管理角色已更新'
        pieces = []
        if added:
            pieces.append('新增：**' + '、'.join(escape(_role_name(r)) for r in sorted(added)) + '**')
        if removed:
            pieces.append('撤销：**' + '、'.join(escape(_role_name(r)) for r in sorted(removed)) + '**')
        detail = '\n\n'.join(pieces) + '\n\n权限已按最新角色调整。'
    elif event['kind'] == 'level':
        title = '级别审核已更新'
        detail = ('审核通过，当前为 **' + str(new['level']) + '级**。'
                  if new['level_status'] == 'approved' and new['level'] is not None
                  else '本次级别审核未通过，当前显示为 **无级别**。')
    elif event['kind'] == 'groups':
        title = '会员组别已更新'
        detail = '当前组别：**' + ('、'.join(escape(g['name']) for g in new['groups']) or '未分组') + '**。'
    return '# ' + title + '\n\n' + mention(event['user_id'], event['member_name']) + '\n\n' + detail


class IdentityNotifications:
    def __init__(self, platform, clock=now):
        self.platform, self.clock = platform, clock
        self.path = platform.workspace_context().db_path
        with connect(self.path, True) as db:
            ensure_schema(db)

    def _event(self, event_id):
        with connect(self.path) as db:
            row = db.execute('SELECT * FROM identity_notification_events WHERE id=?', (event_id,)).fetchone()
        if not row:
            raise ValueError('身份通知不存在')
        return dict(row)

    def _channels(self, event, chats):
        groups = {chat: [] for chat in chats}
        for row in self.platform.list_channels(active_only=True):
            if row['chat_id'] in groups:
                groups[row['chat_id']].append(row)
        selected = {}
        for chat, rows in groups.items():
            # Identity membership is shared across venues: activity slot limits
            # do not hide an identity notice in the person's own related group.
            rows = [r for r in rows if self.platform.channel(r['id'])['webhook_url']]
            rows = [r for r in rows if self._can_notify(self.platform.context_for_channel(r['id']))]
            rows.sort(key=lambda r: (r['id'] != event['preferred_channel_id'], r['id']))
            if rows:
                selected[chat] = rows[0]['id']
        return selected

    def _prepare(self, event):
        chats = json.loads(event['recipient_chats'])
        if not chats:
            # A member added on the website may not have spoken to a robot yet.
            # Keep the event waiting until a genuinely observed source exists.
            with connect(self.path) as db:
                chats = sorted({r[0] for r in db.execute(
                    'SELECT chat_id FROM identity_observations WHERE scope_id=? AND user_id=?',
                    (event['scope_id'], event['user_id'])) if r[0]})
        selected = self._channels(event, chats)
        at = stamp(self.clock())
        with connect(self.path, True) as db:
            if chats:
                db.execute('UPDATE identity_notification_events SET recipient_chats=? WHERE id=?',
                           (dumps(chats), event['id']))
            for chat in chats:
                cid = selected.get(chat, '')
                db.execute('''INSERT OR IGNORE INTO identity_notification_deliveries
                    (event_id,chat_id,channel_id,status,last_error,updated_at) VALUES(?,?,?,?,?,?)''',
                    (event['id'],chat,cid,'pending' if cid else 'blocked',
                     None if cid else '关联群暂无可发送管理通知的机器人，请开启群内管理并检查发送配置',at))
                if cid:
                    db.execute('''UPDATE identity_notification_deliveries SET channel_id=?,status='pending',
                        last_error=NULL,updated_at=? WHERE event_id=? AND chat_id=? AND channel_id=''
                        AND outbox_ids='[]' AND status IN ('pending','blocked')''',
                        (cid,at,event['id'],chat))
        return chats

    @staticmethod
    def _can_notify(ctx):
        # Registration is a member-facing entry point, not permission to publish
        # administrative decisions. Select one management robot in each group.
        return supports(ctx, 'management')

    def dispatch(self, event_id):
        event = self._event(event_id)
        if event['status'] in ('suppressed','sent','simulated','unknown','cancelled'):
            return self.get(event_id)
        self._prepare(event)
        with connect(self.path) as db:
            deliveries = [dict(r) for r in db.execute(
                'SELECT * FROM identity_notification_deliveries WHERE event_id=? ORDER BY chat_id', (event_id,))]
        for item in deliveries:
            if item['status'] not in ('pending','blocked') or not item['channel_id']:
                continue
            try:
                ctx = self.platform.context_for_channel(item['channel_id'])
                if not self._can_notify(ctx):
                    raise PermissionError('机器人未开启群内管理')
                if ctx.chat_id != item['chat_id']:
                    raise ValueError('群绑定已变更')
                if not self.platform.channel(item['channel_id'])['webhook_url']:
                    raise ValueError('未配置发送Webhook')
                ids = Outbox(ctx).enqueue(event_text(event), 'identity:' + event_id)
                state, error = 'queued', None
            except (ValueError, PermissionError):
                ids, state, error = [], 'blocked', '原通知机器人或群绑定不可用，请检查群内管理和发送配置后重试'
            except Exception:
                # A crash after transport commit is safe: the same event key
                # recovers the same outbox rows, never a new sender or message.
                ids, state, error = [], 'blocked', '通知入队暂未完成，将安全重试'
            with connect(self.path, True) as db:
                db.execute('''UPDATE identity_notification_deliveries SET status=?,outbox_ids=?,
                    last_error=?,updated_at=? WHERE event_id=? AND chat_id=?''',
                    (state,dumps(ids),error,stamp(self.clock()),event_id,item['chat_id']))
        self._refresh(event_id)
        return self.get(event_id, refresh=False)

    def _refresh(self, event_id):
        event = self._event(event_id)
        if event['status'] == 'suppressed':
            return
        with connect(self.path) as db:
            deliveries = [dict(r) for r in db.execute(
                'SELECT * FROM identity_notification_deliveries WHERE event_id=?', (event_id,))]
        for item in deliveries:
            ids = json.loads(item['outbox_ids'])
            if not ids:
                continue
            try:
                channel = self.platform.channel(item['channel_id'])
                path = self.platform.root_for(channel['bot_id']) / 'transport.sqlite3'
                invalid_destination = (not channel['active'] or not channel['bot_active'] or
                                       channel['chat_id'] != item['chat_id'])
                invalid_sender = (not invalid_destination and not self._can_notify(
                    self.platform.context_for_channel(item['channel_id'])))
                if invalid_destination or invalid_sender:
                    # Disabled channels are absent from the scheduler's send
                    # loop. Mark their untouched rows visibly failed instead
                    # of leaving a permanent, misleading "queued" receipt.
                    with connect(path, True) as db:
                        placeholders = ','.join('?' for _ in ids)
                        db.execute(f'''UPDATE outbox SET status='failed',last_error=?
                            WHERE channel_id=? AND id IN ({placeholders}) AND status='pending' ''',
                            ['机器人未开启群内管理，管理通知未发送' if invalid_sender
                             else '身份通知目的群已停用或变更', item['channel_id']] + ids)
                with connect(path) as db:
                    placeholders = ','.join('?' for _ in ids)
                    rows = [dict(r) for r in db.execute(
                        f'SELECT status,last_error FROM outbox WHERE channel_id=? AND id IN ({placeholders})',
                        [item['channel_id']] + ids)]
                states = [r['status'] for r in rows]
                state = self._aggregate(states, empty='unknown') if len(rows) == len(ids) else 'unknown'
                error = {'failed': '消息未被平台接受，请检查消息队列', 'unknown': '发送结果待核实，禁止自动重发',
                         'cancelled': '消息已取消', 'partial': '消息部分发送，请检查消息队列'}.get(state)
                if invalid_sender and state == 'failed':
                    error = '原通知机器人已关闭群内管理，请检查消息队列'
            except (ValueError, PermissionError):
                state, error = 'unknown', '原发送通道无法核对，请人工检查消息队列'
            item['status'], item['last_error'] = state, error
            with connect(self.path, True) as db:
                db.execute('''UPDATE identity_notification_deliveries SET status=?,last_error=?,updated_at=?
                    WHERE event_id=? AND chat_id=?''',
                    (state,error,stamp(self.clock()),event_id,item['chat_id']))
        status = self._aggregate([r['status'] for r in deliveries], empty='blocked')
        error = next((r['last_error'] for r in deliveries if r['last_error']), None)
        if not deliveries:
            error = '尚未找到该会员关联的群，等待用户在已接入群与机器人交互'
        sent_at = stamp(self.clock()) if status in ('sent','simulated') else None
        next_attempt = stamp(self.clock() + timedelta(seconds=60)) if status in ('blocked','partial') else None
        with connect(self.path, True) as db:
            db.execute('''UPDATE identity_notification_events SET status=?,last_error=?,
                sent_at=COALESCE(sent_at,?),next_attempt_at=? WHERE id=?''',
                (status,error,sent_at,next_attempt,event_id))

    @staticmethod
    def _aggregate(states, empty='blocked'):
        if not states:
            return empty
        states = set(states)
        if states <= {'sent'}:
            return 'sent'
        if states <= {'simulated'}:
            return 'simulated'
        if states <= {'sent','simulated'}:
            return 'partial'
        if states & {'pending','sending','queued'}:
            return 'queued'
        if len(states) > 1:
            return 'partial'
        return next(iter(states))

    def get(self, event_id, refresh=True):
        # Listing a newly committed event must not postpone its first dispatch.
        # Only queued sends have transport receipts that can change on a read.
        if refresh and self._event(event_id)['status'] == 'queued':
            self._refresh(event_id)
        event = self._event(event_id)
        with connect(self.path) as db:
            deliveries = [dict(r) for r in db.execute(
                '''SELECT chat_id,channel_id,status,last_error FROM identity_notification_deliveries
                WHERE event_id=? ORDER BY chat_id''', (event_id,))]
        can_retry = (event['status'] in ('blocked','partial') and
                     (any(r['status'] == 'blocked' for r in deliveries) or not deliveries))
        return {k: event[k] for k in ('id','member_name','user_id','kind','status','created_at','sent_at','last_error')} | {
            'deliveries': deliveries, 'can_retry': can_retry}

    def list(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('通知记录数量应为1至200')
        with connect(self.path) as db:
            ids = [r[0] for r in db.execute('SELECT id FROM identity_notification_events ORDER BY created_at DESC,rowid DESC LIMIT ?', (limit,))]
        return [self.get(event_id) for event_id in ids]

    def retry(self, event_id, actor='admin'):
        item = self.get(event_id)
        if not item['can_retry']:
            raise ValueError('仅未入队的等待通知可以重试；已发送或结果待核实的消息不会重发')
        with connect(self.path, True) as db:
            db.execute('UPDATE identity_notification_events SET next_attempt_at=NULL WHERE id=?', (event_id,))
            audit(db, actor, 'identity_notification.retry', {'id': event_id})
        return self.dispatch(event_id)

    def recover(self):
        with connect(self.path) as db:
            ids = [r[0] for r in db.execute('''SELECT id FROM identity_notification_events
                WHERE status IN ('pending','blocked','queued','partial')
                AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at,rowid LIMIT 100''',
                (stamp(self.clock()),))]
        for event_id in ids:
            self.dispatch(event_id)
