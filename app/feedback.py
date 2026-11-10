"""Passive feedback permissions, independent from command capabilities.

Null configuration retains the old routing rules. Explicit choices replace that
inheritance; announcements are mandatory. No migration replays historical jobs.
"""
from .bot_capabilities import supports

DEFINITIONS = (
    ('registration_result', '注册审核结果', '会员', 'registration'),
    ('registration_invite', '注册确认邀请', '会员', 'registration'),
    ('member_profile', '会员资料变更通知', '会员', 'member_notifications'),
    ('role_change', '管理角色变更', '会员', 'management'),
    ('activity_open', '报名提醒', '活动', 'enrollment'),
    ('activity_closing', '报名结束提醒', '活动', 'enrollment'),
    ('activity_roster', '定时报名名单', '活动', 'enrollment'),
    ('occupancy', '场地占用与释放', '活动', 'enrollment'),
    ('daily_event', '每日赛事通知', '赛事', 'daily_events'),
    ('large_event', '大型赛事通知', '赛事', 'large_events'),
)
IDENTITY_TYPES = {'membership':'registration_result', 'level':'member_profile',
                  'groups':'member_profile', 'profile_request':'member_profile', 'roles':'role_change'}


def catalog():
    return [{'id':key, 'name':name, 'group':group, 'required':False} for key,name,group,_ in DEFINITIONS] + [
        {'id':'announcement', 'name':'消息中心公告', 'group':'公告', 'required':True}]


def normalize(value):
    if value is None:
        return None
    known = {row[0] for row in DEFINITIONS} | {'announcement'}
    if not isinstance(value, (list, tuple)) or any(not isinstance(v,str) for v in value) or len(value)!=len(set(value)) or set(value)-known:
        raise ValueError('反馈通知类型无效')
    return [row[0] for row in DEFINITIONS if row[0] in value]


def enabled(ctx, kind):
    if kind == 'announcement':
        return True
    definition = next((row for row in DEFINITIONS if row[0] == kind), None)
    if not definition:
        return False
    selected = getattr(ctx, 'notification_types', None)
    return supports(ctx, definition[3]) if selected is None else kind in selected


def require(ctx, kind):
    if kind != 'announcement' and getattr(ctx, 'notification_types', None) is None:
        definition = next((row for row in DEFINITIONS if row[0] == kind), None)
        if definition:
            from .bot_capabilities import require_capability
            return require_capability(ctx, definition[3])
    if not enabled(ctx, kind):
        name = next((row[1] for row in DEFINITIONS if row[0] == kind), '反馈通知')
        raise PermissionError(f'本机器人未开启{name}')


def identity_enabled(ctx, kind):
    return enabled(ctx, IDENTITY_TYPES[kind])


def kind_for_job(job, path):
    if job.get('notification_type'):
        return job['notification_type']
    key = job.get('dedupe_key') or ''
    if key.startswith('availability:'):
        return 'occupancy'
    if key.startswith('broadcast:'):
        return 'activity_' + key.rsplit(':', 2)[-2]
    if job.get('tournament_id') is not None:
        from .db import connect
        with connect(path) as db:
            event = db.execute('SELECT kind FROM tour_events WHERE id=?', (job['tournament_id'],)).fetchone()
        return 'daily_event' if event and event['kind']=='daily' else 'large_event'
    if job.get('session_ids') not in (None, 'null') or job.get('venue_id') is not None:
        return 'activity_roster'
    return 'announcement'
