import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, date
import json

import pytest

from app.bot_capabilities import supports
from app.commands import Commands
from app.db import TZ, connect
from app.help import scoped_help
from app.messages import Outbox
from app.registration import create_form, submit_form
from app.scheduler import Scheduler
from app.time_rules import (normalize_when, normalize_capability_rules, normalize_broadcasts,
                            window_matches, calendar_matches, bind_clock)
from tests.test_core import CoreFixture


class TimePolicyTests(CoreFixture):
    def at(self, value):
        self.now=datetime.fromisoformat(value).replace(tzinfo=TZ)

    def rule(self, **kwargs):
        return {'id':'r1','name':'测试时段','enabled':True,'capabilities':['enrollment'],
                'action':'open','when':{'start_time':'17:00','end_time':'21:00'},**kwargs}

    def install(self, rules):
        self.platform.update(self.admin,self.ctx.id,{'capability_rules':rules})

    def test_open_close_boundaries_help_and_business(self):
        self.install([self.rule()])
        self.at('2026-10-12T16:59:59')
        with pytest.raises(PermissionError,match='当前时段'):
            asyncio.run(Commands(self.member,self.clock).run('报名'))
        help_text=scoped_help(self.member,{'status':'approved'},clock=self.clock)
        assert '报名与预约' not in help_text
        self.at('2026-10-12T17:00:00')
        assert '小李' in asyncio.run(Commands(self.member,self.clock).run('报名'))
        assert '报名与预约' in scoped_help(self.member,{'status':'approved'},clock=self.clock)
        self.at('2026-10-12T21:00:00')
        with pytest.raises(PermissionError,match='当前时段'):
            asyncio.run(Commands(self.member,self.clock).run('名单'))

    def test_overnight_is_anchored_to_start_day_and_close_wins(self):
        when=normalize_when({'start_date':'2026-10-12','end_date':'2026-10-12','start_time':'22:00','end_time':'02:00','weekdays':[0]})
        assert window_matches(when,datetime(2026,10,13,1,59,tzinfo=TZ),self.ctx.db_path)
        assert not window_matches(when,datetime(2026,10,13,2,0,tzinfo=TZ),self.ctx.db_path)
        self.install([self.rule(when=when),self.rule(id='r2',action='closed',when={'start_time':'23:00','end_time':'01:30'})])
        self.at('2026-10-13T00:30')
        ctx=bind_clock(self.platform.context_for_channel(self.ctx.channel_id),self.clock)
        assert not supports(ctx,'enrollment')
        assert supports(ctx,'registration')
        self.at('2026-10-13T01:30');assert supports(ctx,'enrollment')

    def test_statutory_makeup_weekend_holidays_and_missing_calendar(self):
        assert calendar_matches({'day_type':'workday'},date(2026,10,10),self.ctx.db_path) is True
        assert calendar_matches({'day_type':'weekend'},date(2026,10,10),self.ctx.db_path) is True
        assert calendar_matches({'day_type':'workday'},date(2026,10,1),self.ctx.db_path) is False
        assert calendar_matches({'holidays':'exclude'},date(2026,10,1),self.ctx.db_path) is False
        assert calendar_matches({'holidays':'only'},date(2026,10,1),self.ctx.db_path) is True
        assert calendar_matches({'day_type':'workday'},date(2090,10,1),self.ctx.db_path) is None
        self.install([self.rule(action='closed',when={'holidays':'only'})]);self.at('2090-10-01T12:00')
        assert not supports(bind_clock(self.platform.context_for_channel(self.ctx.channel_id),self.clock),'enrollment')

    def test_only_supported_features_and_removing_feature_cleans_rules(self):
        with pytest.raises(ValueError,match='已支持'):
            self.platform.update(self.admin,self.ctx.id,{'capabilities':['registration'],'capability_rules':[self.rule()]})
        self.install([self.rule()])
        self.platform.update(self.admin,self.ctx.id,{'capabilities':['registration']})
        assert self.platform.visible_bot(self.platform.bot(self.ctx.id),self.admin)['capability_rules']==[]
        assert not supports(self.platform.context_for_channel(self.ctx.channel_id),'enrollment')

    def test_form_created_in_open_window_cannot_submit_after_closing(self):
        self.s.update_config({'public_base_url':'http://127.0.0.1:9874'})
        self.install([self.rule(capabilities=['registration'],when={'start_time':'09:00','end_time':'17:00'})])
        actor=replace(self.member,actor='new-person')
        self.at('2026-10-12T16:59')
        link=create_form(actor,self.clock);token=link.split('.')[-1]
        public=replace(actor,actor='public')
        self.at('2026-10-12T17:00')
        with pytest.raises(PermissionError,match='当前时段'):
            submit_form(public,token,{'name':'新人','gender':'female'},self.clock)

    def test_queued_business_message_is_not_sent_after_time_closes(self):
        self.install([self.rule()]);self.at('2026-10-12T18:00')
        ctx=bind_clock(self.platform.context_for_channel(self.ctx.channel_id),self.clock)
        ids=Outbox(ctx).enqueue('名单内容','time-test')
        self.at('2026-10-12T21:00')
        assert asyncio.run(Outbox(ctx).deliver_one(self.platform,live=False,at=self.now))=='failed'
        with connect(ctx.transport_db_path) as db:
            assert db.execute('SELECT status FROM outbox WHERE id=?',(ids[0],)).fetchone()[0]=='failed'

    def broadcast(self, **kwargs):
        return {'id':'daily','name':'报名名单','enabled':True,'kinds':['roster'],
                'bot_ids':[self.ctx.id],'time':'18:00','when':{},**kwargs}

    def jobs(self):
        with connect(self.ctx.db_path) as db:
            return [dict(r) for r in db.execute('SELECT * FROM notification_jobs')]

    def test_broadcast_selects_type_bot_time_and_avoids_duplicate_tick(self):
        self.s.update_config({'broadcast_rules':[self.broadcast()]})
        self.at('2026-10-12T17:59');scheduler=Scheduler(self.platform,False,self.clock)
        self.at('2026-10-12T18:00');scheduler.tick();scheduler.tick()
        jobs=self.jobs();assert len(jobs)==1
        assert '第1场' in jobs[0]['text'] and '第2场' in jobs[0]['text']
        assert jobs[0]['text'].count('暂时无人报名，欢迎报名参与！')==2
        assert json.loads(jobs[0]['channel_ids'])==[self.ctx.channel_id]
        assert '报名+N' not in jobs[0]['text']

    def test_all_daily_broadcast_types_identify_empty_sessions_and_invite(self):
        self.s.update_config({'broadcast_rules':[self.broadcast(kinds=['open','closing','roster'])]})
        self.at('2026-10-12T17:59');scheduler=Scheduler(self.platform,False,self.clock)
        self.at('2026-10-12T18:00');scheduler.tick()
        jobs=self.jobs();assert len(jobs)==3
        assert all('第1场' in job['text'] and '第2场' in job['text'] and job['text'].count('欢迎报名参与')==2 for job in jobs)
        messages=[row for ctx in self.platform.system_contexts() for row in Outbox(ctx).list()]
        assert len(messages)==3
        assert all('第1场' in row['text'] and '第2场' in row['text'] and row['text'].count('欢迎报名参与')==2 for row in messages)

    def test_overlapping_rules_same_group_only_once_but_different_time_runs(self):
        self.s.update_config({'broadcast_rules':[self.broadcast(),self.broadcast(id='second',bot_ids=[self.bots[1]['id']]),self.broadcast(id='later',time='18:05')]})
        self.at('2026-10-12T17:59');scheduler=Scheduler(self.platform,False,self.clock)
        self.at('2026-10-12T18:00');scheduler.tick();assert len(self.jobs())==1
        self.at('2026-10-12T18:05');scheduler.tick();assert len(self.jobs())==2

    def test_mixed_reminders_keep_empty_session_invitation(self):
        self.s.enroll('1001','unused',session_id=self.s.sessions()[0]['id'])
        self.s.update_config({'broadcast_rules':[self.broadcast(kinds=['open','closing','roster'])]})
        self.at('2026-10-12T17:59');scheduler=Scheduler(self.platform,False,self.clock)
        self.at('2026-10-12T18:00');scheduler.tick()
        jobs=self.jobs();assert len(jobs)==3
        for job in jobs:
            assert '第1场' in job['text'] and '第2场' in job['text']
            assert job['text'].count('欢迎报名参与')==1
            assert '**20:30–22:00**' in job['text']

    def test_weekend_filter_skips_monday_and_queued_edits_cancel_delivery(self):
        self.s.update_config({'broadcast_rules':[self.broadcast(when={'day_type':'weekend'})]})
        self.at('2026-10-12T17:59');scheduler=Scheduler(self.platform,False,self.clock)
        self.at('2026-10-12T18:00');scheduler.tick();assert self.jobs()==[]
        self.s.update_config({'broadcast_rules':[self.broadcast(time='18:01')]})
        self.at('2026-10-12T18:01');scheduler.tick();assert len(self.jobs())==1
        self.s.update_config({'broadcast_rules':[]})
        result=asyncio.run(Outbox(self.ctx).deliver_one(self.platform,live=False,at=self.now))
        assert result=='failed'

    def test_invalid_rules_are_rejected_without_saving(self):
        before=self.s.config()['broadcast_rules']
        for rule in (self.broadcast(bot_ids=[]),self.broadcast(bot_ids=['not-a-bot']),self.broadcast(time='25:00'),self.broadcast(kinds=['unknown'])):
            with pytest.raises(ValueError):self.s.update_config({'broadcast_rules':[rule]})
        assert self.s.config()['broadcast_rules']==before

    def test_ai_plan_prepared_when_open_cannot_commit_after_closing(self):
        from app.ai_plan_flow import Plans
        from app.ai_conversation import AiConversation
        self.s.update_config({'ai':{'enabled':True,'api_key':'synthetic-test-key','confirmation_minutes':30}})
        self.install([self.rule()]);self.at('2026-10-12T20:59')
        commands=Commands(self.member,self.clock)
        step={'operation':'Register','arguments':{'venue_id':1,'date':'2026-10-13','number':1,'guests':0,'guest_names':[],'chain':False}}
        proposal={'intent':'BusinessPlan','steps':[step],'requires_confirmation':True,
                  'scope':{'bot_id':self.member.id,'chat_id':self.member.chat_id,'user_id':self.member.actor}}
        assert '确认' in asyncio.run(Plans(commands).propose(proposal))
        token=AiConversation(self.member,self.clock).current()['token']
        self.at('2026-10-12T21:00')
        with pytest.raises((ValueError,PermissionError)):
            asyncio.run(Plans(Commands(self.member,self.clock)).confirm(token))
        with connect(self.ctx.db_path) as db:assert db.execute('SELECT count(*) FROM registrations').fetchone()[0]==0


@pytest.mark.parametrize('value',[{'start_date':'2026-2-01'},{'start_date':'2026-10-20','end_date':'2026-10-01'},
    {'start_time':'12:00'},{'start_time':'12:00','end_time':'12:00'},{'weekdays':[True]}, {'day_type':'unknown'}])
def test_reject_invalid_calendar_conditions(value):
    with pytest.raises(ValueError):normalize_when(value)
