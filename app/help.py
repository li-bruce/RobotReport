"""Task-oriented help, filtered by the same capability and permission scope as commands."""
from html import escape as html_escape
from .bot_capabilities import normalize_capabilities

COMMAND_COLOR = '#0066b2'
PARAMETER_COLOR = '#916100'


def command(text):
    return f'<font color="{COMMAND_COLOR}">{html_escape(text, quote=False)}</font>'


def parameter(text):
    return f'<font color="{PARAMETER_COLOR}">〈{html_escape(text, quote=False)}〉</font>'


def syntax(literal, fields=''):
    return command(literal) + (' ' + parameter(fields) if fields else '')


CATEGORIES = {'注册':'registration','报名':'enrollment','比赛':'daily_events','大型赛事':'large_events',
              '记录':'match_records','统计':'statistics','AI':'ai','管理':'management'}


def _can(enabled, permissions, is_admin, permission):
    return 'management' in enabled and (is_admin or permission in permissions)


def _ai_example(enabled):
    return next((text for key,text in (
        ('enrollment','AI 明天第一场帮我占个位，再看那场名单'),
        ('daily_events','AI 组织今天的双打积分赛'),('large_events','AI 查看春季联赛的赛程'),
        ('statistics','AI 查看我的报名统计'),('match_records','AI 查看我的对战历史'),
        ('registration','AI 我要注册')) if key in enabled),'AI 查看帮助')


def syntax_help(enabled, is_admin=False, permissions=(), category=None):
    """A small command index; detailed syntax appears only in category help."""
    enabled=set(enabled)
    can=lambda key:_can(enabled,permissions,is_admin,key)
    rows=[]

    def add(cat,cap,title,example,actions=(),form=''):
        if cap not in enabled or category not in (None,cat):return
        body=f'**{title}**\n'+command(example)
        if actions:body+='\n'+' · '.join(command(action) for action in actions)
        if category and form:body+='\n格式：'+form
        rows.append(body)

    add('注册','registration','注册与资料','注册确认',('注册资料','修改资料'),command('注册')+' → 本人表单')
    add('报名','enrollment','报名与预约','明天第2场 报名，张三，李四',('报名','名单','取消报名','场地占用'),parameter('日期 / 场地 / 场次，可省略')+' '+command('报名')+'，'+parameter('同行姓名，可省略'))
    add('比赛','daily_events','每日趣味赛','组织比赛 混双',('继续比赛',),syntax('组织比赛','单打 / 双打 / 男双 / 女双 / 混双'))
    add('大型赛事','large_events','大型赛事报名','春季联赛报名',(),parameter('赛事关键词')+command('报名'))
    add('记录','match_records','零散对局','记录 双打 我、张三 VS 李四、王五 21:19',('我的对战历史','我的对战统计'),command('记录')+' '+parameter('项目')+' '+parameter('我方姓名')+' VS '+parameter('对方姓名')+' '+parameter('比分'))
    add('统计','statistics','报名统计','我的统计',('统计',) if can('activity.manage') else ())
    if category in ('比赛','大型赛事') and enabled & {'daily_events','large_events'}:
        rows.append('**赛程与比分**\n'+command('周二双打，对战表')+'\n'+command('周二双打，第十二场 21:19')+'\n格式：'+parameter('赛事名称')+'，'+parameter('第几场')+' '+parameter('比分'))
    if category in (None,'管理'):
        if can('member.manage'):
            rows.append('**会员审核**\n'+command('待审核')+' · '+command('审核通过 申请12')+'\n'+(syntax('审核通过','申请编号 / 唯一姓名') if category else command('帮助 管理')))
        if can('activity.manage'):
            rows.append('**场地占用**\n'+command('场地1 明天 占用 17:30-20:30 校队训练')+'\n'+command('释放占用12'))
        if can('system.manage') and 'ai' in enabled:
            rows.append('**AI开关**\n'+command('开启AI')+' · '+command('关闭AI'))
        if can('message.manage') and 'messages' in enabled:
            rows.append('**消息发布**\n在后台“消息发布”编辑内容、选择群并发送。')
        if can('event.manage') and enabled & {'daily_events','large_events'}:
            rows.append('**赛事管理**\n'+command('春季联赛，暂停比赛')+' · '+command('春季联赛，恢复比赛'))
        if can('bot.manage'):
            rows.append('**机器人设置**\n后台“机器人与群”配置业务、场次与播报。')
    if 'ai' in enabled and category in (None,'AI'):
        rows.append('**自然语言**\n'+command(_ai_example(enabled))+'\n修改先预览再确认；回复 '+command('确认')+' 执行。')
    return '\n\n'.join(rows) or '本机器人当前没有开放的业务指令。'


