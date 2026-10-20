"""Compute each club reminder once and fan out to explicit group channels."""
import asyncio
import logging
from datetime import datetime
from .db import now, TZ
from .service import Service
from .messages import Outbox, roster, escape, empty_roster
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
        from .time_rules import broadcast_rules, calendar_matches, bind_clock, rule_fingerprint
        channels=[bind_clock(c,lambda:at) for c in channels]
        sent_sessions={}
        if config['daily_enabled']:
            for rule in broadcast_rules(config,sorted({c.id for c in channels})):
                target=datetime.fromisoformat(at.astimezone(TZ).date().isoformat()+'T'+rule['time']).replace(tzinfo=TZ)
                if not rule['enabled'] or not previous<target<=at:continue
                if calendar_matches(rule['when'],target.date(),ctx.db_path) is not True:continue
                for venue in service.venues():
                    if not venue['active']:continue
                    for kind in rule['kinds']:
                        for channel in channels:
                            if channel.id not in rule['bot_ids'] or not channel.daily_notifications or not supports(channel,'enrollment'):continue
                            allocation=(target.date().isoformat(),rule['time'],kind,channel.chat_id)
                            scoped=Service(channel,lambda:at)
                            try:sessions=scoped.sessions(target.date().isoformat(),venue['id'])
                            except (ValueError,PermissionError):continue
                            shown=[item for item in sessions if item['id'] not in sent_sessions.get(allocation,set()) and (kind!='closing' or item['status']!='ended')]
                            if not shown:continue
                            data=scoped.registrations(target.date().isoformat(),venue['id'])
                            shown_ids={item['id'] for item in shown}
                            data['sessions']=[item for item in data['sessions'] if item['id'] in shown_ids]
                            data['registrations']=[item for item in data['registrations'] if item['session_id'] in shown_ids]
                            if kind=='roster' or empty_roster(data):
                                text=roster(data,venue['id'])
                            else:
                                title='报名即将结束' if kind=='closing' else '今日报名'
                                text=f"## {escape(venue['name'])} · {title}\n\n"+'\n'.join(
                                    f"{escape(item['name'])} {item['start']}–{item['end']}"+
                                    (' · 🔴 已占用：'+escape(item['booking_notice']) if item.get('occupancies') else '') for item in shown)
                            key=f"broadcast:{rule['id']}:{rule_fingerprint(rule)}:{target.date()}:{rule['time'].replace(':','')}:{venue['id']}:{kind}:{channel.channel_id}"
                            self.notifications.enqueue(text,[channel.channel_id],key=key,
                                                       venue_id=venue['id'],session_ids=[item['id'] for item in shown],session_snapshots=[snapshot(item) for item in shown])
                            sent_sessions.setdefault(allocation,set()).update(item['id'] for item in shown)
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
