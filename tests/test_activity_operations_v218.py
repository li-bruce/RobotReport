import copy
from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient
from app.activity_operations import ActivityOperations
from app.db import connect
from app.main import create_app
from app.service import Service
from app.availability import Availability
from tests.test_core import CoreFixture, TOKEN


class ActivityOperationsTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.workspace = self.platform.workspace_context()
        self.service = Service(self.workspace, self.clock)
        self.ops = ActivityOperations(self.workspace, self.clock)

    def payload(self):
        plan = self.ops.plan()
        return {k: plan[k] for k in ('revision', 'rules')}

    def test_same_rule_can_apply_to_multiple_venues_with_independent_stable_keys(self):
        second = self.service.save_venue({'name': '二号场'})['id']
        data = self.payload()
        self.assertEqual(len(data['rules']), 4)
        self.assertEqual(data['rules'][0]['venue_ids'], [1, second])
        keys = data['rules'][0]['bindings'].copy()
        data['rules'][0].update(name='午间场', start='12:00', end='13:00')
        self.ops.save_plan(data)
        for venue in (1, second):
            result = self.service.sessions('2026-10-13', venue)
            self.assertEqual(result[0]['name'], '午间场')
            self.assertEqual(result[0]['slot_key'], keys[str(venue)])
        self.assertEqual(self.ops.save_plan(self.payload())['changed_venue_ids'], [])

    def test_save_refreshes_previously_read_calendar_and_keeps_booking_snapshot(self):
        self.ops.month('2026-10')
        old = self.s.sessions('2026-10-13')[0]
        self.s.enroll('1001', '小李', day=old['date'], session_id=old['id'])
        data = self.payload(); data['rules'][0].update(name='新场', start='18:00')
        self.ops.save_plan(data)
        changed = self.ops.day(old['date'])['venues'][0]['sessions'][0]
        self.assertEqual((changed['id'], changed['start'], changed['count']), (old['id'], '18:00', 1))
        reg = self.s.registrations(old['date'])['registrations'][0]
        self.assertEqual(reg['session_snapshot']['start'], '17:30')
        new = {'name': '午场', 'weekdays': [0,1,2,3,4], 'start': '12:00', 'end': '13:00',
               'capacity': 8, 'substitute_capacity': 4, 'venue_ids': [1], 'bindings': {}}
        data = self.payload(); data['rules'].append(new); self.ops.save_plan(data)
        day = next(d for d in self.ops.month('2026-10')['days'] if d['date']==old['date'])
        self.assertEqual(day['session_count'], 3)
        self.assertEqual(day['registration_count'], 1)

    def test_atomic_rollback_when_second_venue_has_booked_slot_removal(self):
        second = self.service.save_venue({'name': '二号场'})['id']
        self.service.enroll('1001', '小李', day='2026-10-13', venue_id=second)
        before = self.payload()
        data = copy.deepcopy(before); data['rules'][1]['capacity']=24
        data['rules'][0]['venue_ids']=[1]
        with self.assertRaisesRegex(ValueError, '二号场.*报名历史'):
            self.ops.save_plan(data)
        self.assertEqual(self.payload(), before)

    def test_overlap_and_stale_revisions_leave_every_venue_unchanged(self):
        self.service.save_venue({'name':'二号'})
        before = self.payload(); data=copy.deepcopy(before); data['rules'][1]['start']='20:00'
        with self.assertRaisesRegex(ValueError, '重叠'): self.ops.save_plan(data)
        self.assertEqual(self.payload(), before)
        data=copy.deepcopy(before); data['rules'][0]['capacity']=20; self.ops.save_plan(data)
        with self.assertRaisesRegex(ValueError, '已变化'): self.ops.save_plan(before)

    def test_cannot_cancel_ended_registration_even_through_admin_api(self):
        self.s.enroll('1001','小李',1)
        record=self.s.registrations()['registrations'][0]
        self.assertTrue(record['can_cancel'])
        self.now=self.now.replace(hour=20, minute=30)
        with self.assertRaisesRegex(ValueError,'已结束'): self.s.cancel_person(record['id'])
        rows=self.s.registrations()['registrations']
        self.assertTrue(all(r['status']=='active' and not r['can_cancel'] for r in rows))
        self.now+=timedelta(days=1)
        with self.assertRaisesRegex(ValueError,'已结束'): self.s.cancel_person(record['id'])
        self.assertEqual(len(self.s.registrations('2026-10-12')['registrations']),2)

    def test_cancellation_rechecks_current_time_not_browser_flag(self):
        self.s.enroll('1001','小李')
        record=self.s.registrations()['registrations'][0]
        self.now=self.now.replace(hour=18)
        self.s.cancel_person(record['id'])
        result=self.s.registrations()['registrations'][0]
        self.assertEqual(result['status'],'cancelled'); self.assertFalse(result['can_cancel'])

    def test_day_groups_venues_and_excludes_roster_details_until_requested(self):
        self.service.save_venue({'name':'二号场'})
        self.s.enroll('1001','小李')
        result=self.ops.day('2026-10-12')
        self.assertEqual(len(result['venues']),2)
        self.assertEqual(result['session_count'],4)
        self.assertNotIn('user_id',str(result)); self.assertNotIn('小李',str(result))
        month=self.ops.month('2026-10'); day=month['days'][11]
        self.assertEqual((day['venue_count'],day['session_count'],day['registration_count']),(2,4,1))
        self.assertNotIn('sessions',day)

    def test_large_day_has_all_sessions_but_month_payload_stays_compact(self):
        rules=[]
        for i in range(40):
            rules.append({'name':f'半小时场{i+1}','start':f'{i//2:02d}:{i%2*30:02d}',
                          'end':f'{(i+1)//2:02d}:{(i+1)%2*30:02d}','weekdays':list(range(7))})
        self.service.save_slots(1,rules)
        day=self.ops.day('2026-10-13'); self.assertEqual(day['session_count'],40)
        month=self.ops.month('2026-11')
        self.assertEqual(month['days'][0]['session_count'],40)
        # Month cells now include enrollment segments and per-venue summary counts,
        # still independent of the number of sessions/roster rows.
        self.assertLess(len(str(month)),18000)

    def test_occupancy_and_inactive_venue_are_visible_but_not_bookable(self):
        Availability(self.workspace,self.clock).save({'venue_id':1,'date':'2026-10-13','start':'17:30','end':'19:00','title':'训练'})
        first=self.ops.day('2026-10-13')['venues'][0]['sessions'][0]
        self.assertFalse(first['can_enroll']);self.assertTrue(first['can_manage'])
        self.service.save_venue({'active':False},1)
        first=self.ops.day('2026-10-13')['venues'][0]['sessions'][0]
        self.assertFalse(first['can_enroll'])
        self.assertEqual(self.ops.day('2026-10-14')['session_count'],0)

    def test_started_sessions_and_past_days_remain_original(self):
        old=self.s.sessions()[0];self.now=self.now.replace(hour=18)
        data=self.payload();data['rules'][0]['start']='18:30';self.ops.save_plan(data)
        self.assertEqual(self.ops.day(old['date'])['venues'][0]['sessions'][0]['start'],'17:30')
        self.now+=timedelta(days=1)
        self.assertEqual(self.ops.day(old['date'])['venues'][0]['sessions'][0]['start'],'17:30')

    def test_scope_and_invalid_bindings_cannot_modify_other_venues(self):
        second=self.service.save_venue({'name':'二号'})['id']
        self.platform.update(self.admin,self.ctx.id,{'venue_ids':[1]})
        scoped=ActivityOperations(self.ctx,self.clock)
        self.assertEqual(len(scoped.day('2026-10-13')['venues']),1)
        data={k:scoped.plan()[k] for k in ('revision','rules')};data['rules'][0]['venue_ids'].append(second)
        with self.assertRaises(PermissionError):scoped.save_plan(data)
        data=self.payload();data['rules'][0]['bindings']['1']='slot:999999'
        with self.assertRaisesRegex(ValueError,'标识'):self.ops.save_plan(data)
        with self.assertRaises(PermissionError):ActivityOperations(self.member,self.clock).plan()

    def test_http_calendar_plan_and_history_delete(self):
        self.s.enroll('1001','小李');record=self.s.registrations()['registrations'][0]
        app=create_app(self.temp.name,TOKEN,clock=self.clock,start_scheduler=False)
        with TestClient(app) as client:
            client.headers['Authorization']='Bearer '+TOKEN
            self.assertEqual(client.get('/api/activity/calendar?month=2026-10').status_code,200)
            self.assertEqual(client.get('/api/activity/day?date=2026-10-13').json()['session_count'],2)
            plan=client.get('/api/activity/slot-plan').json()
            self.assertEqual(client.put('/api/activity/slot-plan',json={k:plan[k] for k in ('revision','rules')}).status_code,200)
            self.now+=timedelta(days=1)
            response=client.delete(f"/api/registrations/{record['id']}")
            self.assertEqual(response.status_code,400);self.assertIn('已结束',response.text)
