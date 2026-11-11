import asyncio
import json
from dataclasses import replace

from app.commands import Commands
from app.db import connect
from app.feedback import catalog, enabled, require
from app.identity_notifications import IdentityNotifications
from app.message_parser import message_input, normalize_message
from app.messages import Outbox
from app.notifications import Notifications
from tests.test_core import CoreFixture


class RobotWorkspaceNotificationsTests(CoreFixture):
    def test_display_name_changes_do_not_change_identity_or_group_and_preserve_arguments(self):
        original=self.platform.bot(self.member.id)
        self.platform.update(self.admin,self.member.id,{'name':'内部登记名'})
        router=Commands(self.member,self.clock)
        for name in ['内部登记名','喂-一组专用','喂 - 一组专用','一组助理']:
            reply=asyncio.run(router.run('@'+name+' 名单'))
            self.assertIn('无人报名',reply)
        current=self.platform.bot(self.member.id)
        self.assertEqual(current['callback_hash'],original['callback_hash'])
        self.assertEqual(current['id'],original['id']);self.assertEqual(current['chat_id'],original['chat_id'])
        self.assertIn('无人报名',asyncio.run(router.run('@未登记的机器人 名单')))
        # Structured addressing does not depend on any display name.
        self.assertEqual(normalize_message(message_input('@任意名字 报名','mentions',[{'start':0,'end':5,'kind':'bot'}]),'内部登记名').text,'报名')
        self.assertEqual(normalize_message('@一组助理 报名，内部登记名','内部登记名').text,'报名，内部登记名')

    def test_alias_property_is_removed_and_structured_names_need_no_configuration(self):
        for aliases in ('name',['@bad'],['bad\nname'],['x'*81],['<at>']):
            with self.assertRaises(ValueError):self.platform.update(self.admin,self.member.id,{'aliases':aliases})
        self.assertNotIn('aliases',self.platform.visible_bot(self.platform.bot(self.member.id),self.admin))
        text=normalize_message(message_input('@A B 报名','mentions',[{'start':0,'end':4,'kind':'bot'}]),'A').text
        self.assertEqual(text,'报名')

    def test_feedback_configuration_is_independent_from_commands_and_roles(self):
        self.platform.update(self.admin,self.member.id,{'capabilities':['enrollment'],'notification_types':['member_profile','role_change']})
        ctx=self.platform.context_for_channel(self.member.channel_id,'1001',False,'callback')
        self.assertTrue(enabled(ctx,'member_profile'));self.assertTrue(enabled(ctx,'role_change'))
        self.assertFalse(enabled(ctx,'registration_result'));self.assertFalse(enabled(ctx,'activity_open'))
        with self.assertRaises(PermissionError):asyncio.run(Commands(ctx,self.clock).run('待审核'))
        self.assertEqual([item['id'] for item in catalog() if item['required']],['announcement'])

    def test_announcement_is_mandatory_group_deduped_and_survives_unrelated_scope_changes(self):
        for bot in self.bots:self.platform.update(self.admin,bot['id'],{'capabilities':[],'notification_types':[]})
        notices=Notifications(self.platform,self.clock)
        channels=[b['channels'][0]['id'] for b in self.bots]
        job=notices.enqueue('全体公告',channels,notification_type='announcement')
        self.assertEqual(len(job['deliveries']),1)
        ctx=self.platform.context_for_channel(job['deliveries'][0]['channel_id'])
        self.platform.update(self.admin,ctx.id,{'venue_ids':[],'slot_keys':[],'capabilities':['registration']})
        self.assertEqual(asyncio.run(Outbox(ctx).deliver_one(self.platform,at=self.now)),'simulated')
        self.assertEqual(sum(len(Outbox(self.platform.context_for_channel(c)).list()) for c in channels),1)

    def test_feedback_revocation_is_checked_after_enqueue_before_send(self):
        self.platform.update(self.admin,self.member.id,{'notification_types':['activity_roster']})
        notices=Notifications(self.platform,self.clock)
        session=self.s.sessions()[0]
        job=notices.enqueue('活动名单',[self.member.channel_id],venue_id=1,session_ids=[session['id']],notification_type='activity_roster')
        self.assertEqual(job['deliveries'][0]['status'],'queued')
        self.platform.update(self.admin,self.member.id,{'notification_types':[]})
        self.assertEqual(asyncio.run(Outbox(self.member).deliver_one(self.platform,at=self.now)),'failed')
        self.assertEqual(Outbox(self.member).list()[0]['attempts'],0)

    def test_disabled_feedback_sender_does_not_shadow_enabled_bot_in_same_group(self):
        self.platform.update(self.admin,self.bots[0]['id'],{'notification_types':[]})
        self.platform.update(self.admin,self.bots[1]['id'],{'notification_types':['activity_roster']})
        notices=Notifications(self.platform,self.clock);session=self.s.sessions()[0]
        job=notices.enqueue('名单',[b['channels'][0]['id'] for b in self.bots],venue_id=1,session_ids=[session['id']],notification_type='activity_roster')
        self.assertEqual(job['channel_ids'],[self.bots[1]['channels'][0]['id']])

    def test_legacy_feedback_defaults_remain_derived_until_saved(self):
        self.platform.update(self.admin,self.member.id,{'capabilities':['registration','enrollment']})
        ctx=self.platform.context_for_channel(self.member.channel_id)
        self.assertTrue(enabled(ctx,'registration_result'));self.assertFalse(enabled(ctx,'member_profile'))
        self.platform.update(self.admin,self.member.id,{'notification_types':[]})
        ctx=self.platform.context_for_channel(self.member.channel_id)
        self.assertFalse(enabled(ctx,'registration_result'));self.assertTrue(enabled(ctx,'announcement'))
        with self.assertRaises(ValueError):self.platform.update(self.admin,self.member.id,{'notification_types':['unknown']})

    def test_spoofed_announcement_key_does_not_bypass_scope_revocation(self):
        # An arbitrary queue key cannot acquire a durable notification allocation.
        Notifications(self.platform,self.clock)
        Outbox(self.member).enqueue('stale private reply','notice:manual:made-up')
        self.platform.update(self.admin,self.member.id,{'venue_ids':[],'slot_keys':[]})
        self.assertEqual(asyncio.run(Outbox(self.member).deliver_one(self.platform,at=self.now)),'failed')

    def test_scheduler_can_send_explicit_feedback_without_enrollment_commands(self):
        from app.scheduler import Scheduler
        first,second=self.bots
        self.platform.update(self.admin,first['id'],{'capabilities':[],'notification_types':[]})
        self.platform.update(self.admin,second['id'],{'capabilities':[],'notification_types':['activity_roster']})
        self.s.update_config({'daily_enabled':True,'broadcast_rules':[{'id':'independent','name':'名单','enabled':True,'kinds':['roster'],'bot_ids':[b['id'] for b in self.bots],'time':'18:00','when':{}}]})
        self.now=self.now.replace(hour=17,minute=59);scheduler=Scheduler(self.platform,False,self.clock)
        self.now=self.now.replace(hour=18,minute=0);scheduler.tick()
        with connect(self.ctx.db_path) as db:jobs=[dict(r) for r in db.execute('SELECT * FROM notification_jobs')]
        self.assertEqual(len(jobs),1)
        self.assertEqual(json.loads(jobs[0]['channel_ids']),[second['channels'][0]['id']])
        ctx=self.platform.context_for_channel(second['channels'][0]['id'])
        self.assertEqual(asyncio.run(Outbox(ctx).deliver_one(self.platform,at=self.now)),'simulated')

    def test_disabled_occupancy_feedback_does_not_consume_group_allocation(self):
        from app.availability import Availability,dispatch_notices
        first,second=self.bots
        self.platform.update(self.admin,first['id'],{'notification_types':[]})
        self.platform.update(self.admin,second['id'],{'capabilities':[],'notification_types':['occupancy']})
        Availability(self.platform.workspace_context(),self.clock).save({'venue_id':1,'date':self.now.date().isoformat(),'start':'18:00','end':'20:00','title':'场馆维护'})
        dispatch_notices(self.platform,self.clock)
        with connect(self.ctx.db_path) as db:jobs=[dict(r) for r in db.execute('SELECT * FROM notification_jobs')]
        self.assertEqual(len(jobs),1)
        self.assertEqual(json.loads(jobs[0]['channel_ids']),[second['channels'][0]['id']])
