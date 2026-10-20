"""Explicit send test and authenticated receive proof for robot onboarding."""
import hashlib
import hmac
import json
import re
import sqlite3
from datetime import datetime

import httpx

from .db import connect, now, stamp


def revision(channel, bot):
    return hashlib.sha256(json.dumps([channel['id'],channel['webhook_url'],channel['robot_key'],
                                     bot['callback_hash']]).encode()).hexdigest()


def ensure_schema(platform):
    with connect(platform.path,True) as db:
        first=not db.execute("SELECT 1 FROM sqlite_master WHERE name='bot_connection_checks'").fetchone()
        db.execute('''CREATE TABLE IF NOT EXISTS bot_connection_checks(
            channel_id TEXT PRIMARY KEY REFERENCES bot_channels(id),revision TEXT NOT NULL,
            send_status TEXT NOT NULL DEFAULT '',send_at TEXT NOT NULL DEFAULT '',
            callback_at TEXT NOT NULL DEFAULT '',verified_at TEXT NOT NULL DEFAULT '')''')
        db.execute('''CREATE TABLE IF NOT EXISTS bot_connection_attempts(
            id TEXT PRIMARY KEY,channel_id TEXT NOT NULL REFERENCES bot_channels(id),revision TEXT NOT NULL,
            status TEXT NOT NULL,created_at TEXT NOT NULL,finished_at TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '')''')
        if not first:return
        # Only real completed traffic can carry forward the old connected state.
        for channel in list(db.execute('SELECT * FROM bot_channels WHERE archived=0')):
            path=platform.root_for(channel['bot_id'])/'transport.sqlite3'
            if not path.exists():continue
            try:
                with connect(path) as transport:
                    received=transport.execute("SELECT MAX(created_at) FROM inbox WHERE channel_id=? AND status='completed'",(channel['id'],)).fetchone()[0]
                    sent=transport.execute("SELECT MAX(created_at) FROM outbox WHERE channel_id=? AND status='sent'",(channel['id'],)).fetchone()[0]
            except sqlite3.OperationalError:
                continue
            if received and sent:
                bot=db.execute('SELECT * FROM bots WHERE id=?',(channel['bot_id'],)).fetchone()
                db.execute('INSERT INTO bot_connection_checks(channel_id,revision,send_status,send_at,verified_at) VALUES(?,?,?,?,?)',
                           (channel['id'],revision(channel,bot),'sent',sent,received))


def _check(db, channel, bot):
    rev=revision(channel,bot)
    row=db.execute('SELECT * FROM bot_connection_checks WHERE channel_id=? AND revision=?',(channel['id'],rev)).fetchone()
    return dict(row) if row else {'channel_id':channel['id'],'revision':rev,'send_status':'','send_at':'','callback_at':'','verified_at':''}


def recover_interrupted(platform):
    # Called once from application startup, never during a request/context refresh.
    with connect(platform.path,True) as db:
        db.execute("UPDATE bot_connection_attempts SET status='unknown',error='服务重启，发送结果待确认；请先检查群消息' WHERE status='sending'")
        db.execute("UPDATE bot_connection_checks SET send_status='unknown' WHERE send_status='sending'")


def _ensure(db, channel, bot):
    row=_check(db,channel,bot)
    db.execute('''INSERT INTO bot_connection_checks(channel_id,revision) VALUES(?,?)
        ON CONFLICT(channel_id) DO UPDATE SET revision=excluded.revision,send_status='',send_at='',callback_at='',verified_at=''
        WHERE bot_connection_checks.revision<>excluded.revision''',(channel['id'],row['revision']))
    return row


def summary(platform, channel, bot=None):
    # Archived connection history is readable even after its robot is removed.
    # Send tests and authenticated callbacks still require an active robot.
    bot=bot or platform.bot(channel['bot_id'],include_deleted=True)
    with connect(platform.path) as db:row=_check(db,channel,bot)
    sent=row['send_status']=='sent'
    receive_only = not channel['webhook_url'] and not channel['robot_key'] and bool(channel['receive_commands'])
    if (sent and (row['verified_at'] or not channel['receive_commands'])) or (receive_only and row['verified_at']):status='connected'
    elif receive_only:status='waiting_message' if row['callback_at'] else 'waiting_callback'
    elif sent:status='waiting_message' if row['callback_at'] else 'waiting_callback'
    else:status='needs_test'
    return {'connection_status':status,'connection_mode':'receive_only' if receive_only else ('send_only' if not channel['receive_commands'] else 'duplex'),'send_verified':sent,'callback_checked':bool(row['callback_at']),
            'receive_verified':bool(row['verified_at']),'last_send_status':row['send_status']}


def record_callback(platform, bot_id, token, channel_id=None, body=None):
    from .security import digest
    with connect(platform.path,True) as db:
        bot=db.execute('SELECT * FROM bots WHERE id=? AND active=1',(bot_id,)).fetchone()
        if not bot or bot['callback_hash']!=digest(token):return
        channels=list(db.execute('SELECT * FROM bot_channels WHERE bot_id=? AND active=1 AND archived=0',(bot_id,)))
        for channel in channels:
            if channel_id and channel['id']!=channel_id:continue
            receive_only = not channel['webhook_url'] and not channel['robot_key']
            if channel_id and (not body or channel['chat_id']!=str(body.get('chatid',''))
                    or not channel['receive_commands'] or (not receive_only and (
                    not isinstance(body.get('robot_key'),str)
                    or not hmac.compare_digest(channel['robot_key'].encode(),body['robot_key'].encode())))):continue
            _ensure(db,channel,bot)
            field='verified_at' if channel_id else 'callback_at'
            db.execute(f'UPDATE bot_connection_checks SET {field}=? WHERE channel_id=?',(stamp(),channel['id']))


