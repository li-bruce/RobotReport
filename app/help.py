"""Short, state-aware command help, separate from command execution."""
GUIDES = {
    '注册': '# 注册与会员\n\n**第一次使用**：发送 注册 → 打开本人链接 → 填写姓名和性别并直接提交。\n\n管理员已批量登记者填写登记手机号，精确匹配后关联；不完整资料补全后审核，不按姓名匹配。未匹配则提交审核，会员管理员可在群内或后台审核，结果会主动 @本人通知；手机号已绑定其他账号时不会换绑。\n\n已注册用户无需重复申请。新注册的姓名、性别必填，自评和手机号选填；也可发送 注册 张三 男 2级。\n\n发送 **性别 男** 或 **性别 女** 可补齐或更正本人资料。新比赛按当前性别校验，已记录对局保留原快照。\n\n发送 注册资料 查看状态；发送 自评2级 申请等级，支持 **1–9级**。会员资格跨本后台机器人与群通用。',
    '报名': '# 报名与预约\n\n**报本人**：报名\n**追加同行**：报名，张三，李四 或 报名+2\n**仅取消本人**：取消报名\n**取消自己代报的指定同行**：取消张三，李四\n**查看名单**：名单\n\n指定场地：场地2 报名；指定场次：第2场 报名。\n预约：2026-10-18 18:00 报名；查询：2026-10-18 名单。\n\n本人重复报名不重复占位；带人报名每次追加，保留原同行。报名+2 每次新增2人；已代报同名同行时，本次不添加，请先查名单。\n\n名单显示姓名、性别、名额（正式 / 替补 / 候补）。同行显示“张三（本人姓名代报名）”，不自动成为会员，性别未填写。取消本人不会取消同行。',
    '比赛': '# 每日趣味赛\n\n1. 先报名，再发送 **组织比赛**（默认普通双打）；也可用 组织比赛 单打 / 男双 / 女双 / 混双。\n2. 看人数和可用形式，回复 积分赛、五人转 等赛制名称。\n3. 发起者负责记分：周二双打，第十二场21：19。\n4. 正式会员可查询：周二双打，对战表 / 比分 / 我的赛程。\n\n男双每队两名男性，女双每队两名女性，混双每队一男一女。性别未填写者需先补齐，人数或性别组合不足时会提示。\n\n负责人和管理员可记分、改分及控制比赛；有效记分员只登记新比分。负责人：周二双打，暂停比赛 / 恢复比赛 / 关闭比赛。关闭保留历史，不能恢复。\n\n大型赛事按管理员发布的报名口令参加。追加同行：春季联赛，报名，张三，李四；仅取消本人：春季联赛，取消报名；取消自己代报的指定同行：春季联赛，取消张三，李四。\n\n赛事名，规则 可查看说明；大型赛事由指定负责人、记分员、协调员分工管理。',
    '记录': '# 零散对局与战绩\n\n**记一次对局**：\n记录 单打 我 VS 张三 21:19\n记录 双打 我、张三 VS 李四、王五 21-19\n记录 混双 我、张三 VS 李四、王五 21：19\n\n支持男双、女双；指定活动用 记录 第2场 单打 我 VS 张三 21:19。具名访客可写 访客：小王（女）。\n\n本人必须参赛，当天提交立即生效；同名会员先选择，重复对局先确认。\n\n**查历史**：我的对战历史\n**查胜率和搭档**：我的对战统计\n可加日期：我的对战历史 2026-10-01 至 2026-10-31\n\n**修正比分**：修改记录 12 21:18\n**作废误记**：作废记录 12\n数字12是零散对局编号。只能修改或作废本人提交的记录；管理员可修正全部。\n\n战绩合并已完成的正式赛事和零散对局，区分来源。作废、轮空和未完成比赛不计；查询仅显示当前机器人负责范围内的记录。',
    '管理': '# 管理与职责\n\n后台管理员默认拥有全部管理权限。会员、活动、赛事、消息、机器人和系统管理可按职责分别授予；注册通过不等于成为管理员，角色由后台管理员在人员中心单独配置。\n\n**会员审核**（后台管理员或会员管理员）：\n待审核\n审核通过 申请12\n审核拒绝 申请12\n\n也可用唯一姓名：审核通过 张三。同名时使用待审核名单中的申请编号，不在群里公开手机号。注册、个人资料、等级和组别结果由同群开启“会员注册”的机器人 @本人通知；管理角色变更由“群内管理”机器人通知。\n\n活动管理员可发送 统计；系统管理员可发送 开启AI、关闭AI。\n\n赛事负责人管理本赛事，记分员登记比赛，协调员负责安排和说明；赛事职务不等于后台管理员。\n\n负责人和协调员可发送：\n赛事名，集合说明 18点在一号馆集合\n赛事名，公开说明 欢迎参加周末联赛\n赛事名，协调安排 请工作人员提前布场\n\n协调安排只回复保存确认，不随公开说明发布；群内输入本身仍然可见，敏感内容请在后台填写。\n\n后台“机器人与群”按群查看机器人，展开后配置业务、场地、时段；群连接无需重复配置范围。机器人只展示和通知负责的活动。调整活动时段后，历史报名仍保留当时的场次与时间。\n\n同一群可接入多个机器人，每个机器人只绑定一个群，各自使用独立回调和密钥；@谁由谁回复。同一赛事可以通过多个机器人管理，通知按群合并发送一次；普通回复只回当前群。零散对局在后台个人对局页查询、修正或作废。',
    'AI': '# AI 对话助手\n\n**普通指令**：报名 / 名单 / 周二双打，第一场21-19，直接使用。\n**自然表达**：以 AI 开头描述需求，也支持 AI：内容、AI:内容、/ai 内容。不必照抄固定句式，一句话最多安排 **8步**。\n\n**报名后查名单**：AI 明天第一场帮我占个位，再看那场名单。\n**取消自己代报的同行**：AI 取消我帮张三李四报的名。先核对预览中的姓名；本人和其他同行保留。\n**一步说清比赛**：AI 组织今天第一场的双打积分赛，叫周二双打。会先核对报名人数，再按可用赛制编排。\n**管理员配置**：AI 明天第一场正式12人替补4人，再把报名提醒改成10点。\n**会员审核**：AI 通过张三的注册，再查看还有谁待审核。需要会员管理权限，先预览、确认后办理；同名时用申请编号。\n\n纯查询直接返回结果；包含修改时先展示**整单预览**，核对日期、对象和每步内容后，回复 **确认**，按顺序执行。\n查看执行结果：**AI进度**；放弃待确认方案：**取消计划**。修改发 **AI + 修改意见**，新方案覆盖旧方案。每人只保留一份当前方案，请在原群 @对应机器人确认。步骤失败会停止后续操作，已完成结果可查。\n\n**表单类业务**：AI 我要注册 / AI 批量导入会员 / AI 发布比赛照片，会给出本人表单或管理页面入口，需要在页面提交。\n\n发送 **AI状态** 查看开关，**AI帮助** 查看本说明；管理员可用 开启AI / 关闭AI。信息缺失或有歧义时，按提示补充；可办理范围随会员资格和管理职责确定。',
}

