"""Parse score utterances without selecting events or granting permissions."""
import re
from .input_formats import CHINESE, NUMBER, SCORE_SEPARATOR, date_prefix, integer

_CHINESE = CHINESE
_ORDINAL = NUMBER
_PAIR = rf'({NUMBER})\s*{SCORE_SEPARATOR}\s*({NUMBER})'
_SCORE_HINT = rf'{NUMBER}\s*{SCORE_SEPARATOR}\s*{NUMBER}'


def _number(value):
    return integer(value, 1, 10000)


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
    outcome = re.fullmatch(rf'(?:(.+?)[,，]\s*)?第?\s*({NUMBER})\s*场?\s*[,，]?\s*(甲方?|乙方?|[AaBb])?\s*(胜|负|赢|输)', text)
    if outcome:
        side = 'b' if outcome[3] in ('乙', '乙方', 'b', 'B') else 'a'
        if outcome[4] in ('负', '输'):
            side = 'a' if side == 'b' else 'b'
        return {'title': outcome[1].strip() if outcome[1] else None, 'event_id': None,
                'number': _number(outcome[2]), 'scores': [], 'outcome': side}
    # ISO dates occur in reservations and in default event titles. Only a
    # delimited title followed by a score-shaped body can opt into score parsing.
    prefix = re.match(r'^([^,，]+)[,，]\s*(.*)$', text, re.S)
    if date_prefix(text):
        score_body = prefix[2] if prefix else ''
        if not (prefix and re.search(_SCORE_HINT, score_body) and re.match(
                rf'^(?:第?\s*{_ORDINAL}\s*场|第?\s*[0-9０-９]+\s+|第?\s*[{_CHINESE}①-⑳]+)', score_body)):
            return None
    title, event_id, body = None, None, text
    event = re.match(r'^赛事\s*([0-9０-９]+)\s+', text)
    if event:
        event_id, body = int(event[1]), text[event.end():].strip()
        if not 1 <= event_id <= 2_147_483_647:
            raise ValueError('赛事编号无效')
    else:
        # A comma inside a multi-game score is not an event-name separator.
        # A default title's leading date is not a score pair either.
        unnamed_head = (re.fullmatch(rf'第?\s*(?:{_ORDINAL}\s*场\s*|[0-9０-９]+\s+|[{_CHINESE}①-⑳]+\s*){_PAIR}\s*', prefix[1])
                        if prefix else None)
        if prefix and not unnamed_head and not re.fullmatch(rf'第?\s*{_ORDINAL}\s*场?', prefix[1].strip()):
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
        match = re.fullmatch(rf'第?\s*([0-9０-９]+)\s+(.+)', body, re.S)
    if not match:
        match = re.fullmatch(rf'第?\s*([{_CHINESE}①-⑳]+)\s*(.+)', body, re.S)
    if not match:
        if re.match(rf'^第?\s*[0-9０-９]+\s*{SCORE_SEPARATOR}', body):
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
        values = [integer(score[1], 0, 999), integer(score[2], 0, 999)]
        scores.append(values)
    return {'title': title, 'event_id': event_id, 'number': number, 'scores': scores}
