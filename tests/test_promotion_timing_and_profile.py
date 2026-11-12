"""Session cutoffs and personal display preferences across trusted entry points."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import re

from fastapi.testclient import TestClient

from app.commands import Commands
from app.db import connect, initialize
from app.main import create_app
from app.members import Members
from app.messages import Outbox, registration_table, roster
from app.profile_changes import ProfileChanges
from app.registration import create_form, form_status, submit_form
from app.scheduler import Scheduler
from app.service import Service
from tests.test_core import CoreFixture, TOKEN
from tests import test_current_day_enrollment as queue_tests


class PromotionTimingTests(CoreFixture):
    actor = queue_tests.CurrentDayQueueTests.actor
    book = queue_tests.CurrentDayQueueTests.book
    rows = queue_tests.CurrentDayQueueTests.rows

    def setUp(self):
        super().setUp()
        self.all=Service(self.platform.workspace_context(),self.clock)
        slots=self.all.slots()
        slots[1].update(capacity=2,substitute_capacity=2)
        self.all.save_slots(1,slots)
        self.platform.update(self.admin,self.bots[0]['id'],{'slot_keys':[slots[0]['slot_key']],
            'allow_external_substitute':True,'external_slot_keys':[slots[1]['slot_key']]})
        self.platform.update(self.admin,self.bots[1]['id'],{'slot_keys':[slots[1]['slot_key']]})
        self.day='2026-10-13'
        self.target=self.all.sessions(self.day)[1]

    def test_enrollment_reads_and_early_broadcast_never_promote_before_cutoff(self):
        self.book('1001', guests=1)
        self.book('1002', True)
        self.now = (self.now+timedelta(days=1)).replace(hour=9, minute=29)
        scheduler = Scheduler(self.platform, clock=self.clock)
        for hour, minute in ((9,30), (18,0), (19,59)):
            self.now = self.now.replace(hour=hour, minute=minute)
            scheduler.tick()
            self.book('1001')  # Repeating enrollment must not open the queue.
            self.assertEqual([r['user_id'] for r in self.rows() if r['category']=='正式'], ['1002'])
        self.s.update_config({'daily_enabled':False})
        self.now = self.now.replace(hour=20, minute=0)
        scheduler.tick()
        self.assertEqual([r['user_id'] for r in self.rows() if r['category']=='正式'], ['1001','1002'])
        with connect(self.ctx.db_path) as db:
            before = db.execute("SELECT count(*) FROM audit WHERE action='registration.promote'").fetchone()[0]
        scheduler.tick()
        self.rows()
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='registration.promote'").fetchone()[0], before)

    def test_each_origin_uses_its_own_time_and_does_not_depend_on_reading_bot(self):
        slots = self.all.slots()
        self.platform.update(self.admin, self.bots[1]['id'], {'slot_keys':[slots[0]['slot_key']],
            'allow_external_substitute':True,'external_slot_keys':[slots[1]['slot_key']], 'external_promote_minutes':10})
        self.book('1001', True)
        self.book('1002')
        self.now = (self.now+timedelta(days=1)).replace(hour=20, minute=0)
        self.all.promote_today()
        self.assertEqual([r['user_id'] for r in self.rows() if r['category']=='正式'], ['1002'])
        self.now = self.now.replace(minute=20)
        self.all.promote_today()
        self.assertEqual([r['category'] for r in self.rows()], ['正式','正式'])
        # Post-admission config edits never demote admitted people.
        self.platform.update(self.admin, self.bots[0]['id'], {'external_auto_promote':False})
        self.assertEqual([r['category'] for r in self.rows()], ['正式','正式'])

    def test_default_and_validation_and_zero_minutes_persist(self):
        self.assertEqual(self.actor('1001').external_promote_minutes, 30)
        for bad in (-1,1441,True,2.5,'30',None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.platform.update(self.admin,self.bots[0]['id'],{'external_promote_minutes':bad})
        self.platform.update(self.admin,self.bots[0]['id'],{'external_promote_minutes':0})
        self.book('1001')
        self.now = (self.now+timedelta(days=1)).replace(hour=20,minute=29)
        self.assertEqual(self.rows()[0]['category'],'替补')
        self.now += timedelta(minutes=1)
        self.assertEqual(self.rows()[0]['category'],'正式')

    def test_different_sessions_use_their_own_start_and_midnight_cutoff(self):
        # Midday and near-midnight sessions share a booking robot but not a cutoff.
        slots=self.all.save_slots(1,[{'name':'午场','weekdays':list(range(7)),'start':'12:00','end':'13:00'},
            {'name':'夜场','weekdays':list(range(7)),'start':'00:10','end':'01:10'}])
        self.platform.update(self.admin,self.bots[0]['id'],{'slot_keys':[],
            'external_slot_keys':[s['slot_key'] for s in slots]})
        midnight=next(s for s in self.all.sessions(self.day) if s['start']=='00:10')
        noon=next(s for s in self.all.sessions(self.day) if s['start']=='12:00')
        booker=Service(self.actor('1001'),self.clock)
        for session in (midnight,noon):
            booker.enroll('1001','ignored',day=self.day,session_id=session['id'])
        self.s.update_config({'daily_enabled':False})
        self.now=self.now.replace(hour=23,minute=39)
        scheduler=Scheduler(self.platform,clock=self.clock)
        scheduler.tick()
        self.assertEqual(self.all.registrations(self.day,1,midnight['id'])['registrations'][0]['category'],'替补')
        self.now+=timedelta(minutes=1)
        scheduler.tick()
        self.assertEqual(self.all.registrations(self.day,1,midnight['id'])['registrations'][0]['category'],'正式')
        self.assertEqual(self.all.registrations(self.day,1,noon['id'])['registrations'][0]['category'],'替补')
        self.assertEqual(Outbox(self.ctx).list(),[])


class PersonalProfileTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'public_base_url':'https://example.test'})
        Members(self.ctx,self.clock).update_details('1001',gender='male')
        Members(self.ctx,self.clock).review_level('1001',True,5)

    def form(self, actor=None, purpose='profile'):
        ctx=actor or self.member
        url=create_form(ctx,self.clock,purpose=purpose)
        return url.split('#')[1],url.split('.')[-1]

    def test_default_hides_level_in_receipt_roster_but_preserves_rule_rating(self):
        data=Service(self.member,self.clock).enroll('1001','ignored')
        row=data['registrations'][0]
        self.assertFalse(row['show_level'])
        self.assertEqual(row['level'],5)
        self.assertNotIn('5级',registration_table([row]))
        self.assertNotIn('5级',roster(data,ctx=self.member))
        self.assertEqual(Members(self.member,self.clock).get()['level'],5)
        Members(self.member,self.clock).set_level_visibility(True)
        row=Service(self.member,self.clock).registrations()['registrations'][0]
        self.assertIn('小李（5级）',registration_table([row]))
        Members(self.member,self.clock).set_level_visibility(False)
        self.assertNotIn('5级',registration_table(Service(self.member,self.clock).registrations()['registrations']))

    def test_registration_checkbox_is_optional_and_never_approves_rating(self):
        for index,choice in enumerate((None,False,True)):
            actor=replace(self.member,actor='new-person-'+str(index))
            _,token=self.form(actor,purpose='registration')
            data={'name':'新会员'+str(index),'gender':'female','level':7}
            if choice is not None:data['show_level']=choice
            submit_form(actor,token,data,self.clock)
            record=Members(actor,self.clock).get()
            self.assertEqual(record['show_level'],bool(choice))
            self.assertIsNone(record['level'])
            self.assertEqual(record['requested_level'],7)
            self.assertEqual(record['status'],'pending')

    def test_preference_only_applies_without_review_with_identity_bound_receipt(self):
        reference,token=self.form()
        self.assertFalse(form_status(self.member,token,self.clock)['profile']['show_level'])
        app=create_app(self.platform.root,TOKEN,False,False,self.clock,env_file='-')
        with TestClient(app) as client:
            response=client.post('/public/registration/'+reference,json={'name':'小李','gender':'male','level':5,'group_ids':[],'show_level':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['request_id'])
        self.assertTrue(Members(self.member,self.clock).get()['show_level'])
        self.assertEqual(ProfileChanges(self.member,self.clock).list(),[])
        self.assertEqual(submit_form(self.member,token,{'show_level':False},self.clock),response.json())
        self.assertTrue(Members(self.member,self.clock).get()['show_level'])
        self.assertFalse(Members(replace(self.member,actor='1002'),self.clock).get()['show_level'])

    def test_profile_fields_still_wait_for_review_while_preference_changes_now(self):
        _,token=self.form()
        result=submit_form(self.member,token,{'name':'新姓名','level':8,'show_level':True},self.clock)
        before=Members(self.member,self.clock).get()
        self.assertEqual((before['name'],before['level'],before['show_level']),('小李',5,True))
        ProfileChanges(self.ctx,self.clock).review(result['request_id'],True)
        after=Members(self.member,self.clock).get()
        self.assertEqual((after['name'],after['level'],after['show_level']),('新姓名',8,True))

    def test_bad_checkbox_rolls_back_profile_and_unregistered_application(self):
        _,token=self.form()
        for bad in ('false',0,1,None,[]):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                submit_form(self.member,token,{'name':'错误修改','show_level':bad},self.clock)
        self.assertEqual(ProfileChanges(self.member,self.clock).list(),[])
        self.assertFalse(Members(self.member,self.clock).get()['show_level'])
        actor=replace(self.member,actor='pending-new')
        _,token=self.form(actor,purpose='registration')
        with self.assertRaises(ValueError):submit_form(actor,token,{'name':'测试','gender':'male','show_level':'false'},self.clock)
        self.assertIsNone(Members(actor,self.clock).get())

    def test_new_command_respects_capability_and_reuses_personal_edit_form(self):
        reply=asyncio.run(Commands(self.member,self.clock).run('**修改注册信息**'))
        self.assertIn('小李',reply)
        reference=re.search(r'/register#([^\s)]+)',reply)[1]
        status=form_status(self.member,reference.split('.')[1],self.clock)
        self.assertEqual(status['purpose'],'profile')
        self.assertEqual(status['profile']['name'],'小李')
        self.platform.update(self.admin,self.member.id,{'capabilities':['enrollment']})
        with self.assertRaises(PermissionError):asyncio.run(Commands(self.member,self.clock).run('修改注册信息'))

    def test_unregistered_and_pending_person_get_registration_link_without_approval(self):
        actor=replace(self.member,actor='brand-new')
        for _ in range(2):
            reply=asyncio.run(Commands(actor,self.clock).run('修改注册信息'))
            reference=re.search(r'/register#([^\s)]+)',reply)[1]
            token=reference.split('.')[1]
            self.assertNotEqual(form_status(actor,token,self.clock).get('purpose'),'profile')
            submit_form(actor,token,{'name':'新成员','gender':'male'},self.clock)
            self.assertEqual(Members(actor,self.clock).get()['status'],'pending')

    def test_migration_defaults_hidden_without_rewriting_existing_values(self):
        with connect(self.ctx.db_path,True) as db:
            db.execute('ALTER TABLE member_records DROP COLUMN show_level')
            columns=[row['name'] for row in db.execute('PRAGMA table_info(member_records)')]
            before=[tuple(row) for row in db.execute('SELECT * FROM member_records ORDER BY user_id')]
        initialize(self.ctx.db_path)
        with connect(self.ctx.db_path) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT '+','.join(columns)+' FROM member_records ORDER BY user_id')],before)
            self.assertEqual(db.execute('SELECT sum(show_level) FROM member_records').fetchone()[0],0)
