"""Typed, deny-by-default evaluation of robot audience rules.

The catalog is the sole field/operator allowlist. Policies restrict the acting
account at this entry point; they do not grant membership or management roles.
No rule or member value is passed to a language model or interpolated into SQL.
"""
from datetime import date
import json

from .db import connect, now


OPERATORS = {
    'eq': '等于', 'ne': '不等于', 'contains': '包含文字', 'prefix': '开头为',
    'in': '属于任一', 'not_in': '不属于', 'gte': '不低于 / 不早于', 'lte': '不高于 / 不晚于',
    'any': '包含任一', 'all': '包含全部', 'none': '不包含',
    'empty': '未填写 / 无', 'present': '已填写 / 有',
}
TYPE_OPS = {'text': ('eq','ne','contains','prefix','in','not_in','empty','present'),
            'enum': ('in','not_in','empty','present'), 'set': ('any','all','none','empty','present'),
            'number': ('eq','ne','gte','lte','empty','present'),
            'date': ('eq','ne','gte','lte','empty','present'), 'bool': ('eq',)}


def catalog(ctx):
    from .security import ROLE_DEFINITIONS, PERMISSION_DEFINITIONS
    def field(key, name, kind, options=None, **extra):
        return dict(id=key, name=name, type=kind, operators=list(TYPE_OPS[kind]),
                    options=options or [], **extra)
    def opts(values):
        return [{'id': key, 'name': name} for key, name in values]
    with connect(ctx.db_path) as db:
        groups = [dict(r) for r in db.execute('SELECT id,name FROM member_groups WHERE chat_id=? ORDER BY id', (ctx.scope_id,))]
    fields = [
        field('group_ids','组别','set',groups), field('level','已审核等级','number',min=1,max=9),
        field('gender','性别','enum',opts([('male','男'),('female','女'),('unknown','未填写')])),
        field('status','注册审核状态','enum',opts([('unregistered','未注册'),('pending','待审批'),('approved','已通过'),('rejected','已退回'),('suspended','已停用')])),
        field('roles','管理角色','set',[{'id':r['id'],'name':r['name']} for r in ROLE_DEFINITIONS]),
        field('permissions','管理权限','set',list(PERMISSION_DEFINITIONS)),
        field('name','姓名','text'), field('user_id','用户账号 ID','text'), field('phone','手机号','text',private=True),
        field('requested_level','自评级别','number',min=1,max=9),
        field('level_status','等级审核状态','enum',opts([('none','未申请'),('pending','待审批'),('approved','已通过'),('rejected','已退回')])),
        field('name_source','资料来源','text'), field('review_note','会员审核备注','text',private=True),
        field('level_note','等级审核备注','text',private=True),
        *[field(k,n,'date') for k,n in [('created_at','建档日期'),('updated_at','资料更新日期'),('reviewed_at','会员审核日期'),('level_reviewed_at','等级审核日期')]],
        field('membership.status','会籍状态','enum',opts([('not_configured','待配置有效期'),('active','有效'),('gap','续费间隔期'),('upcoming','尚未生效'),('expired','已到期'),('inactive','无有效期限')])),
        field('membership.configured','已登记会籍','bool'), field('membership.can_enroll','会籍允许新报名','bool'),
        field('membership.period_count','有效会籍记录数','number',min=0,max=100000),
        field('membership.record_count','全部会籍记录数','number',min=0,max=100000),
        *[field(k,n,'date') for k,n in [
            ('membership.current_term.start_date','本期会籍开始日期'),('membership.current_term.end_date','本期会籍结束日期'),
            ('membership.next_term.start_date','下期会籍开始日期'),('membership.next_term.end_date','下期会籍结束日期'),
            ('membership.last_end_date','最近到期日期'),('membership.registration.first_applied_at','首次申请日期'),
            ('membership.registration.first_approved_at','首次审核通过日期')]],
    ]
    return {'fields': fields, 'operators': OPERATORS}


