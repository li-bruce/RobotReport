import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.db import connect, initialize, stamp
from app.security import Platform, digest


TOKEN = 'workspace-test-token-012345678901234567890'
CALLBACK = 'workspace-callback-012345678901234567890'


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def platform(self):
        p = Platform(self.root, TOKEN)
        return p, p.authenticate(TOKEN)

    def bot(self, p, admin, chat='g1', **extra):
        return p.create(admin, {'name': '羽协机器人', 'chat_id': chat, 'robot_key': 'r-'+chat,
                                'callback_token': CALLBACK, **extra})

    def legacy(self, count=1):
        with connect(self.root / 'registry.sqlite3') as db:
            db.execute('CREATE TABLE bots(id TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,chat_id TEXT NOT NULL,webhook_url TEXT NOT NULL DEFAULT \'\',robot_key TEXT NOT NULL DEFAULT \'\',callback_hash TEXT NOT NULL,legacy INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)')
            for n in range(count):
                bot_id = ('a' if n == 0 else 'b') * 32
                db.execute('INSERT INTO bots VALUES(?,?,1,?,?,?,?,1,?)', (bot_id,'旧机器人'+str(n),'old-chat-'+str(n),'','old-key',digest(CALLBACK),stamp()))
        paths = []
        for n in range(count):
            bot_id = ('a' if n == 0 else 'b') * 32
            path = self.root / 'bots' / bot_id / 'business.sqlite3'
            initialize(path)
            with connect(path, True) as db:
                chat = 'old-chat-'+str(n)
                db.execute('INSERT INTO profiles VALUES(?,?,?)', (chat,'u1','张三'))
                db.execute('INSERT INTO profiles VALUES(?,?,?)', (chat,'u2',''))
                db.execute('INSERT INTO profiles VALUES(?,?,?)', (chat,'u3','李四'))
                for user,status in [('u1','approved'),('u2','pending'),('u3','pending')]:
                    db.execute('INSERT INTO member_records(chat_id,user_id,status,created_at,updated_at) VALUES(?,?,?,?,?)', (chat,user,status,stamp(),stamp()))
                db.execute("UPDATE config SET value='[\"u1\"]' WHERE key='admin_user_ids'")
                db.execute('INSERT INTO member_groups(id,chat_id,name,created_at) VALUES(7,?,?,?)', (chat,'一组',stamp()))
                db.execute('INSERT INTO member_group_links VALUES(?,?,7)', (chat,'u1'))
                db.execute("INSERT INTO sessions(id,venue_id,date,name,start,end,capacity,substitute_capacity) VALUES(11,1,'2026-10-13','第一场','17:30','20:30',8,4)")
                db.execute('INSERT INTO registrations(id,session_id,chat_id,user_id,name,queue_seq,created_at,updated_at) VALUES(19,11,?,?,?,?,?,?)', (chat,'u1','张三',1,stamp(),stamp()))
                db.execute("INSERT INTO inbox(event_id,status,reply,created_at) VALUES('done','done','ok',?)", (stamp(),))
                db.execute("INSERT INTO inbox(event_id,status,created_at) VALUES('processing','processing',?)", (stamp(),))
                for no,status in enumerate(('sent','pending','sending'),1):
                    db.execute('INSERT INTO outbox(id,dedupe_key,chat_id,payload,status,created_at) VALUES(?,?,?,?,?,?)', (no,'legacy-'+str(no),chat,'{}',status,stamp()))
                db.execute("CREATE TABLE tour_events(id INTEGER PRIMARY KEY,chat_id TEXT NOT NULL)")
                db.execute('INSERT INTO tour_events VALUES(23,?)',(chat,))
                db.execute('CREATE TABLE tour_matches(id INTEGER PRIMARY KEY,tournament_id INTEGER REFERENCES tour_events(id),scores TEXT NOT NULL)')
                db.execute('INSERT INTO tour_matches VALUES(31,23,?)', ('[[21,19]]',))
                db.execute('CREATE TABLE generated_tables(token_hash TEXT PRIMARY KEY,expires_at TEXT,created_at TEXT)')
                db.execute('INSERT INTO generated_tables VALUES(?,?,?)',('x'*64,'2099-01-01',stamp()))
            for folder in ('uploads','tables'):
                (path.parent/folder).mkdir()
                (path.parent/folder/'sample.bin').write_bytes(b'keep legacy media')
            with connect(path) as db:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            paths.append(path)
        return paths

    def test_workspace_exists_without_any_robot(self):
        p,admin=self.platform()
        ctx=p.context(admin)
        self.assertEqual(ctx.id,'workspace')
        self.assertEqual(ctx.scope_id,'club')
        self.assertTrue(ctx.is_admin)
        self.assertTrue(ctx.db_path.is_file())
        self.assertEqual(p.system_contexts(),[])

    def test_new_bots_share_business_and_have_separate_delivery(self):
        p,admin=self.platform()
        a,b=self.bot(p,admin),self.bot(p,admin,'g2')
        ca,cb=p.context(admin,a['id']),p.context(admin,b['id'])
        self.assertEqual(ca.db_path,cb.db_path)
        self.assertEqual(ca.root,cb.root)
        self.assertNotEqual(ca.transport_db_path,cb.transport_db_path)
        with connect(ca.db_path,True) as db:
            db.execute('INSERT INTO profiles VALUES(?,?,?)',('club','u','共同会员'))
        with connect(cb.db_path) as db:
            self.assertEqual(db.execute("SELECT name FROM profiles WHERE user_id='u'").fetchone()[0],'共同会员')
        self.assertFalse((ca.bot_root/'business.sqlite3').exists())
        again=Platform(self.root)
        self.assertEqual(len(again.list_channels(admin)),2)

    def test_channel_routing_and_key_are_bound_to_real_group(self):
        p,admin=self.platform()
        b=self.bot(p,admin)
        second_bot=self.bot(p,admin,'g2')
        second=p.save_channel(admin,second_bot['id'],{'robot_key':'second-key'},second_bot['channels'][0]['id'])
        b=second_bot
        ctx=p.callback_context(b['id'],CALLBACK,{'chatid':'g2','creator':'user','robot_key':'second-key'})
        self.assertEqual(ctx.channel_id,second['id'])
        self.assertEqual(ctx.chat_id,'g2')
        self.assertEqual(ctx.scope_id,'club')
        for body in ({'chatid':'g2','creator':'user','robot_key':'r-g1'}, {'chatid':'unknown','creator':'user','robot_key':'second-key'}):
            with self.assertRaises(PermissionError):
                p.callback_context(b['id'],CALLBACK,body)
        p.save_channel(admin,b['id'],{'receive_commands':False},second['id'])
        with self.assertRaises(PermissionError):
            p.callback_context(b['id'],CALLBACK,{'chatid':'g2','creator':'user','robot_key':'second-key'})

    def test_webhook_cannot_be_reused_as_two_group_destinations(self):
        p,admin=self.platform()
        url='https://xz.wps.cn/api/v1/webhook/send?key=test-key'
        a=self.bot(p,admin,webhook_url=url)
        with self.assertRaises(ValueError):
            p.save_channel(admin,a['id'],{'chat_id':'g2','webhook_url':url})
        with self.assertRaises(ValueError):
            self.bot(p,admin,'g2',webhook_url=url)
        self.assertEqual(len(p.list(admin)),1)
        self.assertEqual(len(p.list_channels(admin)),1)

    def test_channel_secrets_never_in_api_views(self):
        p,admin=self.platform()
        b=self.bot(p,admin,webhook_url='https://xz.wps.cn/api/v1/webhook/send?key=private-webhook')
        view=json.dumps([p.list(admin),p.list_channels(admin)])
        self.assertNotIn('private-webhook',view)
        self.assertNotIn('r-g1',view)
        self.assertNotIn('callback_hash',view)
        self.assertEqual(p.channel(b['channels'][0]['id'])['robot_key'],'r-g1')

    def test_venue_scope_validation_and_default(self):
        p,admin=self.platform()
        b=self.bot(p,admin)
        with connect(p.workspace_context().db_path,True) as db:
            db.execute("INSERT INTO venues(id,name) VALUES(2,'第二场地')")
        channel=b['channels'][0]['id']
        p.save_channel(admin,b['id'],{'venue_ids':[2],'default_venue_id':2},channel)
        p.update(admin,b['id'],{'venue_ids':[2]})
        ctx=p.context_for_channel(channel)
        self.assertEqual(ctx.venue_ids,(2,))
        self.assertEqual(ctx.default_venue_id,2)
        for patch in ({'venue_ids':[1]}, {'venue_ids':[3],'default_venue_id':3}, {'venue_ids':[2,2]}, {'default_venue_id':True}):
            with self.assertRaises(ValueError):
                p.save_channel(admin,b['id'],patch,channel)

    def test_legacy_bot_admin_bearer_does_not_gain_shared_business_authority(self):
        p,admin=self.platform()
        a,b=self.bot(p,admin),self.bot(p,admin,'g2')
        token=p.grant(admin,[a['id']])['token']
        user=p.authenticate(token)
        ctx=p.context(user)
        self.assertFalse(ctx.is_admin)
        self.assertEqual(ctx.source,'transport_admin')
        p.save_channel(user,a['id'],{'name':'改名'},a['channels'][0]['id'])
        with self.assertRaises(PermissionError):
            p.context(user,b['id'])
        with self.assertRaises(PermissionError):
            p.save_channel(user,b['id'],{'name':'越权'},b['channels'][0]['id'])
        with self.assertRaises(ValueError):
            p.update(admin,a['id'],{'admin_user_ids':['u1']})

    def test_callback_club_admin_requires_independent_role_and_approved_membership(self):
        p,admin=self.platform()
        b=self.bot(p,admin)
        body={'chatid':'g1','creator':'u1','robot_key':'r-g1'}
        self.assertFalse(p.callback_context(b['id'],CALLBACK,body).is_admin)
        with connect(p.workspace_context().db_path,True) as db:
            db.execute('INSERT INTO profiles VALUES(?,?,?)',('club','u1','会员'))
            db.execute("INSERT INTO member_records(chat_id,user_id,status,created_at,updated_at) VALUES('club','u1','approved',?,?)",(stamp(),stamp()))
            db.execute("INSERT INTO user_roles VALUES('u1','club_admin','club','club',?,'admin')",(stamp(),))
        self.assertTrue(p.callback_context(b['id'],CALLBACK,body).is_admin)
        with connect(p.workspace_context().db_path,True) as db:
            db.execute("UPDATE member_records SET status='suspended' WHERE user_id='u1'")
        self.assertFalse(p.callback_context(b['id'],CALLBACK,body).is_admin)

    def test_single_legacy_database_migrates_without_touching_original(self):
        source=self.legacy()[0]
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        p,admin=self.platform()
        ctx=p.context(admin,'a'*32)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
        with connect(ctx.db_path) as db:
            self.assertEqual(tuple(db.execute('SELECT id,session_id,chat_id,user_id,name FROM registrations').fetchone()),(19,11,'club','u1','张三'))
            self.assertEqual(tuple(db.execute('SELECT id,tournament_id,scores FROM tour_matches').fetchone()),(31,23,'[[21,19]]'))
            self.assertEqual(db.execute('SELECT chat_id FROM tour_events').fetchone()[0],'club')
            self.assertEqual(tuple(db.execute('SELECT chat_id,user_id,group_id FROM member_group_links').fetchone()),('club','u1',7))
            self.assertEqual(db.execute("SELECT status FROM member_records WHERE user_id='u2'").fetchone()[0],'unregistered')
            self.assertEqual(db.execute("SELECT name FROM membership_applications WHERE user_id='u3'").fetchone()[0],'李四')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_roles').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT kind FROM workspace_migration_issues').fetchone()[0],'legacy_bot_admin')
            self.assertEqual(db.execute('SELECT channel_id FROM tour_event_channels').fetchone()[0],ctx.channel_id)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM generated_tables').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertFalse(list(db.execute('PRAGMA foreign_key_check')))
        with connect(ctx.transport_db_path) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT status FROM outbox ORDER BY id')],['sent','unknown','unknown'])
            self.assertEqual({r[0] for r in db.execute('SELECT channel_id FROM outbox')},{ctx.channel_id})
            self.assertEqual(db.execute("SELECT status FROM inbox WHERE event_id='processing'").fetchone()[0],'unknown')
            self.assertEqual(db.execute("SELECT status FROM inbox WHERE event_id='done'").fetchone()[0],'done')
        for folder in ('uploads','tables'):
            self.assertEqual((ctx.root/folder/'sample.bin').read_bytes(),b'keep legacy media')

    def test_migration_is_idempotent_and_does_not_restore_stale_delivery(self):
        self.legacy()
        p,admin=self.platform()
        ctx=p.context(admin,'a'*32)
        with connect(ctx.transport_db_path,True) as db:
            db.execute("UPDATE outbox SET status='cancelled' WHERE id=2")
        p2=Platform(self.root)
        with connect(p2.context(admin,'a'*32).transport_db_path) as db:
            self.assertEqual(db.execute('SELECT status FROM outbox WHERE id=2').fetchone()[0],'cancelled')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],3)
        with connect(p2.workspace_context().db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM membership_applications').fetchone()[0],1)

    def test_multiple_old_databases_stop_before_creating_shared_data(self):
        sources=self.legacy(2)
        before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in sources]
        with self.assertRaisesRegex(RuntimeError,'多个历史机器人业务库'):
            self.platform()
        self.assertFalse((self.root/'workspace').exists())
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in sources],before)


if __name__=='__main__':
    unittest.main()
