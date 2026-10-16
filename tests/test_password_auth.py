from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from app.db import TZ, connect
from app.password_auth import PasswordAuth
from app.security import Platform


class PasswordAuthTests(unittest.TestCase):
    PASSWORD = 'test-only-password-value-12345'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.time = datetime(2026, 10, 13, 12, 0, tzinfo=TZ)
        self.platform = Platform(Path(self.temp.name), 'test-only-bootstrap-token-1234567890')
        self.auth = PasswordAuth(self.platform, clock=lambda: self.time)

    def initialized(self):
        self.auth.set_initial(self.PASSWORD)

    def test_only_salted_hash_and_session_digests_are_stored(self):
        self.assertFalse(self.auth.initialized())
        self.initialized()
        self.assertTrue(self.auth.initialized())
        result = self.auth.login(self.PASSWORD, 'test-peer-a')
        with connect(self.platform.path) as db:
            credential = dict(db.execute('SELECT * FROM password_credentials').fetchone())
            session = dict(db.execute('SELECT * FROM password_sessions').fetchone())
        self.assertGreaterEqual(credential['iterations'], 600_000)
        self.assertEqual(len(bytes.fromhex(credential['salt'])), 32)
        self.assertNotIn(self.PASSWORD, json.dumps(credential))
        self.assertNotIn(result['session_token'], json.dumps(session))
        self.assertNotIn(result['csrf_token'], json.dumps(session))
        self.assertEqual(self.auth.session(result['session_token'])['principal'].role, 'platform_admin')
        self.assertEqual(self.auth.session(result['session_token'])['csrf_token'], result['csrf_token'])

    def test_password_validation_and_initialization_cannot_overwrite(self):
        for value in ('', 'short', ' ' * 14, 'a' * 1025, None):
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                self.auth.set_initial(value)
        self.initialized()
        with self.assertRaises(PermissionError):
            self.auth.set_initial('another-test-only-password')
        self.auth.login(self.PASSWORD, 'a')
        with self.assertRaises(PermissionError):
            self.auth.login('another-test-only-password', 'a')

    def test_wrong_credentials_and_uninitialized_return_generic_failure(self):
        with self.assertRaisesRegex(PermissionError, '^登录失败，请稍后重试$'):
            self.auth.login('test-only-wrong-password', 'a')
        self.initialized()
        for value in ('wrong', '', None, 'x' * 1025):
            with self.subTest(value=type(value).__name__), self.assertRaisesRegex(PermissionError, '^登录失败，请稍后重试$'):
                self.auth.login(value, 'different-' + str(type(value)) + str(len(str(value))))

    def test_per_peer_limit_persists_across_instances_and_expires(self):
        self.initialized()
        with patch.object(PasswordAuth, '_verify', return_value=False):
            for _ in range(5):
                with self.assertRaises(PermissionError):
                    self.auth.login('test-only-invalid-password', 'same-peer')
        restarted = PasswordAuth(self.platform, clock=lambda: self.time)
        with self.assertRaises(PermissionError):
            restarted.login(self.PASSWORD, 'same-peer')
        restarted.login(self.PASSWORD, 'other-peer')
        self.time += timedelta(minutes=10)
        restarted.login(self.PASSWORD, 'same-peer')

    def test_global_failure_limit_blocks_distributed_peers(self):
        self.initialized()
        with patch.object(PasswordAuth, '_verify', return_value=False):
            for index in range(50):
                with self.assertRaises(PermissionError):
                    self.auth.login('test-only-invalid-password', 'peer-' + str(index))
        with self.assertRaises(PermissionError):
            self.auth.login(self.PASSWORD, 'new-peer')
        self.time += timedelta(minutes=10, seconds=1)
        self.auth.login(self.PASSWORD, 'new-peer')

    def test_concurrent_failed_logins_cannot_bypass_rate_limit(self):
        self.initialized()
        def fail(_):
            try:
                self.auth.login('wrong-test-password', 'same-peer')
            except PermissionError:
                return False
            return True
        with patch.object(PasswordAuth, '_verify', return_value=False), ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(fail, range(12))), [False] * 12)
        with connect(self.platform.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM password_failures').fetchone()[0], 5)

    def test_logout_and_expiry_revoke_cookie_sessions(self):
        self.initialized()
        first = self.auth.login(self.PASSWORD, 'a')
        second = self.auth.login(self.PASSWORD, 'a')
        for csrf in ('wrong', '错误', None):
            if csrf is not None:
                with self.subTest(csrf=csrf), self.assertRaises(PermissionError):
                    self.auth.logout(first['session_token'], csrf)
        self.auth.logout(first['session_token'], first['csrf_token'])
        with self.assertRaises(PermissionError):
            self.auth.session(first['session_token'])
        self.time += timedelta(hours=24)
        with self.assertRaises(PermissionError):
            self.auth.session(second['session_token'])
        for token in ('', '../x', None, 'f' * 1000):
            with self.subTest(token=type(token).__name__), self.assertRaises(PermissionError):
                self.auth.session(token)

    def test_password_change_checks_csrf_old_password_and_revokes_all(self):
        self.initialized()
        first = self.auth.login(self.PASSWORD, 'a')
        second = self.auth.login(self.PASSWORD, 'b')
        with self.assertRaises(PermissionError):
            self.auth.change(first['session_token'], self.PASSWORD, 'new-test-only-password', 'wrong')
        with self.assertRaises(PermissionError):
            self.auth.change(first['session_token'], 'incorrect-current-password', 'new-test-only-password', first['csrf_token'])
        self.auth.session(first['session_token'])
        self.auth.change(first['session_token'], self.PASSWORD, 'new-test-only-password', first['csrf_token'])
        for token in (first['session_token'], second['session_token']):
            with self.assertRaises(PermissionError):
                self.auth.session(token)
        with self.assertRaises(PermissionError):
            self.auth.login(self.PASSWORD, 'a')
        self.auth.login('new-test-only-password', 'a')
        self.assertEqual(self.platform.authenticate('test-only-bootstrap-token-1234567890').role, 'platform_admin')


if __name__ == '__main__':
    unittest.main()
