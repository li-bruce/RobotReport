"""Group review and AI approval share live authorization and durable records."""
import asyncio
from dataclasses import replace
import re

from app import ai
from app.ai_plan_flow import Plans
from app.ai_planner import validate_plan
from app.db import connect
from app.member_review import MemberReview
from app.members import Members
from app.people import People
from tests.test_core import CoreFixture
from tests.test_ai_planner_v27 import context, step


class GroupReviewTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'local-fake-ai-key'}})
        People(self.ctx,self.clock).set_roles('1001',['member_manager'])
        self.applicant=replace(self.member,actor='new-member')
        Members(self.applicant,self.clock).apply('张三',level=5,phone='13800138000',gender='male')
        with connect(self.ctx.db_path) as db:
            self.application_id=db.execute('SELECT MAX(id) FROM membership_applications').fetchone()[0]

    def run_text(self,text,ctx=None):
        return asyncio.run(self.commands(ctx).run(text))

    def events(self):
        with connect(self.ctx.db_path) as db:
            return [dict(r) for r in db.execute('SELECT * FROM identity_notification_events WHERE user_id=?',('new-member',))]

    def preview(self):
        proposal={'intent':'BusinessPlan','steps':[step('ReviewMember',member_name='张三',decision='approved')],
                  'scope':{'bot_id':self.member.id,'chat_id':self.member.chat_id,'user_id':self.member.actor}}
        reply=asyncio.run(Plans(self.commands()).propose(proposal))
        token=self.pending_match(reply)
        self.assertIsNotNone(token,reply)
        return token[1],reply

    def test_pending_table_omits_phone_and_review_succeeds_without_granting_level_or_role(self):
        reply=self.run_text('待审核')
        self.assertIn('张三',reply)
        self.assertIn(f'申请{self.application_id}',reply)
        self.assertNotIn('13800138000',reply)
        self.assertIn('审核通过',self.run_text('审核通过 张三'))
        profile=People(self.ctx,self.clock).get('new-member')
        self.assertEqual(profile['status'],'approved')
        self.assertIsNone(profile['level'])
        self.assertEqual(profile['roles'],[])
        self.assertEqual(len(self.events()),1)
        self.assertIn('没有重复',self.run_text(f'审核通过 申请{self.application_id}'))
        self.assertEqual(len(self.events()),1)

    def test_ordinary_and_other_manager_cannot_review(self):
        for roles in ([],['event_manager'],['system_manager']):
            People(self.ctx,self.clock).set_roles('1001',roles)
            for text in ('待审核','审核通过 张三','审核拒绝 张三'):
                with self.subTest(roles=roles,text=text),self.assertRaises(PermissionError):
                    self.run_text(text)
        self.assertEqual(People(self.ctx,self.clock).get('new-member')['status'],'pending')
        self.assertEqual(self.events(),[])

    def test_duplicate_name_requires_application_number(self):
        Members(replace(self.applicant,actor='new-member2'),self.clock).apply('张三',gender='female')
        with self.assertRaisesRegex(ValueError,'同名'):
            self.run_text('审核通过 张三')
        self.run_text(f'审核通过 申请{self.application_id}')
        self.assertEqual(People(self.ctx,self.clock).get('new-member2')['status'],'pending')

    def test_rejection_and_resubmission_invalidates_old_application(self):
        self.run_text(f'审核拒绝 申请{self.application_id}')
        Members(self.applicant,self.clock).apply('张三',gender='male')
        with self.assertRaisesRegex(ValueError,'未找到'):
            self.run_text(f'审核通过 申请{self.application_id}')
        self.assertEqual(People(self.ctx,self.clock).get('new-member')['status'],'pending')
        self.run_text('审核通过 张三')

    def test_self_and_nonexistent_application_never_auto_register(self):
        for target in ('小李','不存在的人'):
            with self.subTest(target=target),self.assertRaises(ValueError):
                self.run_text('审核通过 '+target)
        self.assertEqual(self.events(),[])

    def test_revoked_stale_context_cannot_review(self):
        cmd=self.commands()
        cmd.refresh_context()
        People(self.ctx,self.clock).set_roles('1001',[])
        with self.assertRaises(PermissionError):
            MemberReview(cmd.ctx,self.clock).review('approved',member_name='张三')

    def test_ai_preview_is_isolated_then_confirmation_commits_once(self):
        token,reply=self.preview()
        self.assertIn('张三',reply)
        self.assertNotIn('13800138000',reply)
        self.assertEqual(self.events(),[])
        self.assertEqual(People(self.ctx,self.clock).get('new-member')['status'],'pending')
        reply=asyncio.run(Plans(self.commands()).confirm(token))
        self.assertIn('已完成',reply)
        self.assertEqual(People(self.ctx,self.clock).get('new-member')['status'],'approved')
        self.assertEqual(len(self.events()),1)
        with self.assertRaises(ValueError):
            asyncio.run(Plans(self.commands()).confirm(token))
        self.assertEqual(len(self.events()),1)

    def test_ai_confirmation_rechecks_revoked_permission(self):
        token,_=self.preview()
        People(self.ctx,self.clock).set_roles('1001',[])
        with self.assertRaises((ValueError,PermissionError)):
            asyncio.run(Plans(self.commands()).confirm(token))
        self.assertEqual(People(self.ctx,self.clock).get('new-member')['status'],'pending')
        self.assertEqual(self.events(),[])

    def test_ai_confirmation_rejects_changed_application(self):
        token,_=self.preview()
        self.run_text('审核拒绝 张三')
        Members(self.applicant,self.clock).apply('张三',gender='male')
        with self.assertRaises((ValueError,PermissionError)):
            asyncio.run(Plans(self.commands()).confirm(token))
        self.assertEqual(People(self.ctx,self.clock).get('new-member')['status'],'pending')

    def test_ai_permissions_are_visible_but_not_full_admin(self):
        value=self.commands().ai_context(1)
        self.assertFalse(value['is_admin'])
        self.assertEqual(value['permissions'],['member.manage'])
        features={f['feature_id'] for f in ai._feature_catalog(ai._trusted_context(value))}
        self.assertIn('membership.review',features)
        self.assertNotIn('membership.roles',features)
        self.assertNotIn('bots.channels',features)

    def test_planner_allows_only_required_subpermission(self):
        raw={'steps':[step('ReviewMember',member_name='张三',decision='approved'),step('PendingMembers')]}
        result=validate_plan(raw,context(permissions=['member.manage']),text='批准张三的注册，然后查看还有谁待审核')
        self.assertEqual(len(result['steps']),2)
        for permissions in ([],['activity.manage'],['roles.manage']):
            with self.subTest(permissions=permissions),self.assertRaises(PermissionError):
                validate_plan(raw,context(permissions=permissions),text='批准张三注册')

    def test_planner_cannot_guess_review_target(self):
        for args in ({'member_name':'张三'},{'application_id':13}):
            with self.subTest(args=args),self.assertRaises(ai._ClarificationRequired):
                validate_plan({'steps':[step('ReviewMember',decision='approved',**args)]},
                              context(permissions=['member.manage']),text='帮我审核注册')

    def test_activity_and_system_subroles_use_their_own_ai_operations(self):
        for operation,permission,args in [('UpdateSession','activity.manage',{'capacity':12}),
                                           ('ToggleAI','system.manage',{'enabled':False})]:
            result=validate_plan({'steps':[step(operation,**args)]},context(permissions=[permission]))
            self.assertEqual(result['steps'][0]['operation'],operation)
            with self.assertRaises(PermissionError):
                validate_plan({'steps':[step(operation,**args)]},context(permissions=['member.manage']))
