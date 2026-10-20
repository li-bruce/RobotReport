"""Named groups and authenticated first-callback discovery in the registry."""
import hmac
import re
import uuid
from contextlib import ExitStack
from . import env_settings

from .db import connect, stamp


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS chat_groups(
        id TEXT PRIMARY KEY,name TEXT NOT NULL,chat_id TEXT NOT NULL DEFAULT '',
        merged_into TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL)''')
    if 'deleted_at' not in {r['name'] for r in db.execute('PRAGMA table_info(chat_groups)')}:
        db.execute("ALTER TABLE chat_groups ADD COLUMN deleted_at TEXT NOT NULL DEFAULT ''")
        db.execute('DROP INDEX IF EXISTS unique_known_chat_group')
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS unique_known_chat_group ON chat_groups(chat_id) WHERE chat_id<>'' AND merged_into='' AND deleted_at=''")
    if 'group_id' not in {r['name'] for r in db.execute('PRAGMA table_info(bot_channels)')}:
        db.execute("ALTER TABLE bot_channels ADD COLUMN group_id TEXT NOT NULL DEFAULT ''")
    # Historical, removed connections do not recreate groups in the workspace.
    rows=list(db.execute("SELECT id,name,chat_id FROM bot_channels WHERE archived=0 AND group_id='' AND chat_id<>'' ORDER BY created_at,id"))
    for row in rows:
        group=for_chat(db,row['chat_id'],row['name'])
        db.execute('UPDATE bot_channels SET group_id=? WHERE id=?',(group['id'],row['id']))


def get(db, group_id):
    if not isinstance(group_id,str) or not re.fullmatch('[a-f0-9]{32}',group_id):
        raise ValueError('请选择已有的群')
    seen=set()
    while group_id not in seen:
        seen.add(group_id)
        row=db.execute('SELECT * FROM chat_groups WHERE id=?',(group_id,)).fetchone()
        if not row or row['deleted_at']:raise ValueError('群不存在或已删除，请刷新后选择')
        if not row['merged_into']:return dict(row)
        group_id=row['merged_into']
    raise ValueError('群关联异常，请联系管理员')


def for_chat(db, chat_id, name):
    row=db.execute("SELECT * FROM chat_groups WHERE chat_id=? AND merged_into='' AND deleted_at=''",(chat_id,)).fetchone()
    if row:return dict(row)
    group_id=uuid.uuid4().hex
    db.execute('INSERT INTO chat_groups(id,name,chat_id,created_at) VALUES(?,?,?,?)',
               (group_id,name or '未命名群',chat_id,stamp()))
    return get(db,group_id)


def attach(db, record):
    group=get(db,record['group_id']) if record.get('group_id') else for_chat(db,record['chat_id'],record['name'])
    if record['chat_id'] and group['chat_id']!=record['chat_id']:
        raise ValueError('群已被识别或调整，请刷新后重试')
    record['group_id']=group['id']
    return record


class BotGroups:
    def __init__(self, platform):
        self.platform=platform

    def list(self, principal):
        self.platform.require_platform(principal)
        with connect(self.platform.path) as db:
            return [dict(r) for r in db.execute("SELECT id,name,chat_id,created_at FROM chat_groups WHERE merged_into='' AND deleted_at='' ORDER BY created_at,id")]

    def save(self, principal, data, group_id=None):
        self.platform.require_platform(principal)
        if set(data)!={'name'} or not isinstance(data['name'],str):
            raise ValueError('只需填写群名称，群ID由机器人自动识别')
        name=data['name'].strip()
        if not 1<=len(name)<=80 or any(ord(c)<32 for c in name):
            raise ValueError('群名称限1至80字')
        with connect(self.platform.path,True) as db:
            if group_id:
                group_id=get(db,group_id)['id']
                db.execute('UPDATE chat_groups SET name=? WHERE id=?',(name,group_id))
                db.execute('UPDATE bot_channels SET name=? WHERE group_id=? AND archived=0',(name,group_id))
            else:
                group_id=uuid.uuid4().hex
                db.execute('INSERT INTO chat_groups(id,name,created_at) VALUES(?,?,?)',(group_id,name,stamp()))
            return get(db,group_id)

    def remove(self, principal, group_id):
        self.platform.require_platform(principal)
        with env_settings.locked(self.platform.root), ExitStack() as changes, connect(self.platform.path,True) as db:
            group=db.execute('SELECT * FROM chat_groups WHERE id=?',(group_id,)).fetchone()
            if not group:raise ValueError('群不存在，请刷新后重试')
            # A stale modal must not delete the group that absorbed its placeholder.
            if group['merged_into']:raise ValueError('群已合并，请刷新后重新选择')
            if group['deleted_at']:return {'ok':True,'disconnected':0}
            channels=list(db.execute('SELECT id,bot_id FROM bot_channels WHERE group_id=? AND archived=0',(group_id,)))
            for channel in channels:
                changes.enter_context(env_settings.change(self.platform.root, bot_id=channel['bot_id'],
                    bot_values={'webhook_url':None,'robot_key':None}))
            at=stamp()
            db.execute('UPDATE bot_channels SET active=0,archived=1,archived_at=? WHERE group_id=? AND archived=0',(at,group_id))
            for channel in channels:self.platform._sync_primary_channel(db,channel['bot_id'])
            db.execute('UPDATE chat_groups SET deleted_at=? WHERE id=?',(at,group_id))
        return {'ok':True,'disconnected':len(channels)}


def discover(platform, bot_id, token, body):
    """Only a valid POST for a provisioned pending connection can identify a group.

    Each robot must verify its own connection; identifying a shared group never
    activates the other robots or moves their historical queues.
    """
    from .security import digest, identifier
    chat,user=identifier(body.get('chatid')),identifier(body.get('creator'))
    key=body.get('robot_key')
    content,ctime=body.get('content'),str(body.get('ctime',''))
    with connect(platform.path,True) as db:
        bot=db.execute('SELECT * FROM bots WHERE id=?',(bot_id,)).fetchone()
        if not bot or not bot['active'] or not hmac.compare_digest(bot['callback_hash'],digest(token)):
            raise PermissionError('回调凭据无效')
        rows=list(db.execute('SELECT * FROM bot_channels WHERE bot_id=? AND archived=0 AND active=1',(bot_id,)))
        if len(rows)!=1:
            raise PermissionError('回调群未绑定或已停用')
        channel=rows[0]
        receive_only = not channel['webhook_url'] and not channel['robot_key']
        if (not channel['receive_commands'] or (not receive_only and (
                not channel['robot_key'] or not isinstance(key,str)
                or not hmac.compare_digest(key.encode(),channel['robot_key'].encode())))):
            raise PermissionError('回调凭据无效或群指令已停用')
        # A racing callback may have resolved the connection while we waited.
        if channel['chat_id']:
            if channel['chat_id']==chat:return channel['id']
            raise PermissionError('回调群与所选群不符，请检查机器人连接')
        if not isinstance(content,str) or not 0<len(content)<=4000 or not re.fullmatch(r'\d{1,20}',ctime):
            raise ValueError('回调字段无效')
        group=get(db,channel['group_id'])
        if group['chat_id'] and group['chat_id']!=chat:
            raise PermissionError('回调群与所选群不符，请检查机器人连接')
        if not group['chat_id']:
            existing=db.execute("SELECT * FROM chat_groups WHERE chat_id=? AND merged_into='' AND deleted_at=''",(chat,)).fetchone()
            if existing:
                db.execute('UPDATE chat_groups SET merged_into=? WHERE id=?',(existing['id'],group['id']))
                db.execute('UPDATE bot_channels SET group_id=? WHERE group_id=? AND archived=0',(existing['id'],group['id']))
                group=dict(existing)
            else:
                db.execute('UPDATE chat_groups SET chat_id=? WHERE id=?',(chat,group['id']))
        db.execute('UPDATE bot_channels SET chat_id=?,group_id=?,name=? WHERE id=?',
                   (chat,group['id'],group['name'],channel['id']))
        platform._sync_primary_channel(db,bot_id)
        return channel['id']
