"""Authentication and registry. IDs and paths are generated exclusively here."""
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode
import hashlib
import hmac
import json
import re
import secrets
import uuid
from .db import connect, stamp, dumps, now
from .workspace import initialize_workspace, initialize_transport
from .bot_capabilities import normalize_capabilities, DEFAULT_CAPABILITIES
from .registry_schema import GROUP_BINDING_GUARDS
from . import env_settings


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def identifier(value):
    value = str(value or '')
    if not re.fullmatch(r'[\w@.:-]{1,100}', value, re.ASCII):
        raise ValueError('身份标识格式无效')
    return value


def validate_webhook(value, platform_root=None):
    if not value:
        return ''
    from .integrations import json_webhook
    approved = {('xz.wps.cn', '/api/v1/webhook/send')} | env_settings.extra_endpoints(platform_root)
    try:
        url = urlsplit(value)
        query = parse_qs(url.query)
        if (json_webhook(value, platform_root) and url.scheme == 'https' and url.port in (None,443)
                and not (url.username or url.password or url.fragment) and len(value) <= 2048
                and not any(c in value for c in ('\n','\r','\0'))):
            return value
        valid = not (url.scheme != 'https' or (url.hostname, url.path) not in approved or url.port not in (None, 443)
            or url.username or url.password or url.fragment
            or set(query) != {'key'} or len(query['key']) != 1 or not query['key'][0])
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('Webhook 须使用已在本地环境文件配置的 HTTPS 发送地址')
    return 'https://' + url.hostname + url.path + '?' + urlencode({'key': query['key'][0]})


PERMISSION_DEFINITIONS = (
    {'id': 'member.manage', 'name': '会员管理'},
    {'id': 'activity.manage', 'name': '活动管理'},
    {'id': 'event.manage', 'name': '赛事管理'},
    {'id': 'message.manage', 'name': '消息管理'},
    {'id': 'bot.manage', 'name': '机器人管理'},
    {'id': 'system.manage', 'name': '系统设置'},
    {'id': 'roles.manage', 'name': '角色授权'},
)
ALL_PERMISSIONS = tuple(item['id'] for item in PERMISSION_DEFINITIONS)
ROLE_DEFINITIONS = (
    {'id': 'club_admin', 'name': '后台管理员', 'description': '管理全部场地、会员、赛事、机器人和设置，并授予管理角色', 'permissions': list(ALL_PERMISSIONS)},
    {'id': 'member_manager', 'name': '会员管理员', 'description': '审核注册、管理会员资料、等级、分组、会籍和导入人员', 'permissions': ['member.manage']},
    {'id': 'activity_manager', 'name': '活动管理员', 'description': '管理活动场地、场次、报名、提醒和报名统计', 'permissions': ['activity.manage']},
    {'id': 'event_manager', 'name': '赛事管理员', 'description': '管理每日比赛和大型赛事、赛程、比分及赛事职务', 'permissions': ['event.manage']},
    {'id': 'message_manager', 'name': '消息管理员', 'description': '编辑和发布群消息', 'permissions': ['message.manage']},
    {'id': 'bot_manager', 'name': '机器人管理员', 'description': '管理机器人和群通道', 'permissions': ['bot.manage']},
    {'id': 'system_manager', 'name': '系统管理员', 'description': '管理 AI、主题和系统设置', 'permissions': ['system.manage']},
)


def permissions_for_roles(roles):
    selected = set(roles)
    return tuple(sorted({permission for role in ROLE_DEFINITIONS if role['id'] in selected
                         for permission in role['permissions']}))


def current_authority(context, db=None):
    """Re-read callback authority; a stale or forged context cannot retain a role."""
    if context.source not in ('callback', 'simulator'):
        return (context.is_admin, tuple(ALL_PERMISSIONS) if context.is_admin else tuple(context.permissions))
    if db is None:
        with connect(context.db_path) as connection:
            return current_authority(context, connection)
    approved = db.execute("SELECT 1 FROM member_records WHERE chat_id=? AND user_id=? AND status='approved'",
                          (context.scope_id, context.actor)).fetchone()
    if not approved:
        return False, ()
    roles = [row[0] for row in db.execute("SELECT role FROM user_roles WHERE user_id=? AND scope_type='club' AND scope_id=?",
                                         (context.actor, context.scope_id))]
    return 'club_admin' in roles, permissions_for_roles(roles)


def refresh_authority(context):
    if context.source not in ('callback', 'simulator'):
        # Full backend authority comes from is_admin, never an inherited grant list.
        return replace(context, permissions=())
    admin, permissions = current_authority(context)
    return replace(context, is_admin=admin, permissions=permissions)


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    bot_ids: tuple


