"""SQLite primitives and shared workspace business schema."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import sqlite3

TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')


def now():
    return datetime.now(TZ)


def stamp(value=None):
    return (value or now()).astimezone(TZ).isoformat(timespec='seconds')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


@contextmanager
def connect(path, write=False):
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')
    try:
        if write:
            conn.execute('BEGIN IMMEDIATE')
        yield conn
        if write:
            conn.commit()
    except BaseException:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()


DEFAULT_CONFIG = {
    'admin_user_ids': [], 'reminders': {'open': '09:30', 'closing': '17:30', 'roster': '18:00'},
    'daily_enabled': True, 'broadcast_rules': None, 'capacity': 8, 'substitute_capacity': 4,
    'public_base_url': '', 'ai': {'enabled': False, 'model': 'deepseek-ai/DeepSeek-V3.2',
                                'base_url': 'https://api.siliconflow.cn/v1', 'api_key': '', 'confirmation_minutes': 5},
    'theme': {'accent': '#197661', 'banner_url': '', 'enabled': True},
    'scoring_mode': 'members', 'leader_user_ids': [],
}

DEFAULT_SLOTS = [
    {'name': '第一场', 'weekdays': [0, 1, 2, 3, 4], 'start': '17:30', 'end': '20:30', 'capacity': 8, 'substitute_capacity': 4},
    {'name': '第二场', 'weekdays': [0, 1, 2, 3, 4], 'start': '20:30', 'end': '22:00', 'capacity': 8, 'substitute_capacity': 4},
    {'name': '周末第一场', 'weekdays': [5, 6], 'start': '18:00', 'end': '20:00', 'capacity': 8, 'substitute_capacity': 4},
    {'name': '周末第二场', 'weekdays': [5, 6], 'start': '20:00', 'end': '22:00', 'capacity': 8, 'substitute_capacity': 4},
]


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO schema_version VALUES(1);
        CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS profiles(chat_id TEXT NOT NULL,user_id TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(chat_id,user_id));
        CREATE TABLE IF NOT EXISTS venues(id INTEGER PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS slots(id INTEGER PRIMARY KEY AUTOINCREMENT,venue_id INTEGER NOT NULL REFERENCES venues(id),definition TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS special_dates(venue_id INTEGER NOT NULL REFERENCES venues(id),date TEXT NOT NULL,slots TEXT NOT NULL,PRIMARY KEY(venue_id,date));
        CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,venue_id INTEGER NOT NULL REFERENCES venues(id),date TEXT NOT NULL,name TEXT NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL,capacity INTEGER NOT NULL,substitute_capacity INTEGER NOT NULL,UNIQUE(venue_id,date,start));
        CREATE TABLE IF NOT EXISTS registrations(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER NOT NULL REFERENCES sessions(id),chat_id TEXT NOT NULL,user_id TEXT NOT NULL,name TEXT NOT NULL,guest_index INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'active',queue_seq INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(session_id,chat_id,user_id,guest_index));
        CREATE INDEX IF NOT EXISTS reg_session ON registrations(session_id,chat_id,status,queue_seq);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox(event_id TEXT PRIMARY KEY,status TEXT NOT NULL,reply TEXT,created_at TEXT NOT NULL,error TEXT);
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,dedupe_key TEXT NOT NULL UNIQUE,chat_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,last_attempt_at TEXT,last_error TEXT);
        CREATE TABLE IF NOT EXISTS notification_marks(key TEXT PRIMARY KEY,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_pending(token TEXT PRIMARY KEY,bot_id TEXT NOT NULL,chat_id TEXT NOT NULL,user_id TEXT NOT NULL,command TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS ai_requests(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT NOT NULL,user_id TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS legacy_rows(source_key TEXT PRIMARY KEY,row_json TEXT NOT NULL,imported_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS legacy_delivery(source_key TEXT PRIMARY KEY,row_json TEXT NOT NULL,imported_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,mime TEXT NOT NULL,kind TEXT NOT NULL,size INTEGER NOT NULL,tournament_id INTEGER,caption TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS media_links(token_hash TEXT PRIMARY KEY,media_id TEXT NOT NULL REFERENCES media(id),expires_at TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS imports(source_key TEXT PRIMARY KEY,registration_id INTEGER NOT NULL REFERENCES registrations(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS member_records(chat_id TEXT NOT NULL,user_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',level INTEGER,requested_level INTEGER,level_status TEXT NOT NULL DEFAULT 'none',name_source TEXT NOT NULL DEFAULT 'legacy',review_note TEXT NOT NULL DEFAULT '',level_note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reviewed_at TEXT,level_reviewed_at TEXT,PRIMARY KEY(chat_id,user_id));
        CREATE TABLE IF NOT EXISTS member_groups(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT NOT NULL,name TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(chat_id,name));
        CREATE TABLE IF NOT EXISTS member_group_links(chat_id TEXT NOT NULL,user_id TEXT NOT NULL,group_id INTEGER NOT NULL REFERENCES member_groups(id),PRIMARY KEY(chat_id,user_id,group_id),FOREIGN KEY(chat_id,user_id) REFERENCES member_records(chat_id,user_id));
        CREATE TABLE IF NOT EXISTS user_roles(user_id TEXT NOT NULL,role TEXT NOT NULL,scope_type TEXT NOT NULL DEFAULT 'club',scope_id TEXT NOT NULL DEFAULT 'club',created_at TEXT NOT NULL,created_by TEXT NOT NULL,PRIMARY KEY(user_id,role,scope_type,scope_id));
        CREATE TABLE IF NOT EXISTS membership_applications(id INTEGER PRIMARY KEY AUTOINCREMENT,scope_id TEXT NOT NULL,user_id TEXT NOT NULL,name TEXT NOT NULL,requested_level INTEGER,phone TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending',submitted_at TEXT NOT NULL,reviewed_at TEXT,reviewed_by TEXT,note TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS membership_application_user ON membership_applications(scope_id,user_id,submitted_at);
        CREATE TABLE IF NOT EXISTS membership_terms(id INTEGER PRIMARY KEY AUTOINCREMENT,scope_id TEXT NOT NULL,user_id TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,amount_cents INTEGER,note TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'valid' CHECK(status IN ('valid','void')),version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,created_by TEXT NOT NULL,voided_at TEXT,voided_by TEXT,void_note TEXT NOT NULL DEFAULT '',CHECK(start_date<=end_date),CHECK(amount_cents IS NULL OR amount_cents>=0),FOREIGN KEY(scope_id,user_id) REFERENCES member_records(chat_id,user_id));
        CREATE INDEX IF NOT EXISTS membership_term_user ON membership_terms(scope_id,user_id,status,start_date,end_date);
        CREATE TABLE IF NOT EXISTS membership_term_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,scope_id TEXT NOT NULL,user_id TEXT NOT NULL,term_id INTEGER NOT NULL REFERENCES membership_terms(id),actor TEXT NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS identity_observations(scope_id TEXT NOT NULL,user_id TEXT NOT NULL,bot_id TEXT NOT NULL,chat_id TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,PRIMARY KEY(scope_id,user_id,bot_id,chat_id));
        ''')
        from .identity_notifications import ensure_schema as identity_notification_schema
        identity_notification_schema(db)
        from .availability import ensure_schema as availability_schema
        availability_schema(db)
        from .holidays import ensure_schema as holiday_schema
        holiday_schema(db)
    with connect(path, True) as db:
        if 'attributes' not in {r['name'] for r in db.execute('PRAGMA table_info(venues)')}:
            db.execute("ALTER TABLE venues ADD COLUMN attributes TEXT NOT NULL DEFAULT '[]'")
        columns = {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}
        for column, definition in [('min_level', 'INTEGER'), ('max_level', 'INTEGER'), ('allowed_group_ids', "TEXT NOT NULL DEFAULT '[]'"), ('settings_overridden', 'INTEGER NOT NULL DEFAULT 0')]:
            if column not in columns:
                db.execute(f'ALTER TABLE sessions ADD COLUMN {column} {definition}')
        member_columns = {r['name'] for r in db.execute('PRAGMA table_info(member_records)')}
        if 'phone' not in member_columns:
            db.execute("ALTER TABLE member_records ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        for table in ('member_records', 'membership_applications'):
            if 'gender' not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f"ALTER TABLE {table} ADD COLUMN gender TEXT NOT NULL DEFAULT 'unknown'")
        registration_columns = {r['name'] for r in db.execute('PRAGMA table_info(registrations)')}
        if 'guest_name' not in registration_columns:
            db.execute("ALTER TABLE registrations ADD COLUMN guest_name TEXT NOT NULL DEFAULT ''")
        # Preserve immutable legacy_rows as the original evidence. Only repair names
        # that were machine identity fallbacks, never an existing real display name.
        db.execute("INSERT OR IGNORE INTO profiles(chat_id,user_id,name) SELECT chat_id,user_id,CASE WHEN name=user_id THEN '' ELSE name END FROM registrations WHERE guest_index=0 ORDER BY updated_at DESC,id DESC")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tour_participants'").fetchone():
            db.execute('''INSERT OR IGNORE INTO profiles(chat_id,user_id,name)
                          SELECT e.chat_id,p.user_id,CASE WHEN p.name=p.user_id THEN '' ELSE p.name END
                          FROM tour_participants p JOIN tour_events e ON e.id=p.tournament_id
                          WHERE p.guest_index=0 AND p.user_id<>'' ORDER BY p.registered_at DESC''')
        db.execute("UPDATE profiles SET name='' WHERE name=user_id")
        timestamp = stamp()
        db.execute('''INSERT OR IGNORE INTO member_records(chat_id,user_id,created_at,updated_at)
                      SELECT chat_id,user_id,?,? FROM profiles''', (timestamp, timestamp))
        db.execute("UPDATE registrations SET name='' WHERE guest_name='' AND (name=user_id OR (guest_index>0 AND name=user_id || '+' || guest_index))")
        db.execute('INSERT OR IGNORE INTO schema_version VALUES(2)')
        for key, value in DEFAULT_CONFIG.items():
            db.execute('INSERT OR IGNORE INTO config VALUES(?,?)', (key, dumps(value)))
        fresh_venue = db.execute("INSERT OR IGNORE INTO venues(id,name) VALUES(1,'主场地')").rowcount
        if fresh_venue:
            db.executemany('INSERT INTO slots(venue_id,definition) VALUES(1,?)', [(dumps(s),) for s in DEFAULT_SLOTS])


def audit(db, actor, action, detail):
    db.execute('INSERT INTO audit(actor,action,detail,created_at) VALUES(?,?,?,?)',
               (str(actor), action, dumps(detail), stamp()))
