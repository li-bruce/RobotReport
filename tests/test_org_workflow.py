import tempfile
from pathlib import Path
from datetime import datetime
from dataclasses import replace
import asyncio
import unittest
from fastapi.testclient import TestClient
from app.db import TZ,connect
from app.main import create_app
from app.members import Members
from app.people import People
from app.commands import Commands
from app.service import Service
from app.messages import Outbox
from app.notifications import Notifications
from app.registration import create_form,submit_form,confirm_form
from app.scheduler import Scheduler


class OrganizationWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.clock=lambda:datetime(2026,10,19,9,29,59,tzinfo=TZ)
        self.token='organization-test-token-not-real-0000'
        self.app=create_app(Path(self.tmp.name),self.token,start_scheduler=False,live=False,clock=self.clock)
        self.p=self.app.state.platform;self.user=self.p.authenticate(self.token)
        self.admin=self.p.context(self.user)
        self.client=TestClient(self.app);self.headers={'Authorization':'Bearer '+self.token}
        self.a=self.p.create(self.user,{'name':'活动助手'})
        self.b=self.p.create(self.user,{'name':'赛事助手'})
        self.ca=self.p.save_channel(self.user,self.a['id'],{'name':'活动群','chat_id':'10','robot_key':'a','webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=org-a'})
        self.c=self.p.create(self.user,{'name':'第二活动助手'})
        self.cb=self.p.save_channel(self.user,self.c['id'],{'name':'第二群','chat_id':'20','robot_key':'b','webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=org-b'})
        self.cc=self.p.save_channel(self.user,self.b['id'],{'name':'赛事群','chat_id':'30','robot_key':'c','webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=org-c'})
    def tearDown(self):
        self.client.close();self.tmp.cleanup()
    def actor(self,channel,user='u1'):
        return self.p.context_for_channel(channel['id'],user,False,'callback')
    def run_command(self,ctx,command):
        return asyncio.run(Commands(ctx,self.clock).run(command))
    def approved(self,user,name):
        ctx=self.actor(self.ca,user)
        self.run_command(ctx,'注册 '+name+' 男')
        Members(self.admin,self.clock).review(user,'approved')
        return ctx
    def test_shared_members_registration_and_guest_distinctions(self):
        a=self.actor(self.ca)
        self.assertIn('尚未注册',self.run_command(a,'帮助'))
        self.assertEqual(Members(self.admin).get('u1')['status'],'unregistered')
        self.assertEqual(People(self.admin).applications(),[])
        self.run_command(a,'注册 小林 男 2级')
        self.assertEqual(len(People(self.admin).applications()),1)
        self.assertEqual(Members(self.admin).get('u1')['status'],'pending')
        Members(self.admin).review('u1','approved')
        self.assertEqual(People(self.admin).applications()[0]['status'],'approved')
        self.run_command(a,'报名，同行甲')
        b=self.actor(self.cc)
        self.assertIn('小林',self.run_command(b,'名单'))
        before=Service(self.admin,self.clock).registrations()['registrations']
        with self.assertRaisesRegex(ValueError,'请勿重复代报'):
            self.run_command(b,'报名，同行甲')
        self.assertEqual(Service(self.admin,self.clock).registrations()['registrations'],before)
        self.run_command(b,'报名')
        rows=Service(self.admin,self.clock).registrations()['registrations']
        self.assertEqual(len(rows),2)
        guests=People(self.admin).guests()
        self.assertEqual(guests[0]['name'],'同行甲');self.assertFalse(guests[0]['member'])
        self.assertEqual(len(Members(self.admin).list()),1)
    def test_roles_independent_and_suspension_revokes(self):
        ctx=self.approved('u1','小林')
        self.assertFalse(Members(self.admin).get('u1')['roles'])
        People(self.admin).set_roles('u1',['club_admin'])
        token=self.a['callback_path'].split('/')[-1]
        callback=self.p.callback_context(self.a['id'],token,{'chatid':'10','creator':'u1','robot_key':'a'})
        self.assertTrue(callback.is_admin)
        Members(self.admin).review('u1','suspended')
        self.assertFalse(self.p.callback_context(self.a['id'],token,{'chatid':'10','creator':'u1','robot_key':'a'}).is_admin)
        self.assertFalse(Members(self.admin).get('u1')['roles'])
        with self.assertRaises(PermissionError):self.run_command(ctx,'注册 小林 男')
    def test_forms_submit_for_original_account_without_chat_confirmation(self):
        Service(self.admin).update_config({'public_base_url':'http://127.0.0.1:9888'})
        ctx=self.actor(self.ca)
        token=create_form(ctx,self.clock).split('.')[-1]
        result=submit_form(ctx,token,{'name':'小林','gender':'male'},self.clock)
        self.assertEqual(result['status'],'completed')
        self.assertNotIn('command',result)
        self.assertEqual(Members(self.admin).get('u1')['name'],'小林')
        again=submit_form(ctx,token,{'name':'小林','gender':'male'},self.clock)
        self.assertEqual(again,result)
    def test_notification_fanout_dedup_and_channel_reply(self):
        notices=Notifications(self.p,self.clock)
        first=notices.enqueue('测试通知',[self.ca['id'],self.cb['id'],self.cc['id']],key='a')
        self.assertEqual(len(first['deliveries']),3)
        again=notices.enqueue('测试通知',[self.ca['id'],self.cb['id'],self.cc['id']],key='a')
        self.assertEqual(first['id'],again['id'])
        self.assertEqual(len(Outbox(self.actor(self.ca)).list()),1)
        self.assertEqual(len(Outbox(self.actor(self.cb)).list()),1)
        token=self.c['callback_path'].split('/')[-1]
        response=self.client.post('/kingsoft/callback/'+self.c['id']+'/'+token,json={'chatid':'20','creator':'new-user','robot_key':'b','ctime':'100','content':'帮助'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(Outbox(self.actor(self.ca)).list()),1)
        self.assertEqual(len(Outbox(self.actor(self.cb)).list()),2)
        with connect(self.actor(self.cb).transport_db_path) as db:
            self.assertEqual(db.execute('SELECT channel_id FROM inbox').fetchone()[0],self.cb['id'])
    def test_admin_registration_application_and_unregistered_profile_edit(self):
        self.run_command(self.actor(self.ca,'new'),'帮助')
        response=self.client.patch('/api/members/new',headers=self.headers,json={'name':'待登记','status':'unregistered','phone':'13800000000'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'unregistered')
        self.assertFalse(People(self.admin).applications())
        response=self.client.post('/api/members',headers=self.headers,json={'user_id':'new','name':'新球友','requested_level':2})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'pending')
        self.assertEqual(People(self.admin).applications()[0]['name'],'新球友')
        self.client.patch('/api/members/new',headers=self.headers,json={'status':'approved'})
        response=self.client.patch('/api/members/new',headers=self.headers,json={'name':'新球友甲'})
        self.assertEqual(response.json()['status'],'approved')
        response=self.client.patch('/api/members/new',headers=self.headers,json={'status':'unregistered'})
        self.assertEqual(response.status_code,400)
        self.assertFalse(Members(self.admin).get('new')['roles'])
        reply=self.run_command(self.actor(self.cc,'new'),'注册 新球友甲 女')
        self.assertIn('会员资格已通过',reply)
    def test_global_api_and_explicit_destinations(self):
        self.assertEqual(self.client.get('/api/workspace',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/venues',headers=self.headers).status_code,200)
        response=self.client.post('/api/messages/send',headers=self.headers,json={'text':'hello'})
        self.assertEqual(response.status_code,400)
        response=self.client.post('/api/messages/send',headers=self.headers,json={'text':'hello','channel_ids':[self.ca['id'],self.cc['id']]})
        self.assertEqual(response.status_code,200,response.text)
        rows=self.client.get('/api/messages',headers=self.headers).json()
        self.assertEqual(len(rows),2);self.assertNotEqual(rows[0]['id'],rows[1]['id'])
        self.assertEqual(self.client.delete('/api/messages/'+rows[0]['id'],headers=self.headers).status_code,200)
    def test_shared_scheduler_reminder_per_channel_once(self):
        scheduler=Scheduler(self.p,live=False,clock=self.clock)
        at=datetime(2026,10,19,9,30,1,tzinfo=TZ)
        scheduler.tick(at);scheduler.tick(at)
        for channel in (self.ca,self.cb,self.cc):
            self.assertEqual(len(Outbox(self.actor(channel)).list()),1)
