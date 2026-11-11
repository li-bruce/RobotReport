"""One transport-to-command boundary, independent of business and authority.

Adapters should supply command text or explicit mention spans. Legacy callbacks
may contain a leading <at> element or a configured display name. Only that
leading address is removed; arguments, user mentions and identities are never
inferred. See docs/MESSAGE_PARSING.md for the wire contract and fallback policy.
"""
from dataclasses import dataclass
import html
import re
import uuid
from .message_styles import clean_styles


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
    recipient_name: str = ''


@dataclass(frozen=True)
class LiteralCommand:
    """Internal generated command; never constructed from callback metadata."""
    text: str


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

    A supplied name is only a compatibility hint for old unstructured clients.
    Authenticated transport identity never depends on a displayed name.
    """
    if isinstance(content, LiteralCommand):
        return CommandText(message_input(content.text).text.strip(), 'command')
    if isinstance(content, MessageInput):
        value = message_input(content.text, content.text_format,
                              [dict(start=s.start, end=s.end, kind=s.kind) for s in content.mentions])
    else:
        value = message_input(content, 'legacy')
    if value.text_format == 'command':
        return CommandText(clean_styles(value.text), 'command')
    if value.text_format == 'mentions':
        if value.mentions:
            marker = 'ROBORTMENTION' + uuid.uuid4().hex
            fragments, offset, labels = [], 0, {}
            for index, span in enumerate(value.mentions):
                key = marker + str(index) + 'END'
                fragments.extend((value.text[offset:span.start], key))
                labels[key] = value.text[span.start:span.end]
                offset = span.end
            fragments.append(value.text[offset:])
            text = clean_styles(''.join(fragments))
            first = value.mentions[0]
            first_key = next(iter(labels))
            if first.kind == 'bot' and text.startswith(first_key):
                body = _after_address(text[len(first_key):])
                # Multiple leading addressees are ambiguous; never discard
                # the next recipient and reinterpret the rest as a command.
                if any(body.startswith(key) for key in list(labels)[1:]):
                    raise ValueError('请一次只 @ 一个机器人，并在后面填写指令')
                for key, original in labels.items():
                    body = body.replace(key, original)
                label = html.unescape(labels[first_key]).strip().lstrip('@＠').strip()
                return CommandText(body, 'mentions', label)
            for key, original in labels.items():
                text = text.replace(key, original)
            return CommandText(text, 'mentions')
        return CommandText(clean_styles(value.text), 'mentions')

    text = clean_styles(value.text, bot_name)
    tag = _AT.match(text)
    if tag:
        body = _after_address(text[tag.end():])
        if _AT.match(body) or body.startswith(('@', '＠')):
            raise ValueError('请一次只 @ 一个机器人，并在后面填写指令')
        label = re.sub(r'^<at\b[^<>]*>|</at\s*>$', '', tag.group(), flags=re.I).strip()
        return CommandText(body, 'legacy_tag', label)
    if text.startswith(('@', '＠')):
        for display_hint in (bot_name,):
            letters = ''.join(html.unescape(display_hint).split())
            if not letters:
                continue
            name = r'[^\S\r\n]*'.join(re.escape(letter) for letter in letters)
            address = re.match(r'[@＠][^\S\r\n]*' + name + r'(?=\s|[:：,，]|$)', text)
            if address:
                body = _after_address(text[address.end():])
                if _AT.match(body) or body.startswith(('@', '＠')):
                    raise ValueError('请一次只 @ 一个机器人，并在后面填写指令')
                return CommandText(body, 'legacy_name', text[1:address.end()].strip().rstrip(':：,，'))
        # Unicode spaces may also appear inside labels and arguments. Never
        # jump ahead to a later separator or business keyword. Arbitrary
        # multiword labels use the explicit tag/span contract above.
        boundary = re.match(r'[@＠][^\S\r\n]*([^\s@＠:：,，]+(?:[^\S\r\n]+[-—–·][^\S\r\n]+[^\s@＠:：,，]+)*)(?=\s|[:：,，]|$)', text)
        if boundary:
            body = _after_address(text[boundary.end():])
            if _AT.match(body) or body.startswith(('@', '＠')):
                raise ValueError('请一次只 @ 一个机器人，并在后面填写指令')
            return CommandText(body, 'legacy_address', boundary[1].strip())
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