def record_sent(platform, channel, bot, at=None):
    """A normal accepted reply is also send proof; no extra test is required."""
    rev=revision(channel,bot)
    with connect(platform.path,True) as db:
        current=db.execute('SELECT * FROM bot_channels WHERE id=? AND active=1 AND archived=0',(channel['id'],)).fetchone()
        current_bot=db.execute('SELECT * FROM bots WHERE id=? AND active=1',(bot['id'],)).fetchone()
        if not current or not current_bot or revision(current,current_bot)!=rev:return
        _ensure(db,current,current_bot)
        db.execute("UPDATE bot_connection_checks SET send_status='sent',send_at=? WHERE channel_id=?",(stamp(at),channel['id']))


async def send_test(platform, principal, bot_id, channel_id, request_id, *, live=False, client=None, clock=now):
    platform.require_platform(principal)
    if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9-]{32,36}',request_id):
        raise ValueError('测试请求无效，请重新打开连接设置')
    bot=platform.bot(bot_id,active=True)
    channel=platform.channel(channel_id,active=True)
    if channel['bot_id']!=bot_id:raise PermissionError('连接不属于此机器人')
    from .security import validate_webhook
    url=validate_webhook(channel['webhook_url'], platform.root)
    if not url:raise ValueError('请先保存群里提供的发送 Webhook')
    at=stamp(clock());rev=revision(channel,bot)
    with connect(platform.path,True) as db:
        existing=db.execute('SELECT * FROM bot_connection_attempts WHERE id=?',(request_id,)).fetchone()
        if existing:
            if existing['channel_id']!=channel_id or existing['revision']!=rev:raise ValueError('连接已变更，请重新测试')
            return {'status':existing['status'],'message':existing['error'] or '本次测试已提交，请查看连接状态'}
        current=db.execute('SELECT * FROM bot_channels WHERE id=? AND archived=0 AND active=1',(channel_id,)).fetchone()
        current_bot=db.execute('SELECT * FROM bots WHERE id=? AND active=1',(bot_id,)).fetchone()
        if not current or not current_bot or revision(current,current_bot)!=rev:raise ValueError('连接已变更，请重新打开设置')
        latest=db.execute('SELECT * FROM bot_connection_attempts WHERE channel_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(channel_id,)).fetchone()
        if latest and (clock()-datetime.fromisoformat(latest['created_at'])).total_seconds()<20:
            raise ValueError('请等待20秒后再发送测试消息')
        _ensure(db,current,current_bot)
        db.execute('INSERT INTO bot_connection_attempts(id,channel_id,revision,status,created_at) VALUES(?,?,?,?,?)',(request_id,channel_id,rev,'sending',at))
        db.execute("UPDATE bot_connection_checks SET send_status='sending' WHERE channel_id=?",(channel_id,))
    status,error='simulated','当前为演示模式，未向群发送消息；请在正式服务中测试连接'
    if live:
        own=client is None
        client=client or httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False)
        try:
            from .integrations import outbound_payload, accepted
            payload={'msgtype':'text','text':{'content':
                f'机器人「{bot["name"]}」消息发送连接成功。\n请管理员继续在后台复制回调地址，填入机器人平台或消息适配器；随后在群内 @机器人发送“连接”。'}}
            response=await client.post(url,json=outbound_payload(payload,url,platform.root,event_id=request_id,group_id=channel['chat_id']),timeout=15)
            if response.status_code>=500:status,error='unknown','平台响应异常，发送结果待确认；请先检查群消息'
            else:
                status='sent' if accepted(response,url,platform.root) else 'failed'
                error='' if status=='sent' else '平台未接受测试消息，请核对发送地址及机器人的安全设置'
        except (httpx.HTTPError,ValueError):
            status,error='unknown','未收到明确的发送结果，请先检查群消息后再决定是否重试'
        finally:
            if own:await client.aclose()
    with connect(platform.path,True) as db:
        db.execute('UPDATE bot_connection_attempts SET status=?,finished_at=?,error=? WHERE id=?',(status,stamp(clock()),error,request_id))
        current=db.execute('SELECT * FROM bot_channels WHERE id=? AND archived=0 AND active=1',(channel_id,)).fetchone()
        current_bot=db.execute('SELECT * FROM bots WHERE id=? AND active=1',(bot_id,)).fetchone()
        if current and current_bot and revision(current,current_bot)==rev:
            db.execute('UPDATE bot_connection_checks SET send_status=?,send_at=? WHERE channel_id=? AND revision=?',(status,at,channel_id,rev))
        else:
            return {'status':'changed','message':'测试期间连接已被调整，请重新打开连接设置'}
    return {'status':status,'message':error or '测试消息已发送，请继续配置回调地址'}
