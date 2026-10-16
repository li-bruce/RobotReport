"""Per-holiday, versioned venue schedules layered over the annual public calendar."""
from datetime import date
import hashlib
import json
import uuid

from .db import connect, dumps, audit, stamp
from .service import Service, day_value, integer, clean_name
from . import holiday_calendar as calendar
from .holidays import read, planned_periods, rules_for_day


class HolidayPlanning:
    def __init__(self, ctx, clock):
        self.ctx, self.clock = ctx, clock
        self.service = Service(ctx, clock)

    def _scope(self, venue_id):
        self.service.require_admin('activity.manage')
        self.service.venue(venue_id)

    def _read(self, db, venue_id, year):
        integer(year, 2000, 2100, '年份')
        catalog = calendar.read_year(db, year)
        config = read(db, venue_id)
        rows = planned_periods(db, venue_id, year, config)
        revision = hashlib.sha256(dumps([catalog, config, rows]).encode()).hexdigest()
        return {'year': year, 'venue_id': venue_id, 'revision': revision, 'periods': rows,
                'known': catalog['known'], 'source': catalog['source'], 'papers': catalog['papers'],
                'fetched_at': catalog['fetched_at'], 'known_years': calendar.years(db),
                'workdays': [r for r in catalog['days'] if not r['isOffDay']]}

    def get(self, venue_id, year):
        self._scope(venue_id)
        with connect(self.ctx.db_path) as db:
            result = self._read(db, venue_id, year)
        if self.service.ctx.slot_keys is not None:
            result['periods'] = [p | {'slots': [s for s in p['slots'] if s['slot_key'] in self.service.ctx.slot_keys]}
                                 for p in result['periods']]
        return result

    def _sync(self, db, venue_id):
        days = [r[0] for r in db.execute('SELECT DISTINCT date FROM sessions WHERE venue_id=? AND date>=?',
                                        (venue_id, self.clock().date().isoformat()))]
        for day in days:
            self.service._sync_future_sessions(db, venue_id, rules_for_day(db, venue_id, day)[0], day)

    def save(self, venue_id, year, holiday_id, data):
        self._scope(venue_id)
        self.service.venue(venue_id, True)
        if self.service.ctx.slot_keys is not None:
            raise PermissionError('请在后台全局活动管理中配置节假日')
        if not isinstance(data, dict) or set(data) != {'revision', 'name', 'start_date', 'end_date', 'enabled', 'slots'}:
            raise ValueError('请提交完整的节假日安排及版本')
        with connect(self.ctx.db_path, True) as db:
            current = self._read(db, venue_id, year)
            if data['revision'] != current['revision']:
                raise ValueError('节假日安排或数据已变化，请刷新后重试')
            old = next((p for p in current['periods'] if p['id'] == holiday_id), None)
            if old is None:
                raise ValueError('节假日不存在，请先获取年度节假日')
            start, end = day_value(data['start_date']), day_value(data['end_date'])
            if end < start or (end-start).days > 60 or start.year not in (year-1, year) or end.year not in (year, year+1):
                raise ValueError('假期日期范围无效，最长61天')
            if end < self.clock().date() or day_value(old['end_date']) < self.clock().date():
                raise ValueError('已结束的假期安排只读')
            if type(data['enabled']) is not bool:
                raise ValueError('启用状态须为布尔值')
            others = [p for y in (year-1, year, year+1) for p in planned_periods(db, venue_id, y) if p['id'] != holiday_id]
            if any(data['start_date'] <= p['end_date'] and data['end_date'] >= p['start_date'] for p in others):
                raise ValueError('假期日期不能与其他假期重叠')
            rules = self.service.validate_slots(data['slots'])
            known = {r['slot_key'] for r in old['slots']}
            for rule in rules:
                if rule.get('slot_key') and rule['slot_key'] not in known:
                    raise ValueError('场次标识不属于本假期，请刷新后重试')
                rule['slot_key'] = rule.get('slot_key') or 'special:' + uuid.uuid4().hex
                rule['weekdays'] = list(range(7))
                rule.pop('skip_holidays', None)
            self.service.validate_slots(rules)
            plan = {'id': holiday_id, 'name': clean_name(data['name']), 'start_date': data['start_date'],
                    'end_date': data['end_date'], 'enabled': data['enabled'], 'slots': rules}
            db.execute('''INSERT INTO holiday_plans(venue_id,year,holiday_id,definition) VALUES(?,?,?,?)
                ON CONFLICT(venue_id,year,holiday_id) DO UPDATE SET definition=excluded.definition,version=version+1''',
                       (venue_id, year, holiday_id, dumps(plan)))
            self._sync(db, venue_id)
            audit(db, self.ctx.actor, 'holiday_plan.save', {'venue_id': venue_id, 'year': year, 'holiday_id': holiday_id})
            return self._read(db, venue_id, year)

    def refresh(self, year):
        self.service.require_admin('activity.manage')
        # The public calendar affects every venue; scoped bots cannot change other venues.
        if self.service.scope_context().id != 'workspace':
            raise PermissionError('年度节假日数据请在后台全局活动管理中更新')
        payload, source = calendar.fetch(year)
        with connect(self.ctx.db_path, True) as db:
            db.execute('''INSERT INTO holiday_calendars(year,data,source,fetched_at) VALUES(?,?,?,?)
                ON CONFLICT(year) DO UPDATE SET data=excluded.data,source=excluded.source,fetched_at=excluded.fetched_at''',
                       (year, dumps(payload), source, stamp(self.clock())))
            for row in db.execute('SELECT id FROM venues').fetchall():
                self._sync(db, row[0])
            audit(db, self.ctx.actor, 'holiday_calendar.refresh', {'year': year, 'days': len(payload['days'])})
        return {'year': year, 'holidays': len(calendar.periods(payload)), 'fetched_at': stamp(self.clock())}
