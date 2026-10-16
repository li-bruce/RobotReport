"""Verify v1 -> shared-workspace migration on a NEW isolated copy only.

Run: python -m scripts.verify_workspace_migration --source /stopped-snapshot \
         --destination /new-isolated-copy --source-is-snapshot

No app, scheduler, sender, password login, or network client is started. The
source must be a consistent, quiescent snapshot, never the running state tree.
Reports contain counts and check names, never row contents or credentials.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from app.security import Platform
from app.workspace import TRANSPORT_TABLES


BUSINESS_TABLES = (
    'venues','slots','special_dates','sessions','registrations','profiles',
    'member_records','member_groups','member_group_links','audit','legacy_rows',
    'legacy_delivery','media','media_links','imports','tour_events',
    'tour_participants','tour_matches','tour_audit','tour_notifications','generated_tables',
)
REGISTRY_TABLES = ('bots','grants','password_credentials','password_sessions','password_failures')


def readonly(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    return db


def tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def rows(db,table,columns=None):
    names=','.join('"'+name+'"' for name in columns) if columns else '*'
    return [dict(row) for row in db.execute(f'SELECT {names} FROM "{table}"')]


def canonical(records):
    return Counter(json.dumps(row,sort_keys=True,ensure_ascii=False,default=lambda v: v.hex()) for row in records)


def inventory(root):
    result={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('状态快照不能包含符号链接')
        if path.is_file() and not path.name.endswith('-shm'):
            result[path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def logical_database(path):
    db=readonly(path)
    try:
        result={}
        for table in sorted(tables(db)):
            payload=json.dumps(sorted(canonical(rows(db,table)).items()),ensure_ascii=False).encode()
            result[table]=hashlib.sha256(payload).hexdigest()
        return result
    finally:
        db.close()


def require_equal(actual,expected,check):
    if actual != expected:
        raise AssertionError(check+'：验证失败，详细记录仅保留在隔离副本中')


def verify(source,destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if not source.is_dir() or not (source/'registry.sqlite3').is_file():
        raise ValueError('源目录不是完整状态快照')
    if source==destination or source in destination.parents or destination in source.parents or destination.exists():
        raise ValueError('验收目的地必须是源目录之外、尚不存在的新目录')
    before=inventory(source)
    destination.mkdir(parents=True)
    # Never open a source SQLite connection: even mode=ro can create an empty
    # WAL beside a WAL-mode database. Copy the quiescent snapshot byte-for-byte,
    # then consolidate its WAL only inside the new destination.
    for relative in before:
        path=source/relative
        if path.name.endswith('-journal') and path.stat().st_size:
            raise ValueError('快照含未处理的回滚日志，请先制作一致SQLite备份')
        copied=destination/relative
        copied.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,copied)
    for path in list(destination.rglob('*.sqlite3')):
        consolidated=path.with_name(path.name+'.verification-copy')
        reader=readonly(path)
        writer=sqlite3.connect(consolidated)
        try:
            reader.backup(writer)
        finally:
            reader.close();writer.close()
        for suffix in ('-wal','-shm'):
            path.with_name(path.name+suffix).unlink(missing_ok=True)
        consolidated.replace(path)
    original_registry=readonly(destination/'registry.sqlite3')
    try:
        bots=rows(original_registry,'bots')
        originals={name:rows(original_registry,name) for name in REGISTRY_TABLES if name in tables(original_registry)}
    finally:
        original_registry.close()
    if len(bots)!=1 or not (destination/'bots'/bots[0]['id']/'business.sqlite3').is_file() or (source/'workspace').exists():
        raise ValueError('此验收针对单个历史机器人业务库，请使用v1完整状态快照')
    bot=bots[0]
    old_path=destination/'bots'/bot['id']/'business.sqlite3'
    old=readonly(old_path)
    try:
        old_tables=tables(old)
        business={name:rows(old,name) for name in BUSINESS_TABLES if name in old_tables}
        transport={name:rows(old,name) for name in TRANSPORT_TABLES if name in old_tables}
        old_config={row['key']:row['value'] for row in rows(old,'config')}
    finally:
        old.close()
    original_copy_hash=hashlib.sha256((destination/'bots'/bot['id']/'business.sqlite3').read_bytes()).hexdigest()
    platform=Platform(destination)
    ctx=platform.context_for_channel(platform.list_channels()[0]['id'])
    report={'ok':False,'source_bot_count':1,'checks':{},'business_counts':{},'transport_counts':{},'network_started':False}
    with closing(readonly(platform.path)) as db:
        for name,expected in originals.items():
            require_equal(canonical(rows(db,name,list(expected[0]) if expected else None)),canonical(expected),'registry.'+name)
    report['checks']['password_tokens_sessions_and_callback_hash_preserved']=True
    channel=platform.channel(ctx.channel_id)
    for field in ('chat_id','webhook_url','robot_key','active'):
        require_equal(channel[field],bot[field],'channel.'+field)
    report['checks']['original_group_and_delivery_credentials_preserved']=True
    with closing(readonly(ctx.db_path)) as db:
        for name,expected in business.items():
            rewritten=[]
            for original in expected:
                row=dict(original)
                if 'chat_id' in row:row['chat_id']='club'
                if name=='profiles' and row['name']==row['user_id']:row['name']=''
                if name=='registrations' and not row.get('guest_name') and (row['name']==row['user_id'] or row.get('guest_index',0)>0 and row['name']==row['user_id']+'+'+str(row['guest_index'])):
                    row['name']=''
                if name=='member_records' and row['status']=='pending':
                    profile=next((p for p in business.get('profiles',[]) if p['user_id']==row['user_id']),None)
                    if profile is None or not profile['name'].strip() or profile['name']==profile['user_id']:
                        row['status']='unregistered'
                rewritten.append(row)
            actual=rows(db,name,list(expected[0]) if expected else None)
            # initialize may recover missing profiles/member records from legacy
            # registrations. Every original row still must be preserved exactly.
            if name in ('profiles','member_records'):
                old_users={r['user_id'] for r in expected}
                actual=[r for r in actual if r['user_id'] in old_users]
            require_equal(canonical(actual),canonical(rewritten),'business.'+name)
            report['business_counts'][name]=len(actual)
        current_config={r['key']:r['value'] for r in rows(db,'config')}
        for key,value in old_config.items():
            if key!='admin_user_ids':require_equal(current_config[key],value,'config.'+key)
        require_equal(current_config['admin_user_ids'],'[]','independent_roles')
        if 'tour_events' in business:
            expected={(row['id'],ctx.channel_id) for row in business['tour_events']}
            require_equal({(r['tournament_id'],r['channel_id']) for r in rows(db,'tour_event_channels')},expected,'old_event_channel_bindings')
    report['checks']['business_ids_history_scores_memberships_and_config_preserved']=True
    report['checks']['old_events_bound_only_to_original_channel']=True
    with closing(readonly(ctx.transport_db_path)) as db:
        for name,expected in transport.items():
            rewritten=[]
            for original in expected:
                row=dict(original)
                if name=='outbox' and row['status'] in ('pending','sending'):
                    row['status']='unknown';row['last_error']='升级前未确认发送结果，请人工核实；不会自动重发'
                if name=='inbox' and row['status']=='processing':
                    row['status']='unknown';row['error']='升级前处理中，业务结果请人工核实；禁止自动重放'
                if name=='ai_pending' and row['status'] in ('pending','consuming'):row['status']='unknown'
                if name=='registration_forms' and row['status'] in ('draft','submitted'):
                    row.update(status='superseded',payload=None,code=None)
                if 'channel_id' in row and not row['channel_id']:row['channel_id']=ctx.channel_id
                rewritten.append(row)
            require_equal(canonical(rows(db,name,list(expected[0]) if expected else None)),canonical(rewritten),'transport.'+name)
            report['transport_counts'][name]=len(expected)
        require_equal(db.execute("SELECT COUNT(*) FROM outbox WHERE status IN ('pending','sending')").fetchone()[0],0,'old_messages_not_replayed')
        for table in ('inbox','outbox'):
            require_equal(db.execute(f'SELECT COUNT(*) FROM {table} WHERE channel_id<>?',(ctx.channel_id,)).fetchone()[0],0,'legacy_channel_binding.'+table)
    report['checks']['pending_sends_and_incomplete_requests_quarantined']=True
    for relative,digest_value in before.items():
        if relative.endswith(('.sqlite3','-wal','-shm','-journal')):continue
        require_equal(hashlib.sha256((destination/relative).read_bytes()).hexdigest(),digest_value,'private_file_preserved')
    for folder in ('uploads','tables'):
        original_folder=source/'bots'/bot['id']/folder
        if original_folder.is_dir():
            require_equal(inventory(ctx.root/folder),inventory(original_folder),'shared_media.'+folder)
    report['checks']['private_token_files_and_media_preserved']=True
    databases=[platform.path,ctx.db_path,ctx.transport_db_path]
    once={str(path):logical_database(path) for path in databases}
    Platform(destination)
    require_equal({str(path):logical_database(path) for path in databases},once,'second_start_idempotent')
    for path in databases:
        with closing(readonly(path)) as db:
            require_equal(db.execute('PRAGMA integrity_check').fetchone()[0],'ok','integrity')
            require_equal(list(db.execute('PRAGMA foreign_key_check')),[],'foreign_keys')
    require_equal(hashlib.sha256((destination/'bots'/bot['id']/'business.sqlite3').read_bytes()).hexdigest(),original_copy_hash,'legacy_copy_bytes_unchanged')
    require_equal(inventory(source),before,'source_snapshot_bytes_unchanged')
    report['checks'].update(source_snapshot_unchanged=True,legacy_database_unchanged=True,second_start_idempotent=True,sqlite_integrity_and_foreign_keys=True)
    report['ok']=True
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--source-is-snapshot',action='store_true',help='Explicitly acknowledge this is a consistent stopped/backup snapshot')
    args=parser.parse_args()
    if not args.source_is_snapshot:parser.error('只能对一致备份副本验收，请明确添加 --source-is-snapshot')
    print(json.dumps(verify(args.source,args.destination),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