def command_help(member, is_admin=False, category=None, permissions=None, capabilities=None):
    enabled=set(normalize_capabilities(capabilities))
    permissions=set(permissions or ())
    can=lambda key:_can(enabled,permissions,is_admin,key)
    available=[name for name,cap in CATEGORIES.items() if cap in enabled and (name!='管理' or is_admin or permissions)]
    more='查看用法：'+' · '.join(command('帮助 '+name) for name in available) if available else ''
    if category:
        if category not in CATEGORIES:return more or '发送 '+command('帮助')+' 查看可用功能。'
        if CATEGORIES[category] not in enabled:return '本机器人未开启此业务。发送 '+command('帮助')+' 查看可用功能。'
        if category=='管理' and not (is_admin or permissions):return '当前没有群内管理权限。'
        body=syntax_help(enabled,is_admin,permissions,category)
        notes=[]
        if category=='注册':
            notes=[
                '**注册流程**\n'+command('注册')+' → 打开本人链接 → 提交资料。\n姓名、性别必填；手机号、1–9级自评选填。',
                '导入名册中姓名唯一匹配：确认后直接注册。重名填写登记手机号；未匹配则等待审核，结果 @本人通知。',
                '**补充与调整**\n'+command('性别 男')+' / '+command('性别 女')+' 补齐性别。\n'+command('申请分组 提高组')+' · '+command('申请等级 5级')+'\n'+command('我的资料申请')+' 查看审批进度。',
                command('我的会籍')+' 查看有效期；到期后不能新报名，历史仍可查询。']
        elif category=='报名':
            notes=[
                '**同行报名**\n'+command('报名+2')+' 或 '+command('报名，张三，李四')+'。每次追加同行。\n'+command('报名 接龙')+' 报名后显示名单。',
                '**取消**\n'+command('取消报名')+' 仅取消本人。\n'+command('取消张三，李四')+' 取消自己代报的指定同行。',
                '**预约与查询**\n'+command('2026-11-18 第二场 报名')+'\n'+command('明天第二场 名单')+' · '+command('明天 场地占用')+'\n多个场地时加 '+command('场地2')+'；不能报名时按回复调整时间或场次。']
        elif category=='比赛':
            notes=[
                '**发起比赛**\n1. '+command('组织比赛')+' 查看人数与可用赛制，默认双打。\n2. 回复 '+command('积分赛')+' 等可用形式，生成对战表。\n3. 按场次记分，打完查看名次。',
                '可选单打、双打、男双、女双、混双；混双每队一男一女。系统按人数和性别检查可用形式。',
                command('周二双打，我的赛程')+' · '+command('周二双打，比分')+' · '+command('周二双打，规则'),
                '发起人可记分、修改、暂停和关闭；参赛者可查看自己的赛程。未完赛事用 '+command('继续比赛')+' 选择恢复。']
        elif category=='大型赛事':
            notes=[
                '按群公告中的关键词报名，开放时间与资格以公告为准。',
                command('春季联赛，报名，张三')+' 添加同行。\n'+command('春季联赛，取消报名')+' 取消本人。\n'+command('春季联赛，取消张三')+' 取消本人代报的同行。',
                command('春季联赛，我的赛程')+' · '+command('春季联赛，规则')+' · '+command('春季联赛，集合'),
                '只有赛事负责人或指定记分员可登记比分；授权期内登记新比分，修正由负责人处理。',
                '**赛事协调**\n负责人或协调员可发 '+command('赛事名，协调安排 内容')+' 保存安排；'+command('赛事名，公告文案 内容')+' 保存公告。保存后不会自动群发。']
        elif category=='记录':
            notes=[
                '本人须参赛，当天提交立即生效；可用单打、双打、男双、女双、混双。同名人员按回复选择。',
                command('修改记录 12 21:18')+' · '+command('作废记录 12')+'\n本人处理自己提交的记录，管理员可修正全部。',
                '查询当前机器人负责范围内的个人对战历史、搭档组合和对手胜率。']
        elif category=='统计':
            notes=['查看个人报名与出勤汇总。'+('管理人员可用 '+command('统计')+' 查看全部报名汇总。' if can('activity.manage') else '')]
        elif category=='AI':
            notes=[
                '**怎么说**\n'+syntax('AI','需求；可包含多步操作')+'。纯查询直接返回结果。',
                '**执行与修改**\n1. 一次最多8步，整单预览。\n2. 回复 '+command('确认')+' 执行当前方案。\n3. 发 '+syntax('AI','修改意见')+' 替换方案。',
                command('AI进度')+' 查看当前方案；'+command('取消计划')+' 放弃。',
                '注册、资料等表单操作会返回链接，需要在页面提交。']
            if can('system.manage'):notes.append(command('AI状态')+' · '+command('开启AI')+' · '+command('关闭AI'))
        elif category=='管理':
            notes=['后台管理员默认拥有全部管理权限；其他人员按已授予职责操作。']
            if can('member.manage'):
                notes += [command('审核拒绝 申请12')+' 退回注册申请。同名时使用待审核名单中的申请编号。',
                          command('资料待审核')+' · '+command('资料查看 申请12')+'\n'+command('资料通过 申请12')+' · '+command('资料拒绝 申请12'),
                          '姓名、手机号、组别和等级在会员档案维护；会籍可按多段日期续费。停用或到期后禁止新报名，历史报名仍保留。']
            if can('activity.manage'):notes.append(command('开启节假日')+' · '+command('关闭节假日')+' · '+command('节假日设置'))
            if can('bot.manage'):notes.append('同一群可接入多个机器人；每个机器人只关联一个群。')
        return '# '+category+'用法\n\n'+body+'\n\n'+'\n\n'.join(notes)

    status=(member or {}).get('status','unregistered')
    if status!='approved' and not is_admin:
        titles={'unregistered':'尚未注册','pending':'注册申请待审批','rejected':'申请已退回','suspended':'会员已停用'}
        heading='# '+titles.get(status,'会员注册')+'\n\n'
        if 'registration' not in enabled:return heading+'本机器人不办理会员注册，请使用负责注册的机器人。'
        if status=='suspended':return heading+'请联系管理员。'+command('注册资料')+' 查看当前状态。'
        if status=='pending':return heading+'审核结果会 @本人通知。发送 '+command('注册资料')+' 查看进度。'
        return heading+'发送 '+command('注册')+'，打开本人链接提交。\n姓名、性别必填；手机号、1–9级自评选填。\n\n已在导入名册中的成员，发送 '+command('注册确认')+' 核对资料。\n'+command('帮助 注册')+' 查看完整流程。'
    return '# 使用帮助\n\n'+syntax_help(enabled,is_admin,permissions)+'\n\n'+more


