"""Identity messages are committed with the change, and never broadcast blindly."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.db import TZ, connect, initialize
from app.identity_notifications import IdentityNotifications, record_identity_change
from app.members import Members
from app.messages import Outbox
from app.scheduler import Scheduler
from app.security import Platform
from app.bot_capabilities import ALL_CAPABILITIES


class IdentityNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.at = datetime(2026, 10, 19, 12, 0, tzinfo=TZ)
        self.clock = lambda: self.at
        self.platform = Platform(Path(self.temp.name), 'identity-notice-test-token-0000000')
        self.principal = self.platform.authenticate('identity-notice-test-token-0000000')
        self.admin = self.platform.workspace_context(actor='admin-test', source='admin')
        self.bots = []
        for i, chat in enumerate(('known-room', 'known-room', 'second-room', 'unrelated-room')):
            self.bots.append(self.platform.create(self.principal, {
                'name': '机器人' + str(i), 'capabilities': list(ALL_CAPABILITIES), 'chat_id': chat, 'robot_key': 'test-key-' + str(i),
                'webhook_url': 'https://xz.wps.cn/api/v1/webhook/send?key=notice-test-' + str(i)}))
        self.channels = [b['channels'][0]['id'] for b in self.bots]
        self.notices = IdentityNotifications(self.platform, self.clock)

    def ctx(self, index=0, user='person-one', source='callback'):
        return self.platform.context_for_channel(self.channels[index], user, False, source)

    def user(self, observed=(0,)):
        if not observed:
            Members(self.admin, self.clock).touch('张三', 'person-one', 'admin')
        for index in observed:
            Members(self.ctx(index), self.clock).touch('张三')
        return 'person-one'

    def event(self, context=None, kind='membership', before=None, after=None):
        with connect(self.admin.db_path, True) as db:
            return record_identity_change(db, context or self.admin, 'person-one', kind,
                before or {'status': 'pending'}, after or {'status': 'approved', 'name': '张三'}, self.at)

    def outbox(self, index):
        return Outbox(self.ctx(index)).list()

    def caps(self, index, values):
        self.platform.update(self.principal, self.bots[index]['id'], {'capabilities': values})

    def test_personal_results_and_role_notices_use_their_own_business_bots(self):
        self.user((0,))
        self.caps(0, ['registration', 'messages'])
        self.caps(1, ['management'])
        cases = [
            ('membership', {'status':'pending'}, {'status':'approved'}),
            ('roles', {'roles':[]}, {'roles':['member_manager']}),
            ('level', {'level_status':'pending'}, {'level_status':'approved','level':3}),
            ('groups', {'groups':[]}, {'groups':[{'id':1,'name':'会员组'}]}),
            ('profile_request', {'request_id':1,'status':'pending'}, {'request_id':1,'status':'approved','fields':['姓名']}),
        ]
        for kind, before, after in cases:
            with self.subTest(kind=kind):
                expected = 1 if kind == 'roles' else 0
                event_id = self.event(self.ctx(0), kind, before, after)
                item = self.notices.dispatch(event_id)
                self.assertEqual(item['status'], 'queued')
                self.assertEqual([(d['chat_id'],d['channel_id']) for d in item['deliveries']],
                                 [('known-room',self.channels[expected])])
        self.assertEqual(len(self.outbox(1)), 1)
        self.assertEqual(len(self.outbox(0)), len(cases)-1)
        self.assertEqual(self.outbox(2), [])
        self.assertEqual(self.outbox(3), [])

    def test_group_without_management_robot_waits_until_one_is_enabled(self):
        self.user((0,))
        self.caps(0, ['registration'])
        self.caps(1, ['messages'])
        event_id = self.event(kind='roles',before={'roles':[]},after={'roles':['member_manager']})
        item = self.notices.dispatch(event_id)
        self.assertEqual(item['status'], 'blocked')
        self.assertIn('群内管理', item['last_error'])
        self.assertTrue(item['can_retry'])
        # Management robots in other groups must never receive this notice.
        self.assertEqual(sum(len(self.outbox(i)) for i in range(4)), 0)
        self.caps(1, ['management'])
        item = self.notices.retry(event_id)
        self.assertEqual(item['status'], 'queued')
        self.assertEqual(item['deliveries'][0]['channel_id'], self.channels[1])
        self.notices.dispatch(event_id)
        self.assertEqual(len(self.outbox(1)), 1)

    def test_same_group_management_without_webhook_is_not_replaced_by_registration_bot(self):
        self.user((0,))
        self.caps(0, ['registration', 'messages'])
        self.caps(1, ['management'])
        self.platform.save_channel(self.principal,self.bots[1]['id'],{'webhook_url':''},self.channels[1])
        event_id = self.event(kind='roles',before={'roles':[]},after={'roles':['member_manager']})
        self.assertEqual(self.notices.dispatch(event_id)['status'], 'blocked')
        self.assertEqual(sum(len(self.outbox(i)) for i in range(4)), 0)

    def legacy_notice(self):
        self.user((0,))
        self.caps(0, ['registration'])
        event_id = self.event(self.ctx(0),kind='roles',before={'roles':[]},after={'roles':['member_manager']})
        # A role notification on a registration-only sender must stay blocked.
        with patch.object(IdentityNotifications, '_can_notify', return_value=True):
            item = self.notices.dispatch(event_id)
        self.assertEqual(item['deliveries'][0]['channel_id'], self.channels[0])
        return event_id

    def test_legacy_queue_is_blocked_at_send_even_when_scope_has_not_changed(self):
        event_id = self.legacy_notice()
        calls = []
        async def run():
            async def handler(request):
                calls.append(request)
                return httpx.Response(200,json={'result':'ok'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await Outbox(self.ctx(0)).deliver_one(self.platform,live=True,client=client,at=self.at)
        self.assertEqual(asyncio.run(run()), 'failed')
        self.assertEqual(calls, [])
        row = self.outbox(0)[0]
        self.assertEqual(row['attempts'], 0)
        self.assertIn('群内管理', row['last_error'])
        self.assertEqual(self.notices.get(event_id)['status'], 'failed')
        self.assertEqual(self.outbox(1), [])

    def test_legacy_queue_recovery_stops_pending_notice_without_switching_sender(self):
        event_id = self.legacy_notice()
        self.notices.recover()
        item = self.notices.get(event_id)
        self.assertEqual(item['status'], 'failed')
        self.assertIn('群内管理', item['last_error'])
        self.assertEqual(self.outbox(0)[0]['attempts'], 0)
        self.assertFalse(item['can_retry'])
        self.assertEqual(self.outbox(1), [])

    def test_management_revocation_after_enqueue_stops_notice(self):
        self.user((0,))
        event_id = self.event(self.ctx(0),kind='roles',before={'roles':[]},after={'roles':['member_manager']})
        self.notices.dispatch(event_id)
        self.caps(0, ['registration', 'messages'])
        item = self.notices.get(event_id)
        self.assertEqual(item['status'], 'failed')
        self.assertIn('群内管理', item['last_error'])
        self.assertEqual(self.outbox(0)[0]['attempts'], 0)
        self.assertEqual(self.outbox(1), [])

    def test_registration_result_sends_after_management_is_disabled(self):
        self.user((0,))
        event_id = self.event(self.ctx(0))
        self.notices.dispatch(event_id)
        self.caps(0,['registration'])
        self.assertEqual(self.notices.get(event_id)['status'],'queued')
        calls=[]
        async def send():
            async def handler(request):
                calls.append(json.loads(request.content))
                return httpx.Response(200,json={'result':'ok'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await Outbox(self.ctx(0)).deliver_one(self.platform,live=True,client=client,at=self.at)
        self.assertEqual(asyncio.run(send()),'sent')
        self.assertEqual(len(calls),1)
        self.assertIn('注册审核已通过',json.dumps(calls,ensure_ascii=False))
        self.assertEqual(self.notices.get(event_id)['status'],'sent')
        self.assertEqual(self.outbox(1),[])

    def test_missing_registration_waits_then_recovers_without_management(self):
        self.user((0,))
        self.caps(0,['messages']);self.caps(1,['management'])
        event_id=self.event()
        self.assertEqual(self.notices.dispatch(event_id)['status'],'blocked')
        self.assertIn('会员注册',self.notices.get(event_id)['last_error'])
        self.caps(0,['registration'])
        self.at+=timedelta(seconds=61)
        self.notices.recover()
        self.assertEqual(self.notices.get(event_id)['status'],'queued')
        self.assertEqual(self.notices.get(event_id)['deliveries'][0]['channel_id'],self.channels[0])
        self.notices.recover();self.assertEqual(len(self.outbox(0)),1)
        self.assertEqual(self.outbox(1),[])

    def test_registration_revocation_blocks_personal_receipt_before_any_send(self):
        self.user((0,))
        event_id=self.event(self.ctx(0));self.notices.dispatch(event_id)
        self.caps(0,['management'])
        self.assertEqual(asyncio.run(Outbox(self.ctx(0)).deliver_one(self.platform,at=self.at)),'failed')
        self.assertEqual(self.outbox(0)[0]['attempts'],0)
        self.assertIn('会员注册',self.outbox(0)[0]['last_error'])
        self.assertEqual(self.notices.get(event_id)['status'],'failed')

    def test_personal_receipt_respects_registration_time_window(self):
        self.user((0,))
        event_id=self.event(self.ctx(0));self.notices.dispatch(event_id)
        self.platform.update(self.principal,self.bots[0]['id'],{'capability_rules':[
            {'id':'off','name':'暂停注册','enabled':True,'capabilities':['registration'],'action':'closed',
             'when':{'start_time':'12:00','end_time':'13:00'}}]})
        self.assertEqual(asyncio.run(Outbox(self.ctx(0)).deliver_one(self.platform,at=self.at)),'failed')
        self.assertEqual(self.outbox(0)[0]['attempts'],0)

    def test_identity_key_cannot_bypass_ordinary_message_scope_without_record(self):
        self.caps(0,['registration'])
        Outbox(self.ctx(0)).enqueue('无有效通知记录','identity:missing-event')
        self.assertEqual(asyncio.run(Outbox(self.ctx(0)).deliver_one(self.platform,at=self.at)),'failed')
        self.assertEqual(self.outbox(0)[0]['attempts'],0)

    def test_recorded_event_cannot_be_sent_by_a_different_or_unrelated_channel(self):
        self.user((0,))
        event_id=self.event(self.ctx(0));self.notices.dispatch(event_id)
        for index in (1,3):
            Outbox(self.ctx(index)).enqueue('错误发送通道','identity:'+event_id)
            self.assertEqual(asyncio.run(Outbox(self.ctx(index)).deliver_one(self.platform,at=self.at)),'failed')
            self.assertEqual(self.outbox(index)[0]['attempts'],0)

    def test_activity_scope_change_does_not_hide_an_approved_personal_result(self):
        self.user((0,))
        event_id=self.event(self.ctx(0));self.notices.dispatch(event_id)
        self.platform.update(self.principal,self.bots[0]['id'],{'venue_ids':[],'slot_keys':[]})
        self.assertEqual(asyncio.run(Outbox(self.ctx(0)).deliver_one(self.platform,at=self.at)),'simulated')

    def test_legacy_sent_or_uncertain_notices_are_never_replayed_by_management_bot(self):
        self.user((0,))
        self.caps(0, ['registration'])
        for status in ('sent', 'unknown', 'sending'):
            with self.subTest(status=status):
                event_id = self.legacy_notice()
                with connect(self.ctx(0).transport_db_path, True) as db:
                    db.execute('UPDATE outbox SET status=?,attempts=1 WHERE id=?',
                               (status,self.outbox(0)[0]['id']))
                Outbox(self.ctx(0)).recover()
                item = self.notices.get(event_id)
                self.assertEqual(item['status'], 'unknown' if status == 'sending' else status)
                self.assertFalse(item['can_retry'])
                self.notices.dispatch(event_id)
        self.assertEqual(self.outbox(1), [])

    def test_hook_shares_transaction_and_strips_private_fields(self):
        self.user()
        with self.assertRaises(RuntimeError):
            with connect(self.admin.db_path, True) as db:
                record_identity_change(db,self.admin,'person-one','membership',{'status':'pending'},
                    {'status':'approved','phone':'13812345678','note':'内部审核备注'},self.at)
                raise RuntimeError('rollback')
        self.assertEqual(self.notices.list(), [])
        event_id = self.event(after={'status':'approved','name':'张三','phone':'13812345678','note':'内部审核备注'})
        with connect(self.admin.db_path) as db:
            raw = json.dumps(dict(db.execute('SELECT * FROM identity_notification_events WHERE id=?',(event_id,)).fetchone()), ensure_ascii=False)
        self.assertNotIn('13812345678', raw)
        self.assertNotIn('内部审核备注', raw)
        self.assertEqual(sum(len(self.outbox(i)) for i in range(4)), 0)

    def test_no_change_does_not_duplicate_event(self):
        self.user()
        self.assertIsNone(self.event(before={'status':'approved'},after={'status':'approved','name':'不同显示姓名'}))
        self.assertIsNone(self.event(kind='roles', before={'roles':['member_manager','club_admin']},
                                     after={'roles':['club_admin','member_manager','club_admin']}))
        self.assertEqual(self.notices.list(), [])

    def test_real_review_hook_queues_once_after_commit(self):
        self.user()
        manager = Members(self.admin, self.clock)
        manager.review('person-one', 'approved')
        manager.review('person-one', 'approved')
        self.assertEqual(len(self.notices.list()), 1)
        self.notices.recover()
        items = self.notices.list()
        self.assertEqual(items[0]['status'], 'queued')
        delivery = items[0]['deliveries'][0]
        index = self.channels.index(delivery['channel_id'])
        payload = self.outbox(index)[0]['payload']
        self.assertEqual(payload['msgtype'], 'markdown')
        self.assertIn('<at user_id="person-one">张三</at>', payload['markdown']['text'])
        self.assertIn('注册审核已通过', payload['markdown']['text'])

    def test_web_approval_only_related_groups_and_same_group_one_robot(self):
        self.user((0, 1, 2))
        event_id = self.event()
        item = self.notices.dispatch(event_id)
        self.assertEqual({d['chat_id'] for d in item['deliveries']}, {'known-room','second-room'})
        self.assertEqual(sum(len(self.outbox(i)) for i in (0,1)), 1)
        self.assertEqual(len(self.outbox(2)), 1)
        self.assertEqual(self.outbox(3), [])

    def test_group_review_prefers_origin_robot_without_other_group_broadcast(self):
        self.user((0,))
        reviewer = replace(self.ctx(1, 'reviewer'), is_admin=True)
        item = self.notices.dispatch(self.event(reviewer))
        self.assertEqual(item['deliveries'][0]['channel_id'], self.channels[1])
        self.assertEqual(len(self.outbox(1)), 1)
        self.assertEqual(self.outbox(0), [])
        self.assertEqual(self.outbox(2), [])

    def test_unrelated_origin_group_cannot_become_a_recipient(self):
        self.user((0,))
        reviewer = replace(self.ctx(3, 'reviewer'), is_admin=True)
        item = self.notices.dispatch(self.event(reviewer))
        self.assertEqual([d['chat_id'] for d in item['deliveries']], ['known-room'])
        self.assertEqual(self.outbox(3), [])

    def test_unconfigured_preferred_robot_falls_back_before_selection(self):
        self.user((0,))
        self.platform.save_channel(self.principal,self.bots[1]['id'],{'webhook_url':''},self.channels[1])
        item = self.notices.dispatch(self.event(replace(self.ctx(1),is_admin=True)))
        self.assertEqual(item['deliveries'][0]['channel_id'], self.channels[0])

    def test_no_source_waits_then_recovers_after_actual_observation(self):
        self.user(())
        event_id = self.event()
        self.assertEqual(self.notices.get(event_id)['status'], 'pending')
        self.assertEqual(self.notices.dispatch(event_id)['status'], 'blocked')
        self.assertEqual(sum(len(self.outbox(i)) for i in range(4)), 0)
        self.user((2,))
        item = self.notices.retry(event_id, 'admin-test')
        self.assertEqual(item['status'], 'queued')
        self.assertEqual([d['chat_id'] for d in item['deliveries']], ['second-room'])

    def test_missing_webhook_remains_visible_and_safe_to_retry(self):
        self.user((2,))
        self.platform.save_channel(self.principal,self.bots[2]['id'],{'webhook_url':''},self.channels[2])
        event_id = self.event()
        item = self.notices.dispatch(event_id)
        self.assertEqual(item['status'], 'blocked')
        self.assertTrue(item['can_retry'])
        self.assertIn('发送配置',item['last_error'])
        self.platform.save_channel(self.principal,self.bots[2]['id'],{
            'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=restored'},self.channels[2])
        self.assertEqual(self.notices.retry(event_id)['status'], 'queued')

    def test_transport_commit_before_event_receipt_is_idempotent(self):
        self.user((2,))
        event_id = self.event()
        original = Outbox.enqueue
        def crash_after_commit(outbox, *args, **kwargs):
            original(outbox,*args,**kwargs)
            raise RuntimeError('process stopped before business receipt')
        with patch.object(Outbox,'enqueue',crash_after_commit):
            self.assertEqual(self.notices.dispatch(event_id)['status'],'blocked')
        self.assertEqual(len(self.outbox(2)), 1)
        self.assertEqual(self.notices.retry(event_id)['status'], 'queued')
        self.assertEqual(len(self.outbox(2)), 1)

    def test_concurrent_dispatch_keeps_one_group_message(self):
        self.user((2,))
        event_id = self.event()
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(self.notices.dispatch, [event_id,event_id]))
        self.assertEqual(len(self.outbox(2)), 1)

    def test_retry_and_recovery_do_not_switch_a_chosen_sender(self):
        self.user((0,1))
        event_id = self.event(replace(self.ctx(1),is_admin=True))
        self.notices.dispatch(event_id)
        for _ in range(3):
            self.notices.recover()
            self.notices.dispatch(event_id)
        self.assertEqual(len(self.outbox(1)), 1)
        self.assertEqual(self.outbox(0), [])

    def test_real_send_receipt_syncs_and_no_replay(self):
        self.user((2,))
        event_id = self.event()
        self.notices.dispatch(event_id)
        calls = []
        async def run():
            async def handler(request):
                calls.append(request)
                return httpx.Response(200,json={'result':'ok'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                await Outbox(self.ctx(2)).deliver_one(self.platform,live=True,client=client,at=self.at)
        asyncio.run(run())
        item = self.notices.get(event_id)
        self.assertEqual(item['status'],'sent')
        self.assertIsNotNone(item['sent_at'])
        self.assertFalse(item['can_retry'])
        self.notices.recover()
        with self.assertRaises(ValueError):
            self.notices.retry(event_id)
        self.assertEqual(len(calls),1)

    def test_unknown_send_cannot_be_retried_or_switched(self):
        self.user((2,))
        event_id = self.event()
        self.notices.dispatch(event_id)
        async def run():
            async def handler(request):
                raise httpx.ReadTimeout('do not expose webhook details')
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                await Outbox(self.ctx(2)).deliver_one(self.platform,live=True,client=client,at=self.at)
        asyncio.run(run())
        item = self.notices.get(event_id)
        self.assertEqual(item['status'],'unknown')
        self.assertFalse(item['can_retry'])
        self.assertNotIn('webhook',json.dumps(item))
        self.notices.recover()
        with self.assertRaises(ValueError):
            self.notices.retry(event_id)
        self.assertEqual(len(self.outbox(2)),1)

    def test_simulator_event_never_sends_even_after_recovery_or_retry(self):
        self.user()
        event_id = self.event(replace(self.ctx(),source='simulator',is_admin=True))
        self.assertEqual(self.notices.get(event_id)['status'],'suppressed')
        self.notices.dispatch(event_id)
        self.notices.recover()
        with self.assertRaises(ValueError):
            self.notices.retry(event_id)
        self.assertEqual(sum(len(self.outbox(i)) for i in range(4)),0)

    def test_non_live_profile_touches_cannot_create_notification_destinations(self):
        for source in ('simulator','preview','dry_run','admin'):
            with self.subTest(source=source):
                user_id = 'non-live-' + source
                ctx = self.ctx(3,user_id,source)
                # The default name source is 'callback', but it is the trusted
                # context source that determines whether a real user was seen.
                Members(ctx,self.clock).touch('测试会员')
                Members(self.admin,self.clock).review(user_id,'approved')
        with connect(self.admin.db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM identity_observations').fetchone()[0],0)
        self.notices.recover()
        self.assertTrue(all(item['status']=='blocked' for item in self.notices.list()))
        self.assertEqual(sum(len(self.outbox(i)) for i in range(4)),0)

    def test_simulator_does_not_expand_existing_real_groups(self):
        self.user((2,))
        Members(self.ctx(3,source='simulator'),self.clock).touch('张三')
        Members(self.admin,self.clock).review('person-one','approved')
        self.notices.recover()
        self.assertEqual([d['chat_id'] for d in self.notices.list()[0]['deliveries']],['second-room'])
        self.assertEqual(len(self.outbox(2)),1)
        self.assertEqual(self.outbox(3),[])

    def test_trusted_registration_form_and_callback_keep_observation(self):
        Members(self.ctx(2,source='registration_form'),self.clock).touch('张三',source='self')
        Members(self.ctx(0),self.clock).touch('张三')
        with connect(self.admin.db_path) as db:
            groups = {r[0] for r in db.execute('SELECT chat_id FROM identity_observations')}
        self.assertEqual(groups,{'known-room','second-room'})
        self.assertEqual(len(self.notices.dispatch(self.event())['deliveries']),2)

    def test_callback_admin_edit_of_another_person_is_not_an_observation(self):
        self.user(())
        reviewer = replace(self.ctx(3,'reviewer'),is_admin=True,source='admin')
        Members(reviewer,self.clock).touch('张三','person-one',source='admin')
        # Also exercise a legitimately privileged group actor editing someone
        # else's profile. Presence evidence still belongs only to the actor.
        with connect(self.admin.db_path,True) as db:
            db.execute("INSERT INTO profiles VALUES('club','reviewer','审核人')")
            db.execute("INSERT INTO member_records(chat_id,user_id,status,created_at,updated_at) VALUES('club','reviewer','approved','2026-10-19','2026-10-19')")
            db.execute("INSERT INTO user_roles VALUES('reviewer','club_admin','club','club','2026-10-19','system')")
        Members(replace(reviewer,source='callback'),self.clock).touch('张三','person-one',source='admin')
        with connect(self.admin.db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM identity_observations').fetchone()[0],0)

    def test_disabled_queued_channel_is_visible_failure_and_never_switches_sender(self):
        self.user((0,1))
        event_id = self.event(replace(self.ctx(1),is_admin=True))
        self.notices.dispatch(event_id)
        self.platform.save_channel(self.principal,self.bots[1]['id'],{'active':False},self.channels[1])
        item = self.notices.get(event_id)
        self.assertEqual(item['status'],'failed')
        self.assertFalse(item['can_retry'])
        self.assertEqual(self.outbox(0),[])
        with self.assertRaises(ValueError):
            self.notices.retry(event_id)

    def test_rebound_group_never_receives_old_identity_message(self):
        self.user((2,))
        event_id = self.event()
        self.notices.dispatch(event_id)
        with self.assertRaises(ValueError):
            self.platform.save_channel(self.principal,self.bots[2]['id'],{'chat_id':'new-unrelated-room'},self.channels[2])
        # Normal configuration already forbids this. Defend against an old
        # database/manual rebind as well, before any queued payload is sent.
        with connect(self.platform.path, True) as db:
            db.execute('UPDATE bot_channels SET chat_id=? WHERE id=?',('new-unrelated-room',self.channels[2]))
        item = self.notices.get(event_id)
        self.assertEqual(item['status'],'failed')
        self.assertEqual(self.outbox(2)[0]['status'],'failed')

    def test_no_historical_notification_backfill(self):
        self.user()
        with connect(self.admin.db_path, True) as db:
            db.execute("UPDATE member_records SET status='approved'")
        initialize(self.admin.db_path)
        scheduler = Scheduler(self.platform,False,self.clock)
        scheduler.recover()
        self.assertEqual(self.notices.list(),[])
        self.assertEqual(sum(len(self.outbox(i)) for i in range(4)),0)

    def test_scheduler_dispatches_new_identity_events(self):
        self.user((2,))
        event_id = self.event()
        scheduler = Scheduler(self.platform,False,self.clock)
        scheduler.tick()
        self.assertEqual(self.notices.get(event_id)['status'],'queued')
        self.assertEqual(len(self.outbox(2)),1)

    def test_rejection_suspension_roles_levels_and_groups_are_explained_safely(self):
        self.user((2,))
        cases = [
            ('membership',{'status':'pending'},{'status':'rejected','note':'保密审核原因'},'注册审核未通过'),
            ('membership',{'status':'approved'},{'status':'suspended'},'会员资格已停用'),
            ('membership',{'status':'suspended'},{'status':'approved'},'会员资格已恢复'),
            ('roles',{'roles':['club_admin']},{'roles':['member_manager']},'撤销：'),
            ('level',{'level_status':'pending','level':None},{'level_status':'approved','level':7},'7级'),
            ('groups',{'groups':[]},{'groups':[{'id':1,'name':'<script>高级组'}]},'高级组'),
        ]
        for kind,before,after,text in cases:
            event_id = self.event(kind=kind,before=before,after=after)
            self.notices.dispatch(event_id)
            output = self.outbox(2)[0]['text']
            self.assertIn(text, output)
            self.assertNotIn('保密审核原因', output)
            self.assertNotIn('<script>', output)
            self.assertIn('<at user_id="person-one">张三</at>',output)


if __name__ == '__main__':
    unittest.main()
