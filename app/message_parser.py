"""One transport-to-command boundary, independent of business and authority.

Adapters should supply command text or explicit mention spans. Legacy callbacks
may contain a leading <at> element or a configured display name. Only that
leading address is removed; arguments, user mentions and identities are never
inferred. See docs/MESSAGE_PARSING.md for the wire contract and fallback policy.
"""
from dataclasses import dataclass
import html
import re


MAX_TEXT = 4000
FORMATS = ('command', 'mentions', 'legacy')
_AT = re.compile(r'<at\b[^<>]*>[^<>]*</at\s*>', re.I)
_ADDRESS_ERROR = '未识别到完整的机器人提及，请在群中重新选择 @机器人，再发送 注册 或 帮助。'


@dataclass(frozen=True)
class MentionSpan:
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class MessageInput:
    text: str
    text_format: str = 'command'
    mentions: tuple[MentionSpan, ...] = ()


@dataclass(frozen=True)
class CommandText:
    text: str
    addressing: str


def message_input(text, text_format='command', mentions=None):
    """Validate adapter data before it can reach any business writes.

    Offsets count Unicode code points in the original text, not UTF-16 units.
    Mention metadata describes presentation only, never the sender or roles.
    """
    if not isinstance(text, str) or not 0 < len(text) <= MAX_TEXT or not text.strip():
        raise ValueError('请输入1至4000字指令')
    if text_format not in FORMATS:
        raise ValueError('text_format 须为 command、mentions 或 legacy')
    if mentions is None:
        mentions = []
    if not isinstance(mentions, list) or len(mentions) > 32:
        raise ValueError('mentions 须为不超过32项的提及范围列表')
    if text_format != 'mentions' and mentions:
        raise ValueError('提及范围须配合 text_format=mentions 使用')
    spans = []
    previous_end = 0
    for item in mentions:
        if not isinstance(item, dict) or set(item) != {'start', 'end', 'kind'}:
            raise ValueError('提及范围须包含 start、end、kind')
        start, end, kind = item['start'], item['end'], item['kind']
        if (type(start) is not int or type(end) is not int or
                not previous_end <= start < end <= len(text) or kind not in ('bot', 'user')):
            raise ValueError('提及范围须按顺序排列、不能重叠，且 kind 为 bot 或 user')
        spans.append(MentionSpan(start, end, kind))
        previous_end = end
    return MessageInput(text, text_format, tuple(spans))


def _after_address(text):
    # One conventional delimiter belongs to the address; preserve everything
    # else, including punctuation/names inside the command body.
    text = text.lstrip()
    if text.startswith((':', '：', ',', '，')):
        text = text[1:].lstrip()
    return text.rstrip()


def normalize_message(content, bot_name=''):
    """Normalize once, after authentication; raw string callers are legacy.

    A supplied name is a text-only compatibility hint. It never chooses a bot,
    group, user or permission. Unknown/partial plain-text addresses fail closed.
    """
    if isinstance(content, MessageInput):
        value = message_input(content.text, content.text_format,
                              [dict(start=s.start, end=s.end, kind=s.kind) for s in content.mentions])
    else:
        value = message_input(content, 'legacy')
    if value.text_format == 'command':
        return CommandText(value.text.strip(), 'command')
    if value.text_format == 'mentions':
        if value.mentions:
            first = value.mentions[0]
            if first.kind == 'bot' and not value.text[:first.start].strip():
                body = _after_address(value.text[first.end:])
                # Multiple leading addressees are ambiguous; never discard
                # the next recipient and reinterpret the rest as a command.
                if any(not value.text[first.end:s.start].strip() for s in value.mentions[1:]):
                    raise ValueError('请一次只 @ 一个机器人，并在后面填写指令')
                return CommandText(body, 'mentions')
        return CommandText(value.text.strip(), 'mentions')

    text = html.unescape(value.text).strip()
    tag = _AT.match(text)
    if tag:
        body = _after_address(text[tag.end():])
        if _AT.match(body) or body.startswith(('@', '＠')):
            raise ValueError('请一次只 @ 一个机器人，并在后面填写指令')
        return CommandText(body, 'legacy_tag')
    if text.startswith(('@', '＠')):
        letters = ''.join(html.unescape(bot_name).split())
        if letters:
            name = r'[^\S\r\n]*'.join(re.escape(letter) for letter in letters)
            address = re.match(r'[@＠][^\S\r\n]*' + name + r'(?=\s|[:：,，]|$)', text)
            if address:
                body = _after_address(text[address.end():])
                if _AT.match(body) or body.startswith(('@', '＠')):
                    raise ValueError('请一次只 @ 一个机器人，并在后面填写指令')
                return CommandText(body, 'legacy_name')
        raise ValueError(_ADDRESS_ERROR)
    if text.lower().startswith('<at'):
        raise ValueError(_ADDRESS_ERROR)
    return CommandText(text, 'none')


def strip_mention(content, bot_name=''):
    """Compatibility facade; all addressing rules live in normalize_message."""
    return normalize_message(content, bot_name).text


def parse(content, default_venue=1, today=None, *, bot_name=''):
    from .selection_parser import parse_selection
    return parse_selection(normalize_message(content, bot_name).text, default_venue, today)
