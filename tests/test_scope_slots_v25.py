import asyncio
import json
from dataclasses import replace
from app.db import connect
from app.service import Service
from app.notifications import Notifications
from app.scheduler import Scheduler
from app.messages import Outbox
from app.scope import snapshot
from app.commands import Commands
from tests.test_core import CoreFixture


class ScopeAndSlotsTests(CoreFixture):
    def workspace(self):
        return Service(self.platform.workspace_context(),self.clock)

    def restrict(self, **rules):
        return self.platform.update(self.admin,self.ctx.id,rules)

    def test_robot_owns_venue_scope_and_explicit_empty(self):
        second=self.workspace().save_venue({'name':'二号场'})['id']
        self.platform.save_channel(self.admin,self.ctx.id,{'venue_ids':[1], 'default_venue_id':1},self.ctx.channel_id)
        self.restrict(venue_ids=[second])
        self.assertEqual([v['id'] for v in self.s.venues()],[second])
        with self.assertRaises(PermissionError):self.s.venue(1)
        self.restrict(venue_ids=None)
        self.assertEqual([v['id'] for v in self.s.venues()],[1,second])
        self.restrict(venue_ids=[])
        self.assertEqual(self.s.venues(),[])
        self.restrict(venue_ids=[second])
        self.platform.archive_channel(self.admin,self.ctx.id,self.ctx.channel_id)
        other=self.platform.save_channel(self.admin,self.ctx.id,{'name':'新群','chat_id':'888','robot_key':'k'})
        otherctx=self.platform.context_for_channel(other['id'])
        self.assertEqual(otherctx.default_venue_id,second)
        self.assertEqual([v['id'] for v in Service(otherctx,self.clock).venues()],[second])

    def test_slot_scope_preserves_global_session_number_and_filters_stats(self):
        sessions=self.s.sessions()
        self.s.enroll('1001','名字',session_id=sessions[0]['id'])
        self.s.enroll('1001','名字',session_id=sessions[1]['id'])
        self.restrict(slot_keys=[sessions[1]['slot_key']])
        selected=self.s.sessions()
        self.assertEqual([s['number'] for s in selected],[2])
        self.assertEqual(self.s.stats(user_id='1001')['summary']['registrations'],1)
        with self.assertRaises(ValueError):self.s.enroll('1001','名字',session_id=sessions[0]['id'])
        self.restrict(slot_keys=[])
        self.assertEqual(self.s.sessions(),[])
        self.assertEqual(self.s.stats(user_id='1001')['summary']['registrations'],0)

    def test_slot_keys_are_not_reassigned_after_reorder_or_new_rule(self):
        rules=self.s.slots()
        oldkey=rules[0]['slot_key']
        self.restrict(slot_keys=[oldkey])
        updated=self.workspace().save_slots(1,list(reversed(rules))+[
            {'name':'午场','start':'12:00','end':'13:00','weekdays':[0]}])
        self.assertEqual(next(r for r in updated if r['name']=='第一场')['slot_key'],oldkey)
        rows=self.s.sessions()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['name'],'第一场')
        self.assertEqual(rows[0]['number'],2)

    def test_future_bookings_follow_time_change_with_original_snapshot(self):
        old=self.s.sessions('2026-10-13')[0]
        self.s.enroll('1001','小李',day=old['date'],session_id=old['id'])
        rules=self.s.slots();rules[0].update(name='新第一场',start='18:00',end='20:30')
        self.s.save_slots(1,rules)
        new=self.s.sessions(old['date'])[0]
        self.assertEqual(new['id'],old['id'])
        self.assertEqual(new['start'],'18:00')
        row=self.s.registrations(old['date'])['registrations'][0]
        self.assertEqual(row['session_snapshot']['start'],'17:30')
        self.assertEqual(row['session_snapshot']['name'],'第一场')
        with connect(self.ctx.db_path) as db:
            change=db.execute('SELECT * FROM session_changes WHERE session_id=?',(old['id'],)).fetchone()
            self.assertEqual(json.loads(change['before_json'])['start'],'17:30')
            self.assertEqual(json.loads(change['after_json'])['start'],'18:00')

    def test_started_and_past_snapshots_are_unchanged(self):
        today=self.s.sessions()[0]
        self.s.enroll('1001','小李',session_id=today['id'])
        self.now=self.now.replace(hour=18)
        rules=self.s.slots();rules[0].update(name='未来时段',start='18:30')
        self.s.save_slots(1,rules)
        self.assertEqual(self.s.sessions()[0]['start'],'17:30')
        self.now=self.now.replace(day=13,hour=12)
        self.assertEqual(self.s.sessions('2026-10-12')[0]['name'],'第一场')
        self.assertEqual(self.s.sessions()[0]['start'],'18:30')
        self.workspace().save_venue({'name':'新馆名'},1)
        self.assertEqual(self.s.sessions('2026-10-12')[0]['venue_name'],today['venue_name'])

    def test_booked_slot_removal_rejected_atomically(self):
        old=self.s.sessions('2026-10-13')[0]
        self.s.enroll('1001','小李',day=old['date'],session_id=old['id'])
        rules=self.s.slots()
        with self.assertRaisesRegex(ValueError,'报名历史'):
            self.s.save_slots(1,rules[1:])
        self.assertEqual(self.s.slots(),rules)

    def test_future_competition_lock_is_explicit(self):
        from app.competitions import TournamentService
        TournamentService(self.ctx.db_path)
        old=self.s.sessions('2026-10-13')[0]
        with connect(self.ctx.db_path,True) as db:
            db.execute("INSERT INTO tour_events(title,kind,format,venue_id,chat_id,date,status,creator,options,seed,created_at,updated_at) VALUES('测试锁定赛事','daily','round_robin',1,'club','2026-10-13','draft','1001',?,1,'now','now')",(json.dumps({'session_id':old['id']}),))
        rules=self.s.slots();rules[0]['start']='18:00'
        with self.assertRaisesRegex(ValueError,'测试锁定赛事.*锁定|锁定.*测试锁定赛事'):
            self.s.save_slots(1,rules)
        self.assertEqual(self.s.sessions(old['date'])[0]['start'],'17:30')

    def test_special_date_preserves_existing_stable_key_and_scope(self):
        rules=self.s.slots()
        first=rules[0]
        self.s.save_slots(1,[first | {'start':'12:00','end':'13:00'}],'2026-10-13')
        old=self.s.sessions('2026-10-13')[0]
        self.s.enroll('1001','小李',day=old['date'],session_id=old['id'])
        self.restrict(slot_keys=[first['slot_key']])
        self.workspace().save_slots(1,[first | {'start':'13:00','end':'14:00'}],'2026-10-13')
        self.assertEqual(self.s.sessions('2026-10-13')[0]['start'],'13:00')
        self.assertEqual(self.s.registrations('2026-10-13')['registrations'][0]['session_snapshot']['start'],'12:00')

    def test_legacy_channel_slot_scope_does_not_change_robot_scope(self):
        rows=self.s.slots()
        self.restrict(slot_keys=[rows[0]['slot_key']])
        self.platform.save_channel(self.admin,self.ctx.id,{'slot_keys':[rows[1]['slot_key']]},self.ctx.channel_id)
        self.assertEqual([s['name'] for s in self.s.sessions()],['第一场'])
        self.platform.save_channel(self.admin,self.ctx.id,{'slot_keys':None},self.ctx.channel_id)
        self.assertEqual([s['name'] for s in self.s.sessions()],['第一场'])

    def test_queued_roster_and_notification_revoked_scope_never_sent(self):
        sessions=self.s.sessions()
        notifications=Notifications(self.platform,self.clock)
        result=notifications.enqueue('第一场 / 第二场',[self.ctx.channel_id],venue_id=1,session_ids=[s['id'] for s in sessions])
        self.assertEqual(result['status'],'queued')
        self.restrict(slot_keys=[sessions[0]['slot_key']])
        self.assertEqual(asyncio.run(Outbox(self.ctx).deliver_one(self.platform,at=self.now)),'failed')
        out=Outbox(self.ctx)
        out.enqueue('已报名第一场')
        self.restrict(slot_keys=[])
        self.assertEqual(asyncio.run(out.deliver_one(self.platform,at=self.now)),'failed')

    def test_scheduler_only_composes_responsible_sessions(self):
        self.platform.update(self.admin,self.bots[1]['id'],{'active':False})
        self.s.enroll('1001','小李',session_id=self.s.sessions()[1]['id'])
        rows=self.s.slots()
        self.restrict(slot_keys=[rows[1]['slot_key']])
        self.now=self.now.replace(hour=9,minute=29)
        scheduler=Scheduler(self.platform,clock=self.clock)
        self.now=self.now.replace(minute=31)
        scheduler.tick()
        records=Outbox(self.ctx).list()
        self.assertEqual(len(records),1)
        self.assertIn('第二场',records[0]['text'])
        self.assertNotIn('第一场',records[0]['text'])
        self.now=self.now.replace(hour=17,minute=31)
        scheduler.tick()
        self.now=self.now.replace(hour=18,minute=1)
        scheduler.tick()
        records=Outbox(self.ctx).list()
        self.assertEqual(len(records),3)
        for row in records:
            self.assertIn('第二场',row['text'])
            self.assertNotIn('第一场',row['text'])

    def test_event_scope_rechecks_current_limits(self):
        session=self.s.sessions()[0]
        event={'venue_id':1,'options':{'session_id':session['id'],'session_snapshot':snapshot(session)}}
        self.restrict(slot_keys=[session['slot_key']])
        self.assertTrue(self.s.event_allowed(event))
        self.restrict(slot_keys=[])
        self.assertFalse(self.s.event_allowed(event))
        with self.assertRaises(PermissionError):self.s.assert_session_scope(session)

    def test_invalid_scope_does_not_partially_create_bot(self):
        before=len(self.platform.list(self.admin))
        with self.assertRaises(ValueError):self.platform.create(self.admin,{'name':'无效','venue_ids':[999]})
        self.assertEqual(len(self.platform.list(self.admin)),before)

    def test_cross_venue_slot_selection_grants_no_other_venue(self):
        workspace=self.workspace()
        other=workspace.save_venue({'name':'二号'})['id']
        foreign_key=workspace.slots(other)[0]['slot_key']
        self.restrict(venue_ids=[1],slot_keys=[foreign_key])
        self.assertEqual(self.s.sessions(),[])
        with self.assertRaises(PermissionError):self.s.sessions(venue_id=other)
        prior=self.platform.visible_bot(self.platform.bot(self.ctx.id),self.admin)
        for bad in ({'slot_keys':['slot:999999']},{'venue_ids':[True]},{'slot_keys':[1]},{'venue_ids':'all'}):
            with self.assertRaises(ValueError):self.restrict(name='不应保存',**bad)
            self.assertEqual(self.platform.visible_bot(self.platform.bot(self.ctx.id),self.admin),prior)
        self.restrict(venue_ids=None)
        self.assertEqual(self.platform.context_for_channel(self.ctx.channel_id).default_venue_id,other)

    def test_future_time_change_invalidates_queued_old_reminder(self):
        activity=self.s.sessions('2026-10-13')[0]
        Notifications(self.platform,self.clock).enqueue('17:30开始',[self.ctx.channel_id],venue_id=1,session_ids=[activity['id']])
        rules=self.s.slots();rules[0]['start']='18:00'
        self.s.save_slots(1,rules)
        self.assertEqual(asyncio.run(Outbox(self.ctx).deliver_one(self.platform,at=self.now)),'failed')

    def test_stale_composition_scope_cannot_be_relabelled_when_enqueued(self):
        oldctx=self.ctx
        key=self.s.slots()[0]['slot_key']
        self.restrict(slot_keys=[key])
        Outbox(oldctx).enqueue('从旧范围生成的两场名单')
        self.assertEqual(asyncio.run(Outbox(oldctx).deliver_one(self.platform,at=self.now)),'failed')

    def test_command_roster_does_not_report_another_slot(self):
        sessions=self.s.sessions()
        self.s.enroll('1001','名字',session_id=sessions[0]['id'])
        self.s.enroll('1002','名字',session_id=sessions[1]['id'])
        self.restrict(slot_keys=[sessions[1]['slot_key']])
        reply=asyncio.run(Commands(self.member,self.clock).run('名单'))
        self.assertIn('第二场',reply)
        self.assertNotIn('第一场',reply)
        self.assertIn('球友1002',reply)
        self.assertNotIn('小李',reply)

    def test_ai_context_contains_only_current_scope_events(self):
        from app.competitions import TournamentService
        TournamentService(self.ctx.db_path)
        sessions=self.s.sessions()
        with connect(self.ctx.db_path,True) as db:
            for session in sessions:
                db.execute("INSERT INTO tour_events(title,kind,format,venue_id,chat_id,date,status,creator,options,seed,created_at,updated_at) VALUES(?,'daily','round_robin',1,'club','2026-10-12','draft','1001',?,1,'now','now')",(session['name']+'比赛',json.dumps({'session_id':session['id'],'session_snapshot':snapshot(session)})))
        self.restrict(slot_keys=[sessions[1]['slot_key']])
        context=Commands(self.member,self.clock).ai_context(1)
        self.assertEqual([e['title'] for e in context['events']],['第二场比赛'])
