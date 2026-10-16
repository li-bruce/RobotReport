"""Bot-scoped registration, scheduling and management service."""
from datetime import date, datetime, time, timedelta
import calendar
import copy
from . import env_settings
import json
import re
import os
import uuid
from urllib.parse import urlsplit
from .db import connect, audit, dumps, stamp, now, TZ, DEFAULT_SLOTS
from .security import BotContext, identifier
from .members import Members
from .scope import initialize_scope, refresh_context, snapshot, special_rules


def clean_name(value):
    value = re.sub(r'[\x00-\x1f\x7f]', '', str(value)).strip()
    if not value or len(value) > 80:
        raise ValueError('姓名须为1至80字符')
    return value


def day_value(value):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)):
            raise ValueError()
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError('日期格式为 YYYY-MM-DD，请检查日期是否存在') from None


def time_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value):
        raise ValueError('时间格式为 HH:MM')
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise ValueError('时间无效') from None


def integer(value, lower, upper, label):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ValueError(f'{label}须为{lower}至{upper}之间的整数')
    return value


def session_time(session, key):
    return datetime.combine(day_value(session['date']), time_value(session[key]), TZ)


def registration_name(row, member_name=''):
    """Keep each guest's booked identity, including anonymous historical labels."""
    if row['guest_index'] == 0 and member_name:
        return member_name
    if row['guest_name']:
        return row['guest_name']
    saved = row['name']
    if saved and saved != row['user_id'] and not saved.startswith(row['user_id'] + '+'):
        return saved
    return member_name + f"+{row['guest_index']}" if member_name and row['guest_index'] else '待完善姓名'


