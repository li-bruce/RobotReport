import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db import connect, initialize
from app.commands import Commands
from app.main import create_app
from app.members import Members
from app.membership_batch import MembershipBatch
from app.membership_terms import MembershipTerms
from app.scheduler import Scheduler
from app.service import Service
from tests.test_core import CoreFixture, TOKEN
from tests import test_current_day_enrollment as current_day


class ReserveOptionsTests(CoreFixture):
    actor = current_day.CurrentDayQueueTests.actor
    book = current_day.CurrentDayQueueTests.book
    rows = current_day.CurrentDayQueueTests.rows

    def setUp(self):
        # Share the setup without re-running inherited tests.
        CoreFixture.setUp(self)
        self.all = Service(self.platform.workspace_context(), self.clock)
        slots = self.all.slots()
        slots[1].update(capacity=2, substitute_capacity=3)
        self.all.save_slots(1, slots)
        for i, bot in enumerate(self.bots):
            self.platform.update(self.admin, bot['id'], {'slot_keys': [slots[0]['slot_key']],
                'allow_external_substitute': True, 'external_slot_keys': [slots[1]['slot_key']],
                'external_auto_promote': bool(i)})
        self.day = '2026-10-13'
        self.target = self.all.sessions(self.day)[1]

    def toggle(self, index, value):
        return self.platform.update(self.admin, self.bots[index]['id'], {'external_auto_promote': value})

    def test_bot_origin_controls_shared_read_and_scheduler_not_reader(self):
        self.book('1001', guests=1)
        self.book('1002', True)
        self.assertEqual([r['category'] for r in self.rows()], ['替补','替补','替补'])
        self.now += timedelta(days=1)
        Scheduler(self.platform, clock=self.clock).tick()
        for source in (self.actor('1001'), self.actor('1002', True), self.platform.workspace_context()):
            data = Service(source,self.clock).registrations(self.day,1,self.target['id'],registration=True)
            self.assertEqual([r['user_id'] for r in data['registrations'] if r['category']=='正式'], ['1002'])
        self.assertEqual({r['source_bot_id'] for r in self.rows() if r['user_id']=='1001'}, {self.bots[0]['id']})
        self.toggle(0, True)
        self.assertEqual([(r['user_id'],r['guest_index']) for r in self.rows() if r['category']=='正式'], [('1001',0),('1002',0)])
        self.toggle(0, False)
        self.assertEqual(sum(r['category']=='正式' for r in self.rows()),2)
        Service(self.actor('1001'),self.clock).cancel('1001',day=self.day,number=2)
        self.book('1001')
        self.assertEqual([r['user_id'] for r in self.rows() if r['category']=='正式'], ['1002'])

    def test_primary_reserves_still_promote_and_repeat_does_not_change_origin(self):
        self.book('1001')
        slots = self.all.slots()
        self.platform.update(self.admin,self.bots[1]['id'],{'slot_keys':[slots[1]['slot_key']],
            'allow_external_substitute':False,'external_slot_keys':[],'external_auto_promote':False})
        self.book('1001',True)  # Already active; this is not a new booking.
        self.book('1002',True,guests=2)
        self.now += timedelta(days=1)
        rows=self.rows()
        self.assertEqual([r['user_id'] for r in rows if r['category']=='正式'], ['1002','1002'])
        Service(self.actor('1002',True),self.clock).cancel('1002',day=self.day,number=2)
        self.assertEqual([(r['user_id'],r['guest_index']) for r in self.rows() if r['category']=='正式'], [('1002',1),('1002',2)])
        self.assertEqual(next(r for r in self.rows() if r['user_id']=='1001')['source_bot_id'],self.bots[0]['id'])

    def test_old_origin_backfill_preserves_all_old_values_and_history(self):
        self.book('1001',guests=1)
        with connect(self.ctx.db_path,True) as db:
            db.execute('ALTER TABLE registrations DROP COLUMN source_bot_id')
            columns=[r['name'] for r in db.execute('PRAGMA table_info(registrations)')]
            before=[tuple(r) for r in db.execute('SELECT * FROM registrations')]
        initialize(self.ctx.db_path)
        with connect(self.ctx.db_path) as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT '+','.join(columns)+' FROM registrations')])
            self.assertEqual({r[0] for r in db.execute('SELECT source_bot_id FROM registrations')},{self.bots[0]['id']})
        self.now+=timedelta(days=1)
        self.assertEqual([r['category'] for r in self.rows()],['替补','替补'])
        self.now+=timedelta(days=1)
        self.toggle(0,True)
        self.assertEqual([r['category'] for r in self.rows()],['替补','替补'])

    def test_setting_validation_persistence_and_context(self):
        for bad in ('false', 0, 1, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):self.toggle(0,bad)
        self.assertFalse(self.actor('1001').external_auto_promote)
        self.assertIn('保持替补席位',asyncio.run(Commands(self.actor('1001'),self.clock).run('帮助 报名')))
        self.assertTrue(self.toggle(0,True)['external_auto_promote'])
        self.assertTrue(self.actor('1001').external_auto_promote)
        self.assertIn('按报名顺序递补',asyncio.run(Commands(self.actor('1001'),self.clock).run('帮助 报名')))


class MembershipBatchTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.batch=MembershipBatch(self.ctx,self.clock)
        self.terms=MembershipTerms(self.ctx,self.clock)
        self.payload={'user_ids':['1001','1002'],'period':{'start_date':'2026-11-01','end_date':'2027-10-31','amount':'200.00','note':'年度会籍'}}

    def apply(self, preview=None):
        preview=preview or self.batch.preview(self.payload)
        return self.batch.apply({**self.payload,'expected_revision':preview['revision']})

    def test_preview_is_readonly_then_atomic_apply_preserves_status_and_audit(self):
        preview=self.batch.preview(self.payload)
        self.assertTrue(preview['can_apply'])
        self.assertEqual(self.terms.list('1001'),[])
        self.assertEqual(self.apply(preview)['count'],2)
        for user in self.payload['user_ids']:
            self.assertEqual(Members(self.ctx,self.clock).get(user)['status'],'approved')
            self.assertEqual(self.terms.list(user)[0]['amount'],'200.00')
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM membership_term_audit WHERE action='term.add'").fetchone()[0],2)
        with self.assertRaisesRegex(ValueError,'重新预览'):self.apply(preview)

    def test_overlap_pending_and_missing_block_whole_batch(self):
        self.terms.create('1002',self.payload['period'])
        p=self.batch.preview(self.payload)
        self.assertFalse(p['can_apply']);self.assertIn('重叠',p['rows'][1]['error'])
        with self.assertRaises(ValueError):self.apply(p)
        self.assertEqual(self.terms.list('1001'),[])
        Members(self.ctx,self.clock).review('1002','pending')
        self.assertFalse(self.batch.preview(self.payload)['can_apply'])
        missing=self.batch.preview({**self.payload,'user_ids':['missing']})
        self.assertEqual(missing['rows'][0]['error'],'会员不存在')

    def test_stale_preview_and_tampering_write_nothing(self):
        p=self.batch.preview(self.payload)
        self.terms.create('1002',{'start_date':'2025-01-01','end_date':'2025-12-31'})
        with self.assertRaisesRegex(ValueError,'重新预览'):self.apply(p)
        self.assertEqual(self.terms.list('1001'),[])
        p=self.batch.preview(self.payload)
        changed={**self.payload,'period':{**self.payload['period'],'end_date':'2028-10-31'}}
        with self.assertRaisesRegex(ValueError,'重新预览'):self.batch.apply({**changed,'expected_revision':p['revision']})

    def test_permission_input_validation_and_late_failure_rollback(self):
        for method in ('preview','apply'):
            with self.assertRaises(PermissionError):getattr(MembershipBatch(self.member,self.clock),method)(self.payload)
        for users in ([],['1001','1001'],[1],['']):
            with self.subTest(users=users),self.assertRaises(ValueError):self.batch.preview({**self.payload,'user_ids':users})
        preview=self.batch.preview(self.payload)
        for revision in ('', '汉'*64, 'g'*64, None):
            with self.subTest(revision=revision),self.assertRaisesRegex(ValueError,'先预览'):
                self.batch.apply({**self.payload,'expected_revision':revision})
        real=self.batch.terms.create
        def fail_second(user,*args,**kwargs):
            if user=='1002':raise ValueError('simulated failure')
            return real(user,*args,**kwargs)
        with patch.object(self.batch.terms,'create',side_effect=fail_second),self.assertRaises(ValueError):self.apply(preview)
        self.assertEqual(self.terms.list('1001'),[])

    def test_concurrent_confirmation_creates_exactly_one_period_per_member(self):
        preview=self.batch.preview(self.payload)
        def apply(_):
            try:return self.apply(preview)['count']
            except ValueError:return 0
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sorted(pool.map(apply,range(2))),[0,2])
        self.assertEqual(len(self.terms.list('1001')),1)

    def test_authenticated_api_preview_apply_and_unauthenticated_rejection(self):
        app=create_app(self.platform.root,TOKEN,False,live=False,clock=self.clock)
        with TestClient(app) as client:
            path='/api/membership/periods/batch-preview'
            self.assertEqual(client.post(path,json=self.payload).status_code,401)
            headers={'Authorization':'Bearer '+TOKEN}
            response=client.post(path,json=self.payload,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            response=client.post('/api/membership/periods/batch',json={**self.payload,'expected_revision':response.json()['revision']},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['count'],2)
