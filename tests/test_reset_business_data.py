"""Offline reset acceptance. Only disposable state directories, no network."""
import importlib
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.competitions import TournamentService
from app.ai_plan_store import AiPlanStore
from app.db import connect, now
from app.member_imports import ensure_schema as import_schema
from app.notifications import Notifications
from app.password_auth import PasswordAuth
from app.registration import schema as registration_schema
from app.security import Platform
from app.table_images import ensure_schema as table_schema
from scripts import reset_business_data as reset


PRIVATE = 'PERSONAL_HISTORY_MARKER_13812345678'


@contextmanager
def sqlite_file(path):
    with closing(sqlite3.connect(path)) as db, db:
        yield db


def rows(path):
    with sqlite_file(path) as db:
        names = sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        return {name: db.execute('SELECT * FROM "' + name + '"').fetchall() for name in names}


def seed_rows(path, allowed):
    """One canary in every real removable table, including JSON/audit columns."""
    with sqlite_file(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in sorted(tables & allowed):
            if db.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]:
                continue
            info = db.execute('PRAGMA table_info("' + table + '")').fetchall()
            columns = [row[1] for row in info]
            values = [1 if 'INT' in row[2].upper() else PRIVATE for row in info]
            if table == 'membership_terms':
                values[columns.index('status')] = 'valid'
            db.execute('INSERT INTO "' + table + '" (' + ','.join('"' + c + '"' for c in columns)
                       + ') VALUES (' + ','.join('?' for _ in values) + ')', values)


class ResetBusinessDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.state = self.base / 'active'
        self.archive = self.base / 'private-legacy-archive'
        self.token = 'offline-reset-only-bootstrap-token'
        self.platform = Platform(self.state, self.token)
        self.admin = self.platform.authenticate(self.token)
        self.bot = self.platform.create(self.admin, {'name': 'Test robot', 'chat_id': 'test-group', 'robot_key': 'retained-test-key'})
        self.other_bot = self.platform.create(self.admin, {'name': 'Second robot', 'chat_id': 'second-group', 'robot_key': 'second-retained-key'})
        self.ctx = self.platform.context(self.admin, self.bot['id'])
        self.business = self.ctx.db_path
        self.transport = self.ctx.transport_db_path
        self.other_transport = self.platform.context(self.admin, self.other_bot['id']).transport_db_path
        self.registry = self.state / 'registry.sqlite3'
        TournamentService(self.business)
        Notifications(self.platform)
        PasswordAuth(self.platform)
        # Real v2.7 plans retain a token pointer and ordered receipts. Seed both
        # stores through the public API so reset covers their foreign keys and
        # private result text, rather than only synthetic independent rows.
        for bot in (self.bot, self.other_bot):
            store = AiPlanStore(self.platform.context(self.admin, bot['id']))
            plan = store.create([{'operation': 'Help', 'arguments': {}}], {}, 0, now().date().isoformat())
            store.claim(plan['id'])
            store.start_step(plan['id'], 0)
            store.finish_step(plan['id'], 0, PRIVATE)
            store.finish(plan['id'])
        with connect(self.business, True) as db:
            table_schema(db)
            import_schema(db)
            registration_schema(db)
            # Future explicitly named import ledger is also deliberately cleared.
            db.execute('CREATE TABLE member_imports(id INTEGER PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO member_groups(id,chat_id,name,created_at) VALUES(1,?,?,?)', ('club', 'Preserved group', '2026-01-01'))
            db.execute('INSERT INTO special_dates VALUES(1,?,?)', ('2026-12-01', '[{"start":"12:00","end":"13:30","capacity":20}]'))
            db.execute("UPDATE config SET value=? WHERE key='admin_user_ids'", (json.dumps([PRIVATE]),))
            db.execute("UPDATE config SET value=? WHERE key='leader_user_ids'", (json.dumps([PRIVATE]),))
            db.execute("UPDATE config SET value=? WHERE key='ai'", (json.dumps({'enabled': True, 'model': 'test-model', 'api_key': 'retained-secret-key'}),))
        seed_rows(self.business, reset.BUSINESS_DELETE)
        seed_rows(self.transport, reset.TRANSPORT_DELETE)
        seed_rows(self.other_transport, reset.TRANSPORT_DELETE)
        with connect(self.transport, True) as db:
            db.execute("INSERT INTO transport_meta VALUES('legacy_migrated','2026-01-01')")
        self.legacy = self.transport.parent / 'business.sqlite3'
        with sqlite_file(self.business) as source, sqlite_file(self.legacy) as destination:
            source.backup(destination)
        self.attachments = []
        for folder in (self.business.parent / 'uploads', self.business.parent / 'tables',
                       self.legacy.parent / 'uploads', self.legacy.parent / 'tables'):
            folder.mkdir()
            item = folder / 'history.bin'
            item.write_bytes(PRIVATE.encode())
            self.attachments.append(item)
        self.callback = self.legacy.parent / 'callback.local.json'
        self.callback.write_text('{"retained":"private-callback-key"}')
        self.external = self.base / 'old-full-backup'
        self.external.mkdir()
        (self.external / 'keep.bin').write_bytes(b'outside-backup')

    def tearDown(self):
        self.temporary.cleanup()

    def run_reset(self, **kwargs):
        return reset.reset_business_data(self.state, self.archive, confirm=reset.CONFIRMATION,
                                        service_stopped=True, backup_confirmed=True, **kwargs)

    def test_full_reset_preserves_configuration_auth_and_survives_reinitialization(self):
        before = rows(self.business)
        auth = PasswordAuth(self.platform)
        auth.set_initial('only-for-reset-test-password')
        registry = rows(self.registry)
        legacy_rows = rows(self.legacy)
        report = self.run_reset()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['attachments_before'], 4)
        self.assertEqual(report['attachments_after'], 0)
        self.assertEqual(report['legacy_databases_removed'], 1)
        after = rows(self.business)
        for table in reset.BUSINESS_DELETE & before.keys():
            self.assertTrue(before[table], table)
            self.assertEqual(after[table], [], table)
        for table in reset.BUSINESS_PRESERVE - {'config'}:
            self.assertEqual(after[table], before[table], table)
        old_config, new_config = dict(before['config']), dict(after['config'])
        for key in old_config:
            self.assertEqual(new_config[key], '[]' if key in reset.PERSON_CONFIG_KEYS else old_config[key])
        self.assertEqual(rows(self.registry), registry)
        with sqlite_file(self.registry) as db:
            guards = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        self.assertEqual({name: reset.normalized_trigger(sql) for name, sql in guards.items()},
                         {name: reset.normalized_trigger(sql) for name, sql in reset.GROUP_BINDING_GUARDS.items()})
        with self.assertRaisesRegex(ValueError, '只能绑定一个群'):
            self.platform.save_channel(self.admin, self.bot['id'], {'chat_id': 'not-allowed'})
        self.assertEqual(rows(self.transport)['transport_meta'], [('legacy_migrated', '2026-01-01')])
        for table in reset.TRANSPORT_DELETE:
            self.assertEqual(rows(self.transport)[table], [])
            self.assertEqual(rows(self.other_transport)[table], [])
        self.assertFalse(self.legacy.exists())
        archived = self.archive / self.legacy.relative_to(self.state)
        self.assertEqual(rows(archived), legacy_rows)
        self.assertTrue(all(not path.exists() for path in self.attachments))
        self.assertIn('private-callback-key', self.callback.read_text())
        self.assertEqual((self.external / 'keep.bin').read_bytes(), b'outside-backup')
        serialized = json.dumps(report)
        for secret in (PRIVATE, 'retained-secret-key', 'private-callback-key', self.token):
            self.assertNotIn(secret, serialized)
        for store in report['databases']:
            self.assertEqual(store['preserved_fingerprint_before'], store['preserved_fingerprint_after'])
            self.assertEqual(store['config_fingerprint_before'], store['config_fingerprint_after'])
        for database in (self.business, self.transport, self.other_transport):
            self.assertNotIn(PRIVATE.encode(), database.read_bytes())
            wal = Path(str(database) + '-wal')
            if wal.exists():
                self.assertNotIn(PRIVATE.encode(), wal.read_bytes())
        # Importing main must never touch default/local state in an acceptance test.
        with patch.dict(os.environ, {'ROBORT_STATE_DIR': str(self.state), 'ROBORT_LIVE_SEND': '0'}):
            module = importlib.import_module('app.main')
            app = module.create_app(self.state, start_scheduler=False, live=False)
        for table in reset.BUSINESS_DELETE & rows(self.business).keys():
            self.assertEqual(rows(self.business)[table], [], table)
        self.assertEqual(len(app.state.platform.list(self.admin)), 2)
        self.assertTrue(app.state.passwords.login('only-for-reset-test-password', 'isolated-test'))

    def test_readonly_preview_reports_all_tables_without_touching_data(self):
        snapshot = rows(self.business)
        preview = reset.reset_business_data(self.state, self.archive, dry_run=True)
        self.assertEqual(preview['status'], 'preflight')
        self.assertEqual(len(preview['databases']), 5)
        self.assertEqual(rows(self.business), snapshot)
        self.assertFalse(self.archive.exists())

    def test_ai_plan_journal_tables_are_classified_and_foreign_keys_clear_together(self):
        for database in (self.transport, self.other_transport):
            before = rows(database)
            self.assertEqual(len(before['ai_plans']), 1)
            self.assertEqual(len(before['ai_plan_steps']), 1)
            self.assertIn(PRIVATE, repr(before['ai_plan_steps']))
        report = self.run_reset()
        for item in report['databases']:
            if item['kind'] == 'transport':
                self.assertTrue({'ai_pending', 'ai_plans', 'ai_plan_steps'} <= set(item['delete']))
        for database in (self.transport, self.other_transport):
            with sqlite_file(database) as db:
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                for table in ('ai_pending', 'ai_plans', 'ai_plan_steps'):
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_explicit_confirmation_and_complete_absolute_paths_are_required(self):
        with self.assertRaises(reset.ResetError):
            reset.reset_business_data(self.state, self.archive)
        for state, archive in [(Path('relative'), self.archive), (self.business.parent, self.archive),
                               (self.state, self.state / 'archive'), (self.state, self.external)]:
            with self.subTest(state=state, archive=archive), self.assertRaises(reset.ResetError):
                reset.reset_business_data(state, archive, dry_run=True)
        self.assertFalse(self.archive.exists())

    def test_unknown_tables_in_any_database_fail_before_any_reset(self):
        for database in (self.registry, self.business, self.transport, self.legacy):
            snapshot = rows(self.business)
            with sqlite_file(database) as db:
                db.execute('CREATE TABLE future_private_history(id INTEGER PRIMARY KEY)')
            with self.subTest(database=database.name), self.assertRaisesRegex(reset.ResetError, 'Unclassified'):
                self.run_reset()
            self.assertFalse(self.archive.exists())
            self.assertTrue(self.legacy.exists())
            if database != self.business:
                self.assertEqual(rows(self.business), snapshot)
            with sqlite_file(database) as db:
                db.execute('DROP TABLE future_private_history')

    def test_unclassified_database_is_never_removed(self):
        unknown = self.state / 'unexpected.db'
        with sqlite_file(unknown) as db:
            db.execute('CREATE TABLE private(id)')
        with self.assertRaisesRegex(reset.ResetError, 'Unclassified database'):
            self.run_reset()
        self.assertTrue(unknown.exists())
        unknown.unlink()

    def test_symlink_never_exposes_external_backup_to_removal(self):
        link = self.state / 'workspace' / 'uploads' / 'external-link'
        try:
            link.symlink_to(self.external, target_is_directory=True)
        except OSError as exc:
            if os.name == 'nt' and getattr(exc, 'winerror', None) in (5, 1314):
                self.skipTest('This Windows account cannot create symbolic links')
            raise
        try:
            with self.assertRaisesRegex(reset.ResetError, 'symlinks'):
                self.run_reset()
            self.assertEqual((self.external / 'keep.bin').read_bytes(), b'outside-backup')
        finally:
            link.unlink()

    def test_running_writer_and_active_systemd_service_are_rejected(self):
        snapshot = rows(self.business)
        with closing(sqlite3.connect(self.transport, isolation_level=None)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            try:
                with self.assertRaisesRegex(reset.ResetError, 'locks'):
                    self.run_reset()
            finally:
                writer.rollback()
        self.assertEqual(rows(self.business), snapshot)
        self.assertFalse(self.archive.exists())
        with patch.object(reset.subprocess, 'run') as run:
            run.return_value.stdout = 'LoadState=loaded\nActiveState=active\n'
            with self.assertRaisesRegex(reset.ResetError, 'stopped'):
                self.run_reset(service_unit='kingsoft-registration.service')
        self.assertFalse(self.archive.exists())

    def test_precommit_error_rolls_back_every_database_and_leaves_legacy_active(self):
        snapshot = rows(self.business)
        original = reset._delete_rows
        calls = []
        def fail_on_transport(conn, tables, delete):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('injected test failure')
            return original(conn, tables, delete)
        with patch.object(reset, '_delete_rows', side_effect=fail_on_transport):
            with self.assertRaisesRegex(reset.ResetError, 'restore the complete'):
                self.run_reset()
        self.assertEqual(rows(self.business), snapshot)
        self.assertTrue(self.legacy.exists())
        self.assertTrue(all(item.exists() for item in self.attachments))

    def test_legacy_uncheckpointed_wal_is_archived_without_losing_rows(self):
        # Simulate a stopped/crashed old process, not an active SQLite writer.
        program = '''import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA wal_autocheckpoint=0')
db.execute("INSERT INTO audit(actor,action,detail,created_at) VALUES(?,?,?,?)", ('wal-user','wal-test',sys.argv[2],'2026-01-01'))
db.commit()
os._exit(0)
'''
        subprocess.run([sys.executable, '-c', program, str(self.legacy), PRIVATE], check=True, timeout=10)
        self.assertTrue(Path(str(self.legacy) + '-wal').is_file())
        report = self.run_reset()
        archived = self.archive / self.legacy.relative_to(self.state)
        self.assertEqual(len(rows(archived)['audit']), 2)
        old = next(item for item in report['databases'] if item['kind'] == 'legacy')
        self.assertEqual(old['before']['audit'], 2)
        self.assertEqual(old['after']['audit'], 0)
        self.assertFalse(Path(str(self.legacy) + '-wal').exists())

    def test_triggers_require_manual_review(self):
        with sqlite_file(self.business) as db:
            db.execute('CREATE TRIGGER no_reset AFTER DELETE ON profiles BEGIN SELECT 1; END')
        with self.assertRaisesRegex(reset.ResetError, 'views or triggers'):
            self.run_reset()
        self.assertFalse(self.archive.exists())

    def test_registry_guard_name_cannot_hide_an_unexpected_trigger_body(self):
        before = rows(self.business)
        with sqlite_file(self.registry) as db:
            db.execute('DROP TRIGGER single_bot_group_insert')
            db.execute('CREATE TRIGGER single_bot_group_insert AFTER DELETE ON bots BEGIN SELECT 1; END')
        with self.assertRaisesRegex(reset.ResetError, 'registry views or triggers'):
            self.run_reset()
        self.assertEqual(rows(self.business), before)
        self.assertFalse(self.archive.exists())

    def test_database_hardlinks_require_manual_review(self):
        external_link = self.external / 'retained-hardlink.sqlite3'
        try:
            os.link(self.business, external_link)
        except OSError as exc:
            if os.name == 'nt' and getattr(exc, 'winerror', None) in (5, 1314):
                self.skipTest('This Windows account cannot create hard links')
            raise
        try:
            with self.assertRaisesRegex(reset.ResetError, 'hard links'):
                self.run_reset()
            self.assertTrue(rows(external_link)['profiles'])
            self.assertFalse(self.archive.exists())
        finally:
            external_link.unlink()

    def test_unreadable_state_subdirectory_aborts_preflight(self):
        def unreadable_walk(root, **kwargs):
            if kwargs.get('onerror'):
                kwargs['onerror'](PermissionError('isolated unreadable-directory scenario'))
            return iter(())
        with patch.object(reset.os, 'walk', side_effect=unreadable_walk):
            with self.assertRaisesRegex(reset.ResetError, 'enumerate'):
                self.run_reset()
        self.assertFalse(self.archive.exists())
        self.assertTrue(self.legacy.exists())


if __name__ == '__main__':
    unittest.main()
