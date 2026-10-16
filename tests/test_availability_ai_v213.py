import asyncio
from app.ai_plan_flow import Plans
from app.ai_conversation import AiConversation
from app.availability import Availability
from app.commands import Commands
from app.db import connect
from app.ai_planner import validate_plan
from tests.test_ai_planner_v27 import context,step
from tests.test_core import CoreFixture


class AvailabilityAiTests(CoreFixture):
    def test_semantic_plan_permissions_and_release_identity(self):
        steps=[step('ReserveVenue',date='2026-10-13',start='17:30',end='19:00',title='训练'),step('QueryOccupancy',date='2026-10-13')]
        with self.assertRaises(PermissionError):
            validate_plan({'steps':steps},context(),'明天安排训练')
        result=validate_plan({'steps':steps},context(is_admin=True),'明天场地1从17点半到19点训练，然后查占用')
        self.assertEqual(len(result['steps']),2)
        with self.assertRaises(ValueError):
            validate_plan({'steps':[step('ReleaseVenue',reservation_id=912)]},context(is_admin=True),'释放明天的占用')

    def test_preview_no_write_plain_confirmation_single_execution(self):
        self.s.update_config({'ai':{'enabled':True,'api_key':'local-test'}})
        ctx=self.platform.workspace_context()
        commands=Commands(ctx,self.clock)
        asyncio.run(commands.run('帮助'))
        proposal={'scope':{'bot_id':ctx.id,'chat_id':ctx.chat_id,'user_id':ctx.actor},'steps':[
            {'operation':'ReserveVenue','arguments':{'venue_id':1,'date':'2026-10-13','start':'17:30','end':'19:00','title':'训练'}},
            {'operation':'QueryOccupancy','arguments':{'venue_id':1,'date':'2026-10-13'}}]}
        reply=asyncio.run(Plans(commands).propose(proposal))
        self.assertIn('确认',reply)
        self.assertIn('17:30',reply)
        self.assertEqual(Availability(ctx,self.clock).list(1,'2026-10-13'),[])
        result=asyncio.run(commands.run('确认'))
        self.assertIn('占用',result)
        self.assertEqual(len(Availability(ctx,self.clock).list(1,'2026-10-13')),1)
        with self.assertRaises(ValueError):
            asyncio.run(commands.run('确认'))

    def test_pending_booking_rechecks_new_occupancy(self):
        self.s.update_config({'ai':{'enabled':True,'api_key':'local-test'}})
        commands=Commands(self.member,self.clock)
        proposal={'scope':{'bot_id':self.member.id,'chat_id':self.member.chat_id,'user_id':self.member.actor},'steps':[
            {'operation':'Register','arguments':{'venue_id':1,'date':'2026-10-13','number':1,'hour':None,'minute':0,'guests':0,'guest_names':[],'chain':False}}]}
        reply=asyncio.run(Plans(commands).propose(proposal))
        self.assertIn('确认',reply)
        Availability(self.platform.workspace_context(),self.clock).save({'venue_id':1,'date':'2026-10-13','start':'17:30','end':'19:00','title':'新增赛事占用'})
        try:
            reply=asyncio.run(commands.run('确认'))
            self.assertRegex(reply,'变化|失效|未完成|停止')
        except ValueError:
            pass
        self.assertEqual(self.s.registrations('2026-10-13')['registrations'],[])