@dataclass(frozen=True)
class BotContext:
    # First six positional fields are preserved for old integrations.
    id: str
    root: Path
    chat_id: str
    actor: str
    is_admin: bool
    source: str
    scope_id: str = 'club'
    transport_path: Path | None = None
    bot_root: Path | None = None
    platform_root: Path | None = None
    channel_id: str = ''
    venue_ids: tuple = ()
    default_venue_id: int = 1
    receive_commands: bool = True
    daily_notifications: bool = True
    venue_restricted: bool = False
    slot_keys: tuple | None = None
    permissions: tuple = ()
    capabilities: tuple | None = None
    bot_name: str = ''
    capability_rules: tuple = ()
    policy_clock: object = now

    def can(self, permission, db=None):
        if permission not in ALL_PERMISSIONS:
            return False
        return permission in current_authority(self, db)[1]

    def require(self, permission, db=None):
        from .bot_access import require_access
        require_access(self, db=db)
        if not self.can(permission, db):
            name = next((item['name'] for item in PERMISSION_DEFINITIONS if item['id'] == permission), permission)
            raise PermissionError(f'需要{name}权限')

    @property
    def db_path(self):
        return self.root / 'business.sqlite3'

    @property
    def transport_db_path(self):
        return self.transport_path or self.db_path


