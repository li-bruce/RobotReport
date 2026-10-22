"""Optional display metadata for venues, independent of admission rules."""
import json
import unicodedata


def normalize_attributes(value):
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('场地属性须为列表，最多 20 项')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {'label', 'value'}:
            raise ValueError('每项场地属性须包含名称和内容')
        clean = {}
        for key, limit, title in (('label', 24, '属性名称'), ('value', 120, '属性内容')):
            text = item[key]
            if not isinstance(text, str):
                raise ValueError(f'{title}须为文字')
            text = text.strip()
            if not text or len(text) > limit or any(unicodedata.category(c) == 'Cc' for c in text):
                raise ValueError(f'{title}须为 1–{limit} 字的单行文字')
            clean[key] = text
        unique = unicodedata.normalize('NFKC', clean['label']).casefold()
        if unique in seen:
            raise ValueError('属性名称不能重复')
        seen.add(unique)
        result.append(clean)
    return result


def venue_record(row):
    record = dict(row)
    record['active'] = bool(record['active'])
    record['attributes'] = json.loads(record.get('attributes') or '[]')
    return record
