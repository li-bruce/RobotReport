from dataclasses import replace
from datetime import datetime
from pathlib import Path
import json
import tempfile
import unittest

from fastapi.testclient import TestClient
from app.db import TZ, connect, stamp
from app.main import create_app
from app.members import Members
from app.member_imports import MemberImports, ensure_schema
from app.registration import create_form, form_status, lookup_form, submit_form
from app.registration_invites import RegistrationInvites
from app.service import Service


class RegistrationConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock=lambda:datetime(2026,10,20,12,0,tzinfo=TZ)
        self.token='registration-confirmation-isolated-test-token'
        self.app=create_app(Path(self.tmp.name),self.token,start_scheduler=False,live=False,clock=self.clock)
        self.client=TestClient(self.app)
        self.addCleanup(self.client.close)
        self.platform=self.app.state.platform
        self.principal=self.platform.authenticate(self.token)
        self.admin=self.platform.workspace_context()
        self.headers={'Authorization':'Bearer '+self.token}
        self.bot=self.platform.create(self.principal,{'name':'注册助手','capabilities':['registration']})
        self.channel=self.platform.save_channel(self.principal,self.bot['id'],
            {'name':'会员群','chat_id':'confirmation-group','webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=isolated-test'})
        self.owner=self.platform.context_for_channel(self.channel['id'],'owner',False,'callback')
        self.imports=MemberImports(self.admin,self.clock)
        self.members=Members(self.admin,self.clock)
        Service(self.admin,self.clock).update_config({'public_base_url':'http://127.0.0.1:9874'})

    def seed(self,name='张三',phone=None,gender='unknown',level=None):
        with connect(self.admin.db_path,True) as db:
            ensure_schema(db)
            return db.execute('''INSERT INTO pre_registered_members(scope_id,name,phone,gender,level,created_by,created_at,review_required)
                VALUES(?,?,?,?,?,?,?,1)''',(self.admin.scope_id,name,phone,gender,level,'admin',stamp(self.clock()))).lastrowid

    def form(self,name=None):
        reference=create_form(self.owner,self.clock,hint_name=name).split('#')[1]
        return reference,reference.split('.')[1]

    def test_unique_import_name_is_already_approved_even_without_optional_labels(self):
        self.seed()
        ref,token=self.form('张三')
        status=form_status(self.owner,token,self.clock)
        self.assertEqual(status['profile']['name'],'张三')
        self.assertFalse(status['import_hint']['phone_required'])
        result=submit_form(self.owner,token,{'name':'张三','gender':'female'},self.clock)
        self.assertEqual((result['registration_result'],result['member_status']),('linked','approved'))
        self.assertIsNone(result['level'])
        self.assertEqual(self.imports.list()[0]['linked_user_id'],'owner')
        self.assertEqual(Members(self.owner).get()['roles'],[])
        self.assertEqual(self.client.post('/public/registration/'+ref,json={'name':'改名','gender':'male'}).json(),result)
        with connect(self.admin.db_path) as db:
            source=db.execute("SELECT channel_id FROM identity_request_sources WHERE user_id='owner' AND kind='membership'").fetchone()
            self.assertEqual(source[0],self.channel['id'])

    def test_unmatched_name_needs_review_and_self_level_remains_pending(self):
        _,token=self.form()
        result=submit_form(self.owner,token,{'name':'新人','gender':'male'},self.clock)
        self.assertEqual(result['member_status'],'pending')
        self.seed('李四')
        other=replace(self.owner,actor='second')
        token2=create_form(other,self.clock).split('.')[-1]
        linked=submit_form(other,token2,{'name':'李四','gender':'male','level':7},self.clock)
        self.assertEqual(linked['member_status'],'approved')
        record=Members(other).get()
        self.assertEqual((record['level'],record['requested_level'],record['level_status']),(None,7,'pending'))

    def test_name_lookup_does_not_expose_phone_or_bind_before_submit(self):
        self.seed(phone='13800000001',gender='male')
        ref,token=self.form()
        response=self.client.post('/public/registration/'+ref+'/lookup',json={'name':'张三'})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('13800000001',response.text)
        self.assertEqual(response.json()['profile']['gender'],'male')
        self.assertIsNone(self.imports.list()[0]['linked_user_id'])
        result=submit_form(self.owner,token,{'name':'张三','gender':'male','user_id':'attacker'},self.clock)
        self.assertEqual(result['member_status'],'approved')
        self.assertEqual(Members(self.owner).get()['phone'],'13800000001')
        self.assertEqual(self.imports.list()[0]['linked_user_id'],'owner')
        with self.assertRaises(ValueError):lookup_form(self.owner,'wrong-token',{'name':'张三'},self.clock)

    def test_duplicate_names_require_disambiguation_and_bound_records_never_transfer(self):
        self.seed(phone='13800000001')
        self.seed(phone='13800000002')
        with self.assertRaisesRegex(ValueError,'重名'):
            Members(self.owner,self.clock).apply('张三',gender='male')
        member=Members(self.owner,self.clock).apply('张三',phone='13800000002',gender='male')
        self.assertEqual(member['status'],'approved')
        with self.assertRaisesRegex(PermissionError,'其他账号'):
            Members(replace(self.owner,actor='other'),self.clock).apply('张三',phone='13800000002',gender='male')
        self.assertEqual(len(self.members.list(status='approved')),1)

    def test_admin_completing_matching_phone_links_existing_without_overwriting(self):
        old=replace(self.owner,actor='existing')
        Members(old,self.clock).apply('张三',phone='13800000001',gender='female')
        self.members.review('existing','approved')
        group=self.members.save_group('提高组')
        self.members.set_groups('existing',[group['id']])
        before=self.members.get('existing')
        record=self.seed(gender='male',level=9)
        result=self.imports.update(record,{'phone':'13800000001'})
        self.assertEqual(result['status'],'linked')
        self.assertIn('已匹配',result['message'])
        self.assertEqual(self.members.get('existing'),before)

    def test_bulk_reconciliation_is_explicit_scoped_and_idempotent(self):
        Members(self.owner,self.clock).apply('张三',phone='13800000001',gender='male')
        self.members.review('owner','approved')
        self.seed(phone='13800000001')
        self.seed('新会员',phone='13800000002')
        preview=self.imports.reconcile()
        self.assertEqual((preview['matched'],preview['waiting'],preview['linked']),(1,1,0))
        self.assertTrue(all(r['status']=='unbound' for r in self.imports.list()))
        self.assertEqual(self.imports.reconcile(commit=True)['linked'],1)
        self.assertEqual(self.imports.reconcile(commit=True)['linked'],0)
        with self.assertRaises(PermissionError):MemberImports(self.owner).reconcile(commit=True)

    def test_conflicting_phone_completion_does_not_change_preregistration(self):
        Members(self.owner,self.clock).apply('其他人',phone='13800000001',gender='male')
        self.members.review('owner','approved')
        record=self.seed()
        with self.assertRaisesRegex(ValueError,'姓名不一致'):
            self.imports.update(record,{'phone':'13800000001'})
        self.assertEqual(self.imports.list()[0]['phone_masked'],'')

    def test_duplicate_import_merges_into_original_linked_record_without_losing_history(self):
        original=self.seed(phone='13800000001',gender='male')
        Members(self.owner,self.clock).apply('张三',gender='male')
        before=self.members.get('owner')
        duplicate=self.seed()
        result=self.imports.update(duplicate,{'phone':'13800000001'})
        self.assertEqual((result['status'],result['linked_user_id'],result['merged_into_id']),
                         ('linked','owner',original))
        self.assertEqual(self.members.get('owner'),before)
        with connect(self.admin.db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pre_registered_members').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM membership_applications').fetchone()[0],1)
        self.assertEqual(self.imports.reconcile()['matched'],0)

    def test_name_match_cannot_take_another_rosters_phone(self):
        self.seed('张三')
        self.seed('李四',phone='13800000001')
        with self.assertRaisesRegex(PermissionError,'另一份'):
            Members(self.owner,self.clock).apply('张三',phone='13800000001',gender='male')
        self.assertIsNone(Members(self.owner).get())
        self.assertTrue(all(row['status']=='unbound' for row in self.imports.list()))

    def test_phone_only_import_can_complete_its_missing_name(self):
        self.seed('',phone='13800000001')
        result=Members(self.owner,self.clock).apply('补齐姓名',phone='13800000001',gender='female')
        self.assertEqual((result['name'],result['status']),('补齐姓名','approved'))
        self.assertEqual(self.imports.list()[0]['name'],'补齐姓名')

    def test_invitation_uses_selected_registration_robot_and_no_name_only_mentions(self):
        record=self.seed()
        invitations=RegistrationInvites(self.platform,self.admin,self.clock)
        data={'channel_id':self.channel['id'],'record_ids':[record]}
        preview=invitations.preview(data)
        self.assertIn('注册确认',preview['text'])
        self.assertIn('张三',preview['text'])
        self.assertNotIn('<at',preview['text'])
        self.assertRegex(preview['request_id'],r'^[a-f0-9]{32}$')
        next_preview=invitations.preview(data)
        self.assertNotEqual(next_preview['request_id'],preview['request_id'])
        self.assertEqual(next_preview['signature'],preview['signature'])
        payload={**data,'signature':preview['signature'],'request_id':preview['request_id']}
        first=invitations.send(payload);second=invitations.send(payload)
        self.assertEqual(first,second)
        with connect(self.owner.transport_db_path) as db:
            rows=list(db.execute('SELECT channel_id,status FROM outbox'))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['channel_id'],self.channel['id'])
        self.assertNotEqual(rows[0]['status'],'sent')
        with self.assertRaises(PermissionError):RegistrationInvites(self.platform,self.owner)
        self.imports.update(record,{'name':'资料已改'})
        with self.assertRaisesRegex(ValueError,'变化'):invitations.send(payload)

    def test_confirmation_command_uses_trusted_account_and_capability(self):
        self.seed()
        response=self.client.post('/api/simulator',headers=self.headers,
            json={'channel_id':self.channel['id'],'user_id':'owner','content':'注册确认 张三'})
        self.assertEqual(response.status_code,200,response.text)
        reference=response.json()['reply'].split('/register#')[1].split(')')[0]
        self.assertEqual(self.client.get('/public/registration/'+reference).json()['profile']['name'],'张三')
        result=self.client.post('/public/registration/'+reference,json={'name':'张三','gender':'male'})
        self.assertEqual(result.json()['member_status'],'approved')

    def test_already_registered_caller_reconciles_unique_import_without_new_application(self):
        Members(self.owner,self.clock).apply('张三',gender='male')
        self.members.review('owner','approved')
        before=self.members.get('owner')
        self.seed()
        result=Members(self.owner,self.clock).apply('张三')
        self.assertEqual(result['registration_result'],'already_registered')
        self.assertEqual(self.imports.list()[0]['linked_user_id'],'owner')
        self.assertEqual(self.members.get('owner'),before)
        with connect(self.admin.db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM membership_applications').fetchone()[0],1)

    def test_bulk_unique_name_matching_never_picks_among_same_name_accounts(self):
        for actor in ('first','second'):
            Members(replace(self.owner,actor=actor),self.clock).apply('同名',gender='male')
            self.members.review(actor,'approved')
        self.seed('同名')
        result=self.imports.reconcile(commit=True)
        self.assertEqual(result['linked'],0)
        self.assertIn('重名',result['items'][0]['reason'])

    def test_profile_history_user_filter_preserves_authorization(self):
        from app.profile_changes import ProfileChanges
        for actor in ('owner','second'):
            ctx=replace(self.owner,actor=actor)
            Members(ctx,self.clock).apply(actor+'姓名',gender='male')
            self.members.review(actor,'approved')
            ProfileChanges(ctx,self.clock).submit({'name':actor+'新名'})
        rows=ProfileChanges(self.admin,self.clock).list(all_users=True,user_id='owner')
        self.assertEqual([r['user_id'] for r in rows],['owner'])
        with self.assertRaises(PermissionError):
            ProfileChanges(self.owner,self.clock).list(user_id='second')
        response=self.client.get('/api/profile-requests?user_id=owner',headers=self.headers)
        self.assertEqual([r['user_id'] for r in response.json()],['owner'])
