import asyncio
from dataclasses import replace
from datetime import timedelta
import re

from fastapi.testclient import TestClient
from app.commands import Commands
from app.db import connect
from app.enrollment_portal import EnrollmentPortal
from app.main import create_app
from app.members import Members
from tests.test_core import CoreFixture, TOKEN


class MemberPortalTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'public_base_url':'http://localhost'})
        self.portal=EnrollmentPortal(self.platform,self.clock)
        self.app=create_app(self.temp.name,TOKEN,start_scheduler=False,live=False,clock=self.clock,env_file='-')
        self.client=TestClient(self.app)
        self.addCleanup(self.client.close)
        self.token=None

    def session(self):
        token=self.portal.link(self.member).split('#')[1]
        response=self.client.post('/member/exchange',json={'token':token})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertIn('SameSite=strict',response.headers['set-cookie'])
        self.csrf=response.json()['csrf_token']
        self.token=self.client.cookies.get('gather_member')
        return self.token

    def post(self,path,data=None,**kwargs):
        return self.client.post('/member/'+path,json=data or {},headers={'X-CSRF-Token':self.csrf},**kwargs)

    def account(self):
        self.session()
        challenge=self.post('account/request').json()['command']
        reply=asyncio.run(Commands(self.member,self.clock).run(challenge))
        self.assertIn('身份已确认',reply)
        result=self.post('account',{'login':'member.one','password':'long-member-password'})
        self.assertEqual(result.status_code,200,result.text)

    def selection(self):
        response=self.client.get('/member/sessions',params={'day':self.now.date().isoformat()})
        self.assertEqual(response.status_code,200,response.text)
        row=response.json()['sessions'][0]
        return {'action':'enroll','day':self.now.date().isoformat(),'venue_id':row['venue_id'],
                'session_id':row['id'],'request_id':'a'*32}

    def test_bot_link_names_member_and_is_single_use(self):
        text=asyncio.run(Commands(self.member,self.clock).run('手动报名'))
        self.assertIn('小李',text);self.assertIn('/enroll#',text)
        self.session()
        self.assertEqual(self.client.get('/member/session').json()['name'],'小李')
        self.assertEqual(self.client.post('/member/exchange',json={'token':self.token}).status_code,403)

    def test_public_page_has_no_identity_fields_and_admin_cookie_cannot_authorize(self):
        self.assertEqual(self.client.get('/enroll').status_code,200)
        self.assertNotIn('name="user_id"',self.client.get('/enroll').text)
        self.assertEqual(self.client.get('/member/session').status_code,403)
        self.assertEqual(self.client.post('/member/enrollments',json={'user_id':'1001'}).status_code,403)

    def test_enroll_retries_do_not_append_guests_twice_and_cancel_is_owner_bound(self):
        self.session();data=self.selection()|{'guests':['同行甲','同行乙'],'guest_count':2}
        for _ in range(2):
            response=self.post('enrollments',data);self.assertEqual(response.status_code,200,response.text)
        rows=self.s.registrations()['registrations'];self.assertEqual(len(rows),3)
        response=self.post('enrollments',data|{'guests':['别的人'],'guest_count':1})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.post('enrollments',data|{'user_id':'1002'}).status_code,400)
        cancel=data|{'action':'cancel_guests','guests':['同行甲'],'request_id':'b'*32}
        for _ in range(2):self.assertEqual(self.post('enrollments',cancel).status_code,200)
        remaining=[r['name'] for r in self.s.registrations()['registrations'] if r['status']=='active']
        self.assertEqual(set(remaining),{'小李','同行乙'})

    def test_csrf_and_origin_are_required_for_writes(self):
        self.session();data=self.selection()
        self.assertEqual(self.client.post('/member/enrollments',json=data).status_code,403)
        response=self.client.post('/member/enrollments',json=data,headers={'X-CSRF-Token':self.csrf,'Origin':'https://elsewhere.invalid'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.s.registrations()['registrations'],[])

    def test_link_cannot_set_password_without_callback_and_other_member_cannot_confirm(self):
        self.session()
        challenge=self.post('account/request').json()['command']
        response=self.post('account',{'login':'hijack','password':'long-member-password'})
        self.assertEqual(response.status_code,403)
        with self.assertRaises(PermissionError):
            asyncio.run(Commands(replace(self.member,actor='1002'),self.clock).run(challenge))
        with self.assertRaises(PermissionError):
            asyncio.run(Commands(replace(self.member,source='simulator'),self.clock).run(challenge))
        self.assertFalse(self.client.get('/member/account/status').json()['confirmed'])

    def test_callback_confirmation_stays_bound_to_browser_and_expires(self):
        self.session();first_token=self.token
        code=self.post('account/request').json()['command'].split()[-1]
        self.portal.confirm_binding(self.member,code)
        second=self.portal.exchange(self.portal.link(self.member).split('#')[1])
        with self.assertRaises(PermissionError):
            self.portal.save_account(second['token'],second['csrf_token'],'member.two','long-member-password')
        self.now+=timedelta(minutes=11)
        with self.assertRaises(PermissionError):
            self.portal.save_account(first_token,self.csrf,'member.one','long-member-password')

    def test_password_and_phone_login_logout_and_revocation(self):
        self.account()
        with connect(self.ctx.db_path,True) as db:
            db.execute('UPDATE member_records SET phone=? WHERE user_id=?',('13900001234','1001'))
        self.assertEqual(self.post('logout').status_code,200)
        for login in ('MEMBER.ONE','13900001234'):
            response=self.client.post('/member/login',json={'login':login,'password':'long-member-password'})
            self.assertEqual(response.status_code,200,response.text);self.csrf=response.json()['csrf_token']
            self.assertNotIn('token',response.json())
            self.assertEqual(self.client.get('/member/session').json()['name'],'小李')
        Members(self.ctx,self.clock).review('1001','suspended')
        self.assertEqual(self.client.get('/member/session').status_code,403)

    def test_scope_revocation_and_disabled_capability_take_effect_for_open_page(self):
        self.session();data=self.selection()
        self.platform.update(self.admin,self.member.id,{'slot_keys':[]})
        self.assertEqual(self.post('enrollments',data).status_code,400)
        self.platform.update(self.admin,self.member.id,{'capabilities':['registration']})
        self.assertEqual(self.client.get('/member/session').status_code,403)

    def test_expired_member_sees_choices_but_cannot_make_new_booking(self):
        from app.membership_terms import MembershipTerms
        MembershipTerms(self.ctx,self.clock).create('1001',{'start_date':'2025-01-01','end_date':'2025-12-31'})
        self.session();data=self.selection()
        choices=self.client.get('/member/sessions',params={'day':data['day']}).json()['sessions']
        self.assertTrue(all(not r['can_enroll'] for r in choices))
        self.assertEqual(self.post('enrollments',data).status_code,403)

    def test_login_rate_limit_and_short_link_expiry(self):
        token=self.portal.link(self.member).split('#')[1]
        self.now+=timedelta(minutes=21)
        self.assertEqual(self.client.post('/member/exchange',json={'token':token}).status_code,403)
        for _ in range(7):
            result=self.client.post('/member/login',json={'login':'unknown','password':'bad'})
            self.assertEqual(result.status_code,403)
        self.assertIn('稍后',result.json()['detail'])

    def test_portal_only_exposes_own_entries_and_bot_selected_external_slots(self):
        slots=self.s.slots();self.platform.update(self.admin,self.member.id,{'slot_keys':[slots[0]['slot_key']],
            'allow_external_substitute':True,'external_slot_keys':[slots[1]['slot_key']]})
        self.s.enroll('1002','other',number=2)
        self.session()
        rows=self.client.get('/member/sessions',params={'day':self.now.date().isoformat()}).json()['sessions']
        self.assertEqual([r['external_substitute'] for r in rows],[False,True])
        self.assertTrue(all(r['own']==[] for r in rows))
        data=self.selection()|{'session_id':rows[1]['id']}
        self.assertEqual(self.post('enrollments',data).status_code,200)
        choices=self.client.get('/member/sessions',params={'day':data['day']}).json()['sessions']
        self.assertEqual(choices[1]['own'][0]['category'],'正式')

    def test_ongoing_session_accepts_enrollment_but_finished_session_does_not(self):
        self.now=self.now.replace(hour=18)
        self.session()
        data=self.selection()
        response=self.post('enrollments',data)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(sum(len(s['own']) for s in response.json()['sessions']),1)
        self.now=self.now.replace(hour=20,minute=30)
        self.session()
        data['request_id']='a'*32
        response=self.post('enrollments',data)
        self.assertEqual(response.status_code,400,response.text)

    def test_future_date_and_non_nearest_session_are_freely_selectable(self):
        self.session()
        day=(self.now.date()+timedelta(days=14)).isoformat()
        response=self.client.get('/member/sessions',params={'day':day})
        self.assertEqual(response.status_code,200,response.text)
        rows=response.json()['sessions'];self.assertGreaterEqual(len(rows),2)
        row=rows[-1]
        data={'action':'enroll','day':day,'venue_id':row['venue_id'],'session_id':row['id'],'request_id':'f'*32}
        response=self.post('enrollments',data)
        self.assertEqual(response.status_code,200,response.text)
        chosen=[r for r in response.json()['sessions'] if r['own']]
        self.assertEqual([r['id'] for r in chosen],[row['id']])
        self.assertEqual(self.s.registrations()['registrations'],[])

    def test_date_validation_precedes_any_booking_mutation(self):
        self.session();data=self.selection()
        for offset in (-1,367):
            day=(self.now.date()+timedelta(days=offset)).isoformat()
            self.assertEqual(self.post('enrollments',data|{'day':day}).status_code,400)
        self.assertEqual(self.s.registrations()['registrations'],[])
