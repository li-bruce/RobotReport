"""Reviewed typed business operations shared by plan preview and execution."""
from .members import Members
from .messages import escape, roster, table
from .planning import DailyPlanning
from .ai import FORMAT_NAMES, STRATEGY_NAMES


def selection(args):
    result = {'day': args.get('date'), 'venue_id': args.get('venue_id', 1),
            'at': f"{args['hour']:02}:{args.get('minute', 0):02}" if args.get('hour') is not None else None,
            'number': args.get('number')}
    if args.get('_resolved_session'):
        target = args['_resolved_session']
        result.update(day=target['date'],number=target['number'],at=None)
    return result


def bind_target(commands, step):
    """Freeze a backend-resolved session selector, never a model-supplied row ID."""
    op,a = step['operation'],step['arguments']
    if op=='ReviewMember':
        from .member_review import MemberReview
        target=MemberReview(commands.ctx,commands.clock).resolve(a.get('member_name'),a.get('application_id'),decision=a['decision'])
        a['_resolved_application']={key:target[key] for key in ('id','name','user_id','submitted_at')}
        return
    if op not in ('Register','CancelRegistration','StartDailyPlan','UpdateSession','Roster'):
        return
    if op=='Roster' and not (a.get('number') or a.get('hour') is not None): return
    session = commands.service.resolve_session(**selection(a),for_write=op!='Roster')
    a['_resolved_session'] = {key:session[key] for key in ('date','number','name','start','end','slot_key')}


