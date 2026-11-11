"""Numeric tokens and explicit calendar formats; never normalize whole names."""
from datetime import date
import re

DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
          '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_FINANCIAL = str.maketrans('壹贰貳叁參肆伍陆陸柒捌玖拾佰仟兩', '一二二三三四五六六七八九十百千两')
WIDTH = str.maketrans('０１２３４５６７８９', '0123456789')
CHINESE = '零〇一二两三四五六七八九十百千壹贰貳叁參肆伍陆陸柒捌玖拾佰仟兩'
NUMBER = rf'(?:[0-9０-９]+|[{CHINESE}]+|[①-⑳])'
DASHES = '-‐‑‒–—−－﹣'
SCORE_SEPARATOR = rf'[:：{re.escape(DASHES)}比/／]'
_DATE_SEP = rf'[{re.escape(DASHES)}/／.．]'
_YEAR = rf'(?:[0-9０-９]{{4}}|[{CHINESE}]{{4,12}})'
_DATE = re.compile(rf'(?P<year>{_YEAR})\s*(?:{_DATE_SEP}\s*(?P<month>{NUMBER})\s*{_DATE_SEP}\s*(?P<day>{NUMBER})(?![0-9０-９])|年\s*(?P<cmonth>{NUMBER})\s*月\s*(?P<cday>{NUMBER})\s*(?:日|号))')
_COMPACT_DATE = re.compile(r'(?P<year>[2２][0０][0-9０-９]{2})(?P<month>[0-9０-９]{2})(?P<day>[0-9０-９]{2})(?![0-9０-９])')


def integer(value, minimum=0, maximum=10000):
    value = value.translate(WIDTH).translate(_FINANCIAL)
    if len(value) == 1 and '①' <= value <= '⑳':
        number = ord(value)-ord('①')+1
    elif value.isascii() and value.isdigit():
        number = int(value)
    elif value and all(char in DIGITS for char in value):
        number = int(''.join(str(DIGITS[char]) for char in value))
    else:
        units = {'十': 10, '百': 100, '千': 1000}
        number, pending, previous_unit = 0, None, 10000
        for char in value:
            if char in DIGITS:
                if pending not in (None, 0):
                    raise ValueError('中文数字格式无效，请使用十二或12这样的写法')
                pending = DIGITS[char]
            elif char in units and units[char] < previous_unit:
                unit = units[char]
                number += (pending or 1)*unit
                pending, previous_unit = None, unit
            else:
                raise ValueError('数字格式无效，请检查数字和十、百、千的顺序')
        if not value:
            raise ValueError('请填写数字')
        number += pending or 0
    if not minimum <= number <= maximum:
        raise ValueError(f'数字须为{minimum}至{maximum}')
    return number


def date_prefix(text):
    match = _DATE.match(text) or _COMPACT_DATE.match(text)
    if not match:
        return None
    parts = match.groupdict()
    try:
        value = date(integer(parts['year'], 2000, 2099),
                     integer(parts.get('month') or parts['cmonth'], 1, 12),
                     integer(parts.get('day') or parts['cday'], 1, 31))
    except ValueError as error:
        raise ValueError('日期无效，请使用完整年月日，例如2026-11-12') from error
    return match.end(), value.isoformat()
