import copy
import json
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.db import TZ, connect, dumps
from app.service import Service
from app.holidays import HolidaySchedule
from app.holiday_calendar import bundled, validate, periods
from app.holiday_planning import HolidayPlanning
from app.activity_operations import ActivityOperations
from app.main import create_app
from app.tournaments import Tournaments
from tests.test_core import CoreFixture, TOKEN


class HolidayPlanningTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 9, 1, 9, tzinfo=TZ)
        self.workspace = self.platform.workspace_context()
        self.manager = HolidayPlanning(self.workspace, self.clock)
        self.ops = ActivityOperations(self.workspace, self.clock)
        self.service = Service(self.workspace, self.clock)

    def plan(self, name='国庆节', **changes):
        result = self.manager.get(1, 2026)
        row = next(r for r in result['periods'] if r['name'] == name)
        return row['id'], {k: copy.deepcopy(row[k]) for k in ('name','start_date','end_date','enabled','slots')} | {'revision': result['revision']} | changes

    def test_downloaded_2026_calendar_has_seven_holidays_and_six_makeup_days(self):
        data = bundled()[2026]
        self.assertEqual(len(periods(data)), 7)
        self.assertEqual(sum(not r['isOffDay'] for r in data['days']), 6)
        result = self.manager.get(1, 2026)
        self.assertEqual(len(result['periods']), 7)
        self.assertTrue(result['known'])
        self.assertFalse(self.manager.get(1, 2028)['known'])

    def test_weekly_skip_is_independent_per_rule_without_global_switch(self):
        rules = self.s.slots(); rules[0]['skip_holidays'] = True
        self.s.save_slots(1, rules)
        rows = self.s.sessions('2026-10-01')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['start'], '20:30')
        self.assertEqual(rows[0]['day_label'], '国庆节')
        self.assertEqual(len(self.s.sessions('2026-09-03')), 2)
        with self.assertRaisesRegex(ValueError, '布尔'):
            self.s.save_slots(1, [rules[0] | {'skip_holidays':'true'}])

    def test_individual_holiday_schedule_and_adjusted_range(self):
        key, data = self.plan(enabled=True)
        data['start_date'] = '2026-10-02'; data['end_date'] = '2026-10-08'
        data['slots'][0].update(name='国庆午间', start='12:00', end='14:00')
        self.manager.save(1, 2026, key, data)
        self.assertEqual(self.s.sessions('2026-10-02')[0]['name'], '国庆午间')
        self.assertEqual(self.s.sessions('2026-10-08')[0]['start'], '12:00')
        self.assertEqual(self.s.sessions('2026-10-01')[0]['start'], '17:30')
        self.assertEqual(self.s.sessions('2026-09-25')[0]['start'], '17:30')

    def test_date_arrangement_has_priority_and_can_restore(self):
        key, data = self.plan(enabled=True)
        self.manager.save(1, 2026, key, data)
        self.ops.save_date_plan(1, '2026-10-01', {'revision':'','slots':[{'name':'临时加场','start':'10:00','end':'11:00'}]})
        self.assertEqual(self.s.sessions('2026-10-01')[0]['name'], '临时加场')
        row = self.ops.date_plans(2026)['plans'][0]
        self.ops.save_date_plan(1, row['date'], {'revision':row['revision']}, remove=True)
        self.assertEqual(self.s.sessions('2026-10-01')[0]['start'], '18:00')

    def test_disabled_and_empty_holiday_have_distinct_meanings(self):
        key, data = self.plan(enabled=True, slots=[])
        self.manager.save(1, 2026, key, data)
        self.assertEqual(self.s.sessions('2026-10-01'), [])
        key, data = self.plan(enabled=False)
        self.manager.save(1, 2026, key, data)
        self.assertEqual(len(self.s.sessions('2026-10-01')), 2)

    def test_future_updates_keep_registration_snapshot_and_booking_id(self):
        key, data = self.plan(enabled=True)
        self.manager.save(1, 2026, key, data)
        self.s.enroll('1001', '小李', day='2026-10-01')
        before = self.s.registrations('2026-10-01')['registrations'][0]
        key, data = self.plan(); data['slots'][0]['start'] = '17:00'
        self.manager.save(1, 2026, key, data)
        after = self.s.registrations('2026-10-01')
        self.assertEqual(after['sessions'][0]['start'], '17:00')
        self.assertEqual(after['registrations'][0]['session_snapshot'], before['session_snapshot'])
        self.assertEqual(after['registrations'][0]['session_id'], before['session_id'])
        key, data = self.plan(slots=[])
        with self.assertRaisesRegex(ValueError,'报名历史'):
            self.manager.save(1, 2026, key, data)
        self.assertEqual(self.s.sessions('2026-10-01')[0]['start'],'17:00')

    def test_started_and_historical_sessions_preserve_original_times(self):
        key, data = self.plan(enabled=True); self.manager.save(1, 2026, key, data)
        self.s.sessions('2026-10-01')
        self.now = datetime(2026,10,1,19,tzinfo=TZ)
        key, data = self.plan(); data['slots'][0]['start']='17:00'
        self.manager.save(1, 2026, key, data)
        self.assertEqual(self.s.sessions('2026-10-01')[0]['start'],'18:00')
        self.assertEqual(self.s.sessions('2026-10-02')[0]['start'],'17:00')
        self.now = datetime(2026,10,8,9,tzinfo=TZ)
        key, data = self.plan()
        with self.assertRaisesRegex(ValueError,'只读'): self.manager.save(1,2026,key,data)

    def test_stale_revisions_scope_and_overlap_rejected(self):
        key, data = self.plan(enabled=True); self.manager.save(1,2026,key,data)
        with self.assertRaisesRegex(ValueError,'已变化'): self.manager.save(1,2026,key,data)
        key, data = self.plan(start_date='2026-09-26')
        with self.assertRaisesRegex(ValueError,'重叠'): self.manager.save(1,2026,key,data)
        with self.assertRaises(PermissionError): HolidayPlanning(self.member,self.clock).get(1,2026)
        self.platform.update(self.admin,self.ctx.id,{'slot_keys':[self.s.slots()[0]['slot_key']]})
        key, data = self.plan()
        with self.assertRaises(PermissionError): HolidayPlanning(self.ctx,self.clock).save(1,2026,key,data)
        with self.assertRaises(PermissionError): HolidayPlanning(self.ctx,self.clock).refresh(2026)

    def test_refresh_preserves_custom_configuration_and_failed_import_is_atomic(self):
        key, data = self.plan(enabled=True); data['slots'][0]['name']='自定训练'
        self.manager.save(1,2026,key,data)
        with patch('app.holiday_calendar.fetch',return_value=(bundled()[2026],'https://example.test/calendar')):
            self.manager.refresh(2026)
        self.assertEqual(self.s.sessions('2026-10-01')[0]['name'],'自定训练')
        before=self.manager.get(1,2026)
        with patch('app.holiday_calendar.fetch',side_effect=ValueError('网络故障')):
            with self.assertRaises(ValueError): self.manager.refresh(2026)
        self.assertEqual(self.manager.get(1,2026),before)

    def test_refresh_new_year_is_cached_across_service_restarts(self):
        payload={'year':2027,'papers':bundled()[2026]['papers'],'days':[{'date':'2027-01-01','name':'测试新年','isOffDay':True}]}
        with patch('app.holiday_calendar.fetch',return_value=(payload,'https://example.test/calendar')):
            self.manager.refresh(2027)
        with patch('app.holiday_calendar.fetch',side_effect=AssertionError('no network')):
            manager=HolidayPlanning(self.workspace,self.clock)
            self.assertTrue(manager.get(1,2027)['known'])
            self.assertEqual(self.s.sessions('2027-01-01')[0]['day_label'],'测试新年')

    def test_validation_rejects_wrong_year_duplicates_and_invalid_source(self):
        for change in ({'year':2027},{'days':bundled()[2026]['days']*2},{'papers':['http://internal/data']},{'days':[]}):
            with self.subTest(change=list(change)):
                with self.assertRaises(ValueError): validate(bundled()[2026]|change,2026)

    def test_nonrecurring_version_booking_and_history_protection(self):
        day='2026-09-02'
        self.ops.save_date_plan(1,day,{'revision':'','slots':[{'name':'加场','start':'12:00','end':'13:00'}]})
        record=self.ops.date_plans(2026)['plans'][0]
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.ops.save_date_plan(1,day,{'revision':'','slots':[]})
        self.s.enroll('1001','小李',day=day)
        with self.assertRaisesRegex(ValueError,'报名历史'):
            self.ops.save_date_plan(1,day,{'revision':record['revision']},remove=True)
        self.now=datetime(2026,9,3,9,tzinfo=TZ)
        self.assertFalse(self.ops.date_plans(2026)['plans'][0]['can_edit'])
        with self.assertRaisesRegex(ValueError,'只读'):
            self.ops.save_date_plan(1,day,{'revision':record['revision'],'slots':[]})

    def test_calendar_marks_holiday_without_sessions_and_events_across_days(self):
        key, data=self.plan(enabled=True,slots=[]);self.manager.save(1,2026,key,data)
        with patch('app.competitions._now',self.clock):
            event=Tournaments(self.workspace,self.clock).create({'kind':'large','title':'秋季杯','keyword':'秋季杯','date':'2026-10-03',
                'options':{'reservations':[{'date':'2026-10-04','start':'10:00','end':'18:00'}]}})
            Tournaments(self.workspace,self.clock).transition(event['id'],'open_registration')
        month=self.ops.month('2026-10');d=month['days'][0]
        self.assertTrue(d['is_holiday']);self.assertEqual(d['day_label'],'国庆节');self.assertEqual(d['session_count'],0)
        self.assertEqual(month['days'][2]['event_count'],1);self.assertEqual(month['days'][3]['events'][0]['title'],'秋季杯')
        self.assertTrue(month['days'][9]['is_workday']);self.assertTrue(month['days'][9]['is_weekend'])

    def test_calendar_enrollment_parts_and_multiple_venues(self):
        self.service.save_venue({'name':'二号场'})
        self.s.save_slots(1,[{'name':'小场','start':'17:00','end':'20:00','capacity':1,'substitute_capacity':1}])
        for user in ('1001','1002','1003'): self.s.enroll(user,'会员'+user,day='2026-09-02')
        day=self.ops.month('2026-09')['days'][1]
        self.assertEqual((day['formal_count'],day['substitute_count'],day['waiting_count']),(1,1,1))
        self.assertEqual(len(day['venues']),2);self.assertNotIn('user_id',str(day))

    def test_http_schedule_apis_and_permissions(self):
        with TestClient(create_app(self.temp.name,TOKEN,False,False,self.clock)) as client:
            self.assertIn(client.get('/api/holiday-plan?year=2026').status_code,(401,403))
            client.headers['Authorization']='Bearer '+TOKEN
            result=client.get('/api/holiday-plan?year=2026').json()
            self.assertEqual(len(result['periods']),7)
            key,data=self.plan(enabled=True)
            response=client.put(f'/api/holiday-plan/2026/{key}',json=data)
            self.assertEqual(response.status_code,200,response.text)
            response=client.put('/api/activity/date-plans/2026-09-02',json={'revision':'','slots':[]})
            self.assertEqual(response.status_code,200,response.text)
            row=response.json()['plans'][0]
            self.assertEqual(client.request('DELETE','/api/activity/date-plans/2026-09-02',json={'revision':row['revision']}).status_code,200)

    def test_bot_can_be_scoped_to_new_individual_holiday_slot(self):
        key, data=self.plan(enabled=True)
        data['slots'].append({'name':'专用晨场','start':'10:00','end':'12:00'})
        result=self.manager.save(1,2026,key,data)
        period=next(p for p in result['periods'] if p['id']==key)
        slot=next(s for s in period['slots'] if s['name']=='专用晨场')
        self.platform.update(self.admin,self.ctx.id,{'slot_keys':[slot['slot_key']]})
        rows=Service(self.ctx,self.clock).sessions('2026-10-01')
        self.assertEqual([r['name'] for r in rows],['专用晨场'])
        self.assertEqual(Service(self.ctx,self.clock).sessions('2026-09-02'),[])
