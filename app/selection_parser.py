"""Literal venue/date/session selectors shared by fixed commands and AI grounding.

This module only rearranges a small, closed command grammar. It does not infer
an identity, a named event, an approximate time or a missing session number.
"""
from datetime import date, timedelta
import re


DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
          '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
ORDINAL = re.compile(r'(?<![0-9])第?\s*(?P<number>[0-9]{1,3}|[零〇一二两三四五六七八九十百]{1,5})\s*场(?!地|次序)')
_DATE = re.compile(r'(20\d{2})\s*(?:[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})|年\s*(\d{1,2})月\s*(\d{1,2})(?:日|号))')
_RELATIVE = {'今天': 0, '今日': 0, '明天': 1, '明日': 1, '后天': 2, '后日': 2,
             '昨天': -1, '昨日': -1}
_TIME = re.compile(r'(\d{1,2})\s*(?:[:：]\s*(\d{1,2})(?!\d)|[时点](?:(\d{1,2})分?)?)')
_ACTION = re.compile(r'取消报名|报名(?:\s*\+\s*\d+)?(?:\s*接龙)?|名单')


def session_number(value):
    if value.isascii() and value.isdigit():
        result = int(value)
    elif value in DIGITS:
        result = DIGITS[value]
    elif value.count('十') == 1 and '百' not in value:
        left, right = value.split('十')
        if (left and DIGITS.get(left, 0) == 0) or (right and DIGITS.get(right, 0) == 0):
            raise ValueError('请使用明确的场次序号，例如第二场或第2场')
        result = (DIGITS[left] if left else 1) * 10 + (DIGITS[right] if right else 0)
    elif re.fullmatch(r'[一二三四五六七八九]百(?:零?[一二三四五六七八九]|[一二三四五六七八九]十[一二三四五六七八九]?)?', value):
        result = DIGITS[value[0]] * 100
        tail = value[2:].lstrip('零')
        if tail:
            result += session_number(tail)
    else:
        raise ValueError('请使用明确的场次序号，例如第二场或第2场')
    if not 1 <= result <= 999:
        raise ValueError('场次序号必须大于0且不超过999')
    return result


def parse_selection(text, default_venue=1, today=None):
    """Read literal prefixes and a registration action before/among selectors."""
    text = text.translate(str.maketrans('０１２３４５６７８９＋', '0123456789+')).strip()
    today = date.fromisoformat(today) if isinstance(today, str) else today
    if today is None:
        from .db import now
        today = now().date()
    chosen = {}

    def set_value(key, value):
        if key in chosen and chosen[key] != value:
            raise ValueError('一次指令只能选择一个场地、日期和场次，请分开发送')
        chosen[key] = value

    def consume(value, allow_final_clock=False):
        while value:
            value = value.lstrip()
            if 'number' in chosen and re.fullmatch(r'\d+\s*[:：\-－—–比/]\s*\d+(?:\s*[,，;；]\s*\d+\s*[:：\-－—–比/]\s*\d+)*', value):
                # An ordinal followed by points belongs to the score parser.
                # Do not reinterpret the score as a clock selection.
                break
            match = re.match(r'场地\s*(\d+)\s*', value)
            if match:
                if int(match[1]) < 1:
                    raise ValueError('场地编号必须大于0')
                set_value('venue_id', int(match[1]))
            else:
                match = _DATE.match(value)
                if match:
                    set_value('day', date(int(match[1]), int(match[2] or match[4]), int(match[3] or match[5])).isoformat())
                else:
                    match = re.match('|'.join(_RELATIVE), value)
                    if match:
                        set_value('day', (today + timedelta(days=_RELATIVE[match[0]])).isoformat())
                    else:
                        match = _TIME.match(value)
                        if match:
                            if match.end() == len(value) and 'day' not in chosen and not allow_final_clock:
                                break
                            hour, minute = int(match[1]), int(match[2] or match[3] or 0)
                            if hour > 23 or minute > 59:
                                raise ValueError('请使用有效的24小时制时间，例如20:30')
                            set_value('at', f'{hour:02d}:{minute:02d}')
                        else:
                            match = ORDINAL.match(value)
                            if match:
                                set_value('number', session_number(match['number']))
            if not match:
                break
            value = value[match.end():]
        return value.strip()

    text = consume(text)
    action = _ACTION.match(text)
    if action:
        # Roll back speculative selection unless the complete remainder is a
        # selector or the existing comma-separated guest-name syntax.
        before = dict(chosen)
        remainder = consume(text[action.end():], allow_final_clock=True)
        if remainder == '' or remainder.startswith(('，', ',')):
            opcode = re.sub(r'\s*\+\s*', '+', action[0])
            opcode = re.sub(r'\s*接龙', ' 接龙', opcode)
            text = opcode + remainder
        else:
            chosen.clear()
            chosen.update(before)
    if 'at' in chosen and 'number' in chosen:
        raise ValueError('请用场次序号或具体时间选择活动，不要同时指定两种方式')
    return text, {'day': chosen.get('day'), 'venue_id': chosen.get('venue_id', default_venue),
                  'at': chosen.get('at'), 'number': chosen.get('number')}
