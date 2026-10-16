"""Password-backed administrator sessions; registry stores hashes, never passwords."""
from datetime import timedelta
import hashlib
import hmac
import re
import secrets

from .db import connect, stamp, now
from .security import Principal


class PasswordAuth:
    ITERATIONS = 600_000
    SESSION_HOURS = 24

    def __init__(self, platform, clock=now):
        self.path, self.clock = platform.path, clock
        with connect(self.path) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS password_credentials(
                    id INTEGER PRIMARY KEY CHECK(id=1),salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,iterations INTEGER NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS password_sessions(
                    token_hash TEXT PRIMARY KEY,csrf_secret TEXT NOT NULL,
                    created_at TEXT NOT NULL,expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS password_failures(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,peer_hash TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS password_failures_time ON password_failures(created_at);
                CREATE INDEX IF NOT EXISTS password_failures_peer ON password_failures(peer_hash,created_at);
            ''')

    @staticmethod
    def _password(password):
        if not isinstance(password, str) or not 12 <= len(password) <= 1024 or not password.strip():
            raise ValueError('密码须为12至1024字符，不能全部为空白')
        return password.encode('utf-8')

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @classmethod
    def _hash(cls, password):
        raw = cls._password(password)
        salt = secrets.token_bytes(32)
        hashed = hashlib.pbkdf2_hmac('sha256', raw, salt, cls.ITERATIONS)
        return salt.hex(), hashed.hex(), cls.ITERATIONS

    @classmethod
    def _verify(cls, password, row):
        if not isinstance(password, str) or not 1 <= len(password) <= 1024:
            return False
        try:
            raw = password.encode('utf-8')
        except UnicodeError:
            return False
        if row is None:
            # Keep an uninitialized login indistinguishable from a wrong password.
            hashlib.pbkdf2_hmac('sha256', raw, b'not-a-real-password-salt', cls.ITERATIONS)
            return False
        actual = hashlib.pbkdf2_hmac('sha256', raw, bytes.fromhex(row['salt']), row['iterations'])
        return hmac.compare_digest(actual, bytes.fromhex(row['password_hash']))

    @staticmethod
    def _token(token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
            raise PermissionError('登录状态已失效，请重新登录')
        return token

    @staticmethod
    def _csrf(token, secret):
        return hmac.new(bytes.fromhex(secret), token.encode('utf-8'), hashlib.sha256).hexdigest()

    @staticmethod
    def _require_csrf(actual, expected):
        if not isinstance(actual, str) or not re.fullmatch(r'[a-f0-9]{64}', actual) or not hmac.compare_digest(actual, expected):
            raise PermissionError('请求校验失败，请刷新页面后重试')

    def initialized(self):
        with connect(self.path) as db:
            return db.execute('SELECT 1 FROM password_credentials WHERE id=1').fetchone() is not None

    def set_initial(self, password):
        salt, hashed, iterations = self._hash(password)
        with connect(self.path, True) as db:
            if db.execute('SELECT 1 FROM password_credentials WHERE id=1').fetchone():
                raise PermissionError('管理员密码已设置')
            db.execute('INSERT INTO password_credentials VALUES(1,?,?,?,?)', (salt, hashed, iterations, stamp(self.clock())))

    def login(self, password, peer):
        peer_hash = self._digest(str(peer or 'unknown')[:256])
        timestamp = stamp(self.clock())
        cutoff = stamp(self.clock() - timedelta(minutes=10))
        result = None
        with connect(self.path, True) as db:
            db.execute('DELETE FROM password_failures WHERE created_at<=?', (cutoff,))
            db.execute('DELETE FROM password_sessions WHERE expires_at<=?', (timestamp,))
            global_count = db.execute('SELECT COUNT(*) FROM password_failures').fetchone()[0]
            peer_count = db.execute('SELECT COUNT(*) FROM password_failures WHERE peer_hash=?', (peer_hash,)).fetchone()[0]
            if peer_count >= 5 or global_count >= 50:
                raise PermissionError('登录失败，请稍后重试')
            row = db.execute('SELECT * FROM password_credentials WHERE id=1').fetchone()
            if not self._verify(password, row):
                db.execute('INSERT INTO password_failures(peer_hash,created_at) VALUES(?,?)', (peer_hash, timestamp))
            else:
                token, csrf_secret = secrets.token_urlsafe(36), secrets.token_hex(32)
                expires_at = stamp(self.clock() + timedelta(hours=self.SESSION_HOURS))
                db.execute('INSERT INTO password_sessions VALUES(?,?,?,?)', (self._digest(token), csrf_secret, timestamp, expires_at))
                result = {'session_token': token, 'csrf_token': self._csrf(token, csrf_secret), 'expires_at': expires_at}
        if result is None:
            raise PermissionError('登录失败，请稍后重试')
        return result

    def _session(self, db, token):
        self._token(token)
        row = db.execute('SELECT * FROM password_sessions WHERE token_hash=? AND expires_at>?', (self._digest(token), stamp(self.clock()))).fetchone()
        if not row:
            raise PermissionError('登录状态已失效，请重新登录')
        return {'principal': Principal('password-admin', 'platform_admin', ()),
                'csrf_token': self._csrf(token, row['csrf_secret']), 'expires_at': row['expires_at']}

    def session(self, token):
        with connect(self.path) as db:
            return self._session(db, token)

    def logout(self, token, csrf=None):
        with connect(self.path, True) as db:
            current = self._session(db, token)
            if csrf is not None:
                self._require_csrf(csrf, current['csrf_token'])
            db.execute('DELETE FROM password_sessions WHERE token_hash=?', (self._digest(token),))

    def change(self, token, current_password, new_password, csrf):
        self._password(new_password)
        with connect(self.path, True) as db:
            current = self._session(db, token)
            self._require_csrf(csrf, current['csrf_token'])
            row = db.execute('SELECT * FROM password_credentials WHERE id=1').fetchone()
            if not self._verify(current_password, row):
                raise PermissionError('当前密码不正确')
            salt, hashed, iterations = self._hash(new_password)
            db.execute('UPDATE password_credentials SET salt=?,password_hash=?,iterations=?,updated_at=? WHERE id=1',
                       (salt, hashed, iterations, stamp(self.clock())))
            db.execute('DELETE FROM password_sessions')
