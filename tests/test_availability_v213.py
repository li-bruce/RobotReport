import asyncio
import json
from dataclasses import replace
from datetime import datetime
from unittest.mock import patch

from app.availability import Availability, dispatch_notices
from app.holidays import HolidaySchedule, day_kind
from app.db import connect, TZ
from app.service import Service
from app.tournaments import Tournaments
from app.messages import roster
from app.commands import Commands
from tests.test_core import CoreFixture


class AvailabilityTests(CoreFixture):
    def manager(self):
        return Availability(self.platform.workspace_context(),self.clock)

    def block(self,**changes):
        return self.manager().save({'venue_id':1,'date':'2026-10-12','start':'18:00','end':'20:00','title':'校队训练',**changes})

    def test_shared_booking_gate_preserves_roster_and_allows_cancellation(self):
        service=Service(self.member,self.clock)
        service.enroll('1001','小李',guests=1)
        before=service.registrations()['registrations']
        block=self.block()
        other=self.platform.context_for_channel(self.bots[1]['primary_channel_id'] if 'primary_channel_id' in self.bots[1] else self.platform.channels(self.admin,self.bots[1]['id'])[0]['id'])
        self.assertEqual(Service(other,self.clock).sessions()[0]['booking_state'],'occupied')
        with self.assertRaisesRegex(ValueError,'不可报名'):
            service.enroll('1001','小李',guests=1)
        self.assertEqual(len(service.registrations()['registrations']),2)
        self.assertEqual(service.registrations()['registrations'][0]['session_snapshot'],before[0]['session_snapshot'])
        self.assertIn('校队训练',roster(service.registrations()))
        self.assertEqual(service.cancel('1001')['cancelled'],1)
        self.manager().release(block['id'],block['version'])
        service.enroll('1001','小李')
        self.assertEqual(service.sessions()[0]['booking_state'],'available')

    def test_overlap_adjacency_permissions_scope_and_atomic_edit(self):
        block=self.block()
        self.block(start='20:00',end='20:30',title='相邻训练')
        with self.assertRaisesRegex(ValueError,'已由'):
            self.block(start='19:00',end='21:00')
        with self.assertRaises(PermissionError):
            Availability(self.member,self.clock).save({'venue_id':1,'date':'2026-10-12','start':'12:00','end':'13:00','title':'未授权'})
        self.platform.update(self.admin,self.ctx.id,{'slot_keys':[self.s.slots()[1]['slot_key']]})
        with self.assertRaises(PermissionError):
            Availability(self.ctx,self.clock).release(block['id'])
        with self.assertRaisesRegex(ValueError,'已由'):
            self.manager().save({'venue_id':1,'date':block['date'],'start':'19:00','end':'21:00','title':'改动','expected_version':block['version']},block['id'])
        self.assertEqual(self.manager().list(1)[0]['title'],'校队训练')

    def test_large_event_reserves_releases_atomically_and_blocks_daily_plan(self):
        events=Tournaments(self.ctx,self.clock)
        data={'kind':'large','title':'联赛','keyword':'联赛','date':'2026-10-19','options':{'reservations':[{'date':'2026-10-19','start':'17:00','end':'21:00'}]}}
        with patch('app.competitions._now',self.clock):
            event=events.create(data)
            self.assertEqual(self.s.sessions('2026-10-19')[0]['booking_state'],'occupied')
            with self.assertRaisesRegex(ValueError,'已由'):
                events.create(data|{'title':'第二比赛','keyword':'第二比赛'})
            self.assertEqual(len(events.list()),1)
            with self.assertRaisesRegex(ValueError,'不可报名'):
                events.planning_candidates('2026-10-19')
            events.transition(event['id'],'close')
        self.assertEqual(self.s.sessions('2026-10-19')[0]['booking_state'],'available')
        self.assertEqual(self.manager().list(1,'2026-10-19','2026-10-19',True)[0]['status'],'released')

    def test_bot_command_scope_management_and_read_access(self):
        ctx=self.platform.workspace_context()
        reply=asyncio.run(Commands(ctx,self.clock).run('场地1 明天 占用 17:30-20:30 维修'))
        self.assertIn('占用已登记',reply)
        reply=asyncio.run(Commands(self.member,self.clock).run('明天 场地占用'))
        self.assertIn('维修',reply)
        with self.assertRaises(PermissionError):
            asyncio.run(Commands(self.member,self.clock).run('明天 占用 20:30-21:30 非授权'))
        self.assertIn('场地占用',asyncio.run(Commands(self.member,self.clock).run('帮助')))

    def test_change_notifications_dedup_and_only_responsible_sessions(self):
        self.block()
        second=self.platform.context(self.admin,self.bots[1]['id'])
        self.platform.update(self.admin,self.ctx.id,{'slot_keys':[self.s.slots()[1]['slot_key']]})
        dispatch_notices(self.platform,self.clock)
        dispatch_notices(self.platform,self.clock)
        with connect(self.platform.workspace_context().db_path) as db:
            jobs=list(db.execute("SELECT * FROM notification_jobs WHERE dedupe_key LIKE 'availability:%'"))
            self.assertEqual(len(jobs),1)
            self.assertIn('不可报名',jobs[0]['text'])
            self.assertNotIn('第二场',jobs[0]['text'])
            ids=json.loads(jobs[0]['channel_ids'])
            self.assertEqual(self.platform.channel(ids[0])['bot_id'],second.id)

    def test_calendar_shows_reservations_outside_daily_hours(self):
        self.block(date='2026-10-13',start='12:00',end='13:00')
        day=next(d for d in self.s.calendar('2026-10')['days'] if d['date']=='2026-10-13')
        self.assertEqual(day['occupancies'][0]['title'],'校队训练')
        self.assertTrue(all(s['booking_state']=='available' for s in day['sessions']))

    def test_same_group_bots_cover_distinct_sessions_without_duplicates(self):
        rules=self.s.slots()
        self.platform.update(self.admin,self.bots[0]['id'],{'slot_keys':[rules[0]['slot_key']]})
        self.platform.update(self.admin,self.bots[1]['id'],{'slot_keys':[rules[1]['slot_key']]})
        self.block(start='17:30',end='22:00')
        dispatch_notices(self.platform,self.clock)
        dispatch_notices(self.platform,self.clock)
        with connect(self.platform.workspace_context().db_path) as db:
            jobs=[dict(r) for r in db.execute("SELECT * FROM notification_jobs WHERE dedupe_key LIKE 'availability:%'")]
        self.assertEqual(len(jobs),2)
        self.assertEqual(sum('第一场' in job['text'] for job in jobs),1)
        self.assertEqual(sum('第二场' in job['text'] for job in jobs),1)

    def test_event_rescheduling_requires_consistent_reserved_time(self):
        events=Tournaments(self.ctx,self.clock)
        first={'date':'2026-10-19','start':'17:00','end':'21:00'}
        second={'date':'2026-10-20','start':'12:00','end':'16:00'}
        with patch('app.competitions._now',self.clock):
            event=events.create({'kind':'large','title':'改期杯','keyword':'改期杯','date':first['date'],
                'participants':[{'id':u,'user_id':u} for u in ('1001','1002')],
                'options':{'discipline':'singles','reservations':[first]}})
            events.transition(event['id'],'open_registration')
            events.transition(event['id'],'close_registration')
            event=events.transition(event['id'],'start')
            match=event['matches'][0]['id']
            current=events.update(event['id'],{'match_schedule':[{'match_id':match,'scheduled_at':'2026-10-19T18:00:00+08:00'}]})
            with self.assertRaisesRegex(ValueError,'请刷新'):
                events.update(event['id'],{'expected_version':event['version'],'options':{'reservations':[second]}})
            with self.assertRaisesRegex(ValueError,'不在新预留范围'):
                events.update(event['id'],{'expected_version':current['version'],'options':{'reservations':[second]}})
            self.assertEqual(events.get(event['id'])['options']['reservations'],[first])
            updated=events.update(event['id'],{'options':{'reservations':[second]},'match_schedule':[
                {'match_id':match,'scheduled_at':'2026-10-20T13:00:00+08:00'}]})
            self.assertEqual(updated['options']['reservations'],[second])
            self.assertEqual(self.s.sessions('2026-10-19')[0]['booking_state'],'available')

    def test_simultaneous_reservations_cannot_double_book(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        start=Barrier(4)
        def reserve(index):
            start.wait()
            try:
                return self.block(title=f'并发训练{index}')['id']
            except ValueError as error:
                self.assertIn('已由',str(error))
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(reserve,range(4)))
        self.assertEqual(sum(value is not None for value in results),1)

    def test_http_management_and_enrollment_share_occupancy_gate(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from tests.test_core import TOKEN
        app=create_app(self.temp.name,TOKEN,False,False,self.clock)
        with TestClient(app) as client:
            body={'venue_id':1,'date':'2026-10-13','start':'18:00','end':'20:00','title':'场馆维护'}
            self.assertIn(client.post('/api/occupancies',json=body).status_code,(401,403))
            client.headers['Authorization']='Bearer '+TOKEN
            response=client.post('/api/occupancies',json=body)
            self.assertEqual(response.status_code,200,response.text)
            block=response.json()
            self.assertEqual(block['affected_sessions'][0]['booking_state'],'occupied')
            signup={'user_id':'1001','name':'小李','date':'2026-10-13','venue_id':1}
            denied=client.post('/api/registrations',json=signup)
            self.assertEqual(denied.status_code,400,denied.text)
            self.assertIn('不可报名',denied.json()['detail'])
            self.assertEqual(client.post(f"/api/occupancies/{block['id']}/release",json={'expected_version':block['version']}).status_code,200)
            accepted=client.post('/api/registrations',json=signup)
            self.assertEqual(accepted.status_code,200,accepted.text)


class HolidayTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.now=datetime(2026,9,19,9,tzinfo=TZ)
        self.holidays=HolidaySchedule(self.platform.workspace_context(),self.clock)

    def test_official_holiday_workday_switch_and_date_override(self):
        self.assertEqual(self.s.sessions('2026-09-25')[0]['start'],'17:30')
        config=self.holidays.get(1)
        self.assertFalse(config['enabled'])
        self.holidays.save(1,{'enabled':True})
        holiday=self.s.sessions('2026-09-25')
        self.assertEqual(holiday[0]['start'],'18:00')
        self.assertEqual(holiday[0]['day_label'],'中秋节')
        self.assertEqual(self.s.sessions('2026-09-20')[0]['start'],'17:30')
        self.assertEqual(self.s.sessions('2026-09-19')[0]['start'],'18:00')
        self.s.save_slots(1,[{'name':'手动午场','start':'12:00','end':'14:00','capacity':6,'substitute_capacity':0}], '2026-09-25')
        self.assertEqual(self.s.sessions('2026-09-25')[0]['name'],'手动午场')
        self.assertEqual(day_kind('2027-01-01',self.holidays.get(1))[2],False)

    def test_template_edit_preserves_booking_snapshot_and_rollback_on_removal(self):
        self.holidays.save(1,{'enabled':True})
        self.s.enroll('1001','小李',day='2026-09-25')
        old=self.s.registrations('2026-09-25')['registrations'][0]['session_snapshot']
        config=self.holidays.get(1)
        config['slots'][0]['start']='17:00'
        self.holidays.save(1,{'slots':config['slots'],'expected_version':config['version']})
        data=self.s.registrations('2026-09-25')
        self.assertEqual(data['sessions'][0]['start'],'17:00')
        self.assertEqual(data['registrations'][0]['session_snapshot'],old)
        with self.assertRaisesRegex(ValueError,'报名历史'):
            self.holidays.save(1,{'slots':[]})
        self.assertEqual(self.holidays.get(1)['slots'][0]['start'],'17:00')

    def test_new_year_manual_date_and_scoped_holiday_bot(self):
        config=self.holidays.save(1,{'enabled':True,'overrides':[{'date':'2027-01-01','kind':'holiday','name':'新年活动'}]})
        self.platform.update(self.admin,self.ctx.id,{'slot_keys':[config['slots'][1]['slot_key']]})
        sessions=self.s.sessions('2027-01-01')
        self.assertEqual([s['number'] for s in sessions],[2])
        self.assertEqual(sessions[0]['day_label'],'新年活动')
        with self.assertRaises(PermissionError):
            HolidaySchedule(self.ctx,self.clock).save(1,{'enabled':False})