GUIDES['注册'] += '\n\n**资料调整**：补全资料 / 修改资料 打开本人表单；申请分组 提高组 / 申请等级 5级 直接提交申请。多个分组用逗号分隔，申请分组为完整名单。我的资料申请 查看进度；取消资料申请 撤回。审核通过前保留原资料。'

GUIDES['注册'] += '\n\n**会籍与续费**：我的会籍 / 续费记录 查看本人多段有效期。各段首尾日期均计入；到期或续费间隔期不能新增报名，仍可查询历史、取消已有报名。续费请联系管理员。'
GUIDES['报名'] += '\n\n支持中文场次：明天第二场报名 / 2026-10-14 第二场 报名；AI 报名明天第一场 会先给出待确认提案。预约需当前和活动当天均在已登记有效期内。'

GUIDES['AI'] += '\n\n**个人对战**：AI 查看我的对战历史 / AI 统计我的搭档胜率 可直接查询。AI 记录一局比赛、修正或作废零散对局 会给出明确指令示例；按示例发送 记录 / 修改记录 / 作废记录 后办理。AI不会猜测同名人员或代填比分。'


def command_help(member, is_admin=False, category=None, permissions=None, capabilities=None):
    """Show only the robot's enabled businesses and the caller's management work."""
    from .bot_capabilities import normalize_capabilities
    enabled = set(normalize_capabilities(capabilities))
    permissions = set(permissions or ())
    can = lambda key: 'management' in enabled and (is_admin or key in permissions)
    categories = {'注册':'registration', '报名':'enrollment', '比赛':'daily_events',
                  '大型赛事':'large_events', '记录':'match_records', '统计':'statistics',
                  'AI':'ai', '管理':'management'}
    available = [name for name, key in categories.items()
                 if key in enabled and (name != '管理' or is_admin or permissions)]
    more = '更多说明：' + ' / '.join('帮助 ' + name for name in available) + '。'
    if category:
        if category not in categories:
            return more
        if categories[category] not in enabled:
            return '本机器人未开启此业务。发送 帮助 查看可用功能。'
        if category == '管理':
            if not is_admin and not permissions:
                event_help = [name for name in ('比赛', '大型赛事') if categories[name] in enabled]
                return '当前没有群内管理权限。' + ('赛事负责人和工作人员请查看 ' + ' 或 '.join('帮助 ' + name for name in event_help) + '。' if event_help else '')
            sections = ['# 我的管理功能', '后台管理员默认拥有全部管理权限；其他管理角色按职责分别授予。注册审核通过不会自动获得管理角色。']
            if can('member.manage'):
                sections.append('**资料调整审核**：资料待审核 / 资料查看 申请12 / 资料通过 申请12 / 资料拒绝 申请12。也可在人员中心 → 资料调整中审核，结果主动 @本人通知。')
                sections.append('**会员审核**：待审核 / 审核通过 申请12 / 审核拒绝 申请12。\n\n也可用唯一姓名；同名时使用待审核名单中的申请编号，群里不公开手机号。结果会主动 @本人通知。')
            if can('activity.manage') and 'statistics' in enabled:
                sections.append('**活动统计**：统计。')
            if can('activity.manage'):
                sections.append('**场地占用**：场地1 明天 占用 17:30-20:30 校队训练；释放占用12。大型赛事在赛事页面预留。\n\n**节假日**：开启节假日 / 关闭节假日 / 节假日设置。')
            if can('system.manage') and 'ai' in enabled:
                sections.append('**AI 开关**：开启AI / 关闭AI。')
            if can('event.manage') and enabled & {'daily_events','large_events'}:
                sections.append('**赛事管理**：赛事名，暂停比赛 / 恢复比赛 / 关闭比赛；赛事名，集合说明 18点到场。')
            if can('bot.manage'):
                sections.append('后台“机器人与群”配置业务、群、场地和时段；同一群可接入多个机器人，各自使用独立回调和密钥。机器人只展示和通知负责的活动。调整活动时段后，历史报名仍保留当时的场次与时间。')
            if can('message.manage') and 'messages' in enabled:
                sections.append('**消息发布**：后台编辑文字、图片或文件后选择群发送。')
            return '\n\n'.join(sections) + '\n\n' + syntax_help(enabled,is_admin,permissions,category)
        if category == '大型赛事':
            return (syntax_help(enabled,is_admin,permissions,category) + '\n\n# 大型赛事\n\n按公告关键词报名：春季联赛报名。\n追加同行：春季联赛，报名，张三，李四。\n仅取消本人：春季联赛，取消报名；取消自己代报的同行：春季联赛，取消张三，李四。\n\n'
                    '**查询**：赛事名，对战表 / 比分 / 我的赛程 / 规则。\n\n'
                    '**指定赛事人员**：负责人、记分员、协调员按职务工作；赛事职务不等于后台管理员。负责人和协调员可发送：赛事名，集合说明 18点到场 / 赛事名，公开说明 欢迎参赛 / 赛事名，协调安排 请提前布场。\n\n'
                    '协调安排只回复保存确认，群内输入本身仍然可见；敏感内容请在后台填写。')
        if category == '统计':
            return '# 报名统计\n\n我的统计 查看本人报名、取消、代报及出赛汇总。' + ('\n\n统计 查看全部报名汇总。' if can('activity.manage') else '')
        if category == '比赛':
            value = GUIDES['比赛'].split('\n\n大型赛事')[0]
            if 'enrollment' not in enabled:
                value = value.replace('先报名，再发送', '使用后台已有报名人员，发送')
            return syntax_help(enabled,is_admin,permissions,category) + '\n\n' + value + '\n\n赛事名，规则 查看说明；负责人可发送 赛事名，集合说明 18点到场。历史对战与比分持续保存。'
        if category == 'AI':
            import re
            parts = re.split(r'\n\n|\n(?=\*\*)', GUIDES['AI'])
            kept = []
            for part in parts:
                if part.startswith(('**普通指令**','**报名后查名单**','**取消自己代报的同行**')) and 'enrollment' not in enabled:
                    continue
                if part.startswith('**普通指令**') and 'daily_events' not in enabled:
                    part='**普通指令**：报名 / 名单，直接使用。'
                if part.startswith('**管理员配置**') and not (can('activity.manage') and 'enrollment' in enabled):
                    continue
                if part.startswith('**会员审核**') and not can('member.manage'):
                    continue
                if part.startswith('**个人对战**') and 'match_records' not in enabled:
                    continue
                if part.startswith('**一步说清比赛**') and 'daily_events' not in enabled:
                    continue
                if part.startswith('**表单类业务**'):
                    part = '**表单类业务**会给出相应页面入口，需要在页面提交。' + ('例如 AI 我要注册 会给出本人表单。' if 'registration' in enabled else '')
                if part.startswith('发送 **AI状态**') and not can('system.manage'):
                    part = part.replace('；管理员可用 开启AI / 关闭AI', '')
                kept.append(part)
            return '\n\n'.join(kept)
        value = GUIDES[category]
        if category == '报名' and 'ai' not in enabled:
            value = value.replace('；AI 报名明天第一场 会先给出待确认提案', '')
        return syntax_help(enabled,is_admin,permissions,category) + '\n\n' + value

    status = (member or {}).get('status', 'unregistered')
    if status != 'approved' and not is_admin:
        titles = {'unregistered':'您尚未注册', 'pending':'注册申请待审批', 'rejected':'申请已退回，可修改后重提', 'suspended':'会员已停用，请联系管理员'}
        intro = '# 欢迎使用羽协助手\n\n**' + titles.get(status, '请先完成注册') + '**\n\n'
        if 'registration' in enabled:
            return intro + '发送 **注册** 打开本人链接，填写后直接提交；发送 **注册资料** 查看进度。姓名、性别必填，1–9级自评和手机号选填。\n\n已批量登记者填写登记手机号，提交后关联；补全资料及未匹配的申请等待会员管理员审核，审核完成后机器人会在关联群 @本人通知。\n\n帮助 注册 查看说明；身份 可查询账号标识。'
        return intro + '本机器人不办理会员注册，请联系管理员或使用负责注册的机器人。已获批会员可使用各机器人已开启的业务。\n\n身份 可查询账号标识。'
    return '\n\n'.join(['# 羽协助手 · 常用功能',syntax_help(enabled,is_admin,permissions),more])


