import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from app.commands import Commands
from app.db import connect
from app.members import Members
from app.service import Service
from app.tournaments import Tournaments
from tests.test_core import CoreFixture


class ExternalSubstituteTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.workspace = self.platform.workspace_context()
        self.all = Service(self.workspace,self.clock)
        self.slots = self.all.slots()
        self.slots[1].update(capacity=2,substitute_capacity=2)
        self.all.save_slots(1,self.slots)
        self.platform.update(self.admin,self.bots[0]['id'],{'slot_keys':[self.slots[0]['slot_key']],
            'allow_external_substitute':True,'external_slot_keys':[self.slots[1]['slot_key']]})
        self.platform.update(self.admin,self.bots[1]['id'],{'slot_keys':[self.slots[1]['slot_key']]})
        self.a = self.platform.context_for_channel(self.bots[0]['channels'][0]['id'],'1001',False,'callback')
        self.b = self.platform.context_for_channel(self.bots[1]['channels'][0]['id'],'1002',False,'callback')
        self.target = self.all.sessions()[1]

    def service(self,ctx=None,user=None):
        ctx = ctx or self.a
        return Service(replace(ctx,actor=user) if user else ctx,self.clock)

    def enroll(self,ctx=None,user=None,**kwargs):
        service=self.service(ctx,user)
        return service.enroll(service.ctx.actor,'ignored',number=2,day=kwargs.pop('day',self.target['date']),**kwargs)

    def active(self):
        return [r for r in self.all.registrations(day=self.target['date'],session_id=self.target['id'])['registrations'] if r['status']=='active']

    def test_configuration_requires_explicit_nonempty_external_selection(self):
        old=self.platform.visible_bot(self.platform.bot(self.a.id),self.admin)
        for data in ({'external_slot_keys':[]},{'external_slot_keys':None},
                     {'external_slot_keys':[self.slots[0]['slot_key']]},
                     {'external_slot_keys':['slot:99999']},{'allow_external_substitute':'true'},
                     {'capabilities':['daily_events']}):
            with self.subTest(data=data),self.assertRaises(ValueError):
                self.platform.update(self.admin,self.a.id,data)
            self.assertEqual(self.platform.visible_bot(self.platform.bot(self.a.id),self.admin),old)
        self.assertFalse(self.b.allow_external_substitute)
        reopened=type(self.platform)(self.platform.root)
        self.assertTrue(reopened.context_for_channel(self.a.channel_id).allow_external_substitute)

    def test_scope_default_commands_and_unselected_slots_stay_restricted(self):
        normal=self.service().enroll('1001','ignored')
        self.assertEqual(normal['sessions'][0]['number'],1)
        self.assertEqual(normal['registrations'][0]['category'],'正式')
        self.assertEqual([r['number'] for r in self.service().sessions()],[1])
        with self.assertRaises(ValueError):self.service(self.b).enroll('1002','ignored',number=1)
        with self.assertRaises(ValueError):self.service().enroll('1001','ignored',day='2026-10-17',number=1)
        with self.assertRaises(ValueError):self.service().resolve_session(number=2)
        self.assertEqual([r['number'] for r in self.service().registrations()['sessions']],[1])

    def test_future_cross_entries_and_guests_keep_reserve_priority(self):
        self.target=self.all.sessions('2026-10-13')[1]
        self.enroll(guests=2,guest_names=['同行甲','同行乙'])
        self.assertEqual([r['category'] for r in self.active()],['替补','替补','候补'])
        self.enroll(self.b)
        self.enroll(self.b,'1003')
        self.enroll(self.b,'1004')
        rows=self.active()
        self.assertEqual([r['category'] for r in rows],['正式','正式','替补','替补','候补','候补'])
        self.assertEqual([r['user_id'] for r in rows[:2]],['1002','1003'])
        self.service(self.b).cancel('1002',number=2,day=self.target['date'])
        self.assertEqual([r['user_id'] for r in self.active()[:2]],['1003','1004'])
        self.service(self.b,'1003').cancel('1003',number=2,day=self.target['date'])
        self.assertEqual(sum(r['category']=='正式' for r in self.active()),1)
        self.assertTrue(all(r['category']!='正式' for r in self.active() if r['substitute_only']))

    def test_shared_roster_duplicate_and_cancellation_keep_owner(self):
        self.enroll(guests=2,guest_names=['同行甲','同行乙'])
        self.enroll(self.b,'1001')  # Repeating through the main bot cannot promote the existing entry.
        self.assertEqual(len(self.active()),3)
        self.assertTrue(all(r['substitute_only'] for r in self.active()))
        self.assertEqual(self.service().registrations(session_id=self.target['id'],registration=True)['registrations'],
                         self.service(self.b).registrations(session_id=self.target['id'])['registrations'])
        with self.assertRaises(ValueError):self.service(self.b).cancel_guests('1002',['同行甲'],number=2)
        self.service().cancel_guests('1001',['同行甲'],number=2)
        self.service().cancel('1001',number=2)
        self.assertEqual([r['guest_name'] for r in self.active()],['同行乙'])
        self.enroll()
        self.assertEqual(self.active()[-1]['guest_index'],0)
        self.assertTrue(self.active()[-1]['substitute_only'])

    def test_commands_enroll_roster_and_cancel_selected_external_session(self):
        router=Commands(self.a,self.clock)
        reply=asyncio.run(router.run('第二场 报名，同行甲'))
        self.assertIn('正式',reply)
        self.assertIn('同行甲',asyncio.run(router.run('第二场 名单')))
        self.assertNotIn('同行甲',asyncio.run(router.run('名单')))
        self.assertIn('已取消',asyncio.run(router.run('第二场 取消同行甲')))
        self.assertIn('已取消',asyncio.run(router.run('第二场 取消报名')))
        self.assertEqual(self.active(),[])
        with self.assertRaises(ValueError):asyncio.run(router.run('第二场 组织比赛'))

    def test_explicit_cross_venue_only_for_selected_slot(self):
        venue=self.all.save_venue({'name':'另一场地'})
        rules=self.all.save_slots(venue['id'],[{'name':'午场','weekdays':[0],'start':'17:00','end':'19:00'}])
        self.platform.update(self.admin,self.a.id,{'venue_ids':[1],'external_slot_keys':[rules[0]['slot_key']]})
        router=Commands(self.a,self.clock)
        reply=asyncio.run(router.run(f"场地{venue['id']} 第一场 报名"))
        self.assertIn('正式',reply)
        self.assertIn('小李',asyncio.run(router.run(f"场地{venue['id']} 第一场 名单")))
        with self.assertRaises(PermissionError):asyncio.run(router.run(f"场地{venue['id']} 第一场 组织比赛"))
        with self.assertRaises(ValueError):self.enroll()

    def test_revocation_rechecked_after_resolution(self):
        service=self.service()
        original=service.resolve_session
        def resolve(*args,**kwargs):
            result=original(*args,**kwargs)
            self.platform.update(self.admin,self.a.id,{'allow_external_substitute':False})
            return result
        with patch.object(service,'resolve_session',side_effect=resolve),self.assertRaises(PermissionError):
            service.enroll('1001','ignored',number=2)
        self.assertEqual(self.active(),[])

    def test_eligibility_and_occupancy_are_not_bypassed(self):
        self.all.save_session_constraints(self.target['id'],{'min_level':9})
        with self.assertRaises(PermissionError):self.enroll()
        self.all.save_session_constraints(self.target['id'],{'min_level':None})
        from app.availability import Availability
        Availability(self.workspace,self.clock).save({'venue_id':1,'date':self.target['date'],
            'start':self.target['start'],'end':self.target['end'],'title':'训练'})
        with self.assertRaises((ValueError,PermissionError)):self.enroll()
        self.assertEqual(self.active(),[])

    def test_parallel_duplicate_requests_preserve_single_cross_entry(self):
        def enroll(_):return self.service().enroll('1001','ignored',number=2)
        with ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(enroll,range(10)))
        self.assertEqual(len(self.active()),1)
        self.assertTrue(self.active()[0]['substitute_only'])

    def test_calendar_and_planning_share_substitute_counts(self):
        self.enroll(guests=1)
        self.enroll(self.b)
        from app.activity_operations import ActivityOperations
        day=ActivityOperations(self.workspace,self.clock).day(self.target['date'])
        row=next(s for v in day['venues'] for s in v['sessions'] if s['id']==self.target['id'])
        self.assertEqual((row['formal_count'],row['substitute_count'],row['waitlist_count']),(2,1,0))
        candidates=Tournaments(self.b,self.clock).planning_candidates(self.target['date'],1,self.target['id'])
        self.assertEqual((len(candidates['formal']),len(candidates['substitutes'])),(2,1))
        self.enroll(self.b,'1003')
        with patch('app.competitions._now',self.clock):
            event=Tournaments(self.b,self.clock).create({'kind':'daily','format':'round_robin',
                'title':'跨场替补双打','session_id':self.target['id']})
        self.assertEqual(len(event['participants']),4)
        self.assertEqual(len(event['options']['selected_substitute_names']),2)

    def test_ai_preview_pins_external_classification_and_rechecks_config(self):
        router=Commands(self.a,self.clock)
        command,snapshot=router.snapshot('第二场 报名')
        self.assertTrue(snapshot['session']['external_substitute'])
        self.platform.update(self.admin,self.a.id,{'allow_external_substitute':False})
        with self.assertRaises((ValueError,PermissionError)):asyncio.run(router.run(command,expected=snapshot))
        self.assertEqual(self.active(),[])

    def test_help_lists_selected_slots_only_while_enabled(self):
        from app.help import scoped_help
        member=Members(self.a,self.clock).get()
        reply=scoped_help(self.a,member,'报名',self.clock)
        self.assertIn('还可报名替补',reply)
        self.assertIn('20:30',reply)
        self.assertNotIn('周末第一场',reply)
        self.platform.update(self.admin,self.a.id,{'allow_external_substitute':False})
        self.assertNotIn('还可报名替补',scoped_help(self.a,member,'报名',self.clock))

    def test_ai_plan_allows_external_registration_not_management(self):
        from app.ai import _trusted_context
        from app.ai_planner import validate_plan
        trusted=_trusted_context({'bot_id':self.a.id,'chat_id':self.a.chat_id,'user_id':self.a.actor,
            'today':self.target['date'],'venue_id':1,'allowed_venue_ids':[1],'registration_venue_ids':[1,2],
            'is_admin':True,'approved':True})
        for operation in ('Register','CancelRegistration','Roster'):
            result=validate_plan({'steps':[{'operation':operation,'arguments':{'venue_id':2,'number':1}}]},trusted,'场地2第一场报名并查询名单或取消报名')
            self.assertEqual(result['steps'][0]['arguments']['venue_id'],2)
        with self.assertRaises(PermissionError):
            validate_plan({'steps':[{'operation':'UpdateSession','arguments':{'venue_id':2,'number':1,'capacity':12}}]},trusted,'场地2第一场正式人数改成12')

    def test_ai_confirmation_executes_cross_venue_registration_and_roster(self):
        from app import ai
        from tests.test_ai import FakeRunnable
        venue=self.all.save_venue({'name':'第三方场地'})
        rules=self.all.save_slots(venue['id'],[{'name':'午场','weekdays':[0],'start':'17:00','end':'19:00'}])
        self.platform.update(self.admin,self.a.id,{'venue_ids':[1],'external_slot_keys':[rules[0]['slot_key']]})
        self.all.update_config({'ai':{'enabled':True,'api_key':'local-fake-ai-key'}})
        args={'venue_id':venue['id'],'date':self.target['date'],'number':1}
        fake=FakeRunnable('BusinessPlan',{'steps':[{'operation':'Register','arguments':args},
                                                   {'operation':'Roster','arguments':args}]})
        with patch.object(ai,'_make_runnable',return_value=fake):
            reply=asyncio.run(Commands(self.a,self.clock).run(f"AI 今天场地{venue['id']}第一场报名，然后看名单"))
        self.assertIn('确认',reply)
        result=asyncio.run(Commands(self.a,self.clock).run('确认'))
        self.assertIn('正式',result)
        records=self.all.registrations(venue_id=venue['id'])['registrations']
        self.assertEqual(len(records),1)
        self.assertTrue(records[0]['substitute_only'])

    def test_multiple_selected_slots_and_zero_substitute_capacity(self):
        self.platform.update(self.admin,self.a.id,{'external_slot_keys':[self.slots[1]['slot_key'],self.slots[2]['slot_key']]})
        weekend=self.service().enroll('1001','ignored',day='2026-10-17',number=1)
        self.assertEqual(weekend['registrations'][0]['category'],'替补')
        self.all.save_session_constraints(self.target['id'],{'substitute_capacity':0})
        self.enroll()
        self.assertEqual(self.active()[0]['category'],'正式')
