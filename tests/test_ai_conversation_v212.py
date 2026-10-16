"""Real router and typed planner with only the external model replaced."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import json
from unittest.mock import patch

from app import ai
from app.ai_conversation import AiConversation
from app.ai_plan_store import AiPlanStore
from app.db import connect
from app.service import Service
from tests.test_ai import FakeRunnable
from tests.test_core import CoreFixture


class AiConversationTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.s.update_config({'ai':{'enabled':True,'api_key':'isolated-fake-key'}})

    def send(self,text,ctx=None):
        return asyncio.run(self.commands(ctx).run(text))

    def draft(self,number=1,names=None,ctx=None,text='AI 明天第一场报名，张三，再看名单'):
        steps=[{'operation':'Register','arguments':{'date':'2026-10-13','number':number,'guests':len(names or []),'guest_names':names or []}},
               {'operation':'Roster','arguments':{'date':'2026-10-13','number':number}}]
        fake=FakeRunnable('BusinessPlan',{'steps':steps})
        with patch.object(ai,'_make_runnable',return_value=fake):
            reply=self.send(text,ctx)
        return reply,fake

    def registrations(self):
        with connect(self.ctx.db_path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM registrations WHERE status='active'")]

    def current(self,ctx=None,pending=True):
        return AiConversation(ctx or self.member,self.clock).current(pending)

    def test_numbered_preview_and_plain_confirmation_once(self):
        reply,_=self.draft(names=['张三'])
        self.assertIn('1. **本人报名**',reply)
        self.assertIn('2. **查询名单**',reply)
        self.assertNotIn('|',reply)
        self.assertNotIn(self.current()['token'],reply)
        self.assertEqual(self.registrations(),[])
        result=self.send('确认')
        self.assertIn('已完成',result)
        self.assertEqual(len(self.registrations()),2)
        with self.assertRaises(ValueError):self.send('确认')
        self.assertIn('已完成',self.send('AI进度'))
        self.assertNotIn('|',self.send('AI进度'))

    def test_refinement_retains_names_from_draft_and_replaces_old_steps(self):
        self.draft(names=['张三'])
        old=self.current()['token']
        reply,fake=self.draft(2,['张三'],text='AI 改成第二场，仍然查名单')
        self.assertIn('第2场',reply)
        new=self.current()['token']
        self.assertNotEqual(new,old)
        with self.assertRaises(ValueError):self.send('AI确认 '+old)
        self.assertIn('已完成',self.send('确认'))
        self.assertEqual(len(self.registrations()),2)
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute('SELECT DISTINCT number FROM (SELECT s.name AS number FROM registrations r JOIN sessions s ON s.id=r.session_id)').fetchone()[0],'第二场')

    def test_other_member_cannot_confirm_but_has_independent_slot(self):
        self.draft()
        other=replace(self.member,actor='1002')
        with self.assertRaises(ValueError):self.send('确认',other)
        self.draft(2,ctx=other,text='AI 明天第二场报名并查名单')
        self.send('确认',other)
        self.assertEqual({r['user_id'] for r in self.registrations()},{'1002'})
        # Other business writes require re-preview; never silently change the first user's plan.
        with self.assertRaises(ValueError):self.send('确认')

    def test_one_user_one_current_plan_across_robots(self):
        self.draft()
        old=self.current()['token']
        second=replace(self.platform.context(self.admin,self.bots[1]['id']),actor='1001',is_admin=False,source='callback')
        self.draft(2,ctx=second,text='AI 明天第二场报名并查名单')
        with self.assertRaises(ValueError):self.send('确认')
        with self.assertRaises(ValueError):self.send('AI确认 '+old)
        self.send('确认',second)
        self.assertEqual(len(self.registrations()),1)

    def test_configured_ttl_and_restart_lookup(self):
        self.s.update_config({'ai':{'confirmation_minutes':12}})
        self.draft()
        self.now+=timedelta(minutes=6)
        self.assertIn('已完成',self.send('确认'))
        self.draft(2,text='AI 明天第二场报名并查名单')
        self.now+=timedelta(minutes=13)
        with self.assertRaisesRegex(ValueError,'过期'):self.send('确认')
        self.assertEqual(len(self.registrations()),1)

    def test_cancel_without_enrollment_capability(self):
        self.draft()
        self.platform.update(self.admin,self.ctx.id,{'capabilities':['ai']})
        self.assertIn('已取消',self.send('取消计划'))
        self.assertEqual(self.registrations(),[])

    def test_late_model_reply_does_not_replace_newer_proposal(self):
        async def scenario():
            started=asyncio.Event();release=asyncio.Event()
            async def interpret(ctx,text,context,config,clock):
                if text=='旧需求':started.set();await release.wait()
                return {'intent':'BusinessPlan','scope':{'bot_id':ctx.id,'chat_id':ctx.chat_id,'user_id':ctx.actor},
                        'steps':[{'operation':'Register','arguments':{'venue_id':1,'date':'2026-10-13','number':1 if text=='旧需求' else 2,'guests':0,'guest_names':[],'chain':False}}]}
            with patch('app.ai_gateway.interpret_limited',side_effect=interpret):
                older=asyncio.create_task(self.commands().run('AI 旧需求'))
                await started.wait()
                await self.commands().run('AI 新需求')
                token=self.current()['token']
                release.set()
                with self.assertRaisesRegex(ValueError,'更新'):await older
                self.assertEqual(self.current()['token'],token)
                self.assertEqual(AiPlanStore(self.member,self.clock).get(token)['status'],'pending')
                await self.commands().run('确认')
        asyncio.run(scenario())
        self.assertEqual(len(self.registrations()),1)

    def test_ttl_validation(self):
        for value in (0,1441,True,'10',2.5):
            with self.subTest(value=value),self.assertRaises(ValueError):self.s.update_config({'ai':{'confirmation_minutes':value}})

    def test_startup_releases_interrupted_user_slot_without_replaying_plan(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        self.draft()
        old=self.current()['token']
        AiConversation(self.member,self.clock).claim(old)
        AiPlanStore(self.member,self.clock).claim(old)
        with self.assertRaisesRegex(ValueError,'正在执行'):
            self.draft(2,text='AI 明天第二场报名并查名单')
        with TestClient(create_app(state_dir=self.platform.root,start_scheduler=False,live=False,clock=self.clock)) as client:
            self.assertEqual(client.get('/healthz').status_code,200)
        self.assertEqual(self.current(pending=False)['status'],'interrupted')
        self.assertIn('执行曾中断',self.send('AI进度'))
        with self.assertRaises(ValueError):self.send('AI确认 '+old)
        self.assertEqual(self.registrations(),[])
        self.draft(2,text='AI 明天第二场报名并查名单')
        self.assertIn('已完成',self.send('确认'))
        self.assertEqual(len(self.registrations()),1)