class Service:
    def __init__(self, context: BotContext, clock=now):
        if not isinstance(context, BotContext):
            raise PermissionError('缺少可信机器人上下文')
        self.ctx = context
        self.path = context.db_path
        self.clock = clock
        initialize_scope(self.path)

    def require_admin(self, permission='roles.manage'):
        self.ctx.require(permission)

    def config(self, secret=False):
        with connect(self.path) as db:
            result = {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM config')}
        result['ai'].setdefault('confirmation_minutes', 5)
        if not secret:
            result['ai']['configured'] = bool(result['ai'].get('api_key'))
            result['ai'].pop('api_key', None)
        return result

    def update_config(self, data):
        if not isinstance(data, dict):
            raise ValueError('配置字段不支持')
        activity_fields = {'reminders', 'daily_enabled', 'capacity', 'substitute_capacity', 'scoring_mode', 'leader_user_ids'}
        for permission in {'activity.manage' if key in activity_fields else 'system.manage' for key in data}:
            self.require_admin(permission)
        if not data:
            self.require_admin('system.manage')
        if 'admin_user_ids' in data:
            raise ValueError('管理员角色请在人员中心单独配置')
        original = self.config(True)
        if not isinstance(data, dict) or set(data) - set(original):
            raise ValueError('配置字段不支持')
        updated = copy.deepcopy(original)
        for key, value in data.items():
            if key in ('ai', 'theme', 'reminders'):
                if not isinstance(value, dict) or set(value) - set(original[key]):
                    raise ValueError(f'{key} 配置字段不支持')
                updated[key].update(value)
            else:
                updated[key] = value
        for key in ('admin_user_ids', 'leader_user_ids'):
            if not isinstance(updated[key], list) or len(updated[key]) > 200:
                raise ValueError('管理员或组长列表无效')
            updated[key] = list(dict.fromkeys(identifier(x) for x in updated[key]))
        integer(updated['capacity'], 1, 2000, '正式容量')
        integer(updated['substitute_capacity'], 0, 2000, '替补容量')
        if updated['scoring_mode'] not in ('members', 'leader'):
            raise ValueError('记分模式无效')
        if not isinstance(updated['daily_enabled'], bool) or not isinstance(updated['ai']['enabled'], bool):
            raise ValueError('开关必须为布尔值')
        integer(updated['ai']['confirmation_minutes'], 1, 1440, 'AI确认有效期（分钟）')
        for value in updated['reminders'].values():
            time_value(value)
        if updated['ai']['base_url'] != 'https://api.siliconflow.cn/v1':
            raise ValueError('AI 仅允许 SiliconFlow 官方端点')
        if not re.fullmatch(r'[\w./:-]{1,160}', str(updated['ai']['model'])):
            raise ValueError('模型名称无效')
        if not isinstance(updated['ai']['api_key'], str) or len(updated['ai']['api_key']) > 500:
            raise ValueError('AI 密钥格式无效')
        base = updated['public_base_url'].rstrip('/')
        if base:
            url = urlsplit(base)
            if url.username or url.password or url.query or url.fragment or not url.hostname or not (
                    url.scheme == 'https' or (url.scheme == 'http' and (url.hostname in ('127.0.0.1', 'localhost') or base == env_settings.runtime_value(self.ctx.platform_root, 'ROBORT_PUBLIC_BASE_URL').rstrip('/')))):
                raise ValueError('公开地址必须为HTTPS，或本机调试地址')
        updated['public_base_url'] = base
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', updated['theme']['accent']):
            raise ValueError('主题色须为六位十六进制颜色')
        banner = updated['theme']['banner_url']
        if banner and not banner.startswith('https://'):
            raise ValueError('图片主题地址须为HTTPS')
        env_changes = {}
        if 'public_base_url' in data:
            env_changes['ROBORT_PUBLIC_BASE_URL'] = updated['public_base_url']
        for field, suffix in (('api_key','API_KEY'), ('model','MODEL'), ('base_url','BASE_URL'), ('enabled','ENABLED')):
            if field in data.get('ai', {}):
                value = updated['ai'][field]
                env_changes['ROBORT_AI_' + suffix] = ('1' if value else '0') if field == 'enabled' else value
        with env_settings.change(self.ctx.platform_root, env_changes), connect(self.path, True) as db:
            for key, value in updated.items():
                db.execute('UPDATE config SET value=? WHERE key=?', (dumps(value), key))
            audit(db, self.ctx.actor, 'config.update', {'fields': sorted(data)})
        return self.config()

    def scope_context(self):
        self.ctx = refresh_context(self.ctx)
        return self.ctx

    def allows_session(self, session):
        ctx = self.scope_context()
        return (not (ctx.venue_restricted or ctx.venue_ids) or session['venue_id'] in ctx.venue_ids) and (ctx.slot_keys is None or session.get('slot_key','') in ctx.slot_keys)

    def assert_session_scope(self, session):
        if not self.allows_session(dict(session)):
            raise PermissionError('本机器人或群未负责此场地/时段，请选择已开通的活动场次')
        return session

    def assert_event_scope(self, event):
        self.venue(event['venue_id'])
        # Large events have their own dates and schedule, independent of daily slots.
        if event.get('kind') == 'large':
            return event
        if self.ctx.slot_keys is None:
            return event
        options = event.get('options') or {}
        if isinstance(options, str):
            options = json.loads(options)
        saved = options.get('session_snapshot')
        if isinstance(saved,dict) and saved.get('slot_key') and saved.get('venue_id')==event['venue_id']:
            self.assert_session_scope(saved)
            return event
        session_id = options.get('session_id')
        if not session_id:
            raise PermissionError('本机器人仅负责指定时段；此赛事尚未关联负责的活动场次')
        with connect(self.path) as db:
            session = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        if not session or session['venue_id'] != event['venue_id']:
            raise PermissionError('赛事关联场次无效或超出负责范围')
        self.assert_session_scope(dict(session))
        return event

    def event_allowed(self, event):
        try:
            self.assert_event_scope(event)
            return True
        except (PermissionError, ValueError):
            return False

    def venues(self):
        ctx = self.scope_context()
        with connect(self.path) as db:
            return [dict(x) | {'active': bool(x['active'])} for x in db.execute('SELECT * FROM venues ORDER BY id')
                    if not (ctx.venue_restricted or ctx.venue_ids) or x['id'] in ctx.venue_ids]

    def venue(self, venue_id, active=False):
        ctx = self.scope_context()
        if (ctx.venue_restricted or ctx.venue_ids) and int(venue_id) not in ctx.venue_ids:
            raise PermissionError('本机器人或群未开通此场地，请选择已负责的场地')
        with connect(self.path) as db:
            row = db.execute('SELECT * FROM venues WHERE id=?', (venue_id,)).fetchone()
        if not row:
            raise ValueError('场地不存在')
        if active and not row['active']:
            raise ValueError('场地已停用')
        return dict(row)

    def save_venue(self, data, venue_id=None):
        self.require_admin('activity.manage')
        if set(data) - {'name', 'description', 'active'}:
            raise ValueError('场地字段不支持')
        old = self.venue(venue_id) if venue_id else {'name': '', 'description': '', 'active': True}
        old.update(data)
        old['name'] = clean_name(old['name'])
        if len(str(old['description'])) > 1000:
            raise ValueError('场地描述过长')
        if old['active'] not in (True, False, 0, 1):
            raise ValueError('场地开关无效')
        with connect(self.path, True) as db:
            if venue_id:
                db.execute('UPDATE venues SET name=?,description=?,active=? WHERE id=?', (old['name'], old['description'], int(old['active']), venue_id))
            else:
                venue_id = db.execute('INSERT INTO venues(name,description,active) VALUES(?,?,?)', (old['name'], old['description'], int(old['active']))).lastrowid
                db.executemany('INSERT INTO slots(venue_id,definition) VALUES(?,?)', [(venue_id, dumps(s)) for s in DEFAULT_SLOTS])
            audit(db, self.ctx.actor, 'venue.save', {'venue_id': venue_id})
        return self.venue(venue_id)

    def slots(self, venue_id=1):
        self.venue(venue_id)
        with connect(self.path) as db:
            return [json.loads(r['definition']) | {'id': r['id'], 'slot_key':'slot:'+str(r['id'])} for r in db.execute('SELECT * FROM slots WHERE venue_id=? ORDER BY id', (venue_id,)) if not json.loads(r['definition']).get('retired') and (self.ctx.slot_keys is None or 'slot:'+str(r['id']) in self.ctx.slot_keys)]

    @staticmethod
    def validate_slots(slots):
        if not isinstance(slots, list) or len(slots) > 100:
            raise ValueError('场次规则须为列表，最多100项')
        normalized = []
        for slot in slots:
            if not isinstance(slot, dict):
                raise ValueError('场次规则无效')
            weekdays = slot.get('weekdays', list(range(7)))
            if not isinstance(weekdays, list) or not weekdays:
                raise ValueError('请选择星期')
            weekdays = sorted(set(integer(x, 0, 6, '星期') for x in weekdays))
            start, end = slot.get('start'), slot.get('end')
            if time_value(end) <= time_value(start):
                raise ValueError('结束时间须晚于开始时间；跨天请拆成两天场次')
            normalized.append({'name': clean_name(slot.get('name', '活动')), 'weekdays': weekdays,
                               'start': start, 'end': end,
                               'capacity': integer(slot.get('capacity', 8), 1, 2000, '容量'),
                               'substitute_capacity': integer(slot.get('substitute_capacity', 4), 0, 2000, '替补容量')})
            if 'skip_holidays' in slot:
                if type(slot['skip_holidays']) is not bool:
                    raise ValueError('跳过节假日须为布尔值')
                normalized[-1]['skip_holidays'] = slot['skip_holidays']
        for i, slot in enumerate(normalized):
            for other in normalized[i + 1:]:
                if set(slot['weekdays']) & set(other['weekdays']) and slot['start'] < other['end'] and other['start'] < slot['end']:
                    raise ValueError('同一场地的场次不能重叠')
        for original, row in zip(slots, normalized):
            if original.get('slot_key'):
                if not isinstance(original['slot_key'],str) or not re.fullmatch(r'(?:slot:\d+|special:[a-f0-9]{32}|legacy:\d+)',original['slot_key']):
                    raise ValueError('场次标识无效，请刷新后重试')
                row['slot_key'] = original['slot_key']
            elif original.get('id'):
                row['slot_key'] = 'slot:' + str(integer(original['id'],1,2**63-1,'时段编号'))
        keys = [r['slot_key'] for r in normalized if r.get('slot_key')]
        if len(keys) != len(set(keys)):
            raise ValueError('同一时段标识不能重复')
        return normalized

    def save_slots(self, venue_id, slots, special_date=None):
        self.require_admin('activity.manage')
        self.venue(venue_id, True)
        if self.ctx.slot_keys is not None:
            raise PermissionError('此机器人仅负责部分时段，请在后台全局活动管理中调整时段规则')
        normalized = self.validate_slots(slots)
        if special_date and day_value(special_date) < self.clock().date():
            raise ValueError('不能修改过去日期的场次')
        with connect(self.path, True) as db:
            self._save_slots(db, venue_id, normalized, special_date)
        return normalized if special_date else self.slots(venue_id)

    def _save_slots(self, db, venue_id, normalized, special_date=None):
        recurring = [json.loads(r['definition']) | {'slot_key':'slot:'+str(r['id']), 'id':r['id']}
                     for r in db.execute('SELECT * FROM slots WHERE venue_id=?', (venue_id,))]
        old_special = db.execute('SELECT slots FROM special_dates WHERE venue_id=? AND date=?',(venue_id,special_date)).fetchone() if special_date else None
        old_rules = special_rules(venue_id,special_date,json.loads(old_special[0])) if old_special else recurring
        known = {r['slot_key']:r for r in recurring + old_rules if r.get('slot_key')}
        if special_date:
            from .holidays import all_slots
            known.update({r['slot_key']:r for r in all_slots(db,venue_id)})
        for rule in normalized:
            key = rule.get('slot_key')
            if key and key not in known:
                raise ValueError('时段标识不属于此场地或日期，请刷新后重试')
            if not key:
                matched = [r for r in old_rules if not r.get('retired') and all(r.get(k)==rule[k] for k in ('name','start','end'))]
                key = matched[0]['slot_key'] if len(matched)==1 and matched[0].get('slot_key') else None
            if not key:
                if special_date:
                    key = 'special:' + uuid.uuid4().hex
                else:
                    slot_id = db.execute('INSERT INTO slots(venue_id,definition) VALUES(?,?)',(venue_id,dumps(rule))).lastrowid
                    key = 'slot:'+str(slot_id)
            rule['slot_key'] = key
        if len({r['slot_key'] for r in normalized}) != len(normalized):
            raise ValueError('同一时段标识不能重复')
        changes = self._sync_future_sessions(db, venue_id, normalized, special_date)
        if special_date:
            db.execute('INSERT OR REPLACE INTO special_dates VALUES(?,?,?)',(venue_id,special_date,dumps(normalized)))
        else:
            retained = {r['slot_key'] for r in normalized}
            for rule in recurring:
                if rule['slot_key'] not in retained:
                    db.execute('UPDATE slots SET definition=? WHERE id=?',(dumps(rule | {'retired':True}),rule['id']))
            for rule in normalized:
                if not rule['slot_key'].startswith('slot:'):
                    raise ValueError('常规时段必须使用常规场次标识')
                db.execute('UPDATE slots SET definition=? WHERE id=?',(dumps(rule),int(rule['slot_key'].split(':')[1])))
        audit(db,self.ctx.actor,'slots.save',{'venue_id':venue_id,'date':special_date,'count':len(normalized),'session_changes':changes})

    def _sync_future_sessions(self, db, venue_id, rules, special_date):
        rows = [dict(r) for r in db.execute('SELECT * FROM sessions WHERE venue_id=? AND date>=?', (venue_id,self.clock().date().isoformat()))]
        overrides = {r[0] for r in db.execute('SELECT date FROM special_dates WHERE venue_id=?',(venue_id,))}
        plans = []
        has_events = db.execute("SELECT 1 FROM sqlite_master WHERE name='tour_events'").fetchone()
        events = [dict(r) for r in db.execute('SELECT id,title,status,options FROM tour_events')] if has_events else []
        for old in rows:
            if (special_date and old['date'] != special_date) or (not special_date and old['date'] in overrides):
                continue
            if session_time(old,'start') <= self.clock():
                continue
            from .holidays import rules_for_day
            day_rules = rules if special_date else rules_for_day(db, venue_id, old['date'], weekly=rules)[0]
            rule = next((r for r in day_rules if r['slot_key']==old['slot_key']),None)
            new = dict(old)
            if rule:
                new.update({k:rule[k] for k in ('name','start','end')})
                if not old['settings_overridden']:
                    new.update({k:rule[k] for k in ('capacity','substitute_capacity')})
                if session_time(new,'start') <= self.clock():
                    raise ValueError('不能将未来活动调整到已开始的时间')
            if rule and all(old[k]==new[k] for k in ('name','start','end','capacity','substitute_capacity')):
                continue
            locked = [e for e in events if json.loads(e['options']).get('session_id')==old['id']]
            if locked:
                raise ValueError(f"{old['date']} {old['name']} 已被赛事「{locked[0]['title']}」锁定，请先处理赛事安排后调整时段")
            if not rule and db.execute('SELECT 1 FROM registrations WHERE session_id=?',(old['id'],)).fetchone():
                raise ValueError(f"{old['date']} {old['name']} 已有报名历史，不能删除；请保留该时段标识并修改时间")
            plans.append((old,new if rule else None))
        changed_ids = {old['id'] for old,_ in plans}
        final = [r for r in rows if r['id'] not in changed_ids] + [new for _,new in plans if new]
        for i,row in enumerate(final):
            if row['id'] not in changed_ids:
                continue
            if any(row['id']!=other['id'] and row['date']==other['date'] and row['start']<other['end'] and other['start']<row['end'] for other in final):
                raise ValueError('调整后的时段与已保留的历史或正在进行的场次重叠')
        for old,new in plans:
            if new:
                db.execute('UPDATE sessions SET start=? WHERE id=?',('~'+str(old['id']),old['id']))
            else:
                db.execute('DELETE FROM sessions WHERE id=?',(old['id'],))
        for old,new in plans:
            if new:
                db.execute('UPDATE sessions SET name=?,start=?,end=?,capacity=?,substitute_capacity=? WHERE id=?',
                           (*[new[k] for k in ('name','start','end','capacity','substitute_capacity')],old['id']))
            db.execute('INSERT INTO session_changes(session_id,before_json,after_json,actor,created_at) VALUES(?,?,?,?,?)',
                       (old['id'],dumps(snapshot(old)),dumps(snapshot(new) if new else {'removed':True}),self.ctx.actor,stamp(self.clock())))
        return len(plans)

    def sessions(self, day=None, venue_id=1):
        day = day or self.clock().date().isoformat()
        venue = self.venue(venue_id)
        with connect(self.path, True) as db:
            return self._sessions(db, day, venue)

    def _sessions(self, db, day, venue):
        target = day_value(day)
        venue_id = venue['id']
        from .holidays import rules_for_day
        rules, calendar_info = rules_for_day(db, venue_id, day)
        existing = list(db.execute('SELECT * FROM sessions WHERE date=? AND venue_id=?', (day, venue_id)))
        for slot in rules:
            if slot.get('retired'):
                continue
            if target < self.clock().date() or not venue['active']:
                continue
            if any(old['slot_key']==slot.get('slot_key') or (slot['start'] < old['end'] and old['start'] < slot['end']) for old in existing):
                continue
            key = slot.get('slot_key') or 'legacy-special:'+str(venue_id)+':'+day+':'+slot['start']
            db.execute('INSERT OR IGNORE INTO sessions(venue_id,date,name,start,end,capacity,substitute_capacity,slot_key,venue_name) VALUES(?,?,?,?,?,?,?,?,?)',
                       (venue_id,day,slot['name'],slot['start'],slot['end'],slot['capacity'],slot['substitute_capacity'],key,venue['name']))
        rows = [dict(x) for x in db.execute("""SELECT s.*,COUNT(r.id) AS count FROM sessions s LEFT JOIN registrations r ON r.session_id=s.id AND r.status='active' AND r.chat_id=?
                   WHERE s.date=? AND s.venue_id=? GROUP BY s.id ORDER BY s.start""", (self.ctx.scope_id,day,venue_id))]
        from .availability import annotate
        rows = [annotate(db, row) | calendar_info for row in rows]
        for index,row in enumerate(rows,1):
            row['allowed_group_ids'] = json.loads(row['allowed_group_ids'])
            row['number'] = index
            row['can_manage'] = session_time(row,'end') > self.clock()
            row['can_enroll'] = bool(venue['active']) and row['can_manage'] and not row['occupancies']
            row['status'] = 'ended' if session_time(row,'end') <= self.clock() else ('ongoing' if session_time(row,'start') <= self.clock() else 'upcoming')
        return [row for row in rows if self.ctx.slot_keys is None or row['slot_key'] in self.ctx.slot_keys]

    def save_session_constraints(self, session_id, rules):
        self.require_admin('activity.manage')
        if not isinstance(rules, dict) or set(rules) - {'min_level', 'max_level', 'allowed_group_ids', 'capacity', 'substitute_capacity'}:
            raise ValueError('活动设置字段无效')
        members = Members(self.ctx, self.clock)
        with connect(self.path, True) as db:
            session = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
            if not session:
                raise ValueError('活动场次不存在')
            session = dict(session)
            self.assert_session_scope(session)
            if session_time(session, 'end') <= self.clock():
                raise ValueError('已结束活动不能修改报名限制')
            merged = {'min_level': session['min_level'], 'max_level': session['max_level'], 'allowed_group_ids': json.loads(session['allowed_group_ids'])}
            merged.update({k: v for k, v in rules.items() if k in merged})
            normalized = members.validate_rules(merged, db)
            capacity = integer(rules.get('capacity', session['capacity']), 1, 2000, '正式容量')
            substitute_capacity = integer(rules.get('substitute_capacity', session['substitute_capacity']), 0, 2000, '替补容量')
            db.execute('UPDATE sessions SET min_level=?,max_level=?,allowed_group_ids=?,capacity=?,substitute_capacity=?,settings_overridden=1 WHERE id=?',
                       (normalized['min_level'], normalized['max_level'], dumps(normalized['allowed_group_ids']), capacity, substitute_capacity, session_id))
            audit(db, self.ctx.actor, 'session.constraints', {'session_id': session_id, **normalized, 'capacity': capacity, 'substitute_capacity': substitute_capacity})
        result = self.registrations(session['date'], session['venue_id'], session_id)
        return result['sessions'][0] | {'ineligible_registrations': [r['id'] for r in result['registrations'] if r['status'] == 'active' and not r['eligible']]}

    def resolve_session(self, day=None, venue_id=1, session_id=None, at=None, number=None, for_write=True):
        self.venue(venue_id, active=for_write)
        day = day or self.clock().date().isoformat()
        target = day_value(day)
        if for_write and target < self.clock().date():
            raise ValueError('过去日期不能报名或取消；查询历史请使用 日期 名单')
        if for_write and target > self.clock().date() + timedelta(days=366):
            raise ValueError('最多可提前366天预约')
        rows = self.sessions(day, venue_id)
        if session_id is not None:
            candidates = [r for r in rows if r['id'] == int(session_id)]
        elif number is not None:
            candidates = [r for r in rows if r['number'] == int(number)]
        elif at:
            time_value(at)
            candidates = [r for r in rows if r['start'] <= at < r['end']]
        else:
            candidates = [r for r in rows if not for_write or r['status'] != 'ended'][:1]
        if len(candidates) != 1:
            hint = '；'.join(f"第{r['number']}场 {r['start']}–{r['end']}" for r in rows) or '当日无场次'
            raise ValueError(f'未找到唯一可用场次。{hint}。可用：日期 时间 报名')
        row = candidates[0]
        if for_write and row['status'] == 'ended':
            raise ValueError('该场次已经结束')
        return row

    def registrations(self, day=None, venue_id=1, session_id=None):
        members = Members(self.ctx, self.clock)
        members.require_approved()
        sessions = self.sessions(day, venue_id)
        if session_id is not None:
            sessions = [s for s in sessions if s['id'] == int(session_id)]
            if not sessions:
                raise ValueError('场次不属于指定日期或场地')
        records = []
        with connect(self.path) as db:
            for session in sessions:
                rows = db.execute('''SELECT r.*,p.name AS member_name,m.status AS member_status,m.level AS member_level,m.gender AS member_gender
                                     FROM registrations r LEFT JOIN profiles p ON p.chat_id=r.chat_id AND p.user_id=r.user_id
                                     LEFT JOIN member_records m ON m.chat_id=r.chat_id AND m.user_id=r.user_id
                                     WHERE r.session_id=? AND r.chat_id=? ORDER BY r.queue_seq,r.id''', (session['id'], self.ctx.scope_id)).fetchall()
                active_index = 0
                for row in rows:
                    item = dict(row) | {'date': session['date'], 'venue_id': venue_id, 'rank': None, 'category': '已取消'}
                    base_name = row['member_name'] if row['member_status'] == 'approved' and row['member_name'] else ''
                    item['name'] = registration_name(row, base_name)
                    item['session_snapshot'] = json.loads(item.get('session_snapshot') or '{}')
                    item['can_cancel'] = row['status'] == 'active' and session['can_manage'] and self.ctx.can('activity.manage')
                    item['is_proxy'] = row['guest_index'] > 0
                    item['sponsor_name'] = base_name or '待完善姓名'
                    item['gender'] = (row['member_gender'] or 'unknown') if row['guest_index'] == 0 else 'unknown'
                    item['level'] = row['member_level'] if row['guest_index'] == 0 and row['member_status'] == 'approved' else None
                    item['groups'] = [dict(r) for r in db.execute('''SELECT g.id,g.name FROM member_groups g
                                         JOIN member_group_links l ON l.group_id=g.id AND l.chat_id=g.chat_id
                                         WHERE l.chat_id=? AND l.user_id=? ORDER BY g.id''', (self.ctx.scope_id, row['user_id']))] if row['guest_index'] == 0 and row['member_status'] == 'approved' else []
                    item['group_ids'] = [g['id'] for g in item['groups']]
                    item['eligible'], item['eligibility_reason'] = True, ''
                    try:
                        members.assert_eligible(row['user_id'], session, guests=int(row['guest_index'] > 0), db=db)
                    except (PermissionError, ValueError) as exc:
                        item['eligible'], item['eligibility_reason'] = False, str(exc)
                    for key in ('member_name', 'member_status', 'member_level', 'member_gender'):
                        item.pop(key, None)
                    if row['status'] == 'active':
                        active_index += 1
                        item['rank'] = active_index
                        item['category'] = '正式' if active_index <= session['capacity'] else ('替补' if active_index <= session['capacity'] + session['substitute_capacity'] else '候补')
                    records.append(item)
        return {'sessions': sessions, 'registrations': records}

    def enroll(self, user_id, name, guests=0, day=None, venue_id=1, session_id=None, at=None, number=None, guest_names=None):
        user_id = identifier(user_id)
        if user_id != self.ctx.actor and (self.ctx.source in ('callback', 'simulator') or not self.ctx.can('activity.manage')):
            raise PermissionError('只能操作本人及本人的同行人员')
        if not self.ctx.scope_id:
            raise ValueError('缺少后台业务范围')
        integer(guests, 0, 20, '同行人数')
        if guest_names is not None:
            if not isinstance(guest_names, list) or len(guest_names) != guests or any(not isinstance(value, str) for value in guest_names):
                raise ValueError('同行姓名列表须与同行人数一致')
            guest_names = [clean_name(value) for value in guest_names]
            if len({value.casefold() for value in guest_names}) != len(guest_names):
                raise ValueError('同行姓名不能重复')
        session = self.resolve_session(day, venue_id, session_id, at, number)
        timestamp = stamp(self.clock())
        with connect(self.path, True) as db:
            # Re-read both policy and member state inside the same write transaction:
            # approval/group/level revocations cannot race an accepted registration.
            current_session = db.execute('SELECT * FROM sessions WHERE id=?', (session['id'],)).fetchone()
            if not current_session:
                raise ValueError('活动场次已调整，请重新查询场次后报名')
            session = dict(current_session)
            self.assert_session_scope(session)
            member = Members(self.ctx, self.clock).assert_eligible(user_id, session, guests, db)
            # Preserve existing bookings after expiry, but adding/rejoining any
            # participant requires current validity and coverage of the event day.
            bookings = list(db.execute('SELECT * FROM registrations WHERE session_id=? AND chat_id=? AND user_id=?',
                                      (session['id'], self.ctx.scope_id, user_id)))
            own = next((row for row in bookings if row['guest_index'] == 0), None)
            active_guests = [row for row in bookings if row['guest_index'] > 0 and row['status'] == 'active']
            if guests or not own or own['status'] != 'active':
                from .availability import assert_bookable
                assert_bookable(db, session)
                from .membership_terms import MembershipTerms
                MembershipTerms(self.ctx, self.clock).require_enrollment(user_id, session['date'], db=db)
            name = member['name']
            if guest_names and name.casefold() in {value.casefold() for value in guest_names}:
                raise ValueError('同行姓名不能与本人姓名相同')
            if len(active_guests) + guests > 20:
                raise ValueError('同一场活动的有效同行人员最多20人，请先取消不参加的同行人员')
            # Every call appends guests. Canceled indices are never reused, and
            # neither old guest names nor their queue/snapshot are rewritten.
            last_guest = max((row['guest_index'] for row in bookings), default=0)
            existing_names = {registration_name(row, name).casefold() for row in active_guests}
            additions = []
            for offset in range(guests):
                index = last_guest + offset + 1
                guest_name = guest_names[offset] if guest_names is not None else ''
                display = guest_name or f'{name}+{index}'
                if display.casefold() in existing_names:
                    raise ValueError(f'同行人员“{display}”已由您报名，请勿重复代报')
                existing_names.add(display.casefold())
                additions.append((index, guest_name, display))
            if session_time(session, 'end') <= self.clock():
                raise ValueError('该场次已经结束')
            seq = db.execute('SELECT COALESCE(MAX(queue_seq),0) FROM registrations WHERE session_id=?', (session['id'],)).fetchone()[0]
            changed = False
            if own and own['status'] == 'active':
                if own['name'] != name:
                    db.execute('UPDATE registrations SET name=?,updated_at=? WHERE id=?', (name, timestamp, own['id']))
                    changed = True
            else:
                seq += 1
                if own:
                    db.execute("UPDATE registrations SET name=?,status='active',queue_seq=?,updated_at=? WHERE id=?", (name, seq, timestamp, own['id']))
                else:
                    db.execute('INSERT INTO registrations(session_id,chat_id,user_id,name,guest_index,guest_name,queue_seq,created_at,updated_at,session_snapshot) VALUES(?,?,?,?,0,?,?,?,?,?)', (session['id'], self.ctx.scope_id, user_id, name, '', seq, timestamp, timestamp, dumps(snapshot(session))))
                changed = True
            for guest, guest_name, display in additions:
                seq += 1
                db.execute('INSERT INTO registrations(session_id,chat_id,user_id,name,guest_index,guest_name,queue_seq,created_at,updated_at,session_snapshot) VALUES(?,?,?,?,?,?,?,?,?,?)', (session['id'], self.ctx.scope_id, user_id, display, guest, guest_name, seq, timestamp, timestamp, dumps(snapshot(session))))
                changed = True
            if changed:
                audit(db, self.ctx.actor, 'registration.enroll', {'user_id': user_id, 'session_id': session['id'], 'guests': guests, 'added_guests': guests, 'active_guests': len(active_guests) + guests, 'removed': 0, 'chat_id': self.ctx.scope_id})
        return self.registrations(session['date'], venue_id, session['id'])

    def cancel(self, user_id, day=None, venue_id=1, session_id=None, at=None, number=None):
        user_id = identifier(user_id)
        if user_id != self.ctx.actor and (self.ctx.source in ('callback', 'simulator') or not self.ctx.can('activity.manage')):
            raise PermissionError('只能取消本人报名；同行人员请按姓名单独取消')
        session = self.resolve_session(day, venue_id, session_id, at, number)
        with connect(self.path, True) as db:
            Members(self.ctx, self.clock).require_approved(db=db)
            current = db.execute('SELECT * FROM sessions WHERE id=?', (session['id'],)).fetchone()
            if not current or session_time(current, 'end') <= self.clock():
                raise ValueError('该场次已调整或已经结束')
            self.assert_session_scope(current)
            changed = db.execute("UPDATE registrations SET status='cancelled',updated_at=? WHERE session_id=? AND chat_id=? AND user_id=? AND guest_index=0 AND status='active'", (stamp(self.clock()), session['id'], self.ctx.scope_id, user_id)).rowcount
            if changed:
                audit(db, self.ctx.actor, 'registration.cancel', {'user_id': user_id, 'session_id': session['id'], 'count': changed, 'chat_id': self.ctx.scope_id})
        return self.registrations(session['date'], venue_id, session['id']) | {'cancelled': changed}

    def cancel_guests(self, user_id, guest_names, day=None, venue_id=1, session_id=None, at=None, number=None):
        """Cancel only the caller's named active guests, all or none."""
        user_id = identifier(user_id)
        if user_id != self.ctx.actor:
            raise PermissionError('只能取消本人代报名的同行人员')
        if not isinstance(guest_names, list) or not 1 <= len(guest_names) <= 20 or any(not isinstance(value, str) for value in guest_names):
            raise ValueError('请提供1至20位要取消的同行姓名')
        names = [re.sub(r'[\x00-\x1f\x7f]', '', value).strip() for value in guest_names]
        if any(not value or len(value) > 100 for value in names):
            raise ValueError('同行姓名或名单显示名须为1至100字符')
        if len({value.casefold() for value in names}) != len(names):
            raise ValueError('取消名单中的同行姓名不能重复')
        session = self.resolve_session(day, venue_id, session_id, at, number)
        with connect(self.path, True) as db:
            member = Members(self.ctx, self.clock).require_approved(user_id, db=db, allow_admin=False)
            current = db.execute('SELECT * FROM sessions WHERE id=?', (session['id'],)).fetchone()
            if not current or session_time(current, 'end') <= self.clock():
                raise ValueError('该场次已调整或已经结束')
            self.assert_session_scope(current)
            candidates = list(db.execute("SELECT * FROM registrations WHERE session_id=? AND chat_id=? AND user_id=? AND guest_index>0 AND status='active'", (session['id'], self.ctx.scope_id, user_id)))
            targets = []
            for name in names:
                matches = [row for row in candidates if name.casefold() in {row['guest_name'].casefold(), registration_name(row, member['name']).casefold()}]
                if len(matches) != 1:
                    raise ValueError(f'未找到本人代报的唯一有效人员“{name}”，请核对本场名单')
                if matches[0]['id'] in targets:
                    raise ValueError('取消名单中重复指定了同一位同行人员')
                targets.append(matches[0]['id'])
            timestamp = stamp(self.clock())
            db.executemany("UPDATE registrations SET status='cancelled',updated_at=? WHERE id=?", [(timestamp, target) for target in targets])
            audit(db, self.ctx.actor, 'registration.cancel_guests', {'user_id': user_id, 'session_id': session['id'], 'registration_ids': targets, 'count': len(targets), 'chat_id': self.ctx.scope_id})
        return self.registrations(session['date'], venue_id, session['id']) | {'cancelled': len(targets)}

    def cancel_person(self, registration_id):
        self.require_admin('activity.manage')
        with connect(self.path, True) as db:
            row = db.execute('SELECT * FROM registrations WHERE id=? AND chat_id=?', (registration_id, self.ctx.scope_id)).fetchone()
            if not row:
                raise ValueError('报名记录不存在')
            session = db.execute('SELECT * FROM sessions WHERE id=?',(row['session_id'],)).fetchone()
            self.assert_session_scope(dict(session))
            if session_time(dict(session), 'end') <= self.clock():
                raise ValueError('已结束活动的报名记录不能取消')
            if row['status'] == 'cancelled':
                return {'ok': True}
            db.execute("UPDATE registrations SET status='cancelled',updated_at=? WHERE id=?", (stamp(self.clock()), registration_id))
            audit(db, self.ctx.actor, 'registration.cancel_person', {'registration_id': registration_id, 'user_id': row['user_id'], 'session_id': row['session_id']})
        return {'ok': True}

    def calendar(self, month, venue_id=1):
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError('月份格式为 YYYY-MM')
        first = day_value(month + '-01')
        if abs((first - self.clock().date()).days) > 3660:
            raise ValueError('日历查询范围为前后10年')
        days = [{'date': f'{month}-{d:02d}', 'sessions': self.sessions(f'{month}-{d:02d}', venue_id)}
                for d in range(1, calendar.monthrange(first.year, first.month)[1] + 1)]
        from .availability import Availability
        from .holidays import rules_for_day
        reservations = Availability(self.ctx,self.clock).list(venue_id,days[0]['date'],days[-1]['date'])
        with connect(self.path) as db:
            for item in days:
                item.update(rules_for_day(db,venue_id,item['date'])[1])
                item['occupancies']=[r for r in reservations if r['date']==item['date']]
        return {'days': days}

    def stats(self, venue_id=None, user_id=None):
        Members(self.ctx, self.clock).require_approved()
        if user_id is None:
            if not (self.ctx.can('activity.manage') or self.ctx.can('member.manage')):
                self.require_admin('activity.manage')
        elif not (self.ctx.can('activity.manage') or self.ctx.can('member.manage')) and user_id != self.ctx.actor:
            raise PermissionError('只能查询本人统计')
        sql = "SELECT r.*,s.date,s.venue_id,s.slot_key,CASE WHEN m.status='approved' THEN p.name ELSE '' END AS member_name FROM registrations r JOIN sessions s ON s.id=r.session_id LEFT JOIN profiles p ON p.chat_id=r.chat_id AND p.user_id=r.user_id LEFT JOIN member_records m ON m.chat_id=r.chat_id AND m.user_id=r.user_id WHERE r.chat_id=?"
        args = [self.ctx.scope_id]
        if venue_id is not None:
            self.venue(venue_id)
            sql += ' AND s.venue_id=?'
            args.append(venue_id)
        if user_id is not None:
            sql += ' AND r.user_id=?'
            args.append(user_id)
        with connect(self.path) as db:
            rows = [dict(x) for x in db.execute(sql, args) if self.allows_session(dict(x))]
            actions = [dict(x) for x in db.execute("SELECT * FROM audit WHERE action LIKE 'registration.%'")]
        grouped = {}
        for row in rows:
            member_name = row['member_name'] or row['name'].split('+')[0]
            if not member_name or member_name == row['user_id']:
                member_name = '待完善姓名'
            item = grouped.setdefault(row['user_id'], {'user_id': row['user_id'], 'name': member_name, 'registrations': 0, 'active': 0, 'cancelled': 0, 'guests': 0, 'participations': 0})
            item['registrations'] += 1
            item['active'] += row['status'] == 'active'
            item['cancelled'] += row['status'] == 'cancelled'
            item['guests'] += row['guest_index'] > 0
        # Tournament history is always narrowed to this chat before aggregating identities.
        try:
            from .competitions import TournamentService
            tournaments = TournamentService(self.path)
            for summary in tournaments.list(venue_id=venue_id, chat_id=self.ctx.scope_id):
                tournament = tournaments.get(summary['id'])
                if not self.event_allowed(tournament):
                    continue
                for participant in tournament.get('participants', []):
                    uid = str(participant.get('user_id', ''))
                    if uid in grouped:
                        grouped[uid]['participations'] += 1
        except ImportError:
            pass
        days = sorted({x['date'] for x in rows})
        daily = [{'date': d, 'count': sum(r['status'] == 'active' for r in rows if r['date'] == d)} for d in days]
        summary = {'registrations': len(rows), 'active': sum(r['status'] == 'active' for r in rows),
                   'cancelled': sum(r['status'] == 'cancelled' for r in rows), 'members': len(grouped),
                   'guests': sum(r['guest_index'] > 0 for r in rows), 'days': len(days),
                   'participations': sum(x['participations'] for x in grouped.values())}
        session_ids = {r['session_id'] for r in rows}
        summary['cancellation_actions'] = sum(x['action'] in ('registration.cancel', 'registration.cancel_guests', 'registration.cancel_person') and
            json.loads(x['detail']).get('session_id') in session_ids and (user_id is None or json.loads(x['detail']).get('user_id') == user_id) for x in actions)
        return {'summary': summary, 'members': list(grouped.values()), 'daily': daily}

    def dashboard(self, venue_id=1):
        self.require_admin('activity.manage')
        today = self.clock().date().isoformat()
        sessions = self.sessions(today, venue_id)
        stats = self.stats(venue_id)
        recent = self.audit()[:10] if self.ctx.can('system.manage') else []
        return {'today': today, 'counts': {'registrations': sum(s['count'] for s in sessions),
                    'upcoming': sum(s['status'] == 'upcoming' for s in sessions),
                    'ongoing': sum(s['status'] == 'ongoing' for s in sessions), 'members': stats['summary']['members']},
                'trend': stats['daily'][-14:], 'sessions': sessions, 'recent': recent}

    def audit(self):
        self.require_admin('system.manage')
        with connect(self.path) as db:
            return [dict(x) | {'detail': json.loads(x['detail'])} for x in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 500')]
