"""Local deployment settings; no shell expansion and no secret values in errors.

Files supply startup configuration. The admin UI writes changed connection and
AI settings back to the same private file as well as the runtime databases.
An explicit state passed by tests never loads the checkout's private file.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
KEYS = (
    'ROBORT_STATE_DIR', 'ROBORT_LIVE_SEND', 'ROBORT_PUBLIC_BASE_URL',
    'ROBORT_ADMIN_TOKEN', 'ROBORT_INITIAL_ADMIN_PASSWORD',
    'ROBORT_AI_API_KEY', 'ROBORT_AI_MODEL', 'ROBORT_AI_BASE_URL',
    'ROBORT_AI_ENABLED', 'ROBORT_AI_ENDPOINTS', 'ROBORT_WEBHOOK_ENDPOINTS',
    'ROBORT_WEBHOOK_JSON_ENDPOINTS', 'ROBORT_BOTS_JSON',
)
_bindings = {}
_locks = {}
_guard = threading.RLock()


def parse(text):
    result = {}
    if len(text.encode('utf-8')) > 512 * 1024:
        raise ValueError('本地环境配置文件过大')
    for number, line in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise ValueError(f'环境配置第{number}行格式无效')
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if key not in KEYS or key in result:
            raise ValueError(f'环境配置第{number}行名称无效或重复')
        try:
            if value.startswith('"'):
                value = json.loads(value)
            elif value.startswith("'"):
                if not value.endswith("'") or len(value) < 2:
                    raise ValueError()
                value = value[1:-1]
            if not isinstance(value, str) or any(c in value for c in ('\n', '\r', '\0')):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(f'环境配置第{number}行值格式无效') from None
        result[key] = value
    return result


def render(values):
    lines = ['# Private Robort configuration. Never commit this file.',
             '# Values are literal; shell expansion is not performed.']
    for key in KEYS:
        if key in values:
            lines.append(key + '=' + (json.dumps(values[key], ensure_ascii=False) if values[key] else ''))
    return '\n'.join(lines) + '\n'


def _atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.robort-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.chmod(name, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class LocalEnv:
    def __init__(self, path=None, process=None):
        self.path = Path(path).absolute() if path else None
        self.process = {key: value for key, value in (os.environ if process is None else process).items()
                        if key in KEYS and value != ''}
        self.suspended = 0
        with _guard:
            self.lock = _locks.setdefault(str(self.path), threading.RLock())
        self.values()  # Fail early, without printing file contents.

    def file_values(self):
        if not self.path or not self.path.exists():
            return {}
        if self.path.is_symlink():
            raise ValueError('环境配置不能是符号链接')
        if self.path.stat().st_size > 512 * 1024:
            raise ValueError('本地环境配置文件过大')
        return parse(self.path.read_text(encoding='utf-8'))

    def values(self):
        return self.file_values() | self.process

    def get(self, key, default=''):
        return self.values().get(key) or default

    @contextmanager
    def change(self, changes=None, bot_id=None, bot_values=None):
        if self.suspended:
            yield
            return
        with self.lock:
            values = self.file_values()
            before = self.path.read_bytes() if self.path and self.path.exists() else None
            changes = dict(changes or {})
            if bot_id is not None and (bot_values is None or bot_values):
                bots = bot_settings(self.values().get('ROBORT_BOTS_JSON', ''))
                if bot_values is None:
                    bots.pop(bot_id, None)
                else:
                    item = bots.get(bot_id, {}).copy()
                    for key, value in bot_values.items():
                        if value is None:
                            item.pop(key, None)
                        else:
                            item[key] = value
                    bots[bot_id] = item
                changes['ROBORT_BOTS_JSON'] = json.dumps(bots, ensure_ascii=False, separators=(',', ':'))
            for key, value in changes.items():
                same_bots = key == 'ROBORT_BOTS_JSON' and key in self.process and bot_settings(self.process[key]) == bot_settings(value)
                if key in self.process and self.process[key] != value and not same_bots:
                    raise ValueError(f'{key} 由进程环境管理，请修改服务环境并重启')
            if not changes or not self.path:
                yield
                return
            values.update(changes)
            after = render(values).encode('utf-8')
            parse(after.decode('utf-8'))
            try:
                _atomic(self.path, after)
            except OSError:
                raise ValueError('无法保存本地环境配置，请检查文件目录权限') from None
            try:
                yield
            except BaseException:
                if before is None:
                    self.path.unlink(missing_ok=True)
                else:
                    _atomic(self.path, before)
                raise


def load(env_file=None, *, explicit_state=False):
    if env_file is None and explicit_state:
        return LocalEnv(process={})
    selected = env_file if env_file is not None else os.getenv('ROBORT_ENV_FILE')
    if selected == '-':
        return LocalEnv()
    path = Path(selected).expanduser() if selected else ROOT / '.env.local'
    if not path.is_absolute():
        path = ROOT / path
    if selected and not path.is_file():
        raise ValueError('指定的本地环境配置文件不存在')
    return LocalEnv(path)


def bind(root, settings):
    with _guard:
        _bindings[str(Path(root).resolve())] = settings


def settings_for(root):
    if root is None:
        return None
    return _bindings.get(str(Path(root).resolve()))


@contextmanager
def change(root, values=None, *, bot_id=None, bot_values=None):
    settings = settings_for(root)
    if settings:
        with settings.change(values, bot_id, bot_values):
            yield
    else:
        yield


@contextmanager
def locked(root):
    settings = settings_for(root)
    if settings:
        with settings.lock:
            yield
    else:
        yield


def runtime_value(root, key, default=''):
    settings = settings_for(root)
    return settings.get(key, default) if settings else os.getenv(key, default)


def boolean(value, key):
    if value not in ('0', '1', 'true', 'false'):
        raise ValueError(f'{key} 只能为0或1、true或false')
    return value in ('1', 'true')


def bot_settings(value):
    if not value:
        return {}
    try:
        result = json.loads(value)
        if not isinstance(result, dict) or len(result) > 500:
            raise ValueError()
        for bot_id, item in result.items():
            if not re.fullmatch(r'[a-f0-9]{32}', bot_id) or not isinstance(item, dict):
                raise ValueError()
            if set(item) - {'webhook_url', 'robot_key', 'callback_token'}:
                raise ValueError()
            if any(not isinstance(v, str) or len(v) > 2048 or any(c in v for c in ('\n', '\r', '\0')) for v in item.values()):
                raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ValueError('ROBORT_BOTS_JSON 格式无效，请使用导出脚本生成或按说明填写') from None


def extra_endpoints(root=None):
    from urllib.parse import urlsplit
    value = runtime_value(root, 'ROBORT_WEBHOOK_ENDPOINTS')
    result = set()
    for item in value.split(','):
        item = item.strip()
        if not item:
            continue
        try:
            url = urlsplit(item)
            if (url.scheme != 'https' or not url.hostname or url.port not in (None, 443)
                    or url.username or url.password or url.query or url.fragment
                    or not url.path.startswith('/') or len(item) > 512):
                raise ValueError()
        except ValueError:
            raise ValueError('ROBORT_WEBHOOK_ENDPOINTS 必须为不含密钥的 HTTPS 发送端点，使用逗号分隔') from None
        result.add((url.hostname, url.path))
    return result


def apply(platform, settings):
    """Apply trusted local configuration, retaining normal domain validation."""
    from .db import connect
    from .security import Principal
    from .service import Service
    values = settings.values()
    extra_endpoints(platform.root)
    bots = bot_settings(values.get('ROBORT_BOTS_JSON', ''))
    prepared = []
    with connect(platform.path) as db:
        used = {row['webhook_url']:row['bot_id'] for row in db.execute(
            "SELECT webhook_url,bot_id FROM bot_channels WHERE active=1 AND webhook_url<>''")}
    for bot_id, item in bots.items():
        platform.bot(bot_id)
        channels = platform.list_channels(bot_id=bot_id)
        channel_data = {k:v for k,v in item.items() if k != 'callback_token'}
        if channel_data:
            if len(channels) != 1:
                raise ValueError('环境配置中的机器人需要先在后台建立唯一群连接')
            record = platform._channel_data(channel_data, platform.channel(channels[0]['id']))
            owner = used.get(record['webhook_url'])
            if record['active'] and owner and owner != bot_id:
                raise ValueError('环境配置中的Webhook与其他机器人冲突，请逐个调整连接')
            if record['active'] and record['webhook_url']:
                used[record['webhook_url']] = bot_id
        if 'callback_token' in item and len(item['callback_token']) < 24:
            raise ValueError('环境配置中的回调令牌至少24字符')
        prepared.append((bot_id, item))
    data = {}
    ai = {}
    for suffix, field in (('API_KEY','api_key'), ('MODEL','model'), ('BASE_URL','base_url'), ('ENABLED','enabled')):
        key = 'ROBORT_AI_' + suffix
        if values.get(key):
            ai[field] = boolean(values[key], key) if field == 'enabled' else values[key]
    if ai:
        data['ai'] = ai
    if values.get('ROBORT_PUBLIC_BASE_URL'):
        data['public_base_url'] = values['ROBORT_PUBLIC_BASE_URL']
    principal = Principal('local-environment', 'platform_admin', ())
    settings.suspended += 1
    try:
        if data:
            service = Service(platform.context(principal))
            current = service.config(True)
            if any(any(current[key].get(k) != v for k,v in value.items()) if isinstance(value,dict) else current[key] != value for key,value in data.items()):
                service.update_config(data)
        for bot_id, item in prepared:
            bot = platform.bot(bot_id)
            from .security import digest
            changed = {k:v for k,v in item.items() if (bot['callback_hash'] != digest(v) if k == 'callback_token' else bot.get(k) != v)}
            if changed:
                platform.update(principal, bot_id, changed)
    finally:
        settings.suspended -= 1


def exported(platform, *, live=False, public_url=None):
    """Return private configuration to the caller; never log the returned object."""
    from urllib.parse import urlsplit
    from .security import Principal, digest
    from .service import Service
    principal = Principal('local-export', 'platform_admin', ())
    config = Service(platform.context(principal)).config(True)
    values = {key:'' for key in KEYS}
    for key in ('ROBORT_AI_ENDPOINTS', 'ROBORT_WEBHOOK_JSON_ENDPOINTS'):
        values[key] = runtime_value(platform.root, key)
    values.update(ROBORT_STATE_DIR=str(platform.root), ROBORT_LIVE_SEND='1' if live else '0',
                  ROBORT_PUBLIC_BASE_URL=public_url if public_url is not None else config['public_base_url'],
                  ROBORT_AI_API_KEY=config['ai'].get('api_key', ''), ROBORT_AI_MODEL=config['ai']['model'],
                  ROBORT_AI_BASE_URL=config['ai']['base_url'], ROBORT_AI_ENABLED='1' if config['ai']['enabled'] else '0')
    admin_file = platform.root/'admin.local.json'
    if admin_file.is_file():
        token = json.loads(admin_file.read_text(encoding='utf-8')).get('admin_token', '')
        if token:
            platform.authenticate(token)
            values['ROBORT_ADMIN_TOKEN'] = token
    bots = {}; endpoints = set()
    for visible in platform.list(principal):
        bot = platform.bot(visible['id'])
        item = {}
        channels = platform.list_channels(bot_id=bot['id'])
        if channels:
            if len(channels) != 1:
                raise ValueError('请先处理同一机器人多个当前群连接，再导出配置')
            channel = platform.channel(channels[0]['id'])
            item.update(webhook_url=channel['webhook_url'], robot_key=channel['robot_key'])
            if channel['webhook_url']:
                url = urlsplit(channel['webhook_url'])
                endpoints.add('https://' + url.netloc + url.path)
        callback = platform.root_for(bot['id'])/'callback.local.json'
        if callback.is_file():
            token = json.loads(callback.read_text(encoding='utf-8')).get('callback_token', '')
            if digest(token) != bot['callback_hash']:
                raise ValueError('回调私有记录与当前凭据不一致，请先修复再导出')
            item['callback_token'] = token
        if item:
            bots[bot['id']] = item
    values['ROBORT_WEBHOOK_ENDPOINTS'] = ','.join(sorted(endpoints))
    values['ROBORT_BOTS_JSON'] = json.dumps(bots, ensure_ascii=False, separators=(',', ':'))
    return values
