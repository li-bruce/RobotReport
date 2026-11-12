"""Current-day admission, permission-aware routing and actual outbound payloads."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

from app.commands import Commands
from app.db import connect
from app.dialogue import MembershipPrompt
from app.members import Members
from app.messages import Outbox
from app.scheduler import Scheduler
from app.service import Service
from tests.test_core import CoreFixture


class CurrentDayQueueTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.all = Service(self.platform.workspace_context(), self.clock)
        slots = self.all.slots()
        slots[1].update(capacity=2, substitute_capacity=2)
        self.all.save_slots(1, slots)
        self.platform.update(self.admin, self.bots[0]['id'], {'slot_keys': [slots[0]['slot_key']],
            'allow_external_substitute': True, 'external_slot_keys': [slots[1]['slot_key']]})
        self.platform.update(self.admin, self.bots[1]['id'], {'slot_keys': [slots[1]['slot_key']]})
        self.day = '2026-10-13'
        self.target = self.all.sessions(self.day)[1]

    def actor(self, user, primary=False):
        return self.platform.context_for_channel(self.bots[int(primary)]['channels'][0]['id'], user, False, 'callback')

    def book(self, user, primary=False, **kwargs):
        return Service(self.actor(user,primary),self.clock).enroll(user,'ignored',day=self.day,number=2,**kwargs)

    def rows(self):
        return [row for row in self.all.registrations(self.day,1,self.target['id'])['registrations'] if row['status']=='active']

    def test_cutoff_fills_vacancies_preserving_existing_formal_and_future_origin(self):
        self.book('1001',guests=1)
        self.book('1002',True)
        self.assertEqual([r['user_id'] for r in self.rows() if r['category']=='正式'], ['1002'])
        self.all.update_config({'daily_enabled':False})
        scheduler=Scheduler(self.platform,clock=self.clock)
        self.now=self.now.replace(hour=0,minute=1)+timedelta(days=1)
        scheduler.tick()
        self.assertEqual([r['user_id'] for r in self.rows() if r['category']=='正式'], ['1002'])
        self.now=self.now.replace(hour=20,minute=0)
        scheduler.tick()
        with connect(self.ctx.db_path) as db:
            promoted=db.execute('SELECT user_id,guest_index,substitute_only,formal_admitted_at FROM registrations WHERE session_id=? ORDER BY queue_seq', (self.target['id'],)).fetchall()
            self.assertTrue(promoted[0]['formal_admitted_at'])
            self.assertEqual(promoted[0]['substitute_only'],1)
            self.assertFalse(promoted[1]['formal_admitted_at'])
        self.assertEqual({r['user_id'] for r in self.rows() if r['category']=='正式'}, {'1001','1002'})
        scheduler.tick()
        self.assertEqual(Outbox(self.ctx).list(),[])

    def test_future_day_stays_reserve_and_past_queue_is_not_rewritten(self):
        self.book('1001',guests=1)
        self.assertEqual([r['category'] for r in self.rows()], ['替补','替补'])
        self.now=(self.now+timedelta(days=1)).replace(hour=20,minute=0)
        self.assertEqual([r['category'] for r in self.rows()], ['正式','正式'])
        with connect(self.ctx.db_path) as db:
            before=[tuple(r) for r in db.execute('SELECT * FROM registrations ORDER BY id')]
        self.now=(self.now+timedelta(days=1)).replace(hour=20,minute=0)
        self.assertEqual([r['category'] for r in self.rows()], ['正式','正式'])
        self.all.promote_today()
        with connect(self.ctx.db_path) as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM registrations ORDER BY id')],before)

    def test_cancel_rejoin_and_capacity_changes_keep_oldest_reserve_ahead(self):
        self.book('1001',guests=2)
        self.book('1002',True)
        self.now=(self.now+timedelta(days=1)).replace(hour=20,minute=0)
        self.rows()
        Service(self.actor('1001'),self.clock).cancel('1001',day=self.day,number=2)
        self.book('1001')
        self.book('1003',True)
        rows=self.rows()
        self.assertEqual([(r['user_id'],r['guest_index']) for r in rows if r['category']=='正式'], [('1001',1),('1002',0)])
        self.all.save_session_constraints(self.target['id'], {'capacity':3})
        self.assertEqual([(r['user_id'],r['guest_index']) for r in self.rows() if r['category']=='正式'], [('1001',1),('1001',2),('1002',0)])
        self.all.save_session_constraints(self.target['id'], {'capacity':2})
        self.assertEqual(sum(r['category']=='正式' for r in self.rows()),2)
        self.all.save_session_constraints(self.target['id'], {'capacity':5})
        self.assertEqual(sum(r['category']=='正式' for r in self.rows()),5)

    def test_concurrent_bots_share_admission_limit_and_duplicate_receipts(self):
        self.now=(self.now+timedelta(days=1)).replace(hour=20,minute=0)
        users=['1','2','3','4','5','6','9','1001','1002','1003','1004']
        def book(i):
            return self.book(users[i%len(users)],bool(i%2))
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(book,range(22)))
        rows=self.rows()
        self.assertEqual(len(rows),len(users))
        self.assertEqual([r['category'] for r in rows[:4]], ['正式','正式','替补','替补'])
        self.assertEqual([r['queue_seq'] for r in rows],sorted(r['queue_seq'] for r in rows))

    def test_primary_arrival_cannot_take_an_unprocessed_vacancy_from_older_reserve(self):
        self.book('1001',guests=1)
        self.book('1002',True)
        self.now=(self.now+timedelta(days=1)).replace(hour=20,minute=0)
        self.rows()
        with connect(self.ctx.db_path,True) as db:
            db.execute("UPDATE registrations SET status='cancelled' WHERE user_id='1002'")
        self.book('1003',True)
        self.assertEqual([(r['user_id'],r['guest_index']) for r in self.rows() if r['category']=='正式'], [('1001',0),('1001',1)])


class EnrollmentReplyTests(CoreFixture):
    def restrict_first(self):
        self.platform.update(self.admin,self.ctx.id, {'user_policy':{'enabled':True,'match':'all',
            'rules':[{'field':'level','op':'gte','value':9}]}})

    def send(self, text, **kwargs):
        return asyncio.run(Commands(self.member,self.clock).run(text,**kwargs))

    def suggested(self, text):
        with self.assertRaises(MembershipPrompt) as error:
            self.send(text)
        return str(error.exception)

    def test_wrong_audience_recommends_eligible_bot_without_enrolling(self):
        self.restrict_first()
        reply=self.suggested('第二场 报名，张三')
        self.assertIn('@二号 第二场 报名，张三',reply)
        self.assertNotIn('使用条件',reply)
        self.assertNotIn('robot-test',reply)
        self.assertEqual(self.s.registrations()['registrations'],[])
        with self.assertRaises(PermissionError):
            self.send('第二场 报名',expected={'permission_check':'must_still_fail'})

    def test_recommendation_honors_target_scope_availability_and_capability(self):
        self.restrict_first()
        self.platform.update(self.admin,self.bots[1]['id'],{'slot_keys':[self.s.slots()[0]['slot_key']]})
        with self.assertRaises(PermissionError): self.send('第二场 报名')
        self.assertIn('@二号',self.suggested('报名'))
        self.platform.update(self.admin,self.bots[1]['id'],{'capabilities':['registration']})
        with self.assertRaises(PermissionError): self.send('报名')

    def test_no_recommendation_for_ineligible_member_or_unknown_private_group(self):
        self.restrict_first()
        Members(self.ctx,self.clock).review('1001','suspended')
        with self.assertRaises(PermissionError): self.send('报名')
        Members(self.ctx,self.clock).review('1001','approved')
        self.platform.update(self.admin,self.bots[1]['id'],{'active':False})
        self.platform.create(self.admin,{'name':'未加入的群机器人','chat_id':'private-group'})
        with self.assertRaises(PermissionError): self.send('报名')

    def test_success_payload_places_total_and_free_seats_after_image(self):
        self.s.update_config({'public_base_url':'https://example.test'})
        self.s.enroll('1002','ignored')
        reply=self.send('报名，张三')
        outbox=Outbox(self.ctx)
        outbox.enqueue(reply,'success-counts')
        payload=outbox.list()[0]['payload']['markdown']['text']
        self.assertIn('已报名 **3 人**',payload)
        self.assertIn('正式空位 **5 个**',payload)
        self.assertLess(payload.index('/public/tables/'),payload.index('已报名 **3 人**'))
        self.assertNotIn('| 姓名',payload)

    def test_candidate_closed_window_and_expired_membership_are_not_recommended(self):
        self.restrict_first()
        self.platform.update(self.admin,self.bots[1]['id'],{'capability_rules':[
            {'id':'later','name':'晚间开放','enabled':True,'capabilities':['enrollment'],'action':'open',
             'when':{'start_time':'18:00','end_time':'20:00'}}]})
        with self.assertRaisesRegex(PermissionError,'使用条件'): self.send('报名')
        self.now=self.now.replace(hour=18)
        self.assertIn('@二号',self.suggested('报名'))
        from app.membership_terms import MembershipTerms
        MembershipTerms(self.ctx,self.clock).create('1001',{'start_date':'2025-01-01','end_date':'2025-12-31'})
        with self.assertRaisesRegex(PermissionError,'使用条件'): self.send('报名')

    def test_authenticated_callback_sends_only_recommendation_and_deduplicates(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from tests.test_core import TOKEN
        self.restrict_first()
        body={'robot_key':'robot-test','chatid':266382,'creator':1001,'ctime':127856,'content':'报名'}
        with TestClient(create_app(self.temp.name,TOKEN,False,False,self.clock,env_file='-')) as client:
            for _ in range(2):
                self.assertEqual(client.post(self.bots[0]['callback_path'],json=body).json(),{'result':'ok'})
        messages=Outbox(self.ctx).list()
        self.assertEqual(len(messages),1)
        self.assertIn('@二号',messages[0]['text'])
        self.assertNotIn('这次没能完成',messages[0]['text'])
        self.assertEqual(self.s.registrations()['registrations'],[])

    def test_callback_bare_enrollment_does_not_invent_an_explicit_default_venue(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from tests.test_core import TOKEN
        service=Service(self.platform.workspace_context(),self.clock)
        venue=service.save_venue({'name':'午间场地'})
        slots=service.save_slots(venue['id'],[{'name':'近场','weekdays':[0],'start':'16:30','end':'17:30'}])
        self.platform.update(self.admin,self.ctx.id,{'venue_ids':[1],'allow_external_substitute':True,
            'external_slot_keys':[slots[0]['slot_key']]})
        body={'robot_key':'robot-test','chatid':266382,'creator':1001,'ctime':127857,'content':'报名'}
        with TestClient(create_app(self.temp.name,TOKEN,False,False,self.clock,env_file='-')) as client:
            self.assertEqual(client.post(self.bots[0]['callback_path'],json=body).json(),{'result':'ok'})
        self.assertIn('午间场地',Outbox(self.ctx).list()[0]['text'])
        self.assertEqual(service.registrations()['registrations'],[])
        rows=service.registrations(venue_id=venue['id'])['registrations']
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['substitute_only'])
        self.assertEqual(rows[0]['category'],'正式')
