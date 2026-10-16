"""One club workspace and per-bot delivery stores, with fail-closed v1 migration.

The old bot database is evidence: it is opened read-only and never rewritten.
Registry write transactions serialize migration across simultaneous app starts.
"""
from pathlib import Path
import json
import shutil
import sqlite3
import uuid

from .db import connect, initialize, stamp


TRANSPORT_TABLES = (
    'inbox', 'outbox', 'ai_pending', 'ai_requests', 'registration_forms',
    'daily_planning', 'notification_marks',
)


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def initialize_transport(path, source=None, channel_id=''):
    """Initialize only delivery state; copy old rows once and never resend them."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS transport_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox(event_id TEXT PRIMARY KEY,status TEXT NOT NULL,reply TEXT,created_at TEXT NOT NULL,error TEXT,channel_id TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,dedupe_key TEXT NOT NULL UNIQUE,chat_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,last_attempt_at TEXT,last_error TEXT,channel_id TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS ai_pending(token TEXT PRIMARY KEY,bot_id TEXT NOT NULL,chat_id TEXT NOT NULL,user_id TEXT NOT NULL,command TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS ai_requests(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT NOT NULL,user_id TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notification_marks(key TEXT PRIMARY KEY,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS registration_forms(token_hash TEXT PRIMARY KEY,chat_id TEXT NOT NULL,user_id TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft',payload TEXT,code TEXT);
        CREATE TABLE IF NOT EXISTS daily_planning(chat_id TEXT NOT NULL,user_id TEXT NOT NULL,venue_id INTEGER NOT NULL,token TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,expires_at TEXT NOT NULL,event_id INTEGER,PRIMARY KEY(chat_id,user_id,venue_id));
        ''')
    with connect(path, True) as db:
        if 'scope_json' not in {r['name'] for r in db.execute('PRAGMA table_info(outbox)')}:
            db.execute("ALTER TABLE outbox ADD COLUMN scope_json TEXT NOT NULL DEFAULT '{}'")
        for table in ('inbox', 'outbox'):
            if 'channel_id' not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f"ALTER TABLE {table} ADD COLUMN channel_id TEXT NOT NULL DEFAULT ''")
        if source and Path(source).exists() and not db.execute("SELECT 1 FROM transport_meta WHERE key='legacy_migrated'").fetchone():
            old = _readonly(source)
            try:
                available = _tables(old)
                for table in TRANSPORT_TABLES:
                    if table not in available:
                        continue
                    columns = [r['name'] for r in old.execute(f'PRAGMA table_info({table})')]
                    valid = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                    if not set(columns) <= valid:
                        raise RuntimeError(f'旧传输表 {table} 存在未知字段，迁移已停止')
                    rows = old.execute(f'SELECT * FROM {table}').fetchall()
                    if rows:
                        names = ','.join(columns)
                        db.executemany(f"INSERT INTO {table}({names}) VALUES({','.join('?' for _ in columns)})", [tuple(r) for r in rows])
                db.execute("UPDATE outbox SET channel_id=? WHERE channel_id=''", (channel_id,))
                db.execute("UPDATE inbox SET channel_id=? WHERE channel_id=''", (channel_id,))
                db.execute("UPDATE outbox SET status='unknown',last_error='升级前未确认发送结果，请人工核实；不会自动重发' WHERE status IN ('pending','sending')")
                db.execute("UPDATE inbox SET status='unknown',error='升级前处理中，业务结果请人工核实；禁止自动重放' WHERE status='processing'")
                db.execute("UPDATE ai_pending SET status='unknown' WHERE status IN ('pending','consuming')")
                # Old forms carry the old member boundary; require a fresh form.
                db.execute("UPDATE registration_forms SET status='superseded',payload=NULL,code=NULL WHERE status IN ('draft','submitted')")
                db.execute("INSERT INTO transport_meta VALUES('legacy_migrated',?)", (stamp(),))
            finally:
                old.close()


def initialize_workspace(root, bots, channel_ids):
    root = Path(root)
    target = root / 'workspace'
    db_path = target / 'business.sqlite3'
    # The registry is separate from business state and already exists.
    with connect(root / 'registry.sqlite3', True) as registry:
        registry.execute('CREATE TABLE IF NOT EXISTS workspace_migrations(version INTEGER PRIMARY KEY,detail TEXT NOT NULL,completed_at TEXT NOT NULL)')
        migrated = registry.execute('SELECT 1 FROM workspace_migrations WHERE version=2').fetchone()
        if migrated:
            if not db_path.is_file():
                raise RuntimeError('共享业务数据库缺失，已停止启动以避免创建空库覆盖历史')
            initialize(db_path)
            for bot in bots:
                initialize_transport(root / 'bots' / bot['id'] / 'transport.sqlite3')
            return target
        sources = [(bot, root / 'bots' / bot['id'] / 'business.sqlite3') for bot in bots
                   if (root / 'bots' / bot['id'] / 'business.sqlite3').is_file()]
        if len(sources) > 1:
            ids = ', '.join(bot['id'] for bot, _ in sources)
            raise RuntimeError('检测到多个历史机器人业务库；需先审核场地、赛事编号和会员冲突映射，自动迁移已停止，原库未改变：' + ids)
        if target.exists():
            # A process may have stopped between atomic publish and registry commit.
            marker = target / 'migration-v2.json'
            if not db_path.is_file() or not marker.is_file():
                raise RuntimeError('发现未完成的共享工作区，请核实迁移记录后恢复；不会覆盖现有文件')
            detail = json.loads(marker.read_text(encoding='utf-8'))
        else:
            pending = root / ('workspace-migrating-' + uuid.uuid4().hex)
            pending.mkdir()
            candidate = pending / 'business.sqlite3'
            detail = {'version': 2, 'scope_id': 'club', 'sources': [bot['id'] for bot, _ in sources], 'created_at': stamp()}
            try:
                if sources:
                    bot, source = sources[0]
                    old = _readonly(source)
                    new = sqlite3.connect(candidate)
                    try:
                        old.backup(new)
                    finally:
                        old.close()
                        new.close()
                initialize(candidate)
                with connect(candidate, True) as db:
                    db.execute('''CREATE TABLE IF NOT EXISTS workspace_migration_issues(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,
                        source_bot_id TEXT NOT NULL,user_id TEXT NOT NULL DEFAULT '',
                        detail TEXT NOT NULL,created_at TEXT NOT NULL,resolved INTEGER NOT NULL DEFAULT 0)''')
                    tables = _tables(db)
                    # Single legacy bot bound one immutable chat; refuse ambiguous
                    # duplicates rather than merge people solely by display name.
                    for table in ('profiles', 'member_records'):
                        if db.execute(f'SELECT user_id FROM {table} GROUP BY user_id HAVING COUNT(*)>1 LIMIT 1').fetchone():
                            raise RuntimeError('旧会员存在跨群重复身份，需要审核后合并；原数据库未改变')
                    for table in sorted(tables - set(TRANSPORT_TABLES) - {'sqlite_sequence','identity_observations'}):
                        columns = {r['name'] for r in db.execute(f'PRAGMA table_info("{table}")')}
                        if 'chat_id' in columns:
                            # Updating related composite keys must be deferred.
                            db.execute('PRAGMA defer_foreign_keys=ON')
                            db.execute(f'UPDATE "{table}" SET chat_id=?', ('club',))
                    for table in TRANSPORT_TABLES:
                        if table in tables:
                            db.execute(f'DELETE FROM {table}')
                    # Bot-local authority cannot silently become club authority.
                    row = db.execute("SELECT value FROM config WHERE key='admin_user_ids'").fetchone()
                    admins = json.loads(row[0]) if row else []
                    for user in admins:
                        db.execute('INSERT INTO workspace_migration_issues(kind,source_bot_id,user_id,detail,created_at) VALUES(?,?,?,?,?)',
                                   ('legacy_bot_admin',sources[0][0]['id'] if sources else '',str(user),'旧机器人管理员未自动授予后台角色，请审核后在会员与权限中授予',stamp()))
                    db.execute("UPDATE member_records SET status='unregistered' WHERE status='pending' AND NOT EXISTS(SELECT 1 FROM profiles p WHERE p.chat_id=member_records.chat_id AND p.user_id=member_records.user_id AND trim(p.name)<>'')")
                    if 'membership_applications' in tables:
                        db.execute('''INSERT INTO membership_applications(scope_id,user_id,name,requested_level,phone,status,submitted_at,note)
                            SELECT 'club',m.user_id,p.name,m.requested_level,m.phone,'pending',m.created_at,'历史注册申请迁移'
                            FROM member_records m JOIN profiles p ON p.chat_id=m.chat_id AND p.user_id=m.user_id
                            WHERE m.status='pending' AND trim(p.name)<>'' AND NOT EXISTS(
                            SELECT 1 FROM membership_applications a WHERE a.scope_id='club' AND a.user_id=m.user_id AND a.status='pending')''')
                    if sources and 'identity_observations' in tables:
                        db.execute('''INSERT OR IGNORE INTO identity_observations(scope_id,user_id,bot_id,chat_id,first_seen_at,last_seen_at)
                                      SELECT 'club',user_id,?,?,created_at,updated_at FROM member_records''',
                                   (sources[0][0]['id'],sources[0][0]['chat_id']))
                    if sources and 'tour_events' in tables:
                        db.execute('''CREATE TABLE IF NOT EXISTS tour_event_channels(
                            tournament_id INTEGER NOT NULL REFERENCES tour_events(id),channel_id TEXT NOT NULL,
                            created_by TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(tournament_id,channel_id))''')
                        original_channel = channel_ids.get(sources[0][0]['id'])
                        if original_channel:
                            db.execute("INSERT OR IGNORE INTO tour_event_channels SELECT id,?,'migration-v2',? FROM tour_events", (original_channel,stamp()))
                    db.execute("UPDATE config SET value='[]' WHERE key='admin_user_ids'")
                    failures = list(db.execute('PRAGMA foreign_key_check'))
                    if failures:
                        raise RuntimeError('共享数据库外键校验失败，迁移未发布')
                if sources:
                    bot, source = sources[0]
                    for folder in ('uploads', 'tables'):
                        original = source.parent / folder
                        if original.is_dir():
                            shutil.copytree(original, pending / folder)
                with connect(candidate) as db:
                    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError('共享数据库完整性校验失败')
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                (pending / 'migration-v2.json').write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding='utf-8')
                pending.replace(target)
            except BaseException:
                # Keep the candidate for diagnosis; only the original remains live.
                raise
        for bot in bots:
            old = root / 'bots' / bot['id'] / 'business.sqlite3'
            initialize_transport(root / 'bots' / bot['id'] / 'transport.sqlite3', old if old.is_file() else None, channel_ids.get(bot['id'], ''))
        registry.execute('INSERT INTO workspace_migrations VALUES(2,?,?)', (json.dumps(detail, ensure_ascii=False), stamp()))
    return target
