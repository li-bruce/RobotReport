"""Validated, cached China holiday data; no network in booking/query paths."""
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import httpx

from .db import dumps, stamp

URLS = ('https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json',
        'https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json')


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS holiday_calendars(
        year INTEGER PRIMARY KEY,data TEXT NOT NULL,source TEXT NOT NULL,fetched_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS holiday_plans(
        venue_id INTEGER NOT NULL,year INTEGER NOT NULL,holiday_id TEXT NOT NULL,
        definition TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(venue_id,year,holiday_id))''')


def validate(payload, year):
    if not isinstance(payload, dict) or type(payload.get('year')) is not int or payload['year'] != year:
        raise ValueError('节假日数据年份不匹配')
    days, papers = payload.get('days'), payload.get('papers')
    if not isinstance(days, list) or not 1 <= len(days) <= 400 or not isinstance(papers, list) or not papers:
        raise ValueError('尚无已公布的完整年度节假日数据')
    from urllib.parse import urlsplit
    if any(not isinstance(p, str) or len(p) > 1000 or urlsplit(p).scheme != 'https'
           or not (urlsplit(p).hostname or '').endswith('.gov.cn') for p in papers):
        raise ValueError('节假日数据缺少政府公告来源')
    normalized, seen = [], set()
    for row in days:
        if not isinstance(row, dict) or type(row.get('isOffDay')) is not bool:
            raise ValueError('节假日日期格式无效')
        value, name = row.get('date'), row.get('name')
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError('节假日日期格式无效')
        if date.fromisoformat(value).year not in (year-1, year, year+1) or value in seen:
            raise ValueError('节假日日期重复或超出年份范围')
        if not isinstance(name, str) or not name.strip() or len(name) > 50 or any(ord(c) < 32 for c in name):
            raise ValueError('节假日名称无效')
        seen.add(value)
        normalized.append({'date': value, 'name': name.strip(), 'isOffDay': row['isOffDay']})
    if not any(r['isOffDay'] for r in normalized):
        raise ValueError('年度数据没有假期')
    return {'year': year, 'papers': papers, 'days': sorted(normalized, key=lambda r: r['date'])}


@lru_cache(maxsize=1)
def bundled():
    path = Path(__file__).parent / 'data' / 'holidays'
    return {int(p.stem): validate(json.loads(p.read_text(encoding='utf-8')), int(p.stem))
            for p in path.glob('*.json')}


def years(db):
    return sorted(set(bundled()) | {r[0] for r in db.execute('SELECT year FROM holiday_calendars')})


def read_year(db, year):
    row = db.execute('SELECT * FROM holiday_calendars WHERE year=?', (year,)).fetchone()
    if row:
        return json.loads(row['data']) | {'source': row['source'], 'fetched_at': row['fetched_at'], 'known': True}
    if year in bundled():
        return bundled()[year] | {'source': URLS[0].format(year=year), 'fetched_at': '', 'known': True}
    return {'year': year, 'papers': [], 'days': [], 'source': '', 'fetched_at': '', 'known': False}


def periods(catalog):
    groups = []
    for row in catalog['days']:
        if not row['isOffDay']:
            continue
        if groups and groups[-1]['name'] == row['name'] and date.fromisoformat(groups[-1]['end_date']) + timedelta(days=1) == date.fromisoformat(row['date']):
            groups[-1]['end_date'] = row['date']
        else:
            ordinal = sum(p['name'] == row['name'] for p in groups)
            key = hashlib.sha256(f"{catalog['year']}:{row['name']}:{ordinal}".encode()).hexdigest()[:16]
            groups.append({'id': str(catalog['year'])+'-'+key, 'name': row['name'],
                           'start_date': row['date'], 'end_date': row['date']})
    return groups


def fetch(year):
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError('节假日年份应在2000至2100之间')
    for template in URLS:
        url = template.format(year=year)
        try:
            # Fixed hosts, redirects disabled, bounded time and response size.
            with httpx.stream('GET', url, timeout=8, follow_redirects=False) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 256_000:
                        raise ValueError('节假日数据过大')
            return validate(json.loads(body), year), url
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            continue
    raise ValueError('未能获取该年已公布的节假日，请稍后重试；已保存的安排仍然有效')
