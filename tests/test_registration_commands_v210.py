"""End-to-end command and callback contract for additive proxy registrations."""
import asyncio
import json
from dataclasses import replace
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.commands import cancellation_guests
from app.db import connect
from app.main import create_app
from app.service import Service
from app.tournaments import Tournaments
from tests.test_core import CoreFixture, TOKEN


class RegistrationCommandsV210(CoreFixture):
    def send(self, text, actor=None):
        return asyncio.run(self.commands(actor).run(text))

    def active(self, day=None, session_id=None):
        return [r for r in self.s.registrations(day, session_id=session_id)['registrations'] if r['status']=='active']

    def test_append_five_keeps_two_named_guests_and_receipt_has_seats(self):
        self.send('报名，张三、李四')
        original={r['name']:r['id'] for r in self.active()}
        reply=self.send('报名+5')
        rows=self.active()
        self.assertEqual(len(rows),8)
        for name in ('张三','李四'):
            self.assertEqual(next(r['id'] for r in rows if r['name']==name),original[name])
        self.assertIn('本人及7位代报名人员',reply)
        self.assertIn('| 姓名 | 性别 | 名额 |',reply)
        self.assertIn('张三（小李代报名）',reply)
        self.assertNotIn('| 序号',reply)
        self.assertNotIn('| 状态',reply)
        self.send('报名')
        self.assertEqual(len(self.active()),8)

    def test_self_then_selected_guest_cancel_preserves_remaining(self):
        self.send('报名，张三，李四，王五')
        reply=self.send('取消报名')
        self.assertIn('3人仍保留',reply)
        self.assertEqual({r['name'] for r in self.active()},{'张三','李四','王五'})
        reply=self.send('取消张三， 王五')
        self.assertIn('2人',reply)
        self.assertEqual([r['name'] for r in self.active()],['李四'])
        self.send('报名')
        self.assertEqual([r['name'] for r in self.active()],['李四','小李'])

    def test_cancel_other_sponsors_name_is_atomic_and_disallowed(self):
        other=replace(self.member,actor='1002')
        self.send('报名，张三')
        self.send('报名，李四',other)
        before=self.active()
        for text in ('取消李四','取消张三，李四'):
            with self.subTest(text=text),self.assertRaises((ValueError,PermissionError)):
                self.send(text)
            self.assertEqual(self.active(),before)

    def test_names_can_use_common_list_separators_without_empty_targets(self):
        for text in ('取消张三,李四','取消 张三，李四','取消代报名：张三、李四'):
            self.assertEqual(cancellation_guests(text),['张三','李四'])
        for text in ('取消','取消张三，','取消张三，张三','取消张三\n李四'):
            with self.subTest(text=text),self.assertRaises(ValueError):cancellation_guests(text)
        self.assertIsNone(cancellation_guests('取消报名'))

    def test_future_session_cancellation_does_not_touch_today(self):
        self.send('报名，张三')
        self.send('2026-10-13 第一场 报名，张三')
        self.send('2026-10-13 第一场 取消张三')
        self.assertIn('张三',[r['name'] for r in self.active()])
        self.assertNotIn('张三',[r['name'] for r in self.active('2026-10-13')])

    def test_names_starting_with_command_labels_are_preserved(self):
        for name in ('代报张三','代报名张三','报名张三','代报验收甲+3'):
            with self.subTest(name=name):
                self.assertEqual(cancellation_guests('取消'+name),[name])
        self.send('报名，代报张三，报名张三')
        self.send('取消代报张三')
        self.assertEqual({r['name'] for r in self.active()},{'小李','报名张三'})
        self.send('取消报名张三')
        self.assertEqual([r['name'] for r in self.active()],['小李'])

    def test_duplicate_callback_does_not_append_again_new_message_does(self):
        app=create_app(self.temp.name,TOKEN,False,False,self.clock)
        body={'robot_key':'robot-test','chatid':266382,'creator':1001,'ctime':123456,'content':'报名+2'}
        path=self.bots[0]['callback_path']
        with TestClient(app) as client:
            for request in (body,body,body|{'ctime':123457}):
                self.assertEqual(client.post(path,json=request).json(),{'result':'ok'})
        self.assertEqual(len(self.active()),5)

    def test_large_event_append_named_guests_and_individual_cancel(self):
        with patch('app.competitions._now',self.clock):
            event=Tournaments(self.ctx,self.clock).create({'kind':'large','title':'周末友谊杯','keyword':'友谊杯','format':'round_robin','date':'2026-10-13',
                'registration_open':'2026-10-12T00:00:00+08:00','registration_close':'2026-10-13T17:00:00+08:00'})
            Tournaments(self.ctx,self.clock).transition(event['id'],'open_registration')
        self.send('周末友谊杯，报名，张三、李四')
        self.send('周末友谊杯，报名+5')
        tournaments=Tournaments(self.member,self.clock)
        self.assertEqual(len(tournaments.get(event['id'])['participants']),8)
        self.send('周末友谊杯，取消报名')
        self.send('周末友谊杯，取消张三，李四')
        remaining=[r for r in tournaments.get(event['id'])['participants'] if r['status']=='active']
        self.assertEqual(len(remaining),5)
        self.assertTrue(all(r['guest_index']>0 for r in remaining))

    def test_stale_guest_snapshot_and_old_single_proposal_cannot_write(self):
        self.send('报名，张三')
        cmd=self.commands();command,before=cmd.snapshot('取消张三')
        self.send('取消张三');self.send('报名，张三')
        self.assertNotEqual(before,cmd.snapshot(command)[1])
        self.s.update_config({'ai':{'enabled':True}})
        proposal={'command':'取消报名','intent':'CancelRegistration','arguments':{},
                  'scope':{'bot_id':self.member.id,'chat_id':self.member.chat_id,'user_id':self.member.actor}}
        with patch('app.ai_gateway.interpret_limited',return_value=proposal):
            reply=self.send('AI 取消本人报名')
        import re
        token=self.pending_match(reply)[1]
        with connect(self.ctx.transport_db_path,True) as db:
            row=db.execute('SELECT command FROM ai_pending WHERE token=?',(token,)).fetchone()
            payload=json.loads(row['command']);self.assertEqual(payload['version'],6)
            payload['version']=3
            db.execute('UPDATE ai_pending SET command=? WHERE token=?',(json.dumps(payload),token))
        current=self.active()
        with self.assertRaisesRegex(ValueError,'版本已变化'):
            self.send('AI确认 '+token)
        self.assertEqual(self.active(),current)
