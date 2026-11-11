"""Literal venue/date/session selectors shared by fixed commands and AI grounding.

This module only rearranges a small, closed command grammar. It does not infer
an identity, a named event, an approximate time or a missing session number.
"""
from datetime import date, timedelta
import re
from .input_formats import NUMBER, SCORE_SEPARATOR, date_prefix, integer


ORDINAL = re.compile(rf'(?<![0-9０-９])第?\s*(?P<number>{NUMBER})\s*场(?!地|次序)')
_RELATIVE = {'今天': 0, '今日': 0, '明天': 1, '明日': 1, '后天': 2, '后日': 2,
             '昨天': -1, '昨日': -1}
_TIME = re.compile(rf'(?P<hour>{NUMBER})\s*(?:[:：]\s*(?P<minute>{NUMBER})(?![0-9０-９])|[时点]\s*(?:(?P<half>半)|(?P<cminute>{NUMBER})\s*分?)?)')
_ACTION = re.compile(rf'取\s*消\s*报\s*名|报\s*名(?:\s*[+＋]\s*{NUMBER})?(?:\s*接\s*龙)?|名\s*单')


def session_number(value):
    return integer(value, 1, 999)


def parse_selection(text, default_venue=1, today=None):
    """Read literal prefixes and a registration action before/among selectors."""
    text = text.strip()
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
            if 'number' in chosen and re.fullmatch(rf'{NUMBER}\s*{SCORE_SEPARATOR}\s*{NUMBER}(?:\s*[,，;；]\s*{NUMBER}\s*{SCORE_SEPARATOR}\s*{NUMBER})*', value):
                # An ordinal followed by points belongs to the score parser.
                # Do not reinterpret the score as a clock selection.
                break
            match = re.match(rf'场地\s*({NUMBER})\s*', value)
            if match:
                set_value('venue_id', integer(match[1], 1, 2_147_483_647))
            else:
                selected_date = date_prefix(value)
                if selected_date:
                    set_value('day', selected_date[1])
                    value = value[selected_date[0]:]
                    continue
                else:
                    match = re.match('|'.join(_RELATIVE), value)
                    if match:
                        set_value('day', (today + timedelta(days=_RELATIVE[match[0]])).isoformat())
                    else:
                        match = _TIME.match(value)
                        if match:
                            if match.end() == len(value) and 'day' not in chosen and not allow_final_clock:
                                break
                            hour = integer(match['hour'])
                            minute = 30 if match['half'] else integer(match['minute'] or match['cminute'] or '0')
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
            opcode = re.sub(r'\s+', '', action[0]).replace('＋', '+')
            opcode = re.sub(rf'\+({NUMBER})', lambda m: '+'+str(integer(m[1],0,999)), opcode)
            opcode = opcode.replace('接龙', ' 接龙')
            text = opcode + remainder
        else:
            chosen.clear()
            chosen.update(before)
    level = re.fullmatch(rf'自\s*评\s*({NUMBER})\s*级?', text)
    if level:
        text = '自评'+str(integer(level[1],1,9))+'级'
    if 'at' in chosen and 'number' in chosen:
        raise ValueError('请用场次序号或具体时间选择活动，不要同时指定两种方式')
    return text, {'day': chosen.get('day'), 'venue_id': chosen.get('venue_id', default_venue),
                  'at': chosen.get('at'), 'number': chosen.get('number')}