def normalize(policy, ctx):
    if policy is None:
        return {'enabled': False, 'match': 'all', 'rules': []}
    if not isinstance(policy, dict) or set(policy) - {'enabled','match','rules'}:
        raise ValueError('用户条件配置无效')
    enabled, mode, rules = policy.get('enabled',False), policy.get('match','all'), policy.get('rules',[])
    if type(enabled) is not bool or mode not in ('all','any') or not isinstance(rules,list) or len(rules)>30:
        raise ValueError('用户条件须选择全部或任一满足，最多30条')
    if enabled and not rules:
        raise ValueError('启用用户限制时请至少添加一条条件')
    fields = {f['id']: f for f in catalog(ctx)['fields']}
    normalized = []
    for rule in rules:
        if not isinstance(rule,dict) or set(rule)-{'field','op','value'}:
            raise ValueError('用户条件字段无效')
        key, op, value = rule.get('field'), rule.get('op'), rule.get('value')
        if not isinstance(key,str) or key not in fields or not isinstance(op,str) or op not in fields[key]['operators']:
            raise ValueError('请选择支持的用户属性与匹配方式')
        f = fields[key]
        if op in ('empty','present'):
            value = None
        elif f['type'] in ('enum','set') or op in ('in','not_in'):
            if not isinstance(value,list) or not 1<=len(value)<=100:
                raise ValueError(f"{f['name']}请选择1至100个取值")
            allowed = [o['id'] for o in f['options']]
            if f['type'] in ('enum','set'):
                if any(type(v) not in (int,str) or v not in allowed for v in value):
                    raise ValueError(f"{f['name']}包含不存在的取值")
            elif any(not isinstance(v,str) or not v.strip() or len(v)>500 or any(ord(c)<32 for c in v) for v in value):
                raise ValueError('请填写有效的文字取值，每项不超过500字')
            value = list(dict.fromkeys(value))
        elif f['type']=='number':
            if type(value) is not int or not f['min']<=value<=f['max']:
                raise ValueError(f"{f['name']}须为{f['min']}至{f['max']}的整数")
        elif f['type']=='bool':
            if type(value) is not bool:
                raise ValueError(f"{f['name']}请选择是或否")
        elif f['type']=='date':
            try:
                if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError()
            except (ValueError,TypeError):raise ValueError('日期请填写 YYYY-MM-DD') from None
        elif not isinstance(value,str) or not value.strip() or len(value)>500 or any(ord(c)<32 for c in value):
            raise ValueError('文字条件须为1至500字')
        normalized.append({'field':key,'op':op,'value':value})
    return {'enabled':enabled,'match':mode,'rules':normalized}


def read_policy(ctx):
    if not ctx.platform_root or ctx.id=='workspace':return None
    with connect(ctx.platform_root/'registry.sqlite3') as db:
        bot = db.execute('SELECT * FROM bots WHERE id=?',(ctx.id,)).fetchone()
        if not bot or not bot['active'] or ('deleted_at' in bot.keys() and bot['deleted_at']):
            raise PermissionError('机器人已停用或删除')
        return json.loads(bot['user_policy']) if 'user_policy' in bot.keys() else None


def value_at(member, key):
    value = member
    for part in key.split('.'):
        value = value.get(part) if isinstance(value,dict) else None
    # A pending self-assessment is distinct from an approved level.
    if key=='level' and member.get('level') is None:return None
    return value


def matches(rule, member):
    key, op, expected = rule['field'], rule['op'], rule.get('value')
    value = value_at(member,key)
    absent = value is None or value=='' or value==[]
    if op=='empty':return absent
    if op=='present':return not absent
    # Missing data cannot pass a negative predicate accidentally.
    if value is None or value=='':return False
    if key.endswith(('_at','_date')) and isinstance(value,str):value=value[:10]
    if op=='eq':return value==expected
    if op=='ne':return value!=expected
    if op=='gte':return value>=expected
    if op=='lte':return value<=expected
    if op=='contains':return expected.casefold() in value.casefold()
    if op=='prefix':return value.casefold().startswith(expected.casefold())
    if op=='in':return value in expected
    if op=='not_in':return value not in expected
    if op=='any':return bool(set(value)&set(expected))
    if op=='all':return set(expected)<=set(value)
    if op=='none':return not set(value)&set(expected)
    return False


def require_access(ctx, clock=now, db=None):
    # Web administration and background publication have no acting group user.
    if ctx.source not in ('callback','simulator') or ctx.id=='workspace':return
    policy = read_policy(ctx)
    if not policy or not policy.get('enabled'):return
    from .members import Members
    if db is None:
        with connect(ctx.db_path) as connection:return require_access(ctx,clock,connection)
    member = Members(ctx,clock)._record(db,ctx.actor) or {'user_id':ctx.actor,'status':'unregistered'}
    checks = [matches(rule,member) for rule in policy['rules']]
    if checks and (all(checks) if policy['match']=='all' else any(checks)):return
    # Never echo phone numbers, review notes or filter values to a group.
    labels = {f['id']:f['name'] for f in catalog(ctx)['fields']}
    failed = list(dict.fromkeys(labels.get(r['field'],'用户资料') for r,ok in zip(policy['rules'],checks) if not ok))
    raise PermissionError('当前账号不符合本机器人的使用条件（'+ '、'.join(failed[:5]) +'）。可发送 注册资料 查看本人信息，或联系管理员。')


def public_summary(ctx):
    policy = read_policy(ctx)
    if not policy or not policy.get('enabled'):return ''
    labels = {f['id']:f['name'] for f in catalog(ctx)['fields']}
    names = list(dict.fromkeys(labels.get(r['field'],'用户资料') for r in policy['rules']))
    return '**使用条件** · '+('全部满足' if policy['match']=='all' else '任一满足')+'：'+'、'.join(names)
