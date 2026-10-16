"""Single current group per bot, shared groups and non-destructive upgrades."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app
from app.security import Platform
from tests.legacy_registry import seed_legacy_channel


class BotGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token = 'bot-groups-local-test-token-32-characters'
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root, self.token, live=False, start_scheduler=False)
        self.p = self.app.state.platform
        self.admin = self.p.authenticate(self.token)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer ' + self.token}
        self.bot = self.p.create(self.admin, {'name': '活动机器人', 'chat_id': '266382',
            'robot_key': 'first-key', 'callback_token': 'first-callback-token-32-characters'})
        self.cid = self.bot['channels'][0]['id']

    def rows(self, legacy_columns=False):
        with connect(self.p.path) as db:
            columns=','.join(r['name'] for r in db.execute('PRAGMA table_info(bot_channels)') if not legacy_columns or r['name']!='group_id')
            return [tuple(r) for r in db.execute('SELECT '+columns+' FROM bot_channels ORDER BY id')]

    def test_same_group_multiple_bots_keep_independent_keys_and_callbacks(self):
        other = self.p.create(self.admin, {'name': '学生机器人', 'chat_id': '266382',
            'robot_key': 'second-key', 'callback_token': 'second-callback-token-32-characters'})
        for bot, key in ((self.bot, 'first-key'), (other, 'second-key')):
            callback = bot['callback_path'].split('/')[-1]
            context = self.p.callback_context(bot['id'], callback,
                {'chatid': '266382', 'creator': 'member', 'robot_key': key})
            self.assertEqual(context.channel_id, bot['channels'][0]['id'])
            with self.assertRaises(PermissionError):
                self.p.callback_context(bot['id'], callback,
                    {'chatid': '266382', 'creator': 'member', 'robot_key': 'wrong-key'})
        self.assertEqual(len(self.p.list_channels()), 2)
        self.assertEqual({r['chat_id'] for r in self.p.list_channels()}, {'266382'})

    def test_http_second_group_rejected_without_partial_changes(self):
        before = self.rows()
        response = self.client.post(f"/api/bots/{self.bot['id']}/channels", headers=self.headers,
                                    json={'name': '另一个群', 'chat_id': 'other', 'robot_key': 'other-key'})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('只能绑定一个群', response.text)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.p.bot(self.bot['id'])['chat_id'], '266382')

    def test_paused_connection_and_disabled_bot_still_reserve_the_group(self):
        self.p.save_channel(self.admin, self.bot['id'], {'active': False}, self.cid)
        self.p.update(self.admin, self.bot['id'], {'active': False})
        with self.assertRaisesRegex(ValueError, '只能绑定一个群'):
            self.p.save_channel(self.admin, self.bot['id'], {'name': '新群', 'chat_id': 'new'})
        self.assertEqual(len(self.p.list_channels()), 1)

    def test_rebinding_archives_original_and_preserves_the_other_bot(self):
        other = self.p.create(self.admin, {'name': '同群机器人', 'chat_id': '266382', 'robot_key': 'other'})
        before = self.p.channel(other['channels'][0]['id'])
        new = self.p.save_channel(self.admin, self.bot['id'], {'chat_id': 'new-group',
            'webhook_url': 'https://xz.wps.cn/api/v1/webhook/send?key=fresh-key'}, self.cid)
        self.assertTrue(self.p.channel(self.cid)['archived'])
        self.assertEqual(self.p.channel(self.cid)['chat_id'], '266382')
        self.assertEqual(self.p.channel(other['channels'][0]['id']), before)
        self.assertEqual(new['replaced_channel_id'], self.cid)
        self.assertEqual(len(self.p.list_channels(bot_id=self.bot['id'])), 1)
        self.p.archive_channel(self.admin, self.bot['id'], new['id'])
        third = self.p.save_channel(self.admin, self.bot['id'], {'chat_id': '266382'})
        self.assertNotEqual(third['id'], self.cid)
        self.assertEqual(len(self.p.list_channels(bot_id=self.bot['id'], include_archived=True)), 3)

    def test_parallel_first_bindings_only_one_wins(self):
        bot = self.p.create(self.admin, {'name': '待绑定'})
        def bind(number):
            try:
                return self.p.save_channel(self.admin, bot['id'], {'chat_id': str(number)})['id']
            except ValueError as error:
                self.assertIn('只能绑定一个群', str(error))
                return None
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(bind, range(12)))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(len(self.p.list_channels(bot_id=bot['id'])), 1)

    def test_database_guards_insert_restore_and_reassignment(self):
        with connect(self.p.path, True) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, '只能绑定一个群'):
                db.execute('INSERT INTO bot_channels(id,bot_id,name,chat_id,created_at) VALUES(?,?,?,?,?)',
                           ('bad', self.bot['id'], '非法第二群', 'bad', '2026-10-15'))
        self.p.archive_channel(self.admin, self.bot['id'], self.cid)
        self.p.save_channel(self.admin, self.bot['id'], {'chat_id': 'current'})
        with connect(self.p.path, True) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, '只能绑定一个群'):
                db.execute('UPDATE bot_channels SET archived=0 WHERE id=?', (self.cid,))
        other = self.p.create(self.admin, {'name': '其他机器人', 'chat_id': 'other'})
        with connect(self.p.path, True) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, '只能绑定一个群'):
                db.execute('UPDATE bot_channels SET bot_id=? WHERE id=?',
                           (self.bot['id'], other['channels'][0]['id']))

    def test_upgrade_preserves_legacy_rows_until_explicit_consolidation(self):
        legacy = seed_legacy_channel(self.p, self.bot['id'], {'name': '旧版第二群', 'chat_id': 'legacy'})
        before = self.rows(legacy_columns=True)
        restarted = Platform(self.root)
        self.assertEqual(self.rows(legacy_columns=True), before)
        self.assertTrue(restarted.channel(legacy['id'])['group_id'])
        self.assertEqual(len(restarted.list_channels()), 2)
        with self.assertRaisesRegex(ValueError, '只能绑定一个群'):
            restarted.save_channel(self.admin, self.bot['id'], {'chat_id': 'third'})
        restarted.save_channel(self.admin, self.bot['id'], {'active': False}, legacy['id'])
        restarted.archive_channel(self.admin, self.bot['id'], legacy['id'])
        self.assertEqual(len(restarted.list_channels()), 1)
        self.assertEqual(restarted.channel(legacy['id'])['chat_id'], 'legacy')
        self.assertEqual(len(restarted.list_channels(include_archived=True)), 2)

    def test_scoped_administrator_cannot_change_another_bot_binding(self):
        other = self.p.create(self.admin, {'name': '另一个机器人', 'chat_id': '266382'})
        grant = self.p.grant(self.admin, [self.bot['id']])
        before = self.rows()
        response = self.client.patch(f"/api/bots/{other['id']}/channels/{other['channels'][0]['id']}",
            headers={'Authorization': 'Bearer ' + grant['token']}, json={'active': False})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.rows(), before)