def scoped_help(ctx, member, category=None, clock=None):
    """Use the same current bot scope for replies and administrator previews."""
    from .service import Service
    from .db import now, connect
    from .messages import escape
    from .scope import special_rules
    import json
    service=Service(ctx,clock or now);ctx=service.scope_context()
    from dataclasses import replace
    from .time_rules import effective_capabilities
    ctx=replace(ctx,capabilities=effective_capabilities(ctx))
    value=command_help(member,ctx.is_admin,category,ctx.permissions,ctx.capabilities)
    from .bot_access import public_summary
    audience=public_summary(ctx)
    if audience:value=audience+'\n\n'+value
    if category or not set(ctx.capabilities or ()) & {'enrollment','daily_events','large_events','match_records'}:return value
    venues=[v for v in service.venues() if v['active']]
    lines=[]
    for venue in venues:
        if not set(ctx.capabilities or ()) & {'enrollment','daily_events','match_records'}:
            lines.append(f'场地{venue["id"]} {escape(venue["name"])} · 大型赛事按赛事安排')
            continue
        rules=service.slots(venue['id'])
        with connect(ctx.db_path) as db:
            from .holidays import read as holiday_config
            holiday=holiday_config(db,venue['id'])
            if holiday['enabled']:
                rules.extend(dict(s,period='节假日') for s in holiday['slots'] if ctx.slot_keys is None or s['slot_key'] in ctx.slot_keys)
            for saved in db.execute('SELECT definition FROM holiday_plans WHERE venue_id=?',(venue['id'],)):
                plan=json.loads(saved[0])
                if plan['enabled'] and plan['end_date'] >= (clock or now)().date().isoformat():
                    rules.extend(dict(s,period=plan['name']+' '+plan['start_date']+'至'+plan['end_date'])
                                 for s in plan['slots'] if ctx.slot_keys is None or s['slot_key'] in ctx.slot_keys)
            for special in db.execute('SELECT * FROM special_dates WHERE venue_id=? AND date>=?',(venue['id'],(clock or now)().date().isoformat())):
                rules.extend(dict(s,period=special['date']) for s in special_rules(venue['id'],special['date'],json.loads(special['slots'])) if ctx.slot_keys is None or s['slot_key'] in ctx.slot_keys)
        if not rules:
            lines.append(f'场地{venue["id"]} {escape(venue["name"])} · '+('大型赛事按赛事安排' if 'large_events' in ctx.capabilities else '暂无负责的日常场次'))
        for rule in rules:
            days=rule.get('weekdays',[])
            period=rule.get('period') or ({(0,1,2,3,4):'工作日',(5,6):'周末'}.get(tuple(days)) or '周'+''.join('一二三四五六日'[d] for d in days))
            lines.append(f'场地{venue["id"]} {escape(venue["name"])} · {escape(period)} {escape(rule["name"])} {rule["start"]}–{rule["end"]}')
    scope='**本机器人负责**\n'+'\n'.join(lines[:8]) if lines else '**本机器人负责**\n暂未分配可用场地。'
    if len(lines)>8:scope+='\n更多时段可查看活动名单。'
    if ctx.slot_keys is not None or ctx.venue_restricted:
        value=value.replace('明天第2场 报名，张三，李四','报名，张三，李四').replace('明天第二场 名单','名单').replace('AI 明天第一场帮我占个位，再看那场名单','AI 帮我报名最近一场，再看那场名单')
    return value+'\n\n'+scope
