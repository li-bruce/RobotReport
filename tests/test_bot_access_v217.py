"""Robot audience authorization and deletion with synthetic identities only."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import patch

from app.bot_access import catalog, matches, normalize, require_access
from app.commands import Commands
from app.db import connect
from app.members import Members
from app.membership_terms import MembershipTerms
from app.security import Platform, Principal
from app.service import Service
from tests import test_core, test_bot_onboarding_v215


class AudienceTests(test_core.CoreFixture):
    def policy(self, *rules, mode='all', enabled=True):
        data={'enabled':enabled,'match':mode,'rules':[{'field':k,'op':op,'value':value} for k,op,value in rules]}
        return self.platform.update(self.admin,self.ctx.id,{'user_policy':data})['user_policy']

    def run_command(self,text,ctx=None):return asyncio.run(Commands(ctx or self.member,self.clock).run(text))

    def test_all_existing_profile_fields_are_selectable_and_typed(self):
        fields={f['id'] for f in catalog(self.ctx)['fields']}
        with connect(self.ctx.db_path) as db:
            columns={r['name'] for r in db.execute('PRAGMA table_info(member_records)')}
        self.assertTrue(columns-{'chat_id'}<=fields)
        self.assertTrue({'name','group_ids','roles','permissions','membership.status'}<=fields)
        invalid=[('unknown','eq','x'),('phone','sql','1=1'),('level','gte',True),('level','gte',10),
                 ('gender','in',['anything']),('roles','any',['root']),('group_ids','any',[999]),
                 ('reviewed_at','gte','yesterday'),('membership.configured','eq','true')]
        for rule in invalid:
            with self.subTest(rule=rule),self.assertRaises(ValueError):self.policy(rule)
        with self.assertRaises(ValueError):self.policy()

    def test_all_any_groups_levels_and_gender_refresh_without_new_context(self):
        manager=Members(self.ctx,self.clock);g=manager.save_group('学生')
        manager.set_groups(self.member.actor,[g['id']]);manager.review_level(self.member.actor,True,4)
        manager.update_details(self.member.actor,gender='female')
        self.policy(('group_ids','any',[g['id']]),('level','gte',3),('level','lte',6),('gender','in',['female']))
        require_access(self.member,self.clock)
        self.assertIn('名单',self.run_command('帮助'))
        self.run_command('报名')
        manager.set_groups(self.member.actor,[])
        with self.assertRaisesRegex(PermissionError,'组别'):self.run_command('名单')
        with self.assertRaises(PermissionError):Service(self.member,self.clock).enroll(self.member.actor,'ignored')
        self.policy(('group_ids','any',[g['id']]),('level','gte',3),mode='any')
        require_access(self.member,self.clock)
        # Same group, different robot has its own unrestricted audience.
        self.policy(('level','gte',9))
        other=self.platform.context_for_channel(self.bots[1]['channels'][0]['id'],self.member.actor,False,'callback')
        self.run_command('名单',other)
        self.policy(('level','gte',9),enabled=False);self.run_command('名单')

    def test_account_repair_help_and_registration_are_available_but_no_business(self):
        self.policy(('gender','in',['female']),('level','gte',9))
        for text in ('帮助','身份','注册资料','注册','使用说明','我的权限','性别 女','自评9级'):
            with self.subTest(text=text):self.run_command(text)
        for text in ('报名','名单','组织比赛','我的统计','AI 帮我报名','确认'):
            with self.subTest(text=text),self.assertRaisesRegex(PermissionError,'使用条件'):self.run_command(text)
        newcomer=replace(self.member,actor='brand-new')
        result=self.run_command('注册 测试新用户 女 3级',newcomer)
        self.assertIn('等待',result)
        self.assertEqual(Members(newcomer,self.clock).get()['status'],'pending')

    def test_roles_are_constraints_not_grants_and_admins_cannot_bypass(self):
        self.policy(('roles','any',['club_admin']))
        with self.assertRaises(PermissionError):self.run_command('待审核')
        # Trusted role assignment fixture; policy must not trust is_admin in ctx.
        with connect(self.ctx.db_path,True) as db:
            db.execute("INSERT INTO user_roles(user_id,role,scope_type,scope_id,created_at,created_by) VALUES(?,'club_admin','club','club','2026-10-12','test')",(self.member.actor,))
        self.run_command('待审核')
        self.policy(('gender','in',['female']))
        with self.assertRaises(PermissionError):self.run_command('待审核',replace(self.member,is_admin=True))
        # Separate web administration can always repair the rule.
        Members(self.platform.workspace_context(),self.clock).review(self.member.actor,'approved')

    def test_private_rules_do_not_escape_in_denials_or_help(self):
        private='13812345678'
        self.policy(('phone','eq',private),('review_note','contains','private-note'))
        with self.assertRaises(PermissionError) as failure:self.run_command('名单')
        for value in (private,'private-note'):
            self.assertNotIn(value,str(failure.exception));self.assertNotIn(value,self.run_command('帮助'))
        limited=Principal('limited','bot_admin',(self.ctx.id,))
        self.assertIsNone(self.platform.list(limited)[0]['user_policy'])

    def test_membership_dates_and_renewal_gap_rechecked_at_execution(self):
        terms=MembershipTerms(self.ctx,self.clock)
        terms.create(self.member.actor,{'start_date':'2026-10-12','end_date':'2026-10-12'})
        terms.create(self.member.actor,{'start_date':'2026-10-14','end_date':'2027-10-13'})
        self.policy(('membership.status','in',['active']),('membership.current_term.end_date','gte','2026-10-12'))
        require_access(self.member,self.clock)
        self.now+=timedelta(days=1)
        with self.assertRaises(PermissionError):require_access(self.member,self.clock)
        self.run_command('我的会籍')
        self.now+=timedelta(days=1);require_access(self.member,self.clock)

    def test_saved_ai_step_and_domain_write_recheck_changed_rules(self):
        from app.ai_operations import execute_step
        commands=Commands(self.member,self.clock)
        # Proposal/confirmation does not confer authority after configuration changes.
        self.policy(('level','gte',8))
        with self.assertRaises(PermissionError):
            asyncio.run(execute_step(commands,{'operation':'Register','arguments':{}}))
        with connect(self.ctx.db_path,True) as db:
            with self.assertRaises(PermissionError):Members(self.member,self.clock).require_approved(db=db)
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM registrations').fetchone()[0],0)

    def test_actual_pending_ai_plan_cannot_confirm_after_audience_changes(self):
        from app.ai_conversation import AiConversation
        self.s.update_config({'ai':{'enabled':True,'api_key':'local-fake-key'}})
        proposal={'intent':'BusinessPlan','steps':[{'operation':'Register','arguments':{
            'venue_id':1,'date':'2026-10-13','number':1,'guests':0,'guest_names':[],'chain':False}}],
            'requires_confirmation':True,'scope':{'bot_id':self.member.id,'chat_id':self.member.chat_id,'user_id':self.member.actor}}
        with patch('app.ai_gateway.interpret_limited',return_value=proposal):
            self.assertIn('确认',self.run_command('AI 帮我报名明天第一场'))
        self.assertIsNotNone(AiConversation(self.member,self.clock).current())
        self.policy(('gender','in',['female']))
        with self.assertRaises(PermissionError):self.run_command('确认')
        with connect(self.ctx.db_path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM registrations').fetchone()[0],0)

    def test_blocked_ai_never_calls_provider(self):
        self.policy(('level','gte',9))
        with patch('app.ai.interpret') as model:
            with self.assertRaises(PermissionError):self.run_command('AI 明天帮我报名')
        model.assert_not_called()

    def test_operators_missing_values_and_unicode(self):
        member={'name':'球友Alice','roles':[],'level':None,'created_at':'2026-10-12T16:00:00+08:00'}
        cases=[('name','contains','ALICE',True),('name','prefix','球友',True),('name','in',['球友Alice'],True),
               ('roles','none',['club_admin'],True),('roles','all',['club_admin'],False),('roles','empty',None,True),
               ('level','ne',1,False),('level','present',None,False),('level','empty',None,True),
               ('created_at','lte','2026-10-12',True)]
        for key,op,value,expected in cases:
            with self.subTest(key=key,op=op):self.assertEqual(matches({'field':key,'op':op,'value':value},member),expected)


class DeleteAndApiTests(unittest.TestCase):
    setUp=test_bot_onboarding_v215.OnboardingTests.setUp
    pending=test_bot_onboarding_v215.OnboardingTests.pending
    body=test_bot_onboarding_v215.OnboardingTests.body
    callback=test_bot_onboarding_v215.OnboardingTests.callback
    def test_deleted_robot_message_history_remains_readable_without_reactivation(self):
        bot=self.pending();self.callback(bot)
        cid=bot['channels'][0]['id']
        before=self.client.get('/api/messages',headers=self.headers)
        self.assertEqual(before.status_code,200)
        records=[r for r in before.json() if r['bot_id']==bot['id']]
        self.assertTrue(records)
        self.p.remove(self.admin,bot['id'])
        after=self.client.get('/api/messages',headers=self.headers)
        self.assertEqual(after.status_code,200)
        self.assertEqual([r for r in after.json() if r['bot_id']==bot['id']],records)
        history=self.p.list_channels(self.admin,bot['id'],include_archived=True)
        self.assertEqual(len(history),1);self.assertTrue(history[0]['archived'])
        self.assertFalse(self.p.bot(bot['id'],include_deleted=True)['active'])
        self.assertEqual(self.client.get(bot['callback_path']).status_code,403)
        test=self.client.post('/api/bots/'+bot['id']+'/connection/test',headers=self.headers,
                             json={'channel_id':cid,'request_id':'d'*32})
        self.assertEqual(test.status_code,403)

    def test_delete_bound_robot_preserves_group_other_robot_shared_data_and_history(self):
        bot=self.pending();other=self.pending('other-key');self.callback(bot);self.callback(other,self.body('other-key'))
        cid=bot['channels'][0]['id'];path='/api/bots/'+bot['id'];transport=self.p.root_for(bot['id'])/'transport.sqlite3'
        with connect(self.p.workspace_root/'business.sqlite3') as db:before='\n'.join(db.iterdump())
        self.assertEqual(self.client.delete(path).status_code,401)
        limited=self.p.grant(self.admin,[bot['id']])['token']
        self.assertEqual(self.client.delete(path,headers={'Authorization':'Bearer '+limited}).status_code,403)
        self.assertEqual(self.client.delete(path,headers=self.headers).json(),{'ok':True,'disconnected':1})
        self.assertEqual(self.client.delete(path,headers=self.headers).json()['disconnected'],0)
        self.assertNotIn(bot['id'],[b['id'] for b in self.p.list(self.admin)])
        self.assertTrue(self.p.channel(cid)['archived']);self.assertEqual(len(self.groups.list(self.admin)),1)
        with connect(self.p.workspace_root/'business.sqlite3') as db:self.assertEqual('\n'.join(db.iterdump()),before)
        self.assertEqual(self.callback(other,self.body('other-key',ctime='1792020000999')).status_code,200)
        self.assertEqual(self.client.get(bot['callback_path']).status_code,403)
        self.assertEqual(self.callback(bot,self.body(ctime='1792020000999')).status_code,403)
        self.assertEqual(self.client.patch(path,headers=self.headers,json={'active':True}).status_code,403)
        self.assertEqual(self.client.get(path+'/connection',headers=self.headers).status_code,403)
        self.assertEqual(self.client.post(path+'/rotate-callback',headers=self.headers).status_code,403)
        with self.assertRaises(PermissionError):self.p.authenticate(limited)
        with self.assertRaises(PermissionError):self.p.save_channel(self.admin,bot['id'],{'group_id':self.group['id']})
        with connect(self.p.workspace_root/'business.sqlite3') as db:
            # The other robot's touch updates last_seen only; membership and business rows persist.
            self.assertEqual(db.execute('SELECT COUNT(*) FROM member_records').fetchone()[0],1)
        with connect(transport) as db:self.assertGreater(db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],0)
        self.assertTrue(transport.exists())
        resumed=Platform(self.p.root,self.token)
        self.assertNotIn(bot['id'],[b['id'] for b in resumed.list(self.admin)])

    def test_unbound_delete_and_stale_update_cannot_resurrect(self):
        bot=self.p.create(self.admin,{'name':'待接入'})
        original=self.p._scope_value
        def concurrent_delete(key,value):
            self.p.remove(self.admin,bot['id']);return original(key,value)
        with patch.object(self.p,'_scope_value',side_effect=concurrent_delete):
            with self.assertRaises(PermissionError):self.p.update(self.admin,bot['id'],{'active':True,'venue_ids':None})
        self.assertEqual(self.p.bot(bot['id'],include_deleted=True)['active'],0)
        self.assertEqual(self.p.remove(self.admin,bot['id'])['disconnected'],0)

    def test_policy_api_validation_default_and_restart(self):
        self.assertEqual(self.client.get('/api/bot-user-attributes').status_code,401)
        fields=self.client.get('/api/bot-user-attributes',headers=self.headers).json()['fields']
        self.assertGreaterEqual(len(fields),30)
        bot=self.pending();path='/api/bots/'+bot['id']
        self.assertFalse(bot['user_policy']['enabled'])
        policy={'enabled':True,'match':'all','rules':[{'field':'gender','op':'in','value':['female']}]}
        self.assertEqual(self.client.patch(path,headers=self.headers,json={'user_policy':policy}).json()['user_policy'],policy)
        restarted=Platform(self.p.root,self.token)
        self.assertEqual(restarted.list(self.admin)[0]['user_policy'],policy)
        bad=self.client.patch(path,headers=self.headers,json={'name':'must-not-change','user_policy':{'enabled':True,'rules':[]}})
        self.assertEqual(bad.status_code,400);self.assertEqual(self.p.bot(bot['id'])['name'],bot['name'])

    def test_deleted_queue_not_delivered_or_replayed_by_replacement(self):
        from app.messages import Outbox
        bot=self.pending();self.callback(bot)
        ctx=self.p.context_for_channel(bot['channels'][0]['id'])
        with connect(ctx.transport_db_path) as db:before=list(map(tuple,db.execute('SELECT * FROM outbox')))
        self.p.remove(self.admin,bot['id'])
        with self.assertRaises(PermissionError):asyncio.run(Outbox(ctx).deliver_one(self.p))
        replacement=self.pending();self.callback(replacement,self.body(ctime='1792020000123'))
        self.assertNotEqual(bot['id'],replacement['id'])
        with connect(ctx.transport_db_path) as db:self.assertEqual(list(map(tuple,db.execute('SELECT * FROM outbox'))),before)
