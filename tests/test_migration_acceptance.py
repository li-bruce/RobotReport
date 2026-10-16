"""Production-shaped offline acceptance: private credentials stay byte-identical."""
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from app.db import connect, stamp
from app.password_auth import PasswordAuth
from app.security import digest
from scripts.verify_workspace_migration import verify, inventory
from tests import test_workspace_v2


class MigrationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'source-snapshot'
        self.source.mkdir()
        # Reuse only the v1 fixture constructor, not any migration implementation.
        fixture=test_workspace_v2.WorkspaceTests()
        fixture.root=self.source
        self.old=fixture.legacy()[0]
        platform=SimpleNamespace(path=self.source/'registry.sqlite3')
        auth=PasswordAuth(platform)
        auth.set_initial('migration-test-only-password-12345')
        session=auth.login('migration-test-only-password-12345','test-peer')
        self.session_token=session['session_token']
        with connect(platform.path,True) as db:
            db.execute('CREATE TABLE grants(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,role TEXT NOT NULL,bot_ids TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)')
            db.execute("INSERT INTO grants VALUES('existing-platform',?,'platform_admin','[]',1,?)",(digest('legacy-test-token-01234567890123456789'),stamp()))
            db.execute("UPDATE bots SET webhook_url='https://xz.wps.cn/api/v1/webhook/send?key=private-test-only'")
        (self.source/'admin.local.json').write_text(json.dumps({'admin_token':'legacy-test-token-01234567890123456789'}),encoding='utf-8')
        (self.old.parent/'callback.local.json').write_text(json.dumps({'callback_token':'workspace-callback-012345678901234567890'}),encoding='utf-8')

    def test_isolated_migration_preserves_credentials_data_history_and_source(self):
        before=inventory(self.source)
        report=verify(self.source,self.root/'isolated-migrated')
        self.assertTrue(report['ok'])
        self.assertTrue(all(report['checks'].values()))
        self.assertFalse(report['network_started'])
        self.assertEqual(report['business_counts']['registrations'],1)
        self.assertEqual(report['business_counts']['tour_matches'],1)
        self.assertEqual(report['transport_counts']['outbox'],3)
        self.assertEqual(inventory(self.source),before)
        public=json.dumps(report)
        for secret in ('private-test-only','legacy-test-token','workspace-callback',self.session_token,'migration-test-only-password'):
            self.assertNotIn(secret,public)
        # Session/token hashes remain exactly available; verify doesn't log in.
        restored=PasswordAuth(SimpleNamespace(path=self.root/'isolated-migrated'/'registry.sqlite3'))
        self.assertEqual(restored.session(self.session_token)['principal'].role,'platform_admin')

    def test_destination_cannot_overwrite_or_nest_inside_source(self):
        for destination in (self.source,self.source/'nested',self.root):
            with self.subTest(destination=destination.name),self.assertRaises(ValueError):
                verify(self.source,destination)
        self.assertFalse((self.source/'nested').exists())

    def test_second_run_requires_new_isolated_destination(self):
        destination=self.root/'isolated-migrated'
        verify(self.source,destination)
        original=inventory(destination)
        with self.assertRaises(ValueError):verify(self.source,destination)
        self.assertEqual(inventory(destination),original)

    def test_quiescent_snapshot_wal_is_consolidated_only_in_the_copy(self):
        writer=sqlite3.connect(self.old,isolation_level=None)
        try:
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute('''INSERT INTO registrations(id,session_id,chat_id,user_id,name,guest_index,queue_seq,created_at,updated_at)
                VALUES(20,11,'old-chat-0','u1','张三+1',1,2,?,?)''',(stamp(),stamp()))
            self.assertGreater(self.old.with_name(self.old.name+'-wal').stat().st_size,0)
            before=inventory(self.source)
            report=verify(self.source,self.root/'wal-isolated-migrated')
            self.assertEqual(report['business_counts']['registrations'],2)
            self.assertEqual(inventory(self.source),before)
        finally:
            writer.close()


if __name__=='__main__':unittest.main()
