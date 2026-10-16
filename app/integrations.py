"""Explicit endpoint policies and the provider-neutral JSON Webhook contract."""
from urllib.parse import urlsplit
import hashlib
import re

from . import env_settings


LEGACY_AI_URL = 'https://api.siliconflow.cn/v1'


def endpoint_urls(root, key):
    result = set()
    for value in env_settings.runtime_value(root, key).split(','):
        value = value.strip().rstrip('/')
        if not value:
            continue
        try:
            url = urlsplit(value)
            if (url.scheme != 'https' or not url.hostname or url.port not in (None, 443)
                    or url.username or url.password or url.query or url.fragment
                    or not url.path.startswith('/') or len(value) > 512):
                raise ValueError()
        except ValueError:
            raise ValueError(f'{key} 须为不含密钥的完整 HTTPS 接口地址，以逗号分隔') from None
        result.add('https://' + url.hostname + url.path)
    return result


def ai_endpoints(root):
    return {LEGACY_AI_URL, 'https://api.openai.com/v1'} | endpoint_urls(root, 'ROBORT_AI_ENDPOINTS')


def validate_ai_url(value, allowed):
    if not isinstance(value, str) or value.rstrip('/') not in allowed:
        raise ValueError('AI 接口地址尚未配置，请在本地环境文件 ROBORT_AI_ENDPOINTS 添加该 HTTPS 端点')
    return value.rstrip('/')


def json_webhook(url, root):
    parsed = urlsplit(url)
    endpoint = 'https://' + (parsed.hostname or '') + parsed.path
    return endpoint in endpoint_urls(root, 'ROBORT_WEBHOOK_JSON_ENDPOINTS')


def callback_path(platform, bot_id, token):
    channels = platform.list_channels(bot_id=bot_id)
    generic = bool(channels and json_webhook(platform.channel(channels[0]['id'])['webhook_url'], platform.root))
    return f'/webhooks/{bot_id}/{token}' if generic else f'/kingsoft/callback/{bot_id}/{token}'


def outbound_payload(payload, url, root, *, event_id='', group_id=''):
    if not json_webhook(url, root):
        return payload
    kind = payload.get('msgtype', 'markdown')
    return {'type': kind, 'content': payload.get(kind, {}), 'event_id': str(event_id), 'group_id': group_id}


def accepted(response, url, root):
    if json_webhook(url, root):
        # The adapter reports delivery acceptance using HTTP status, including 204.
        return 200 <= response.status_code < 300
    body = response.json()
    return response.status_code == 200 and isinstance(body, dict) and body.get('result') == 'ok'


def incoming_json(platform, bot_id, token, body):
    """The unguessable bot callback URL authenticates an already trusted adapter.

    The adapter must verify the originating platform's signature and identity.
    It cannot select another bot or switch the bot's existing group binding.
    """
    platform.callback_context(bot_id, token)
    channels = platform.list_channels(bot_id=bot_id, active_only=True)
    if len(channels) != 1:
        raise PermissionError('机器人连接不存在或已停用')
    channel = platform.channel(channels[0]['id'], active=True)
    if not json_webhook(channel['webhook_url'], platform.root):
        raise PermissionError('此机器人未配置通用 JSON Webhook')
    if not isinstance(body, dict) or set(body) - {'event_id','group_id','user_id','text','display_name'}:
        raise ValueError('JSON Webhook 字段无效')
    event_id = body.get('event_id')
    if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', event_id):
        raise ValueError('event_id 须为稳定且唯一的消息标识')
    text = body.get('text')
    if not isinstance(text, str) or not 0 < len(text) <= 4000:
        raise ValueError('text 须为1至4000字')
    name = body.get('display_name')
    if name is not None and (not isinstance(name, str) or len(name) > 80):
        raise ValueError('display_name 格式无效')
    # Numeric ctime is retained solely for the existing group-discovery validator.
    return {'chatid': body.get('group_id'), 'creator': body.get('user_id'),
            'content': text, 'creator_name': name, 'robot_key': channel['robot_key'],
            'ctime': str(int(hashlib.sha256(event_id.encode()).hexdigest()[:15], 16)),
            '_event_id': event_id}