def describe_step(step):
    op, a = step['operation'], step['arguments']
    target = ' '.join(str(x) for x in [a.get('date'), f"场地{a['venue_id']}" if a.get('venue_id') else None,
                  f"第{a['number']}场" if a.get('number') else None,
                  f"{a['hour']:02}:{a.get('minute', 0):02}" if a.get('hour') is not None else None] if x)
    if a.get('_resolved_session'):
        selected = a['_resolved_session']
        target = f"{selected['date']} 场地{a.get('venue_id',1)} 第{selected['number']}场 {selected['name']} {selected['start']}–{selected['end']}"
    labels = {'Register':'本人报名', 'CancelRegistration':'取消指定代报' if a.get('guest_names') else '取消本人报名', 'Roster':'查询名单',
              'PersonalStats':'查询我的统计', 'AllStats':'查询全部报名统计', 'StartDailyPlan':'组织每日比赛',
              'DailyPlan':'生成对战表', 'ResumeTournament':'继续比赛', 'RecordScore':'记录比分',
              'TournamentQuery':'查询赛事', 'ToggleAI':'修改AI开关', 'SelfLevel':'提交自评级别',
              'EventAction':'调整赛事状态', 'EventRegistration':'赛事报名', 'FeatureRoute':'查看功能入口',
              'Help':'查看帮助', 'UpdateReminders':'调整每日提醒', 'UpdateSession':'调整活动设置',
              'AddMembershipPeriod':'登记会籍有效期', 'AddActivitySlot':'新增每周活动时段',
              'PendingMembers':'查询待审核注册','ReviewMember':'审核会员注册',
              'QueryOccupancy':'查询场地占用','ReserveVenue':'登记场地占用',
              'ReleaseVenue':'释放场地占用','ToggleHoliday':'设置节假日场次'}
    details = []
    if op=='ReserveVenue': details=[a['title'],a['start']+'–'+a['end'],'重叠日常场次暂停新报名，已有报名保留']
    elif op=='ReleaseVenue': details=['占用'+str(a['reservation_id'])]
    elif op=='ToggleHoliday': details=['开启' if a['enabled'] else '关闭']
    if op == 'Register':
        details = [f"本次新增{a.get('guests', 0)}位同行，保留已有代报"]
        if a.get('guest_names'): details.append('、'.join(a['guest_names']))
        if a.get('chain'): details.append('报名后展示名单')
    elif op == 'CancelRegistration':
        details = ['只取消代报：'+'、'.join(a['guest_names'])+'；保留本人及其他同行' if a.get('guest_names') else '只取消本人，保留所有同行']
    elif op == 'StartDailyPlan':
        details = [a.get('title') or '每日比赛', '单打' if a['discipline']=='singles' else {'open':'双打','mens':'男双','womens':'女双','mixed':'混双'}[a['doubles_category']], STRATEGY_NAMES[a['assignment_strategy']]]
    elif op == 'DailyPlan': details = [FORMAT_NAMES[a['format']]]
    elif op == 'UpdateReminders':
        details = [f"{label}：{a[k]}" for k,label in [('open','报名提醒'),('closing','即将结束提醒'),('roster','名单通知')] if k in a]
        if 'daily_enabled' in a: details.append('每日提醒' + ('开启' if a['daily_enabled'] else '关闭'))
    elif op == 'UpdateSession':
        details = [f"{label}：{a[k] if a[k] is not None else '不限'}" for k,label in [('capacity','正式名额'),('substitute_capacity','替补名额'),('min_level','最低级别'),('max_level','最高级别')] if k in a]
        if 'allowed_group_names' in a: details.append('组别：' + ('、'.join(a['allowed_group_names']) or '不限'))
    elif op == 'AddMembershipPeriod':
        details = [a['member_name'], a['start_date'] + ' 至 ' + a['end_date']]
        if a.get('amount') is not None: details.append('登记金额：' + a['amount'] + '元')
        if a.get('note'): details.append('备注：' + a['note'])
    elif op == 'AddActivitySlot':
        details = [a['name'], '周' + '、'.join('一二三四五六日'[i] for i in a['weekdays']), a['start'] + '–' + a['end'], f"正式{a['capacity']} / 替补{a['substitute_capacity']}"]
    elif op == 'SelfLevel': details = [str(a['level']) + '级，待审核']
    elif op == 'ToggleAI': details = ['开启' if a['enabled'] else '关闭']
    elif op == 'ReviewMember':
        reviewed=a.get('_resolved_application') or {}
        details=[('通过' if a['decision']=='approved' else '拒绝')+'注册审核',reviewed.get('name') or a.get('member_name') or '',
                 '申请'+str(reviewed.get('id') or a.get('application_id') or ''),'不变更管理角色或核准等级']
    elif op == 'FeatureRoute':
        from .ai_catalog import get_feature
        feature = get_feature(a['feature_id'])
        details = [feature['name'] if feature else a['feature_id']]
    elif op in ('ResumeTournament','RecordScore','TournamentQuery','EventAction','EventRegistration'):
        details = [a.get('tournament_name') or '匹配未结束赛事']
        if op == 'RecordScore': details += [f"第{a['match_id']}场", ' / '.join(f'{x}:{y}' for x,y in a['scores'])]
        if op == 'TournamentQuery': details.append({'schedule':'对战表','results':'积分表','mine':'我的赛程','rules':'规则'}[a['view']])
        if op == 'EventAction': details.append({'pause':'暂停','close':'关闭','advance':'晋级'}[a['action']])
        if op == 'EventRegistration':
            if a['action']=='cancel':
                details.append('只取消代报：'+'、'.join(a['guest_names'])+'；保留本人及其他同行' if a.get('guest_names') else '只取消本人，保留所有同行')
            else:
                details.append(f"本人报名，本次新增{a['guests']}位同行，保留已有代报")
                if a.get('guest_names'): details.append('、'.join(a['guest_names']))
    return labels[op], '；'.join([target, *details]).strip('；') or '当前业务范围'


