"""Opt-in China holiday schedules, with explicit date overrides and stable slot keys."""
from datetime import date, timedelta
import hashlib
import json
import uuid

from .db import connect, dumps, now, audit

# State Council notice 国办发明电〔2025〕7号; local data, no runtime network dependency.
SOURCE = 'https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html'
PERIODS = [('2026-01-01','2026-01-03','元旦'), ('2026-02-15','2026-02-23','春节'),
           ('2026-04-04','2026-04-06','清明节'), ('2026-05-01','2026-05-05','劳动节'),
           ('2026-06-19','2026-06-21','端午节'), ('2026-09-25','2026-09-27','中秋节'),
           ('2026-10-01','2026-10-07','国庆节')]
WORKDAYS = {'2026-01-04','2026-02-14','2026-02-28','2026-05-09','2026-09-20','2026-10-10'}


def ensure_schema(db):
    from .holiday_calendar import ensure_schema as calendar_schema
    calendar_schema(db)
    db.execute('''CREATE TABLE IF NOT EXISTS holiday_schedules(
        venue_id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,
        slots TEXT NOT NULL DEFAULT '[]',overrides TEXT NOT NULL DEFAULT '[]',version INTEGER NOT NULL DEFAULT 1)''')


def defaults(venue_id):
    return [{'name':'节假日第一场' if i==0 else '节假日第二场', 'weekdays':list(range(7)),
             'start':start,'end':end,'capacity':8,'substitute_capacity':4,
             'slot_key':'special:'+hashlib.md5(f'holiday:{venue_id}:{i}'.encode()).hexdigest()}
            for i,(start,end) in enumerate([('18:00','20:00'),('20:00','22:00')])]


def read(db, venue_id):
    from .holiday_calendar import years
    row = db.execute('SELECT * FROM holiday_schedules WHERE venue_id=?',(venue_id,)).fetchone()
    return {'enabled':bool(row['enabled']) if row else False,
            'slots':json.loads(row['slots']) if row else defaults(venue_id),
            'overrides':json.loads(row['overrides']) if row else [], 'version':row['version'] if row else 0,
            'known_years':years(db), 'source':SOURCE}


def planned_periods(db, venue_id, year, config=None):
    from .holiday_calendar import read_year, periods
    config = config or read(db, venue_id)
    catalog = read_year(db, year)
    saved = {r['holiday_id']: json.loads(r['definition']) for r in db.execute(
        'SELECT * FROM holiday_plans WHERE venue_id=? AND year=?', (venue_id, year))}
    result = []
    for period in periods(catalog):
        custom = saved.pop(period['id'], None)
        result.append((custom or (period | {'enabled': config['enabled'], 'slots': config['slots']}))
                      | {'official': period, 'customized': custom is not None})
    result.extend(row | {'official': None, 'customized': True} for row in saved.values())
    return sorted(result, key=lambda r: (r['start_date'], r['id']))


def all_slots(db, venue_id):
    result = read(db, venue_id)['slots'][:]
    for row in db.execute('SELECT definition FROM holiday_plans WHERE venue_id=?', (venue_id,)):
        result.extend(json.loads(row[0])['slots'])
    return list({r['slot_key']: r for r in result}.values())


def date_info(db, venue_id, day, config=None):
    from .holiday_calendar import read_year
    config = config or read(db, venue_id)
    year = date.fromisoformat(day).year
    # Adjacent annual notices can include a New Year break spanning December/January.
    catalogs = [read_year(db, y) for y in (year-1, year, year+1)]
    plans = [p for y in (year-1, year, year+1) for p in planned_periods(db, venue_id, y, config)]
    period = next((p for p in plans if p['start_date'] <= day <= p['end_date']), None)
    override = next((r for r in config['overrides'] if r['date'] == day), None)
    if override:
        return override['kind'], override.get('name') or '手动指定', True, period if override['kind'] == 'holiday' else None
    if period:
        return 'holiday', period['name'], True, period
    # Official days moved by a custom period no longer count as a holiday here.
    workday = next((r for c in catalogs for r in c['days'] if r['date'] == day and not r['isOffDay']), None)
    return ('workday', '调休上班', True, None) if workday else ('regular', '', year in config['known_years'], None)


def day_kind(day, config):
    target = date.fromisoformat(day)
    override = next((r for r in config['overrides'] if r['date']==day),None)
    if override:
        return override['kind'], override.get('name') or '手动指定', True
    for start, end, name in PERIODS:
        if start <= day <= end:
            return 'holiday', name, True
    if day in WORKDAYS:
        return 'workday','调休上班',True
    return 'regular','',target.year in config['known_years']