class Platform:
    def __init__(self, root, bootstrap_token=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'registry.sqlite3'
        with connect(self.path) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS bots(id TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,chat_id TEXT NOT NULL,webhook_url TEXT NOT NULL DEFAULT '',robot_key TEXT NOT NULL DEFAULT '',callback_hash TEXT NOT NULL,legacy INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,role TEXT NOT NULL,bot_ids TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bot_channels(id TEXT PRIMARY KEY,bot_id TEXT NOT NULL REFERENCES bots(id),name TEXT NOT NULL,chat_id TEXT NOT NULL,webhook_url TEXT NOT NULL DEFAULT '',robot_key TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,default_venue_id INTEGER NOT NULL DEFAULT 1,venue_ids TEXT NOT NULL DEFAULT '[]',receive_commands INTEGER NOT NULL DEFAULT 1,daily_notifications INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS unique_channel_destination ON bot_channels(webhook_url) WHERE webhook_url<>'' AND active=1;
            DROP INDEX IF EXISTS unique_active_destination;
            ''')
        with connect(self.path, True) as db:
            bot_columns = {r['name'] for r in db.execute('PRAGMA table_info(bots)')}
            for field, default in (('deleted_at', ''), ('user_policy', 'null'), ('capability_rules','[]')):
                if field not in bot_columns:
                    db.execute(f"ALTER TABLE bots ADD COLUMN {field} TEXT NOT NULL DEFAULT '{default}'")
            for table, fields in (('bots', ('venue_ids','slot_keys','capabilities')), ('bot_channels', ('slot_keys',))):
                columns = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                for field in fields:
                    if field not in columns:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {field} TEXT NOT NULL DEFAULT 'null'")
            channel_columns = {r['name'] for r in db.execute('PRAGMA table_info(bot_channels)')}
            if 'archived' not in channel_columns:
                db.execute('ALTER TABLE bot_channels ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
            if 'archived_at' not in channel_columns:
                db.execute("ALTER TABLE bot_channels ADD COLUMN archived_at TEXT NOT NULL DEFAULT ''")
            self._migrate_channel_uniqueness(db)
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS unique_current_bot_group ON bot_channels(bot_id,chat_id) WHERE archived=0')
            self._guard_single_group(db)
        if bootstrap_token:
            if len(bootstrap_token) < 24:
                raise ValueError('平台令牌至少24字符')
            with connect(self.path, True) as db:
                db.execute("INSERT OR IGNORE INTO grants VALUES(?,?,'platform_admin','[]',1,?)", ('bootstrap-' + digest(bootstrap_token)[:12], digest(bootstrap_token), stamp()))
        with connect(self.path, True) as db:
            bots = [dict(r) for r in db.execute('SELECT * FROM bots ORDER BY created_at,id')]
            for bot in bots:
                if bot['chat_id'] and not db.execute('SELECT 1 FROM bot_channels WHERE bot_id=?', (bot['id'],)).fetchone():
                    db.execute('INSERT INTO bot_channels(id,bot_id,name,chat_id,webhook_url,robot_key,active,created_at) VALUES(?,?,?,?,?,?,?,?)', (uuid.uuid4().hex,bot['id'],'默认群',bot['chat_id'],bot['webhook_url'],bot['robot_key'],bot['active'],bot['created_at']))
            channel_ids = {r['bot_id']: r['id'] for r in db.execute('SELECT id,bot_id FROM bot_channels ORDER BY created_at DESC,id DESC')}
            from .bot_groups import ensure_schema
            ensure_schema(db)
        self.workspace_root = initialize_workspace(self.root, bots, channel_ids)
        from .bot_connection import ensure_schema as connection_schema
        connection_schema(self)

    @staticmethod
    def _guard_single_group(db):
        """Prevent new multi-group bindings without rewriting legacy history.

        Old installations may contain several current rows. Leave those intact
        so an administrator can explicitly remove obsolete bindings. Pausing a
        connection still reserves its single group; only archiving releases it.
        """
        for statement in GROUP_BINDING_GUARDS.values():
            db.execute(statement.replace('CREATE TRIGGER ', 'CREATE TRIGGER IF NOT EXISTS ', 1))

    @staticmethod
    def _migrate_channel_uniqueness(db):
        """Retain archived routing identities while allowing a fresh connection.

        The old table-level UNIQUE covers historical rows too. Rebuild only that
        constraint, preserving every column and row (including future columns),
        and recreate all explicit indexes and triggers in the same transaction.
        """
        old_unique = False
        for index in db.execute('PRAGMA index_list(bot_channels)'):
            if index['origin'] == 'u':
                columns = [row['name'] for row in db.execute('PRAGMA index_info("' + index['name'].replace('"', '""') + '")')]
                old_unique = old_unique or columns == ['bot_id', 'chat_id']
        if not old_unique:
            return
        sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bot_channels'").fetchone()[0]
        sql, changed = re.subn(r',\s*UNIQUE\s*\(\s*bot_id\s*,\s*chat_id\s*\)', '', sql, flags=re.IGNORECASE)
        if changed != 1:
            raise RuntimeError('群通道历史索引结构异常，迁移已停止')
        schema = [row['sql'] for row in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='bot_channels' AND type IN ('index','trigger') AND sql IS NOT NULL")]
        sql = re.sub(r'^(CREATE\s+TABLE\s+)(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?bot_channels[\"`\]]?', r'\1bot_channels_v211', sql, count=1, flags=re.IGNORECASE)
        db.execute(sql)
        names = ','.join('"' + row['name'].replace('"', '""') + '"' for row in db.execute('PRAGMA table_info(bot_channels)'))
        db.execute(f'INSERT INTO bot_channels_v211({names}) SELECT {names} FROM bot_channels')
        db.execute('DROP TABLE bot_channels')
        db.execute('ALTER TABLE bot_channels_v211 RENAME TO bot_channels')
        for statement in schema:
            db.execute(statement)

    def authenticate(self, token):
        with connect(self.path) as db:
            row = db.execute('SELECT * FROM grants WHERE token_hash=? AND active=1', (digest(token),)).fetchone()
        if not row:
            raise PermissionError('管理令牌无效或已撤销')
        return Principal(row['id'], row['role'], tuple(json.loads(row['bot_ids'])))

    def grant(self, principal, bot_ids, token=None):
        self.require_platform(principal)
        if not bot_ids:
            raise ValueError('请选择授权机器人')
        for bot_id in bot_ids:
            self.bot(bot_id)
        token = token or secrets.token_urlsafe(36)
        if len(token) < 24:
            raise ValueError('令牌至少24字符')
        grant_id = uuid.uuid4().hex
        with connect(self.path, True) as db:
            for bot_id in bot_ids:
                self._live_record(db, bot_id)
            db.execute("INSERT INTO grants VALUES(?,?,'bot_admin',?,1,?)", (grant_id, digest(token), dumps(bot_ids), stamp()))
        return {'id': grant_id, 'token': token, 'bot_ids': bot_ids}

    @staticmethod
    def require_platform(principal):
        if principal.role != 'platform_admin':
            raise PermissionError('需要平台管理员权限')

    @staticmethod
    def _require_bot(principal, bot_id):
        if principal.role != 'platform_admin' and bot_id not in principal.bot_ids:
            raise PermissionError('无权访问此机器人')

    def bot(self, bot_id, active=False, *, include_deleted=False):
        if not isinstance(bot_id, str) or not re.fullmatch(r'[a-f0-9]{32}', bot_id):
            raise ValueError('机器人不存在')
        with connect(self.path) as db:
            row = db.execute('SELECT * FROM bots WHERE id=?', (bot_id,)).fetchone()
        if not row:
            raise ValueError('机器人不存在')
        if row['deleted_at'] and not include_deleted:
            raise PermissionError('机器人已删除')
        if active and not row['active']:
            raise PermissionError('机器人已停用')
        return dict(row)

    def root_for(self, bot_id):
        self.bot(bot_id, include_deleted=True)
        return self.root / 'bots' / bot_id

    @staticmethod
    def _live_record(db, bot_id):
        row = db.execute('SELECT * FROM bots WHERE id=?', (bot_id,)).fetchone()
        if not row or row['deleted_at']:
            raise PermissionError('机器人已删除或不存在，请刷新页面')
        return row

    def remove(self, principal, bot_id):
        self.require_platform(principal)
        self.bot(bot_id, include_deleted=True)
        with env_settings.change(self.root, bot_id=bot_id), connect(self.path, True) as db:
            row = db.execute('SELECT deleted_at FROM bots WHERE id=?', (bot_id,)).fetchone()
            if row['deleted_at']:
                return {'ok': True, 'disconnected': 0}
            at = stamp()
            count = db.execute('UPDATE bot_channels SET active=0,archived=1,archived_at=? WHERE bot_id=? AND archived=0', (at,bot_id)).rowcount
            db.execute('UPDATE bots SET active=0,deleted_at=? WHERE id=?', (at,bot_id))
            self._sync_primary_channel(db,bot_id)
            # Mixed grants keep access to their other robots.
            for grant in db.execute("SELECT id,bot_ids FROM grants WHERE role='bot_admin' AND active=1").fetchall():
                ids = json.loads(grant['bot_ids'])
                if bot_id in ids:
                    ids = [value for value in ids if value!=bot_id]
                    db.execute('UPDATE grants SET bot_ids=?,active=? WHERE id=?', (dumps(ids),int(bool(ids)),grant['id']))
        return {'ok': True, 'disconnected': count}

    def channel(self, channel_id, active=False):
        if not isinstance(channel_id, str) or not re.fullmatch(r'[a-f0-9]{32}', channel_id):
            raise ValueError('群通道不存在')
        with connect(self.path) as db:
            row = db.execute('SELECT c.*,b.name AS bot_name,b.active AS bot_active,b.capabilities AS bot_capabilities,b.venue_ids AS bot_venue_ids,b.slot_keys AS bot_slot_keys FROM bot_channels c JOIN bots b ON b.id=c.bot_id WHERE c.id=?', (channel_id,)).fetchone()
        if not row:
            raise ValueError('群通道不存在')
        result = dict(row)
        result['venue_ids'] = json.loads(result['venue_ids'])
        result['slot_keys'] = json.loads(result['slot_keys'])
        if active and (result['archived'] or not result['active'] or not result['bot_active']):
            raise PermissionError('机器人或群通道已停用')
        return result

    def visible_channel(self, channel):
        result = {k: channel[k] for k in ('id','bot_id','name','chat_id','default_venue_id','created_at')}
        result['channel_id'] = result['id']
        result['group_id'] = channel.get('group_id', '')
        result['awaiting_callback'] = not bool(channel['chat_id'])
        result['slot_keys'] = json.loads(channel['slot_keys']) if isinstance(channel['slot_keys'], str) else channel['slot_keys']
        result['bot_name'] = channel.get('bot_name', '')
        result['venue_ids'] = json.loads(channel['venue_ids']) if isinstance(channel['venue_ids'], str) else list(channel['venue_ids'])
        result.update({k: bool(channel[k]) for k in ('active','receive_commands','daily_notifications')})
        result['archived'] = bool(channel.get('archived', False))
        result['archived_at'] = channel.get('archived_at', '')
        result['capabilities'] = list(normalize_capabilities(json.loads(channel.get('bot_capabilities', 'null'))))
        result['bot_active'] = bool(channel.get('bot_active', True))
        result['bot_venue_ids'] = json.loads(channel.get('bot_venue_ids', 'null'))
        result['bot_slot_keys'] = json.loads(channel.get('bot_slot_keys', 'null'))
        result['receive_configured'] = bool(channel['robot_key']) and not str(channel['robot_key']).startswith(('http://','https://'))
        result['send_configured'] = bool(channel['webhook_url'])
        result['configured'] = bool(channel['chat_id'] and channel['robot_key'] and channel['webhook_url'])
        from .bot_connection import summary
        result.update(summary(self,channel))
        return result

    def list_channels(self, principal=None, bot_id=None, active_only=False, include_archived=False):
        if principal is not None and bot_id:
            self._require_bot(principal, bot_id)
        with connect(self.path) as db:
            rows = [dict(r) for r in db.execute('SELECT c.*,b.name AS bot_name,b.active AS bot_active,b.capabilities AS bot_capabilities,b.venue_ids AS bot_venue_ids,b.slot_keys AS bot_slot_keys FROM bot_channels c JOIN bots b ON b.id=c.bot_id ORDER BY c.created_at,c.id')]
        return [self.visible_channel(r) for r in rows if (not bot_id or r['bot_id'] == bot_id)
                and (principal is None or principal.role == 'platform_admin' or r['bot_id'] in principal.bot_ids)
                and (include_archived or not r['archived'])
                and (not active_only or (r['active'] and r['bot_active'] and not r['archived'] and r['chat_id']))]

    channels = list_channels

    def visible_bot(self, bot, principal):
        channels = self.list_channels(principal, bot['id'])
        return {k: bot[k] for k in ('id','name','chat_id')} | {
            'active': bool(bot['active']), 'configured': any(c['configured'] and c['active'] for c in channels),
            'role': principal.role, 'legacy': bool(bot['legacy']), 'channel_count': len(channels),
            'channels': channels, 'admin_user_ids': [],
            'user_policy': json.loads(bot['user_policy']) if principal.role=='platform_admin' else None,
            'venue_ids': json.loads(bot['venue_ids']), 'slot_keys': json.loads(bot['slot_keys']),
            'capabilities': list(normalize_capabilities(json.loads(bot['capabilities']))),
            'capability_rules': json.loads(bot['capability_rules'])}

    def list(self, principal):
        with connect(self.path) as db:
            rows = [dict(x) for x in db.execute("SELECT * FROM bots WHERE deleted_at='' ORDER BY created_at,id")]
        return [self.visible_bot(x, principal) for x in rows if principal.role == 'platform_admin' or x['id'] in principal.bot_ids]

    def _scope_value(self, key, value):
        if value is None:
            return None
        if not isinstance(value, list) or len(value) > 300 or len(value) != len(set(map(str, value))):
            raise ValueError('负责范围须为不重复数组，null表示全部，空数组表示禁用')
        if key == 'venue_ids':
            if any(type(v) is not int or v < 1 for v in value):
                raise ValueError('场地范围必须使用场地编号')
            with connect(self.workspace_root / 'business.sqlite3') as db:
                known = {r[0] for r in db.execute('SELECT id FROM venues WHERE active=1')}
            if not set(value) <= known:
                raise ValueError('负责范围包含不存在或停用的场地')
        else:
            if any(not isinstance(v,str) or not re.fullmatch(r'(?:slot:\d+|special:[a-f0-9]{32}|legacy:\d+)',v) for v in value):
                raise ValueError('请选择有效的稳定场次标识')
            with connect(self.workspace_root / 'business.sqlite3') as db:
                known = {'slot:'+str(r[0]) for r in db.execute('SELECT id FROM slots')}
                from .scope import special_rules
                for row in db.execute('SELECT * FROM special_dates'):
                    known.update(s['slot_key'] for s in special_rules(row['venue_id'],row['date'],json.loads(row['slots'])))
                from .holidays import all_slots as holiday_slots
                for venue in db.execute('SELECT id FROM venues'):
                    known.update(s['slot_key'] for s in holiday_slots(db,venue[0]))
                if 'slot_key' in {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}:
                    known.update(r[0] for r in db.execute('SELECT DISTINCT slot_key FROM sessions'))
            if not set(value) <= known:
                raise ValueError('负责范围包含不存在的场次')
        return value

    def _save_bot_scope(self, bot_id, data):
        validated = {key:self._scope_value(key,value) for key,value in data.items()}
        if validated:
            with connect(self.path,True) as db:
                self._live_record(db,bot_id)
                db.execute('UPDATE bots SET '+','.join(k+'=?' for k in validated)+' WHERE id=?',
                           (*[dumps(v) for v in validated.values()],bot_id))

    def _channel_data(self, data, old=None):
        allowed = {'name','chat_id','group_id','webhook_url','robot_key','active','default_venue_id','venue_ids','receive_commands','daily_notifications','slot_keys'}
        if set(data) - allowed:
            raise ValueError('不支持的群通道配置字段')
        record = dict(old or {'name':'默认群','chat_id':'','webhook_url':'','robot_key':'','active':1,'default_venue_id':1,'venue_ids':[],'receive_commands':1,'daily_notifications':1,'slot_keys':None})
        record.setdefault('group_id','')
        if 'group_id' in data:
            if 'chat_id' in data or 'name' in data:
                raise ValueError('选择已有群即可，无需填写群名称或ID')
            from .bot_groups import get
            with connect(self.path) as db:
                group=get(db,data['group_id'])
            if not old or old.get('group_id')!=group['id']:
                record['chat_id']=''
            record.update(group_id=group['id'],name=group['name'])
        for key, value in data.items():
            if key=='group_id':
                continue
            if key == 'chat_id':
                value = identifier(value)
                if value!=record['chat_id']:
                    record['group_id']=''
            elif key == 'webhook_url':
                value = validate_webhook(value, self.root)
            elif key in ('active','receive_commands','daily_notifications'):
                if not isinstance(value, bool):
                    raise ValueError('开关必须为布尔值')
                value = int(value)
            elif key == 'slot_keys':
                value = self._scope_value(key, value)
            elif key == 'venue_ids':
                if not isinstance(value, list) or any(type(v) is not int or v < 1 for v in value) or len(value) != len(set(value)):
                    raise ValueError('场地范围必须为不重复的场地编号数组')
            elif key == 'default_venue_id':
                if type(value) is not int or value < 1:
                    raise ValueError('请选择默认场地')
            else:
                if not isinstance(value, str) or len(value) > (80 if key == 'name' else 512) or any(ord(c)<32 for c in value):
                    raise ValueError('群通道名称或密钥格式无效')
                value = value.strip()
                if key == 'robot_key' and value.startswith(('http://','https://')):
                    raise ValueError('Robot Key 不能填写网址。请粘贴发送 Webhook 地址，系统会自动提取 key。')
            record[key] = value
        if data.get('webhook_url') and 'robot_key' not in data:
            record['robot_key'] = parse_qs(urlsplit(record['webhook_url']).query).get('key', [record.get('robot_key') or secrets.token_urlsafe(32)])[0]
        elif 'webhook_url' in data and not record['webhook_url'] and 'robot_key' not in data:
            record['robot_key'] = ''
        if not record['receive_commands'] and not record['chat_id'] and record['group_id']:
            from .bot_groups import get
            with connect(self.path) as db:
                record['chat_id'] = get(db,record['group_id'])['chat_id']
            if not record['chat_id']:
                raise ValueError('仅发送连接请选择已识别的群；新群请先接收一次群消息完成识别')
        if (not record['chat_id'] and not record['group_id']) or not record['name']:
            raise ValueError('群名称和群ID不能为空')
        if 'venue_ids' in data and record['venue_ids'] and record['default_venue_id'] not in record['venue_ids']:
            raise ValueError('默认场地必须位于群通道场地范围内')
        with connect(self.workspace_root / 'business.sqlite3') as db:
            known = {r[0] for r in db.execute('SELECT id FROM venues WHERE active=1')}
        if any(k in data for k in ('venue_ids','default_venue_id')) and (not set(record['venue_ids'] or [record['default_venue_id']]) <= known or record['default_venue_id'] not in known):
            raise ValueError('群通道包含不存在或停用的场地')
        return record

    def save_channel(self, principal, bot_id, data, channel_id=None):
        self._require_bot(principal, bot_id)
        self.bot(bot_id)
        old = self.channel(channel_id) if channel_id else None
        if old and old['bot_id'] != bot_id:
            raise PermissionError('群通道不属于此机器人')
        if old and old['archived']:
            raise ValueError('此群绑定已移除，请新增群绑定；历史消息不会重新发送')
        record = self._channel_data(data, old)
        replacing = bool(old and (old['chat_id'] != record['chat_id'] or old.get('group_id','') != record['group_id']))
        if replacing and (old['webhook_url'] or old['robot_key']):
            if (not data.get('webhook_url')
                    or not record['webhook_url'] or not record['robot_key']):
                raise ValueError('变更群ID时请重新填写目标群的发送Webhook和机器人密钥；如仅纠正群ID，可填写原凭据确认')
        channel_id = uuid.uuid4().hex if replacing or not channel_id else channel_id
        secrets_data = {key:record[key] for key in ('webhook_url','robot_key')}
        with env_settings.change(self.root, bot_id=bot_id, bot_values=secrets_data), connect(self.path, True) as db:
            self._live_record(db,bot_id)
            if old:
                current = db.execute('SELECT * FROM bot_channels WHERE id=?', (old['id'],)).fetchone()
                if not current or current['archived'] or any((json.loads(current[k]) if k in ('venue_ids','slot_keys') else current[k]) != old[k]
                                                           for k in ('name','chat_id','group_id','webhook_url','robot_key','active','venue_ids','slot_keys','default_venue_id','receive_commands','daily_notifications')):
                    raise ValueError('群绑定已被调整，请刷新后重试')
            from .bot_groups import attach
            attach(db,record)
            collision = db.execute("SELECT id FROM bot_channels WHERE webhook_url=? AND webhook_url<>'' AND active=1 AND id<>?", (record['webhook_url'],old['id'] if replacing else channel_id)).fetchone()
            if record['active'] and collision:
                raise ValueError('该Webhook已用于另一个机器人群通道；同群多个机器人须分别配置各自的Webhook')
            duplicate = db.execute('SELECT id FROM bot_channels WHERE bot_id=? AND chat_id=? AND id<>? AND archived=0', (bot_id,record['chat_id'],old['id'] if replacing else channel_id)).fetchone()
            if duplicate:
                raise ValueError('该机器人已绑定此群')
            if (not old or replacing) and db.execute(
                    'SELECT 1 FROM bot_channels WHERE bot_id=? AND archived=0 AND id<>?',
                    (bot_id, old['id'] if old else channel_id)).fetchone():
                raise ValueError('机器人只能绑定一个群，请编辑现有绑定或先移除；同一群可以添加多个机器人')
            keys = ('name','chat_id','webhook_url','robot_key','active','default_venue_id','venue_ids','receive_commands','daily_notifications','slot_keys','group_id')
            values = [dumps(record[k]) if k in ('venue_ids','slot_keys') else record[k] for k in keys]
            if replacing:
                db.execute('UPDATE bot_channels SET active=0,archived=1,archived_at=? WHERE id=?', (stamp(),old['id']))
            if old and not replacing:
                db.execute('UPDATE bot_channels SET '+','.join(k+'=?' for k in keys)+' WHERE id=?', (*values,channel_id))
            else:
                db.execute('INSERT INTO bot_channels(id,bot_id,'+','.join(keys)+',created_at) VALUES('+','.join('?' for _ in range(len(keys)+3))+')', (channel_id,bot_id,*values,stamp()))
            self._sync_primary_channel(db, bot_id)
        result = self.visible_channel(self.channel(channel_id))
        if replacing:
            result['replaced_channel_id'] = old['id']
        return result

    @staticmethod
    def _sync_primary_channel(db, bot_id):
        primary = db.execute('SELECT chat_id,webhook_url,robot_key FROM bot_channels WHERE bot_id=? AND archived=0 ORDER BY created_at,id LIMIT 1', (bot_id,)).fetchone()
        values = tuple(primary) if primary else ('', '', '')
        db.execute('UPDATE bots SET chat_id=?,webhook_url=?,robot_key=? WHERE id=?', (*values,bot_id))

    def archive_channel(self, principal, bot_id, channel_id):
        self._require_bot(principal, bot_id)
        self.bot(bot_id)
        old = self.channel(channel_id)
        if old['bot_id'] != bot_id:
            raise PermissionError('群通道不属于此机器人')
        values = {'webhook_url':None, 'robot_key':None} if not old['archived'] else {}
        with env_settings.change(self.root, bot_id=bot_id, bot_values=values), connect(self.path, True) as db:
            db.execute('UPDATE bot_channels SET active=0,archived=1,archived_at=? WHERE id=? AND archived=0', (stamp(),channel_id))
            self._sync_primary_channel(db, bot_id)
        return self.visible_channel(self.channel(channel_id))

    def create(self, principal, data):
        self.require_platform(principal)
        if data.get('admin_user_ids'):
            raise ValueError('管理员角色请在会员与权限中单独授予，不能通过机器人配置授予')
        name = str(data.get('name','')).strip()
        if not name or len(name)>80 or any(ord(c)<32 for c in name):
            raise ValueError('机器人名称限1至80字')
        callback = data.get('callback_token') or secrets.token_urlsafe(36)
        if not isinstance(callback,str) or len(callback)<24:
            raise ValueError('回调令牌至少24字符')
        chat = identifier(data['chat_id']) if data.get('chat_id') else ''
        channel_data = {k:data[k] for k in ('chat_id','group_id','webhook_url','robot_key','default_venue_id') if k in data}
        record = self._channel_data(channel_data) if chat or data.get('group_id') else None
        scope_data = {k:self._scope_value(k,data[k]) for k in ('venue_ids','slot_keys') if k in data}
        capabilities = normalize_capabilities(data.get('capabilities', DEFAULT_CAPABILITIES))
        from .time_rules import normalize_capability_rules
        capability_rules = normalize_capability_rules(data.get('capability_rules'),capabilities)
        from .bot_access import normalize
        user_policy = normalize(data.get('user_policy'),self.workspace_context())
        bot_id = uuid.uuid4().hex
        secrets_data = {'callback_token':callback}
        if record:
            secrets_data.update({key:record[key] for key in ('webhook_url','robot_key')})
        with env_settings.change(self.root, bot_id=bot_id, bot_values=secrets_data), connect(self.path, True) as db:
            if record and record['webhook_url'] and db.execute("SELECT 1 FROM bot_channels WHERE webhook_url=? AND active=1", (record['webhook_url'],)).fetchone():
                raise ValueError('该Webhook已用于另一个群通道')
            db.execute('INSERT INTO bots(id,name,active,chat_id,webhook_url,robot_key,callback_hash,legacy,created_at,capabilities) VALUES(?,?,1,?,?,?,?,?,?,?)', (bot_id,name,chat,record['webhook_url'] if record else '',record['robot_key'] if record else '',digest(callback),int(bool(data.get('legacy',False))),stamp(),dumps(capabilities)))
            db.execute('UPDATE bots SET user_policy=? WHERE id=?',(dumps(user_policy),bot_id))
            db.execute('UPDATE bots SET capability_rules=? WHERE id=?',(dumps(capability_rules),bot_id))
            if record:
                from .bot_groups import attach
                attach(db,record)
                db.execute('INSERT INTO bot_channels(id,bot_id,name,chat_id,webhook_url,robot_key,default_venue_id,venue_ids,created_at,group_id) VALUES(?,?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex,bot_id,record['name'],chat,record['webhook_url'],record['robot_key'],record['default_venue_id'],dumps(record['venue_ids']),stamp(),record['group_id']))
        scope_data = {k: data[k] for k in ('venue_ids','slot_keys') if k in data}
        if scope_data:
            self._save_bot_scope(bot_id, scope_data)
        initialize_transport(self.root_for(bot_id) / 'transport.sqlite3')
        private = self.root_for(bot_id) / 'callback.local.json'
        private.write_text(dumps({'bot_id':bot_id,'callback_token':callback}),encoding='utf-8')
        from .integrations import callback_path
        return self.visible_bot(self.bot(bot_id),principal) | {'callback_path':callback_path(self,bot_id,callback)}

    def update(self, principal, bot_id, data):
        self.require_platform(principal)
        old = self.bot(bot_id)
        validated_scope = {k:self._scope_value(k,data[k]) for k in ('venue_ids','slot_keys') if k in data}
        capabilities = normalize_capabilities(data['capabilities']) if 'capabilities' in data else None
        from .time_rules import normalize_capability_rules
        selected = capabilities if capabilities is not None else normalize_capabilities(json.loads(old['capabilities']))
        rules = data.get('capability_rules',json.loads(old['capability_rules']))
        if 'capability_rules' not in data and capabilities is not None:
            rules=[dict(r,capabilities=[c for c in r['capabilities'] if c in selected]) for r in rules]
            rules=[r for r in rules if r['capabilities']]
        capability_rules=normalize_capability_rules(rules,selected)
        from .bot_access import normalize
        user_policy = normalize(data['user_policy'],self.workspace_context()) if 'user_policy' in data else None
        allowed = {'name','active','callback_token','legacy','chat_id','webhook_url','robot_key','admin_user_ids','venue_ids','slot_keys','capabilities','user_policy','capability_rules'}
        if set(data)-allowed:
            raise ValueError('不支持的机器人配置字段')
        if data.get('admin_user_ids'):
            raise ValueError('管理员角色请在会员与权限中单独授予')
        channel_data = {k:data[k] for k in ('chat_id','webhook_url','robot_key') if k in data}
        for key in ('name','active','legacy','callback_token'):
            if key not in data:
                continue
            value=data[key]
            if key=='callback_token':
                if not isinstance(value,str) or len(value)<24:
                    raise ValueError('回调令牌至少24字符')
                old['callback_hash']=digest(value)
            elif key in ('active','legacy'):
                if not isinstance(value,bool):
                    raise ValueError('开关必须为布尔值')
                old[key]=int(value)
            else:
                if not isinstance(value,str) or not value.strip() or len(value.strip())>80:
                    raise ValueError('机器人名称限1至80字')
                old[key]=value.strip()
        if channel_data:
            channels=self.list_channels(principal,bot_id)
            self.save_channel(principal,bot_id,channel_data,channels[0]['id'] if channels else None)
        secrets_data = {key:data[key] for key in ('callback_token',) if key in data}
        with env_settings.change(self.root, bot_id=bot_id, bot_values=secrets_data), connect(self.path,True) as db:
            self._live_record(db,bot_id)
            db.execute('UPDATE bots SET name=?,active=?,callback_hash=?,legacy=? WHERE id=?',tuple(old[k] for k in ('name','active','callback_hash','legacy','id')))
            if capabilities is not None:
                db.execute('UPDATE bots SET capabilities=? WHERE id=?', (dumps(capabilities),bot_id))
            if user_policy is not None:
                db.execute('UPDATE bots SET user_policy=? WHERE id=?',(dumps(user_policy),bot_id))
            db.execute('UPDATE bots SET capability_rules=? WHERE id=?',(dumps(capability_rules),bot_id))
        self._save_bot_scope(bot_id, validated_scope)
        if 'callback_token' in data:
            (self.root_for(bot_id)/'callback.local.json').write_text(dumps({'bot_id':bot_id,'callback_token':data['callback_token']}),encoding='utf-8')
        return self.visible_bot(self.bot(bot_id),principal)

    def workspace_context(self, actor='system', is_admin=True, source='system'):
        return refresh_authority(BotContext('workspace',self.workspace_root,'',actor,is_admin,source,platform_root=self.root,receive_commands=False,daily_notifications=False))

    def context_for_channel(self, channel_id, actor='scheduler', is_admin=True, source='system'):
        channel=self.channel(channel_id,active=True)
        if not channel['chat_id']:
            raise PermissionError('等待群内首次@机器人完成连接')
        bot_root=self.root_for(channel['bot_id'])
        from .scope import refresh_context
        return refresh_authority(refresh_context(BotContext(channel['bot_id'],self.workspace_root,channel['chat_id'],actor,is_admin,source,
                          transport_path=bot_root/'transport.sqlite3',bot_root=bot_root,platform_root=self.root,
                          channel_id=channel['id'],venue_ids=tuple(channel['venue_ids']),default_venue_id=channel['default_venue_id'],
                          receive_commands=bool(channel['receive_commands']),daily_notifications=bool(channel['daily_notifications']))))

    def context(self, principal, bot_id=None, channel_id=None):
        if not bot_id and not channel_id and principal.role=='platform_admin':
            return self.workspace_context(principal.id,True,'admin')
        if channel_id:
            channel=self.channel(channel_id,active=True)
            if bot_id and channel['bot_id']!=bot_id:
                raise PermissionError('群通道不属于指定机器人')
            bot_id=channel['bot_id']
        if not bot_id:
            visible=self.list(principal)
            if len(visible)!=1:
                raise ValueError('请明确选择机器人')
            bot_id=visible[0]['id']
        self._require_bot(principal,bot_id)
        self.bot(bot_id,active=True)
        admin=principal.role=='platform_admin'
        source='admin' if admin else 'transport_admin'
        if not channel_id:
            channels=self.list_channels(principal,bot_id,active_only=True)
            channel_id=channels[0]['id'] if channels else None
        if channel_id:
            return self.context_for_channel(channel_id,principal.id,admin,source)
        from .scope import refresh_context
        return refresh_context(BotContext(bot_id,self.workspace_root,'',principal.id,admin,source,transport_path=self.root_for(bot_id)/'transport.sqlite3',bot_root=self.root_for(bot_id),platform_root=self.root,receive_commands=False,daily_notifications=False))

    def callback_context(self, bot_id, token, body=None):
        bot=self.bot(bot_id,active=True)
        if not hmac.compare_digest(bot['callback_hash'],digest(token)):
            raise PermissionError('回调凭据无效')
        if body is None:
            return bot
        chat=identifier(body.get('chatid'))
        user=identifier(body.get('creator'))
        with connect(self.path) as db:
            row=db.execute('SELECT id FROM bot_channels WHERE bot_id=? AND chat_id=? AND active=1 AND archived=0',(bot_id,chat)).fetchone()
        if not row:
            from .bot_groups import discover
            channel_id=discover(self,bot_id,token,body)
            row={'id':channel_id}
        channel=self.channel(row['id'],active=True)
        key=body.get('robot_key')
        receive_only = not channel['webhook_url'] and not channel['robot_key']
        if not receive_only and (not channel['robot_key'] or not isinstance(key,str) or not hmac.compare_digest(key.encode(),channel['robot_key'].encode())):
            raise PermissionError('回调凭据无效')
        if not channel['receive_commands']:
            raise PermissionError('该群通道已关闭机器人指令')
        with connect(self.workspace_root/'business.sqlite3') as db:
            has_roles=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_roles'").fetchone()
            approved=db.execute("SELECT 1 FROM member_records WHERE chat_id='club' AND user_id=? AND status='approved'",(user,)).fetchone()
            admin=bool(has_roles and approved and db.execute("SELECT 1 FROM user_roles WHERE user_id=? AND role='club_admin' AND scope_type='club' AND scope_id='club'",(user,)).fetchone())
        return self.context_for_channel(channel['id'],user,admin,'callback')

    def legacy_bot(self, token):
        with connect(self.path) as db:
            rows=db.execute('SELECT id FROM bots WHERE legacy=1 AND callback_hash=?',(digest(token),)).fetchall()
        if len(rows)!=1:
            raise PermissionError('旧回调未明确映射')
        return rows[0]['id']

    def system_contexts(self):
        return [self.context_for_channel(row['id']) for row in self.list_channels(active_only=True)]
