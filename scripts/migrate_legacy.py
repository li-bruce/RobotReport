"""Offline idempotent migration. Never modifies old files or sends messages."""
import argparse
import collections
import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from app.db import connect, dumps, stamp
from app.security import Platform, digest, identifier
from app.service import Service, day_value, clean_name


def migrate(old_config_path, csv_path, delivery_path, state, platform_token, session_start='17:30', session_end='20:30'):
    old_config_path, csv_path, delivery_path = map(Path, (old_config_path, csv_path, delivery_path))
    config = json.loads(old_config_path.read_text(encoding='utf-8-sig'))
    platform = Platform(state, platform_token)
    principal = platform.authenticate(platform_token)
    callback = config['callback_token']
    chat = identifier(config.get('target_chat_id'))
    with csv_path.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        day_value(row['date'])
        identifier(row['user_id'])
        if row['chat_id'] != chat:
            raise ValueError('CSV包含其他群，请拆分后迁移到独立机器人')
        if row['status'] not in ('active', 'cancelled'):
            raise ValueError('旧CSV状态无效')
    Service.validate_slots([{'name':'旧系统场次','start':session_start,'end':session_end}])
    with connect(platform.path) as db:
        found = db.execute('SELECT id FROM bots WHERE legacy=1 AND callback_hash=?', (digest(callback),)).fetchall()
    if len(found) > 1:
        raise ValueError('旧回调映射不唯一')
    if found:
        bot_id = found[0][0]
    else:
        bot_id = platform.create(principal, {'name':'羽协机器人（迁移）', 'chat_id':chat,
            'webhook_url':config['webhook_url'], 'robot_key':config['robot_key'],
            'callback_token':callback, 'legacy':True})['id']
    ctx = platform.context(principal, bot_id)
    service = Service(ctx)
    if not found:
        service.update_config({k:config[k] for k in ('capacity','substitute_capacity','daily_enabled') if k in config})
    if config.get('admin_token'):
        with connect(platform.path) as db:
            grant = db.execute('SELECT id FROM grants WHERE token_hash=?', (digest(config['admin_token']),)).fetchone()
        if not grant:
            platform.grant(principal,[bot_id],config['admin_token'])
    occurrences = collections.Counter()
    imported = 0
    with connect(ctx.db_path, True) as db:
        for admin_id in config.get('admin_user_ids', []):
            admin_id = identifier(admin_id)
            db.execute('''INSERT INTO workspace_migration_issues(kind,source_bot_id,user_id,detail,created_at)
                SELECT 'legacy_bot_admin',?,?,?,? WHERE NOT EXISTS(
                SELECT 1 FROM workspace_migration_issues WHERE kind='legacy_bot_admin' AND source_bot_id=? AND user_id=?)''',
                (bot_id,admin_id,'旧机器人管理员未自动授予后台角色，请审核后独立授予',stamp(),bot_id,admin_id))
        for row in rows:
            canonical = dumps(row)
            row_hash = hashlib.sha256(canonical.encode()).hexdigest()
            occurrences[row_hash] += 1
            source_key = 'csv:' + row_hash + ':' + str(occurrences[row_hash])
            if not db.execute('INSERT OR IGNORE INTO legacy_rows VALUES(?,?,?)',(source_key,canonical,stamp())).rowcount:
                continue
            imported += 1
            db.execute('INSERT OR IGNORE INTO sessions(venue_id,date,name,start,end,capacity,substitute_capacity) VALUES(1,?,?,?,?,?,?)',
                (row['date'],'旧系统迁移场次',session_start,session_end,config.get('capacity',8),config.get('substitute_capacity',4)))
            session = db.execute('SELECT id FROM sessions WHERE venue_id=1 AND date=? AND start=?',(row['date'],session_start)).fetchone()[0]
            created = row.get('registered_at') or row['date']+'T00:00:00+08:00'
            updated = row.get('cancelled_at') or created
            raw_name = row.get('name') or ''
            name = clean_name(raw_name) if raw_name and raw_name != row['user_id'] else ''
            existing = db.execute('SELECT * FROM registrations WHERE session_id=? AND chat_id=? AND user_id=? AND guest_index=0',(session,ctx.scope_id,row['user_id'])).fetchone()
            seq = db.execute('SELECT COALESCE(MAX(queue_seq),0)+1 FROM registrations WHERE session_id=?',(session,)).fetchone()[0]
            if existing:
                registration_id = existing['id']
                if updated >= existing['updated_at']:
                    db.execute('UPDATE registrations SET name=?,status=?,queue_seq=?,updated_at=? WHERE id=?',(name,row['status'],seq,updated,registration_id))
            else:
                registration_id = db.execute('INSERT INTO registrations(session_id,chat_id,user_id,name,status,queue_seq,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                    (session,ctx.scope_id,row['user_id'],name,row['status'],seq,created,updated)).lastrowid
            db.execute('INSERT INTO imports VALUES(?,?,?)',(source_key,registration_id,stamp()))
            db.execute('INSERT OR IGNORE INTO profiles VALUES(?,?,?)',(ctx.scope_id,row['user_id'],name))
            db.execute('''INSERT OR IGNORE INTO member_records(chat_id,user_id,status,created_at,updated_at)
                          VALUES(?,?,?,?,?)''',(ctx.scope_id,row['user_id'],'pending' if name else 'unregistered',created,updated))
            db.execute('''INSERT OR IGNORE INTO identity_observations VALUES(?,?,?,?,?,?)''',
                       (ctx.scope_id,row['user_id'],bot_id,chat,created,updated))
            db.execute('''INSERT INTO membership_applications(scope_id,user_id,name,status,submitted_at,note)
                SELECT ?,?,?,'pending',?,'旧CSV注册资料迁移' WHERE ?<>'' AND EXISTS(
                SELECT 1 FROM member_records WHERE chat_id=? AND user_id=? AND status='pending') AND NOT EXISTS(
                SELECT 1 FROM membership_applications WHERE scope_id=? AND user_id=? AND status='pending')''',
                (ctx.scope_id,row['user_id'],name,created,name,ctx.scope_id,row['user_id'],ctx.scope_id,row['user_id']))
        db_count = db.execute('SELECT COUNT(*) FROM legacy_rows').fetchone()[0]
        counts = dict(db.execute('SELECT status,COUNT(*) FROM registrations GROUP BY status').fetchall())
    # Every run revisits delivery rows independently. If a process stops after
    # the business commit, retry completes missing transport rows idempotently.
    legacy_events = legacy_outbox = 0
    if delivery_path.exists():
        old = sqlite3.connect(delivery_path.resolve().as_uri()+'?mode=ro',uri=True)
        old.row_factory = sqlite3.Row
        try:
            events = [dict(row) for row in old.execute('SELECT * FROM events')]
            outgoing = [dict(row) for row in old.execute('SELECT * FROM outbox')]
        finally:
            old.close()
        with connect(ctx.db_path,True) as db:
            for entry in outgoing:
                db.execute('INSERT OR IGNORE INTO legacy_delivery VALUES(?,?,?)',
                           ('legacy:'+bot_id+':'+str(entry['id']),dumps(entry),stamp()))
        with connect(ctx.transport_db_path,True) as db:
            for event in events:
                db.execute("INSERT OR IGNORE INTO inbox(event_id,status,created_at,channel_id) VALUES(?,'imported',?,?)",
                           (event['event_id'],event['received_at'],ctx.channel_id))
                legacy_events += 1
            for entry in outgoing:
                status = 'sent' if entry['status']=='sent' else 'unknown'
                db.execute('INSERT OR IGNORE INTO outbox(dedupe_key,chat_id,payload,status,created_at,attempts,last_error,channel_id) VALUES(?,?,?,?,?,?,?,?)',
                    ('legacy:'+str(entry['id']),chat,entry['payload'],status,entry['created_at'],entry.get('attempts',0),
                     None if status=='sent' else '旧系统迁移消息，需人工核实；禁止自动重发',ctx.channel_id))
                legacy_outbox += 1
    return {'bot_id':bot_id,'csv_rows':len(rows),'newly_imported':imported,'preserved_source_rows':db_count,
            'current_status_counts':counts,'legacy_events':legacy_events,'legacy_outbox':legacy_outbox,
            'legacy_session':f'{session_start}–{session_end}','delivery':'No messages sent. Old pending deliveries quarantined.'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-config',required=True)
    parser.add_argument('--csv',required=True)
    parser.add_argument('--delivery',required=True)
    parser.add_argument('--state',required=True)
    parser.add_argument('--credentials',required=True,help='Private bootstrap admin.local.json path')
    parser.add_argument('--session-start',default='17:30')
    parser.add_argument('--session-end',default='20:30')
    args=parser.parse_args()
    token=json.loads(Path(args.credentials).read_text(encoding='utf-8'))['admin_token']
    result=migrate(args.old_config,args.csv,args.delivery,args.state,token,args.session_start,args.session_end)
    print(json.dumps(result,ensure_ascii=False,indent=2))