def rules_for_day(db, venue_id, day, weekly=None):
    from .scope import special_rules
    special = db.execute('SELECT slots FROM special_dates WHERE venue_id=? AND date=?',(venue_id,day)).fetchone()
    config = read(db,venue_id)
    kind, label, known, period = date_info(db,venue_id,day,config)
    info = {'holiday_enabled':bool(period['enabled']) if period else config['enabled'], 'day_type':kind,
            'day_label':label, 'calendar_known':known, 'schedule_source':'weekly'}
    if special:
        info['schedule_source']='date_override'
        return special_rules(venue_id,day,json.loads(special[0])), info
    if kind=='holiday' and (period['enabled'] if period else config['enabled']):
        info['schedule_source']='holiday'
        return period['slots'] if period else config['slots'], info
    weekday = date.fromisoformat(day).weekday()
    if config['enabled'] and kind in ('workday','weekend'):
        weekday = 0 if kind=='workday' else 5
    weekly = weekly if weekly is not None else [json.loads(r['definition'])|{'slot_key':'slot:'+str(r['id'])}
              for r in db.execute('SELECT * FROM slots WHERE venue_id=?',(venue_id,))]
    return [r for r in weekly if not r.get('retired') and weekday in r['weekdays']
            and not (kind == 'holiday' and r.get('skip_holidays', False))],info


class HolidaySchedule:
    def __init__(self, ctx, clock=now):
        from .service import Service
        self.ctx, self.clock = ctx, clock
        self.service = Service(ctx,clock)

    def get(self, venue_id):
        self.service.venue(venue_id)
        with connect(self.ctx.db_path) as db:
            result=read(db,venue_id)
            result['available_slots'] = all_slots(db,venue_id)
        if self.service.scope_context().slot_keys is not None:
            result['slots']=[r for r in result['slots'] if r['slot_key'] in self.service.ctx.slot_keys]
            result['available_slots']=[r for r in result['available_slots'] if r['slot_key'] in self.service.ctx.slot_keys]
        return result

    def save(self, venue_id, data):
        self.ctx.require('activity.manage')
        self.service.venue(venue_id, True)
        if self.service.scope_context().slot_keys is not None:
            raise PermissionError('节假日场次覆盖整日，请在后台或负责全部时段的机器人中配置')
        if not isinstance(data,dict) or set(data)-{'enabled','slots','overrides','expected_version'}:
            raise ValueError('节假日排期字段无效')
        with connect(self.ctx.db_path,True) as db:
            old=read(db,venue_id)
            if 'expected_version' in data and data['expected_version']!=old['version']:
                raise ValueError('节假日配置已变化，请刷新后重试')
            enabled=data.get('enabled',old['enabled'])
            if type(enabled) is not bool:
                raise ValueError('节假日开关必须为布尔值')
            raw=data.get('slots',old['slots'])
            rules=self.service.validate_slots(raw)
            known={r['slot_key'] for r in old['slots']}
            for rule in rules:
                if rule.get('slot_key') and rule['slot_key'] not in known:
                    raise ValueError('节假日场次标识不属于本场地')
                rule['slot_key']=rule.get('slot_key') or 'special:'+uuid.uuid4().hex
                rule['weekdays']=list(range(7))
            # Weekday masks do not allow overlaps in the holiday template.
            self.service.validate_slots(rules)
            overrides=data.get('overrides',old['overrides'])
            if not isinstance(overrides,list) or len(overrides)>400:
                raise ValueError('日期类型最多配置400天')
            normalized=[]
            from .service import day_value
            for row in overrides:
                if not isinstance(row,dict) or set(row)-{'date','kind','name'} or row.get('kind') not in ('holiday','workday','weekend'):
                    raise ValueError('日期类型应为节假日、工作日或周末')
                day_value(row.get('date'))
                label=row.get('name','')
                if not isinstance(label,str) or len(label)>50 or any(ord(c)<32 for c in label):
                    raise ValueError('日期备注最多50字')
                normalized.append({'date':row['date'],'kind':row['kind'],'name':label})
            if len({r['date'] for r in normalized})!=len(normalized):
                raise ValueError('指定日期不能重复')
            db.execute('''INSERT INTO holiday_schedules(venue_id,enabled,slots,overrides,version) VALUES(?,?,?,?,?)
                ON CONFLICT(venue_id) DO UPDATE SET enabled=excluded.enabled,slots=excluded.slots,
                overrides=excluded.overrides,version=excluded.version''',
                (venue_id,int(enabled),dumps(rules),dumps(normalized),old['version']+1))
            if 'enabled' in data:
                for row in db.execute('SELECT * FROM holiday_plans WHERE venue_id=?',(venue_id,)).fetchall():
                    definition = json.loads(row['definition']) | {'enabled': enabled}
                    db.execute('UPDATE holiday_plans SET definition=?,version=version+1 WHERE venue_id=? AND year=? AND holiday_id=?',
                               (dumps(definition),venue_id,row['year'],row['holiday_id']))
            days=[r[0] for r in db.execute('SELECT DISTINCT date FROM sessions WHERE venue_id=? AND date>=?',(venue_id,self.clock().date().isoformat()))]
            for day in days:
                selected,_=rules_for_day(db,venue_id,day)
                # Existing service protects started sessions, event locks and all registration snapshots.
                # Incompatible booked templates require an explicit date arrangement instead of silently moving people.
                self.service._sync_future_sessions(db,venue_id,selected,day)
            audit(db,self.ctx.actor,'holiday_schedule.save',{'venue_id':venue_id,'enabled':enabled,'version':old['version']+1})
        return self.get(venue_id)
