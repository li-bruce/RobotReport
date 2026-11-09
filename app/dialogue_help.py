"""Editable category explanations; command availability stays permission-filtered."""
COPY = {
    '注册': ('registration', '{content}\n\n发送 {register_command}，打开本人链接提交资料。姓名、性别必填；手机号和1–9级自评选填。\n\n导入名册唯一匹配后，确认即可注册；重名时补充登记手机号；未匹配则等待审核。审核结果会 @您通知。\n\n需要调整资料，请发送 {edit_command}；{profile_command} 可查看状态。'),
    '报名': ('enrollment', '{content}\n\n想带朋友一起？发送 {guests_command}，每次追加同行。{cancel_self_command} 只取消本人；取消自己代报的同行可发 {cancel_guests_command}。\n\n预约时带上日期、场次；多个场地时再加场地编号。'),
    '比赛': ('daily_events', '{content}\n\n先发送 {organize_command}，查看人数与可用赛制，再选择赛制。默认双打。\n\n每场活动只保留一项每日比赛，各机器人共享。发起人可记分、修改、暂停和关闭，参赛者可查看赛程。发送 {resume_command} 继续。\n\n需重新组赛，由本场赛事负责人或赛事管理员发送 {delete_example} 删除原比赛后再组织。'),
    '大型赛事': ('large_events', '{content}\n\n按群公告的赛事关键词报名，开放时间和资格以公告为准。想查看自己的安排，可发送 {schedule_example}。\n\n记分由负责人或指定记分员处理；赛程、集合和规则以赛事通知为准。负责人或协调员可发送 {coordination_example} 保存安排，不会自动群发。'),
    '记录': ('match_records', '{content}\n\n打完一局即可记录，当天提交生效。请带上双方姓名和比分，本人需要参赛。\n\n同名时按提示选择；比分有误可发送 {edit_example}，作废可发送 {void_example}。'),
    '统计': ('statistics', '{content}\n\n想回顾最近的活动？发送 {stats_command}，查看自己的报名与出勤汇总。'),
    'AI': ('ai', '{content}\n\n在 {ai_command} 后告诉我需求，可以包含多个步骤。纯查询直接返回；涉及修改时，一次最多8步，整单预览。表单操作需要在页面提交。发送 {progress_command} 可查看进度。\n\n回复 {confirm_command} 执行；用 {edit_command} 提出修改；{cancel_command} 可以放弃当前方案。'),
    '管理': ('management', '{content}\n\n请按您负责的事项处理申请或活动。群内操作与后台使用相同权限；角色与会籍可在人员中心维护。'),
}


def category_reply(ctx, category, content):
    from .dialogue import render, command, Markup
    key = COPY[category][0]
    commands = {'edit_command':'AI 修改意见' if category=='AI' else '修改资料',
                'guests_command':'报名，张三，李四','cancel_self_command':'取消报名',
                'cancel_guests_command':'取消张三，李四','schedule_example':'春季联赛，我的赛程',
                'edit_example':'修改记录 12 21:18','void_example':'作废记录 12',
                'stats_command':'我的统计','ai_command':'AI','progress_command':'AI进度','coordination_example':'赛事名，协调安排 18点到场准备',
                'delete_example':'周二双打，删除比赛'}
    return render(ctx,'help.'+key,content=Markup(content),**{k:command(ctx,v) for k,v in commands.items()})