def syntax_help(enabled, is_admin=False, permissions=(), category=None):
    """One usable syntax and literal example per enabled action family."""
    can=lambda key:'management' in enabled and (is_admin or key in permissions)
    rows=[]
    def add(cat,cap,title,fields,example,extra=''):
        if cap in enabled and (category is None or category==cat):
            rows.append(f'**{title}**\n字段：{fields}\n示例：{example}'+ ('\n'+extra if extra else ''))
    add('注册','registration','注册与资料','姓名、性别必填；手机号、1–9级自评选填','注册','打开本人表单填写；注册资料 / 我的会籍 查看状态；修改资料 申请调整；申请分组 提高组 / 申请等级 5级；我的资料申请 查看进度。')
    add('报名','enrollment','报名与预约','日期、场地、场次可选，同行姓名可追加','明天第2场 报名，张三，李四','格式：[日期] [场地N] [第N场或时间] 报名[，姓名…]；报名+2 追加2位同行；报名 接龙 返回完整名单。')
    add('报名','enrollment','取消与名单','可选日期、场地、场次；取消同行需填写本人代报的姓名','取消张三，李四','取消报名 仅取消本人；名单 / 明天第二场 名单 查询；明天 场地占用 查看安排。')
    add('比赛','daily_events','每日趣味赛','可选日期、场地、场次、单打/双打/男双/女双/混双','组织比赛 混双','收到人数与赛制后，回复 积分赛 等可用形式；继续比赛 找回未完赛事。')
    add('大型赛事','large_events','大型赛事报名','公告中的赛事关键词，可追加同行姓名','春季联赛，报名，张三','春季联赛报名 / 春季联赛，取消报名 / 春季联赛，取消张三。')
    if enabled & {'daily_events','large_events'} and category in (None,'比赛','大型赛事'):
        rows.append('**赛程与比分**\n字段：赛事名称、查询事项；记分再填第几场和比分。\n示例：周二双打，对战表\n查询：赛事名，比分 / 我的赛程 / 规则。负责人或有效记分员：周二双打，第十二场 21:19；冒号可用 ： 或 -。负责人可发 赛事名，暂停比赛 / 恢复比赛 / 关闭比赛。')
    add('记录','match_records','零散对局','项目、双方姓名、比分；本人必须参赛','记录 双打 我、张三 VS 李四、王五 21:19','格式：记录 [第N场] 项目 我方姓名 VS 对方姓名 比分。我的对战历史 / 我的对战统计 查询；修改记录 12 21:18 / 作废记录 12 处理本人记录。')
    add('统计','statistics','报名统计','无需额外字段','我的统计','统计 查看全部汇总。' if can('activity.manage') else '')
    if category in (None,'管理'):
        if can('member.manage'):rows.append('**会员审核**\n字段：申请编号或唯一姓名。\n示例：审核通过 申请12\n待审核 查看申请；审核拒绝 申请12 退回。')
        if can('activity.manage'):rows.append('**场地占用**\n字段：场地、日期、起止时间、占用事由。\n示例：场地1 明天 占用 17:30-20:30 校队训练\n释放占用12 解除；开启节假日 / 关闭节假日 / 节假日设置 调整排期。')
        if can('system.manage') and 'ai' in enabled:rows.append('**AI开关**\n字段：开启或关闭。\n示例：开启AI\n关闭AI 停用。')
        if can('message.manage') and 'messages' in enabled:rows.append('**消息发布**\n字段：内容、图片或文件、目标群。\n在后台“消息发布”编辑并发送。')
    if 'ai' in enabled and category in (None,'AI'):
        example=next((text for key,text in [('enrollment','AI 明天第一场帮我占个位，再看那场名单'),('daily_events','AI 组织今天的双打积分赛'),('large_events','AI 查看春季联赛的赛程'),('statistics','AI 查看我的报名统计'),('match_records','AI 查看我的对战历史'),('registration','AI 我要注册')] if key in enabled),'AI 查看帮助')
        rows.append(f'**自然语言**\n字段：AI + 业务需求；可一次描述多步。\n示例：{example}\n修改先预览再确认；回复 确认 执行，AI + 修改意见 替换方案；AI进度 / 取消计划 / AI状态。')
    return '\n\n'.join(rows) or '本机器人当前没有开放的业务指令。身份 可查看本人账号。'


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
    return scope+'\n\n'+value
