"""Shared China-local calendar predicates for broadcasts and bot availability."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import re

from .db import TZ, now, connect


def bind_clock(ctx, clock=now):
    return replace(ctx, policy_clock=clock)


def normalize_when(value=None, *, timed=True):
    value = {} if value is None else value
    allowed = {'start_date','end_date','weekdays','day_type','holidays'} | ({'start_time','end_time'} if timed else set())
    if not isinstance(value,dict) or set(value)-allowed:
        raise ValueError('时间条件字段无效')
    result = {'start_date':'','end_date':'','weekdays':[], 'day_type':'all','holidays':'any'}
    if timed:result.update(start_time='',end_time='')
    result.update(value)
    for key in ('start_date','end_date'):
        text=result[key]
        if not isinstance(text,str):raise ValueError('日期格式为YYYY-MM-DD')
        if text:
            try:
                if date.fromisoformat(text).isoformat()!=text:raise ValueError()
            except ValueError:raise ValueError('日期格式为YYYY-MM-DD') from None
    if result['start_date'] and result['end_date'] and result['start_date']>result['end_date']:
        raise ValueError('结束日期不能早于开始日期')
    days=result['weekdays']
    if not isinstance(days,list) or len(days)>7 or any(type(d) is not int or not 0<=d<=6 for d in days):
        raise ValueError('星期须为周一至周日')
    result['weekdays']=sorted(set(days))
    if result['day_type'] not in ('all','workday','weekend','restday') or result['holidays'] not in ('any','exclude','only'):
        raise ValueError('日期类型无效')
    if timed:
        for key in ('start_time','end_time'):
            text=result[key]
            if not isinstance(text,str) or (text and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',text)):
                raise ValueError('时间格式为HH:MM')
        if bool(result['start_time'])!=bool(result['end_time']):raise ValueError('请同时填写起止时间')
        if result['start_time'] and result['start_time']==result['end_time']:raise ValueError('起止时间不能相同；全天请留空')
    return result


def calendar_matches(when, day, path):
    """None means the required statutory calendar is unavailable (fail closed)."""
    iso=day.isoformat()
    if when.get('start_date') and iso<when['start_date']:return False
    if when.get('end_date') and iso>when['end_date']:return False
    if when.get('weekdays') and day.weekday() not in when['weekdays']:return False
    kind=when.get('day_type','all');holiday=when.get('holidays','any')
    if kind=='weekend' and day.weekday()<5:return False
    if kind in ('workday','restday') or holiday!='any':
        from .holiday_calendar import read_year
        with connect(path) as db:catalog=read_year(db,day.year)
        if not catalog['known']:return None
        entry=next((r for r in catalog['days'] if r['date']==iso),None)
        is_holiday=bool(entry and entry['isOffDay'])
        is_workday=not entry['isOffDay'] if entry else day.weekday()<5
        if kind=='workday' and not is_workday:return False
        if kind=='restday' and is_workday:return False
        if holiday=='exclude' and is_holiday:return False
        if holiday=='only' and not is_holiday:return False
    return True


def window_matches(when, at, path):
    at=at.astimezone(TZ);day=at.date();hhmm=at.strftime('%H:%M')
    start,end=when.get('start_time',''),when.get('end_time','')
    if start:
        if start<end:
            if not start<=hhmm<end:return False
        else:
            if hhmm<end:day-=timedelta(days=1)
            elif hhmm<start:return False
    return calendar_matches(when,day,path)


def normalize_capability_rules(value, capabilities):
    if value is None:return []
    if not isinstance(value,list) or len(value)>50:raise ValueError('每个机器人最多50条功能时间规则')
    result=[];ids=set()
    for rule in value:
        if not isinstance(rule,dict) or set(rule)-{'id','name','enabled','capabilities','action','when'}:
            raise ValueError('功能时间规则字段无效')
        rid=rule.get('id');name=rule.get('name','')
        if not isinstance(rid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',rid) or rid in ids:raise ValueError('时间规则标识重复或无效')
        if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValueError('请填写规则名称，最多80字')
        if type(rule.get('enabled',True)) is not bool:raise ValueError('规则开关无效')
        caps=rule.get('capabilities')
        if not isinstance(caps,list) or not caps or any(c not in capabilities for c in caps):
            raise ValueError('时间规则只能选择本机器人已支持的功能')
        if rule.get('action') not in ('open','closed'):raise ValueError('请选择开放或关闭')
        result.append({'id':rid,'name':name.strip(),'enabled':rule.get('enabled',True),'capabilities':sorted(set(caps)),
                       'action':rule['action'],'when':normalize_when(rule.get('when'))})
        ids.add(rid)
    return result


def capability_open(ctx, capability, at=None):
    rules=[r for r in (getattr(ctx,'capability_rules',None) or ()) if r['enabled'] and capability in r['capabilities']]
    if not rules:return True
    at=at or ctx.policy_clock()
    opened=[r for r in rules if r['action']=='open']
    if opened and not any(window_matches(r['when'],at,ctx.db_path) is True for r in opened):return False
    # Unknown calendars cannot accidentally open a configured closed period.
    return not any(window_matches(r['when'],at,ctx.db_path) is not False for r in rules if r['action']=='closed')


def effective_capabilities(ctx):
    from .bot_capabilities import normalize_capabilities
    return tuple(c for c in normalize_capabilities(ctx.capabilities) if capability_open(ctx,c))


def normalize_broadcasts(value, bot_ids=None, *, limit=50):
    if value is None:return None  # Preserve legacy three reminder defaults.
    if not isinstance(value,list) or len(value)>limit:raise ValueError(f'最多{limit}条定时播报规则')
    result=[];ids=set()
    for rule in value:
        if not isinstance(rule,dict) or set(rule)-{'id','name','enabled','kinds','bot_ids','time','when','excluded_bot_ids'}:raise ValueError('播报规则字段无效')
        rid=rule.get('id');name=rule.get('name','')
        if not isinstance(rid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',rid) or rid in ids:raise ValueError('播报规则标识重复或无效')
        if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValueError('请填写规则名称，最多80字')
        if type(rule.get('enabled',True)) is not bool:raise ValueError('规则开关无效')
        kinds=rule.get('kinds');bots=rule.get('bot_ids');hhmm=rule.get('time')
        if not isinstance(kinds,list) or not kinds or any(k not in ('open','closing','roster') for k in kinds):raise ValueError('请选择播报类型')
        if not isinstance(bots,list) or not bots or len(bots)>100 or any(not isinstance(b,str) or (bot_ids is not None and b not in bot_ids) for b in bots):raise ValueError('请选择有效的执行机器人')
        excluded=rule.get('excluded_bot_ids',[])
        if not isinstance(excluded,list) or any(b not in bots for b in excluded):raise ValueError('播报排除范围无效')
        if not isinstance(hhmm,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',hhmm):raise ValueError('播报时间格式为HH:MM')
        result.append({'id':rid,'name':name.strip(),'enabled':rule.get('enabled',True),'kinds':list(dict.fromkeys(kinds)),
                       'bot_ids':list(dict.fromkeys(bots)),'time':hhmm,'when':normalize_when(rule.get('when'),timed=False)})
        if excluded:result[-1]['excluded_bot_ids']=list(dict.fromkeys(excluded))
        ids.add(rid)
    return result


def broadcast_rules(config, bot_ids):
    if config.get('broadcast_rules') is not None:return config['broadcast_rules']
    return [{'id':'legacy-'+kind,'name':kind,'enabled':True,'kinds':[kind],'bot_ids':list(bot_ids),'time':hhmm,'when':{}}
            for kind,hhmm in config['reminders'].items()]


def rule_fingerprint(rule):
    import hashlib
    import json
    content={k:v for k,v in rule.items() if k!='excluded_bot_ids'}
    return hashlib.sha256(json.dumps(content,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]


def broadcast_delivery_allowed(key, ctx, at, config):
    """Queued reminders are invalidated by edits, disabled senders or a new day."""
    marker='broadcast:'
    if marker not in key:return True
    parts=key.split(marker,1)[1].split(':')
    if len(parts)<7:return False
    rid,fingerprint,day,hhmm,venue,kind,channel=parts[:7]
    if not config['daily_enabled'] or not ctx.daily_notifications or day!=at.astimezone(TZ).date().isoformat():return False
    if ctx.platform_root:
        with connect(ctx.platform_root/'registry.sqlite3') as db:
            bots=[r[0] for r in db.execute("SELECT id FROM bots WHERE deleted_at='' ORDER BY id")]
    else:bots=[ctx.id]
    rules=broadcast_rules(config,bots)
    rule=next((r for r in rules if r['id']==rid),None)
    if not rule or not rule['enabled'] or ctx.id not in rule['bot_ids'] or ctx.id in rule.get('excluded_bot_ids',[]) or kind not in rule['kinds']:return False
    if config.get('broadcast_rules') is None or (rid=='legacy-'+kind and rule.get('when')=={} and rule.get('kinds')==[kind] and rule.get('name')==kind):
        # Legacy default recipients are intentionally all current robots.
        return rule['time'].replace(':','')==hhmm
    return rule_fingerprint(rule)==fingerprint and calendar_matches(rule['when'],at.astimezone(TZ).date(),ctx.db_path) is True
