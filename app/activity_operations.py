"""Multi-venue calendar summaries and atomic weekly schedule planning."""
import calendar
import hashlib
import json
import re
from datetime import date

from .db import connect, dumps, audit
from .service import Service, day_value, integer
from .availability import Availability


class ActivityOperations:
    def __init__(self, ctx, clock):
        self.service = Service(ctx, clock)
        self.clock = clock

    def _venues(self):
        self.service.require_admin('activity.manage')
        return self.service.venues()

    def day(self, day):
        day_value(day)
        venues = self._venues()
        result = []
        with connect(self.service.path, True) as db:
            for venue in venues:
                sessions = self.service._sessions(db, day, venue)
                result.append(venue | {'sessions': sessions})
        return {'date': day, 'venues': result, 'session_count': sum(len(v['sessions']) for v in result)}

    def month(self, month):
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError('月份格式为 YYYY-MM')
        first = day_value(month + '-01')
        if abs((first - self.clock().date()).days) > 3660:
            raise ValueError('日历查询范围为前后10年')
        venues = self._venues()
        days = []
        # One transaction for the whole month; never load member/roster records here.
        end = f'{month}-{calendar.monthrange(first.year, first.month)[1]:02d}'
        occupancies = [r for v in venues for r in Availability(self.service.ctx, self.clock).list(v['id'], first.isoformat(), end)]
        with connect(self.service.path, True) as db:
            from .holidays import date_info
            events = self._event_days(db, first.isoformat(), end, venues)
            for number in range(1, calendar.monthrange(first.year, first.month)[1] + 1):
                day = f'{month}-{number:02d}'
                rows = [s for venue in venues for s in self.service._sessions(db, day, venue)]
                labels = [date_info(db, v['id'], day) for v in venues]
                metrics = [{'venue_id': v['id'], 'name': v['name'],
                            'session_count': sum(s['venue_id'] == v['id'] for s in rows),
                            'registration_count': sum(s['count'] for s in rows if s['venue_id'] == v['id'])}
                           for v in venues if any(s['venue_id'] == v['id'] for s in rows)]
                days.append({'date': day, 'session_count': len(rows),
                             'venue_count': len({s['venue_id'] for s in rows}),
                             'registration_count': sum(s['count'] for s in rows),
                             'occupied_count': sum(bool(s.get('occupancies')) for s in rows),
                             'occupancy_count': sum(r['date'] == day for r in occupancies),
                             'day_label': ' / '.join(dict.fromkeys(label for _, label, _, _ in labels if label)),
                             'is_weekend': date.fromisoformat(day).weekday() >= 5,
                             'is_holiday': any(kind == 'holiday' for kind, *_ in labels),
                             'is_workday': any(kind == 'workday' for kind, *_ in labels),
                             'calendar_known': all(known for _, _, known, _ in labels),
                             'capacity': sum(s['capacity'] + s['substitute_capacity'] for s in rows),
                             'formal_count': sum(min(s['count'], s['capacity']) for s in rows),
                             'substitute_count': sum(min(max(0,s['count']-s['capacity']),s['substitute_capacity']) for s in rows),
                             'waiting_count': sum(max(0,s['count']-s['capacity']-s['substitute_capacity']) for s in rows),
                             'venues': metrics, 'events': events.get(day, []), 'event_count': len(events.get(day, []))})
        return {'month': month, 'venues': venues, 'days': days}

    def _event_days(self, db, start, end, venues):
        result = {}
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='tour_events'").fetchone():
            return result
        visible = {v['id'] for v in venues}
        for event in db.execute("SELECT id,title,kind,venue_id,date,options FROM tour_events WHERE chat_id=? AND status NOT IN ('cancelled','draft')", (self.service.ctx.scope_id,)):
            if event['venue_id'] not in visible:
                continue
            options = json.loads(event['options'])
            if self.service.ctx.slot_keys is not None:
                session = db.execute('SELECT slot_key FROM sessions WHERE id=?', (options.get('session_id'),)).fetchone()
                if event['kind'] == 'daily' and (not session or session[0] not in self.service.ctx.slot_keys):
                    continue
            dates = {event['date']} | {r['date'] for r in options.get('reservations', [])}
            dates.update(r[0][:10] for r in db.execute('SELECT scheduled_at FROM tour_matches WHERE tournament_id=? AND scheduled_at IS NOT NULL', (event['id'],)))
            for day in dates:
                if start <= day <= end:
                    result.setdefault(day, []).append({k: event[k] for k in ('id','title','kind','venue_id')})
        return result

    @staticmethod
    def _date_revision(row):
        return hashlib.sha256(row['slots'].encode()).hexdigest() if row else ''

    def date_plans(self, year):
        integer(year, 2000, 2100, '年份')
        venues = self._venues()
        from .scope import special_rules
        with connect(self.service.path) as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM special_dates WHERE date>=? AND date<=? ORDER BY date,venue_id',
                                                (f'{year}-01-01', f'{year}-12-31')) if r['venue_id'] in {v['id'] for v in venues}]
        result = []
        for row in rows:
            rules = special_rules(row['venue_id'], row['date'], json.loads(row['slots']))
            if self.service.ctx.slot_keys is not None:
                rules = [s for s in rules if s['slot_key'] in self.service.ctx.slot_keys]
                if not rules:
                    continue
            result.append(row | {'slots': rules, 'revision': self._date_revision(row),
                                 'can_edit': row['date'] >= self.clock().date().isoformat()})
        return {'year': year, 'venues': venues, 'plans': result}

    def save_date_plan(self, venue_id, day, data, remove=False):
        self._venues()
        self.service.venue(venue_id, True)
        if self.service.scope_context().slot_keys is not None:
            raise PermissionError('请在后台全局活动管理中调整指定日期')
        if day_value(day) < self.clock().date():
            raise ValueError('过去的非周期性安排只读')
        if not isinstance(data, dict) or set(data) != ({'revision'} if remove else {'revision','slots'}):
            raise ValueError('请提交指定日期安排及版本')
        with connect(self.service.path, True) as db:
            old = db.execute('SELECT * FROM special_dates WHERE venue_id=? AND date=?', (venue_id, day)).fetchone()
            if data['revision'] != self._date_revision(old):
                raise ValueError('指定日期安排已变化，请刷新后重试')
            if remove:
                db.execute('DELETE FROM special_dates WHERE venue_id=? AND date=?', (venue_id, day))
                from .holidays import rules_for_day
                self.service._sync_future_sessions(db, venue_id, rules_for_day(db, venue_id, day)[0], day)
                audit(db, self.service.ctx.actor, 'date_plan.remove', {'venue_id': venue_id, 'date': day})
            else:
                normalized = self.service.validate_slots(data['slots'])
                for row in normalized:
                    row['weekdays'] = list(range(7))
                    row.pop('skip_holidays', None)
                self.service.validate_slots(normalized)
                self.service._save_slots(db, venue_id, normalized, day)
        return self.date_plans(date.fromisoformat(day).year)

    def _plan(self, db, venues):
        records = [dict(r) for r in db.execute('SELECT * FROM slots ORDER BY id') if r['venue_id'] in {v['id'] for v in venues}]
        revision = hashlib.sha256(dumps([venues, records]).encode()).hexdigest()
        grouped = {}
        for record in records:
            rule = json.loads(record['definition'])
            if rule.get('retired'):
                continue
            key = 'slot:' + str(record['id'])
            if self.service.ctx.slot_keys is not None and key not in self.service.ctx.slot_keys:
                continue
            definition = {k: rule[k] for k in ('name', 'weekdays', 'start', 'end', 'capacity', 'substitute_capacity')}
            definition['skip_holidays'] = rule.get('skip_holidays', False)
            signature = json.dumps(definition, sort_keys=True)
            group = grouped.setdefault(signature, definition | {'venue_ids': [], 'bindings': {}})
            group['venue_ids'].append(record['venue_id'])
            group['bindings'][str(record['venue_id'])] = key
        return {'revision': revision, 'venues': venues, 'rules': list(grouped.values())}

    def plan(self):
        venues = self._venues()
        with connect(self.service.path) as db:
            return self._plan(db, venues)

    def save_plan(self, data):
        venues = self._venues()
        if self.service.scope_context().slot_keys is not None:
            raise PermissionError('请在后台全局活动管理中调整时段规则')
        if not isinstance(data, dict) or set(data) != {'revision', 'rules'}:
            raise ValueError('请提交完整时段计划并携带当前版本')
        if not isinstance(data['rules'], list) or len(data['rules']) > 1000:
            raise ValueError('时段规则须为列表，最多1000条')
        with connect(self.service.path, True) as db:
            current = self._plan(db, venues)
            if data['revision'] != current['revision']:
                raise ValueError('时段或场地已变化，请刷新后重试')
            by_venue = {v['id']: [] for v in venues}
            known = {str(v): {r['bindings'][str(v)] for r in current['rules'] if v in r['venue_ids']} for v in by_venue}
            for rule in data['rules']:
                if not isinstance(rule, dict) or set(rule) - {'name', 'weekdays', 'start', 'end', 'capacity', 'substitute_capacity', 'skip_holidays', 'venue_ids', 'bindings'}:
                    raise ValueError('时段计划字段无效')
                ids, bindings = rule.get('venue_ids'), rule.get('bindings', {})
                if not isinstance(ids, list) or not ids or len(ids) != len(set(map(str, ids))):
                    raise ValueError('每条规则须选择适用场地，且不能重复')
                if not isinstance(bindings, dict) or any(not isinstance(k, str) or not isinstance(v, str) or v not in known.get(k, set()) for k, v in bindings.items()):
                    raise ValueError('时段标识不属于当前场地，请刷新后重试')
                for venue_id in ids:
                    integer(venue_id, 1, 2**63-1, '场地编号')
                    if venue_id not in by_venue:
                        raise PermissionError('不能管理范围之外的场地')
                    definition = {k: v for k, v in rule.items() if k not in ('venue_ids', 'bindings')}
                    definition.setdefault('skip_holidays', False)
                    if str(venue_id) in bindings:
                        definition['slot_key'] = bindings[str(venue_id)]
                    by_venue[venue_id].append(definition)
            # Validate every venue before any changes; synchronization uses this same transaction.
            plans = {v: self.service.validate_slots(rows) for v, rows in by_venue.items()}
            changed = []
            for venue in venues:
                vid = venue['id']
                before = [dict({k: x for k, x in r.items() if k not in ('venue_ids', 'bindings')}, slot_key=r['bindings'][str(vid)])
                          for r in current['rules'] if vid in r['venue_ids']]
                if sorted(map(dumps, plans[vid])) == sorted(map(dumps, before)):
                    continue
                if not venue['active']:
                    raise ValueError(f"请先启用场地「{venue['name']}」后调整时段")
                try:
                    self.service._save_slots(db, vid, plans[vid])
                except ValueError as error:
                    raise ValueError(f"{venue['name']}：{error}") from None
                changed.append(vid)
            result = self._plan(db, venues)
            return result | {'changed_venue_ids': changed}
