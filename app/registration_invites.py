"""Explicit administrator invitations through a selected registration robot."""
import hashlib
import json
import re
import uuid
from html import escape as html_escape

from .db import connect, now
from .member_imports import ensure_schema
from .bot_capabilities import require_capability
from .time_rules import bind_clock
from .messages import Outbox, escape


class RegistrationInvites:
    def __init__(self, platform, ctx, clock=now):
        ctx.require('member.manage')
        self.platform,self.ctx,self.clock=platform,ctx,clock

    def destination(self, channel_id):
        channel=self.platform.channel(channel_id,active=True)
        target=bind_clock(self.platform.context_for_channel(channel_id),self.clock)
        require_capability(target,'registration')
        if not channel['webhook_url'] or not target.receive_commands:
            raise ValueError('请选择能发送消息并接收注册指令的机器人')
        return target

    def channels(self):
        result=[]
        for row in self.platform.list_channels(active_only=True):
            try:self.destination(row['id'])
            except (PermissionError,ValueError):continue
            result.append({key:row.get(key,'') for key in ('id','name','bot_name','bot_id')})
        return result

    def preview(self, data):
        target=self.destination(data.get('channel_id'))
        ids=data.get('record_ids')
        if (not isinstance(ids,list) or not ids or len(ids)>1000
                or any(type(i) is not int or i<1 for i in ids)):
            raise ValueError('请勾选待本人确认的人员')
        with connect(self.ctx.db_path) as db:
            ensure_schema(db)
            rows=[dict(r) for r in db.execute('SELECT * FROM pre_registered_members WHERE scope_id=? AND linked_user_id IS NULL AND merged_into_id IS NULL',
                                            (self.ctx.scope_id,)) if r['id'] in ids]
        if len(rows)!=len(set(ids)):raise ValueError('名单已变化，请刷新后重新选择')
        names=[]
        for row in rows:
            email=json.loads(row.get('identity_attributes') or '{}').get('email','')
            # The provider accepts a real email or ID for a mention, not a name.
            if re.fullmatch(r'[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',email):
                names.append(f'<at email="{html_escape(email,quote=True)}">{html_escape(row["name"])}</at>')
            else:names.append(escape(row['name'] or '待补姓名'))
        text='**请确认会员资料**\n\n'+'、'.join(names)+'\n\n请 @本机器人 发送 **注册确认**，打开链接确认本人姓名和资料。'
        signature=hashlib.sha256(json.dumps([target.channel_id,rows],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return {'text':text,'count':len(rows),'signature':signature,'channel_id':target.channel_id,
                'request_id':uuid.uuid4().hex}

    def send(self,data):
        request_id=data.get('request_id','')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-fA-F0-9-]{32,36}',request_id):
            raise ValueError('请重新预览通知后发送')
        preview=self.preview(data)
        if data.get('signature')!=preview['signature']:raise ValueError('名单已变化，请重新预览后发送')
        target=self.destination(preview['channel_id'])
        key='registration-invite:'+self.ctx.actor+':'+request_id
        ids=Outbox(target).enqueue(preview['text'],key)
        return {'queued':len(ids),'outbox_ids':ids}
