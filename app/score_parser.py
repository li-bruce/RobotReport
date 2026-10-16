"""Parse score utterances without selecting events or granting permissions."""
import re

_CHINESE = '零〇一二两三四五六七八九十百千'
_ORDINAL = rf'(?:[0-9]+|[{_CHINESE}]+)'
_PAIR = r'([0-9]+)\s*[:：\-－—–比/]\s*([0-9]+)'
_SCORE_HINT = r'[0-9]\s*[:：\-－—–比/]\s*[0-9]'


def _number(value):
    if value.isascii() and value.isdigit():
        number = int(value)
    else:
        digits = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3,
                  '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
        units = {'十': 10, '百': 100, '千': 1000}
        if not any(char in units for char in value):
            number = int(''.join(str(digits[char]) for char in value))
        else:
            number, pending, previous_unit = 0, None, 10000
            for char in value:
                if char in digits:
                    if pending not in (None, 0):
                        raise ValueError('中文场次编号格式无效，请使用“第十二场”或“第12场”')
                    pending = digits[char]
                else:
                    unit = units[char]
                    if unit >= previous_unit:
                        raise ValueError('中文场次编号格式无效，请检查十、百、千的顺序')
                    number += (pending or 1) * unit
                    pending, previous_unit = None, unit
            number += pending or 0
    if not 1 <= number <= 10000:
        raise ValueError('比赛场次编号须为1至10000')
    return number


def parse_score(text):
    """Return a parsed score request or None for text that is not a score command.

    Selection by title, event access, and badminton winning-score rules belong to
    the caller. An undelimited Arabic ordinal/score is intentionally rejected.
    """
    if not isinstance(text, str):
        return None
    if len(text) > 1000:
        raise ValueError('记分指令最多1000字')
    text = text.strip()
    if not text:
        return None
    # ISO dates occur in reservations and in default event titles. Only a
    # delimited title followed by a score-shaped body can opt into score parsing.
    prefix = re.match(r'^([^,，]+)[,，]\s*(.*)$', text, re.S)
    if re.match(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:\s|$)', text):
        score_body = prefix[2] if prefix else ''
        if not (prefix and re.search(_SCORE_HINT, score_body) and re.match(
                rf'^(?:第?\s*{_ORDINAL}\s*场|第?\s*[0-9]+\s+|第?\s*[{_CHINESE}]+)', score_body)):
            return None
    title, event_id, body = None, None, text
    event = re.match(r'^赛事\s*([0-9]+)\s+', text)
    if event:
        event_id, body = int(event[1]), text[event.end():].strip()
        if not 1 <= event_id <= 2_147_483_647:
            raise ValueError('赛事编号无效')
    else:
        # A comma inside a multi-game score is not an event-name separator.
        # A default title's leading date is not a score pair either.
        title_hint = re.sub(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:\s+|$)', '', prefix[1]) if prefix else ''
        if prefix and not re.search(_SCORE_HINT, title_hint) and not re.fullmatch(rf'第?\s*{_ORDINAL}\s*场?', prefix[1].strip()):
            title, body = prefix[1].strip(), prefix[2].strip()
            if not title or len(title) > 200 or '\n' in title:
                raise ValueError('赛事名称须为1至200字，且不能换行')
    if not re.search(_SCORE_HINT, body):
        return None
    # 场 is an unambiguous separator even if surrounding spaces are absent.
    match = re.fullmatch(rf'第?\s*({_ORDINAL})\s*场\s*(.+)', body, re.S)
    if not match:
        # An explicit space separates Arabic ordinals; Chinese numerals already
        # provide a lexical boundary before an Arabic score such as 十二21—19.
        match = re.fullmatch(rf'第?\s*([0-9]+)\s+(.+)', body, re.S)
    if not match:
        match = re.fullmatch(rf'第?\s*([{_CHINESE}]+)\s*(.+)', body, re.S)
    if not match:
        if re.match(r'^第?\s*[0-9]+\s*[:：\-－—–比/]', body):
            raise ValueError('场次与比分之间需要“场”或空格，例如：12场21-19 或 12 21:19')
        return None
    number, score_text = _number(match[1]), match[2]
    pairs = re.split(r'[,，;；]', score_text)
    if not 1 <= len(pairs) <= 9:
        raise ValueError('一次最多提交9局比分')
    scores = []
    for pair in pairs:
        score = re.fullmatch(r'\s*' + _PAIR + r'\s*', pair)
        if not score:
            raise ValueError('比分格式无效，例如：第12场21:19，18:21，21:16')
        values = [int(score[1]), int(score[2])]
        if any(value > 999 for value in values):
            raise ValueError('单局比分须为0至999')
        scores.append(values)
    return {'title': title, 'event_id': event_id, 'number': number, 'scores': scores}