async def execute_step(commands, step):
    """No model text is evaluated as Python, SQL, an URL, or a backend method."""
    commands.refresh_context(enforce_access=True)
    ctx, service = commands.ctx, commands.service
    from .bot_policy import require_operation, require_event
    require_operation(ctx, step['operation'])
    member = Members(ctx, commands.clock).require_approved() or {}
    op, a = step['operation'], step['arguments']
    from .ai_permissions import OPERATION_PERMISSIONS
    if op in OPERATION_PERMISSIONS: ctx.require(OPERATION_PERMISSIONS[op])
    if op in ('PendingMembers','ReviewMember'):
        from .member_review import MemberReview
        reviews=MemberReview(ctx,commands.clock)
        if op=='PendingMembers': return reviews.describe_pending()
        target=a.get('_resolved_application')
        if target:
            current=reviews.resolve(application_id=target['id'],decision=a['decision'])
            if any(current[k]!=v for k,v in target.items()): raise ValueError('注册申请资料已变化，请重新核对后审核')
        return reviews.review(a['decision'],member_name=None if target else a.get('member_name'),application_id=target['id'] if target else a.get('application_id'))
    venue = a.get('venue_id', ctx.default_venue_id)
    service.venue(venue, True)
    if op in ('QueryOccupancy','ReserveVenue','ReleaseVenue'):
        from .availability import Availability
        manager=Availability(ctx,commands.clock)
        if op=='QueryOccupancy': return manager.describe(venue,a.get('date'))
        if op=='ReserveVenue':
            result=manager.save({k:a[k] for k in ('venue_id','date','start','end','title')})
            return f"占用{result['id']}已登记。"+manager.describe(venue,a['date'])
        from .db import connect
        with connect(ctx.db_path) as db:
            row=db.execute('SELECT venue_id FROM venue_reservations WHERE id=?',(a['reservation_id'],)).fetchone()
        if not row or row['venue_id']!=venue: raise ValueError('此占用不属于指定场地')
        manager.release(a['reservation_id'])
        return '占用已释放。'
    if op=='ToggleHoliday':
        from .holidays import HolidaySchedule
        HolidaySchedule(ctx,commands.clock).save(venue,{'enabled':a['enabled']})
        return '节假日专用场次已'+('开启。' if a['enabled'] else '关闭。')
    if a.get('_resolved_session'):
        current = service.resolve_session(**selection(a),for_write=op!='Roster')
        if any(current.get(k)!=v for k,v in a['_resolved_session'].items()):
            raise ValueError('所选场次或时段已变化，请重新提出操作')
    name = member.get('name') or '会员'
    if op == 'Register':
        data = service.enroll(ctx.actor, name, a['guests'], **selection(a), guest_names=a.get('guest_names') or None)
        return ('报名已更新。\n\n' + roster(data, venue))
    if op == 'CancelRegistration':
        names = a.get('guest_names') or []
        data = service.cancel_guests(ctx.actor, names, **selection(a)) if names else service.cancel(ctx.actor, **selection(a))
        target = '指定代报' if names else '本人报名'
        return f"已取消{target}{data['cancelled']}人，其他报名保持不变。"
    if op == 'Roster':
        sid = service.resolve_session(**selection(a), for_write=False)['id'] if a.get('number') or a.get('hour') is not None else None
        return roster(service.registrations(a.get('date'), venue, sid), venue)
    if op in ('PersonalStats','AllStats'):
        from .message_parser import MessageInput
        return await commands.run(MessageInput('场地' + str(venue) + (' 我的统计' if op=='PersonalStats' else ' 统计')), allow_ai=False)
    if op == 'StartDailyPlan':
        return DailyPlanning(ctx,commands.clock).begin(selection(a), a['discipline'], a['assignment_strategy'], a.get('title'), doubles_category=a['doubles_category'])
    if op == 'DailyPlan':
        planner = DailyPlanning(ctx, commands.clock)
        record = planner.get(venue)
        if not record or record['status'] != 'ready': raise ValueError('请先组织比赛，再选择比赛形式')
        _, _, formats = planner.info(record)
        available = next((f for f in formats if f['id']==a['format']), None)
        if not available or not available['available']: raise ValueError('当前报名人数或比赛模式不支持该赛制')
        result = planner.create(venue, a['format'])
        created = planner.get(venue)
        if not created or created['token'] != record['token'] or created['status'] != 'created' or not created.get('event_id'):
            raise ValueError('赛事尚未成功创建，请查询当前策划状态后重试')
        commands.tournaments().get(created['event_id'],venue)
        return result
    if op in ('ResumeTournament','RecordScore','TournamentQuery','EventAction','EventRegistration'):
        events = commands.tournaments()
        if a.get('tournament_name'): event = commands.event_by_name(a['tournament_name'], venue)
        elif a.get('tournament_id'): event = events.get(a['tournament_id'], venue)
        elif op == 'ResumeTournament':
            candidates = [event for event in events.resume_candidates(venue) if commands.event_allowed(event)]
            if len(candidates)!=1: raise ValueError('请明确要继续的赛事名称')
            event = events.get(candidates[0]['id'], venue)
        else: raise ValueError('请明确赛事名称')
        require_event(ctx, event)
        if a.get('tournament_id') and event['id'] != a['tournament_id']: raise ValueError('赛事目标已变化，请重新提出操作')
        if op == 'TournamentQuery': return events.describe(event['id'], a['view'])
        if op == 'RecordScore':
            match = next((m for m in event['matches'] if m['number']==a['match_id']),None)
            if not match: raise ValueError('该赛事没有此对战场次')
            events.score(event['id'], a['match_id'], a['scores'], match['version'], venue, by_number=True)
        elif op == 'EventRegistration':
            if a['action']=='cancel':
                if a.get('guest_names'): events.cancel_guests(event['id'],ctx.actor,a['guest_names'],venue)
                else: events.cancel(event['id'],ctx.actor,venue)
            else: events.enroll(event['id'],ctx.actor,name,a['guests'],venue,guest_names=a.get('guest_names') or None)
            return events.describe(event['id'],'registration')
        elif op == 'ResumeTournament':
            if event['status']=='paused': events.transition(event['id'],'resume',venue)
            elif event['status']!='running': raise ValueError('赛事当前不能继续')
        else: events.transition(event['id'],a['action'],venue)
        return events.describe(event['id'])
    if op == 'ToggleAI':
        service.update_config({'ai':{'enabled':a['enabled']}})
        return 'AI业务助手已' + ('开启' if a['enabled'] else '关闭')
    if op == 'SelfLevel':
        Members(ctx,commands.clock).request_level(a['level'])
        return f"已提交自评{a['level']}级，等待管理员审核。"
    if op == 'UpdateReminders':
        patch = {'reminders':{k:a[k] for k in ('open','closing','roster') if k in a}}
        if 'daily_enabled' in a: patch['daily_enabled'] = a['daily_enabled']
        service.update_config(patch)
        return '每日提醒设置已保存：' + describe_step(step)[1]
    if op == 'UpdateSession':
        session = service.resolve_session(**selection(a))
        rules = {k:a[k] for k in ('capacity','substitute_capacity','min_level','max_level') if k in a}
        if 'allowed_group_names' in a:
            groups = Members(ctx,commands.clock).groups()
            ids = []
            for value in a['allowed_group_names']:
                matches = [g for g in groups if g['name']==value]
                if len(matches)!=1: raise ValueError('组别名称不存在或不唯一，请在后台核对')
                ids.append(matches[0]['id'])
            rules['allowed_group_ids'] = ids
        service.save_session_constraints(session['id'],rules)
        return '活动设置已保存：' + describe_step(step)[1]
    if op == 'AddMembershipPeriod':
        from .membership_terms import MembershipTerms
        found = [m for m in Members(ctx,commands.clock).list() if m['name']==a['member_name']]
        if len(found)!=1: raise ValueError('会员姓名不存在或不唯一，请在后台核对后登记')
        MembershipTerms(ctx,commands.clock).create(found[0]['user_id'],{k:a[k] for k in ('start_date','end_date','amount','note') if k in a})
        return f"已登记 {escape(a['member_name'])} 的会籍：{a['start_date']} 至 {a['end_date']}（含首尾日）。"
    if op == 'AddActivitySlot':
        slots = service.slots(venue)
        fields = ('weekdays','start','end','name','capacity','substitute_capacity','slot_key')
        saved = [{k:s[k] for k in fields if k in s} for s in slots]
        service.save_slots(venue, saved + [{k:a[k] for k in fields if k in a}])
        return '每周活动时段已新增：' + describe_step(step)[1]
    if op == 'FeatureRoute': return await commands.route_feature(a['feature_id'])
    if op == 'Help':
        from .message_parser import MessageInput
        return await commands.run(MessageInput('帮助'),allow_ai=False)
    raise ValueError('此业务操作尚不可执行，请使用对应管理页面')
