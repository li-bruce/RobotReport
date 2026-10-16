from tests.legacy_registry import seed_legacy_channel
"""Robot feature combinations and immutable group routing history."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from app.bot_capabilities import ALL_CAPABILITIES, DEFAULT_CAPABILITIES, capability_catalog, normalize_capabilities, require_capability, supports
from app.db import connect, stamp
from app.messages import Outbox
from app.scope import refresh_context
from app.security import Platform, digest


TOKEN = 'v211-registry-test-platform-token-000000'
CALLBACK = 'v211-callback-test-token-00000000000'


class RobotRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.platform = Platform(self.root, TOKEN)
        self.admin = self.platform.authenticate(TOKEN)
        self.bot = self.platform.create(self.admin, {
            'name': '学生活动', 'chat_id': 'students', 'robot_key': 'student-robot-key',
            'webhook_url': self.webhook('students'), 'callback_token': CALLBACK,
        })
        self.channel_id = self.bot['channels'][0]['id']

    @staticmethod
    def webhook(key):
        return 'https://xz.wps.cn/api/v1/webhook/send?key=' + key

    def test_catalog_defaults_and_optional_daily_events(self):
        self.assertEqual(normalize_capabilities(None), ALL_CAPABILITIES)
        self.assertEqual(normalize_capabilities([]), ())
        self.assertEqual([item['id'] for item in capability_catalog() if item['required']], [])
        self.assertEqual(self.bot['capabilities'], list(DEFAULT_CAPABILITIES))
        changed = self.platform.update(self.admin, self.bot['id'], {'capabilities': ['registration', 'ai']})
        self.assertEqual(set(changed['capabilities']), {'registration', 'ai'})
        self.assertEqual(changed['channels'][0]['capabilities'], changed['capabilities'])
        self.assertEqual(self.platform.list_channels()[0]['capabilities'], changed['capabilities'])
        context = self.platform.context_for_channel(self.channel_id)
        self.assertTrue(supports(context, 'registration'))
        self.assertFalse(supports(context, 'management'))
        self.assertFalse(supports(context, 'unknown'))
        with self.assertRaisesRegex(PermissionError, '群内管理'):
            require_capability(context, 'management')
        self.assertTrue(supports(replace(context, capabilities=None), 'management'))

    def test_disable_features_refreshes_stale_context_and_no_channel_context(self):
        old = self.platform.context_for_channel(self.channel_id)
        self.platform.update(self.admin, self.bot['id'], {'capabilities': []})
        self.assertTrue(supports(old, 'daily_events'))  # Pure helper uses its explicit snapshot.
        self.assertFalse(supports(refresh_context(old), 'ai'))
        self.assertFalse(supports(refresh_context(old), 'daily_events'))
        self.platform.archive_channel(self.admin, self.bot['id'], self.channel_id)
        self.assertEqual(self.platform.context(self.admin, self.bot['id']).capabilities, ())
        self.assertTrue(supports(self.platform.workspace_context(), 'ai'))

    def test_invalid_capabilities_do_not_partially_mutate_bot(self):
        for value in ('ai', {}, ['made-up'], ['ai', 'ai'], [True]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.platform.update(self.admin, self.bot['id'], {'name': 'should-not-save', 'capabilities': value})
            self.assertEqual(self.platform.bot(self.bot['id'])['name'], '学生活动')

    def test_group_replacement_preserves_old_credentials_history_and_rejects_stale_context(self):
        old_context = self.platform.context_for_channel(self.channel_id)
        Outbox(old_context).enqueue('旧群待发消息', 'before-replace')
        with connect(old_context.transport_db_path) as db:
            before = [tuple(row) for row in db.execute('SELECT * FROM outbox')]
        replaced = self.platform.save_channel(self.admin, self.bot['id'], {
            'chat_id': 'community', 'webhook_url': self.webhook('community-student-bot'),
            'robot_key': 'community-student-key',
        }, self.channel_id)
        self.assertNotEqual(replaced['id'], self.channel_id)
        self.assertEqual(replaced['replaced_channel_id'], self.channel_id)
        archived = self.platform.channel(self.channel_id)
        self.assertTrue(archived['archived'])
        self.assertFalse(archived['active'])
        self.assertEqual(archived['chat_id'], 'students')
        self.assertEqual(archived['robot_key'], 'student-robot-key')
        self.assertEqual(archived['webhook_url'], self.webhook('students'))
        with connect(old_context.transport_db_path) as db:
            self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM outbox')])
        with self.assertRaises(PermissionError):
            refresh_context(old_context)
        with self.assertRaises(PermissionError):
            asyncio.run(Outbox(old_context).deliver_one(self.platform, live=False))
        new_context = self.platform.context_for_channel(replaced['id'])
        self.assertIsNone(asyncio.run(Outbox(new_context).deliver_one(self.platform, live=False)))
        with self.assertRaises(PermissionError):
            self.platform.callback_context(self.bot['id'], CALLBACK,
                {'chatid': 'students', 'creator': 'user', 'robot_key': 'student-robot-key'})
        self.assertEqual(self.platform.callback_context(self.bot['id'], CALLBACK,
            {'chatid': 'community', 'creator': 'user', 'robot_key': 'community-student-key'}).channel_id, replaced['id'])

    def test_group_replacement_requires_explicit_destination_credentials_and_is_atomic(self):
        for patch in ({'chat_id': 'community'},
                      {'chat_id': 'community', 'robot_key': 'student-robot-key'},
                      {'chat_id': 'community', 'webhook_url': self.webhook('students'), 'robot_key': '   '},
                      {'chat_id': 'community', 'webhook_url': '', 'robot_key': 'student-robot-key'}):
            with self.subTest(patch=patch), self.assertRaisesRegex(ValueError, '重新填写目标群'):
                self.platform.save_channel(self.admin, self.bot['id'], patch, self.channel_id)
            self.assertFalse(self.platform.channel(self.channel_id)['archived'])
        other = seed_legacy_channel(self.platform, self.bot['id'], {
            'chat_id': 'community', 'webhook_url': self.webhook('community'), 'robot_key': 'community-key'})
        with self.assertRaisesRegex(ValueError, '已绑定此群'):
            self.platform.save_channel(self.admin, self.bot['id'], {
                'chat_id': 'community', 'webhook_url': self.webhook('fresh'), 'robot_key': 'fresh'}, self.channel_id)
        self.assertFalse(self.platform.channel(self.channel_id)['archived'])
        self.assertEqual(len(self.platform.list_channels()), 2)

    def test_correcting_group_id_accepts_explicit_same_credentials_without_moving_old_messages(self):
        old_context = self.platform.context_for_channel(self.channel_id)
        Outbox(old_context).enqueue('旧绑定历史消息', 'before-correction')
        with connect(old_context.transport_db_path) as db:
            before = [tuple(row) for row in db.execute('SELECT * FROM outbox')]
        corrected = self.platform.save_channel(self.admin, self.bot['id'], {
            'chat_id': 'correct-group', 'webhook_url': self.webhook('students'),
            'robot_key': 'student-robot-key'}, self.channel_id)
        self.assertNotEqual(corrected['id'], self.channel_id)
        self.assertEqual(corrected['replaced_channel_id'], self.channel_id)
        self.assertTrue(self.platform.channel(self.channel_id)['archived'])
        self.assertEqual(self.platform.channel(self.channel_id)['chat_id'], 'students')
        with connect(old_context.transport_db_path) as db:
            self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM outbox')])
        with self.assertRaises(PermissionError):
            self.platform.callback_context(self.bot['id'], CALLBACK,
                {'chatid': 'students', 'creator': 'user', 'robot_key': 'student-robot-key'})
        current = self.platform.callback_context(self.bot['id'], CALLBACK,
            {'chatid': 'correct-group', 'creator': 'user', 'robot_key': 'student-robot-key'})
        self.assertEqual(current.channel_id, corrected['id'])
        self.assertEqual(Outbox(current).list(), [])
        self.assertIsNone(asyncio.run(Outbox(current).deliver_one(self.platform, live=False)))
        with self.assertRaises(PermissionError):
            asyncio.run(Outbox(old_context).deliver_one(self.platform, live=False))

    def test_archive_is_idempotent_and_reconnect_never_reuses_old_binding(self):
        first = self.platform.archive_channel(self.admin, self.bot['id'], self.channel_id)
        again = self.platform.archive_channel(self.admin, self.bot['id'], self.channel_id)
        self.assertEqual(first, again)
        self.assertEqual(self.platform.list_channels(), [])
        self.assertEqual(len(self.platform.list_channels(include_archived=True)), 1)
        self.assertEqual(self.platform.list_channels(active_only=True, include_archived=True), [])
        self.assertEqual(self.platform.bot(self.bot['id'])['chat_id'], '')
        restarted = Platform(self.root)
        self.assertEqual(restarted.list_channels(), [])
        with self.assertRaisesRegex(ValueError, '已移除'):
            restarted.save_channel(self.admin, self.bot['id'], {'active': True}, self.channel_id)
        new = restarted.save_channel(self.admin, self.bot['id'], {'chat_id': 'students',
            'webhook_url': self.webhook('students-new'), 'robot_key': 'students-new-key'})
        self.assertNotEqual(new['id'], self.channel_id)
        self.assertEqual(len(restarted.list_channels(include_archived=True)), 2)

    def test_same_group_multiple_bots_keep_separate_credentials_and_ownership(self):
        other = self.platform.create(self.admin, {'name': '社团助手', 'chat_id': 'community',
            'webhook_url': self.webhook('community-general'), 'robot_key': 'general-key'})
        with self.assertRaisesRegex(ValueError, '各自的Webhook'):
            self.platform.save_channel(self.admin, self.bot['id'], {
                'chat_id': 'community', 'webhook_url': self.webhook('community-general'),
                'robot_key': 'student-robot-key'}, self.channel_id)
        self.assertFalse(self.platform.channel(self.channel_id)['archived'])
        replaced = self.platform.save_channel(self.admin, self.bot['id'], {
            'chat_id': 'community', 'webhook_url': self.webhook('community-student'),
            'robot_key': 'student-new-key'}, self.channel_id)
        self.assertEqual(len(self.platform.list_channels()), 2)
        with self.assertRaisesRegex(ValueError, '各自的Webhook'):
            self.platform.save_channel(self.admin, self.bot['id'], {
                'webhook_url': self.webhook('community-general')}, replaced['id'])
        with self.assertRaises(PermissionError):
            self.platform.archive_channel(self.admin, other['id'], replaced['id'])
        grant = self.platform.authenticate(self.platform.grant(self.admin, [self.bot['id']])['token'])
        with self.assertRaises(PermissionError):
            self.platform.archive_channel(grant, other['id'], other['channels'][0]['id'])
        view = json.dumps(self.platform.list_channels(include_archived=True))
        self.assertNotIn('student-new-key', view)
        self.assertNotIn('community-general', view)

    def test_disabled_channel_remains_visible_and_can_reenable_without_replacement(self):
        disabled = self.platform.save_channel(self.admin, self.bot['id'], {'active': False}, self.channel_id)
        self.assertFalse(disabled['archived'])
        self.assertEqual(len(self.platform.list_channels()), 1)
        self.assertEqual(self.platform.list_channels(active_only=True), [])
        enabled = self.platform.save_channel(self.admin, self.bot['id'], {'active': True}, self.channel_id)
        self.assertEqual(enabled['id'], self.channel_id)

    def test_http_capability_config_and_group_move_remove_reconnect(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        app = create_app(self.root, TOKEN, start_scheduler=False, live=False)
        headers = {'Authorization': 'Bearer ' + TOKEN}
        base = '/api/bots/' + self.bot['id']
        with TestClient(app) as client:
            catalog = client.get('/api/bot-capabilities', headers=headers)
            self.assertEqual(catalog.status_code, 200, catalog.text)
            self.assertEqual({item['id'] for item in catalog.json()['capabilities']}, set(ALL_CAPABILITIES))
            changed = client.patch(base, headers=headers, json={'capabilities': ['messages']})
            self.assertEqual(changed.status_code, 200, changed.text)
            channels = client.get(base + '/channels', headers=headers).json()
            self.assertEqual(set(channels[0]['capabilities']), {'messages'})
            moved = client.patch(base + '/channels/' + self.channel_id, headers=headers, json={
                'chat_id': 'community', 'webhook_url': self.webhook('http-community'), 'robot_key': 'http-community-key'})
            self.assertEqual(moved.status_code, 200, moved.text)
            self.assertEqual(moved.json()['replaced_channel_id'], self.channel_id)
            new_id = moved.json()['id']
            removed = client.delete(base + '/channels/' + new_id, headers=headers)
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertTrue(removed.json()['archived'])
            self.assertEqual(client.get(base + '/channels', headers=headers).json(), [])
            archived = client.get(base + '/channels?include_archived=true', headers=headers).json()
            self.assertEqual({item['id'] for item in archived}, {self.channel_id, new_id})
            reconnected = client.post(base + '/channels', headers=headers, json={
                'chat_id': 'community', 'webhook_url': self.webhook('http-reconnect'), 'robot_key': 'http-reconnect-key'})
            self.assertEqual(reconnected.status_code, 200, reconnected.text)
            self.assertNotIn(reconnected.json()['id'], {self.channel_id, new_id})


class RegistryMigrationTests(unittest.TestCase):
    def test_old_unique_constraint_and_every_existing_column_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bot_id, channel_id = 'a' * 32, 'b' * 32
            with connect(root / 'registry.sqlite3') as db:
                db.executescript('''
                    CREATE TABLE bots(id TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,chat_id TEXT NOT NULL,webhook_url TEXT NOT NULL DEFAULT '',robot_key TEXT NOT NULL DEFAULT '',callback_hash TEXT NOT NULL,legacy INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
                    CREATE TABLE bot_channels(id TEXT PRIMARY KEY,bot_id TEXT NOT NULL REFERENCES bots(id),name TEXT NOT NULL,chat_id TEXT NOT NULL,webhook_url TEXT NOT NULL DEFAULT '',robot_key TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,default_venue_id INTEGER NOT NULL DEFAULT 1,venue_ids TEXT NOT NULL DEFAULT '[]',receive_commands INTEGER NOT NULL DEFAULT 1,daily_notifications INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,future_metadata TEXT NOT NULL DEFAULT 'keep',UNIQUE(bot_id,chat_id));
                    CREATE INDEX custom_channel_name ON bot_channels(name);
                ''')
                db.execute('INSERT INTO bots VALUES(?,?,1,?,?,?,?,0,?)', (bot_id,'旧机器人','old-room',RobotRegistryTests.webhook('old'),'old-key',digest(CALLBACK),stamp()))
                db.execute('INSERT INTO bot_channels(id,bot_id,name,chat_id,webhook_url,robot_key,created_at,future_metadata) VALUES(?,?,?,?,?,?,?,?)',
                           (channel_id,bot_id,'旧群','old-room',RobotRegistryTests.webhook('old'),'old-key',stamp(),'preserved'))
                before = dict(db.execute('SELECT * FROM bot_channels').fetchone())
            platform = Platform(root, TOKEN)
            admin = platform.authenticate(TOKEN)
            with connect(platform.path) as db:
                after = dict(db.execute('SELECT * FROM bot_channels').fetchone())
                self.assertEqual({k: after[k] for k in before}, before)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(list(db.execute('PRAGMA foreign_key_check')), [])
                self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='custom_channel_name'").fetchone())
            self.assertEqual(platform.list(admin)[0]['capabilities'], list(ALL_CAPABILITIES))
            platform.archive_channel(admin, bot_id, channel_id)
            replacement = platform.save_channel(admin, bot_id, {'chat_id': 'old-room', 'robot_key': 'new-key', 'webhook_url': RobotRegistryTests.webhook('new')})
            self.assertNotEqual(replacement['id'], channel_id)
            self.assertEqual(len(Platform(root).list_channels(include_archived=True)), 2)
