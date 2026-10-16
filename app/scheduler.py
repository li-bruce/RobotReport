"""Compute each club reminder once and fan out to explicit group channels."""
import asyncio
import logging
from datetime import datetime
from .db import now, TZ
from .service import Service
from .messages import Outbox, roster, escape
from .notifications import Notifications
from .identity_notifications import IdentityNotifications
from .scope import snapshot
from .bot_capabilities import supports
log=logging.getLogger(__name__)

class Scheduler:
    def __init__(self,platform,live=False,clock=now):
        self.platform,self.live,self.clock=platform,live,clock
        self.last_tick=clock();self.running=True;self.last_cleanup=None
        self.notifications=Notifications(platform,clock)
        self.identity_notifications=IdentityNotifications(platform,clock)
    def recover(self):
        seen=set()
        for ctx in self.platform.system_contexts():
            if ctx.transport_db_path not in seen:
                Outbox(ctx).recover();seen.add(ctx.transport_db_path)
        self.notifications.recover()
        self.identity_notifications.recover()
    def tick(self,at=None):
        at=at or self.clock();previous=self.last_tick;self.last_tick=at
        ctx=self.platform.workspace_context()
        if self.last_cleanup is None or (at-self.last_cleanup).total_seconds()>=3600:
            from .table_images import cleanup
            cleanup(ctx,self.clock);self.last_cleanup=at
        service=Service(ctx,self.clock);config=service.config()
        from .availability import dispatch_notices
        dispatch_notices(self.platform, self.clock)
        channels=self.platform.system_contexts()
        # Within a shared group, prefer a sender with an outgoing webhook;
        # otherwise an unconfigured robot could consume the dedupe allocation.
        channels.sort(key=lambda channel:not bool(self.platform.channel(channel.channel_id)['webhook_url']))
        if config['daily_enabled']:
            for venue in service.venues():
                if not venue['active']:continue
                for kind,hhmm in config['reminders'].items():
                    target=datetime.fromisoformat(at.date().isoformat()+'T'+hhmm).replace(tzinfo=TZ)
                    if not previous<target<=at:continue
                    sent_sessions={}
                    for channel in channels:
                        if not channel.daily_notifications or not supports(channel, 'enrollment'):continue
                        scoped=Service(channel,self.clock)
                        try:
                            sessions=scoped.sessions(at.date().isoformat(),venue['id'])
                        except (ValueError,PermissionError):continue
                        sessions=[item for item in sessions if item['id'] not in sent_sessions.get(channel.chat_id,set())]
                        if not sessions:continue
                        remaining=[item for item in sessions if item['status']!='ended']
                        shown=sessions if kind!='closing' else remaining
                        if not shown:continue
                        if kind=='roster':
                            data=scoped.registrations(at.date().isoformat(),venue['id'])
                            shown_ids={item['id'] for item in shown}
                            data['sessions']=[item for item in data['sessions'] if item['id'] in shown_ids]
                            data['registrations']=[item for item in data['registrations'] if item['session_id'] in shown_ids]
                            text=roster(data,venue['id'])
                        elif kind=='closing':
                            text='# 活动提醒\n\n'+ '；'.join(f"{item['name']} {item['start']}–{item['end']}，"+('已占用，不可报名：'+escape(item['booking_notice']) if item.get('occupancies') else item['end']+'结束报名') for item in shown)
                            if any(not item.get('occupancies') for item in shown):text+='\n\n发送 场地'+str(venue['id'])+' 报名。'
                            if supports(channel, 'registration'):text+='新用户先发送 注册。'
                        else:
                            onboarding='新用户先发送 注册。' if supports(channel, 'registration') else ''
                            action='发送 报名 或 名单。' if any(not item.get('occupancies') and item['status']!='ended' for item in shown) else '发送 名单 查看安排。'
                            text=f"# {venue['name']} · 今日活动\n\n{action}{onboarding}\n\n"+'；'.join(f"{item['name']} {item['start']}–{item['end']}"+(' · 已占用，不可报名：'+escape(item['booking_notice']) if item.get('occupancies') else '') for item in shown)
                        self.notifications.enqueue(text,[channel.channel_id],key=f"daily:{at.date()}:{venue['id']}:{kind}:{channel.channel_id}",
                                                   venue_id=venue['id'],session_ids=[item['id'] for item in shown],session_snapshots=[snapshot(item) for item in shown])
                        sent_sessions.setdefault(channel.chat_id,set()).update(item['id'] for item in shown)
        from .competitions import TournamentService
        tournaments=TournamentService(ctx.db_path)
        for notice in tournaments.due_notifications(at):
            event=tournaments.get(notice['tournament_id'])
            destinations=event.get('channel_ids',[])
            if destinations:
                try:self.notifications.enqueue(notice['text'],destinations,key='tournament:'+notice['key'],venue_id=event['venue_id'],tournament_id=event['id'])
                except (ValueError,PermissionError):log.warning('Tournament notification channel needs configuration')
        self.notifications.recover()
        self.identity_notifications.recover()
    async def run(self):
        self.recover()
        while self.running:
            try:
                self.tick()
                for ctx in self.platform.system_contexts():await Outbox(ctx).deliver_one(self.platform,live=self.live)
            except asyncio.CancelledError:raise
            except Exception:log.error('Scheduler iteration failed; inspect local queue and configuration')
            await asyncio.sleep(3)
