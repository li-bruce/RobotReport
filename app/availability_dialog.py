"""Small explicit group commands for venue operations; no free-form code execution."""
import re
from .availability import Availability
from .holidays import HolidaySchedule
from .bot_capabilities import require_capability


def handle_availability(ctx,text,selection,clock):
    venue=selection['venue_id']
    if text in ('场地占用','占用查询','场地安排'):
        require_capability(ctx,'enrollment')
        return Availability(ctx,clock).describe(venue,selection.get('day'))
    if text in ('开启节假日','关闭节假日'):
        require_capability(ctx,'management')
        result=HolidaySchedule(ctx,clock).save(venue,{'enabled':text.startswith('开启')})
        return f"场地{venue}节假日专用场次已"+('开启。' if result['enabled'] else '关闭。')
    if text=='节假日设置':
        require_capability(ctx,'management')
        ctx.require('activity.manage')
        from .holiday_planning import HolidayPlanning
        from .messages import escape
        year=int((selection.get('day') or clock().date().isoformat())[:4])
        config=HolidayPlanning(ctx,clock).get(venue,year)
        rows=[f"**{escape(p['name'])}** {p['start_date']}—{p['end_date']} · "+
              ('、'.join(f"{escape(s['name'])} {s['start']}–{s['end']}" for s in p['slots']) or '暂停日常活动' if p['enabled'] else '按周期规则')
              for p in config['periods']]
        return f"**场地{venue} · {year}年节假日排期**\n\n"+'\n\n'.join(rows)+'\n\n在后台活动运营 → 节假日安排中编辑。'
    matched=re.fullmatch(r'释放占用\s*(\d+)',text)
    if matched:
        require_capability(ctx,'management')
        service=Availability(ctx,clock)
        # The explicit venue prefix remains authoritative when a numeric record is used.
        from .db import connect
        with connect(ctx.db_path) as db:
            row=db.execute('SELECT venue_id FROM venue_reservations WHERE id=?',(int(matched[1]),)).fetchone()
        if not row or row['venue_id']!=venue:
            raise ValueError('此占用不属于指定场地，请先查询场地占用')
        service.release(int(matched[1]))
        return '占用已释放。发送 名单 查看当前场次状态。'
    matched=re.fullmatch(r'占用\s*(\d{1,2}[:：]\d{2})\s*[-—–~～至]\s*(\d{1,2}[:：]\d{2})\s+(.+)',text)
    if matched:
        require_capability(ctx,'management')
        def hhmm(value):
            h,m=value.replace('：',':').split(':')
            return f'{int(h):02}:{int(m):02}'
        row=Availability(ctx,clock).save({'venue_id':venue,'date':selection.get('day') or clock().date().isoformat(),
                                        'start':hhmm(matched[1]),'end':hhmm(matched[2]),'title':matched[3]})
        affected=sum(s['count'] for s in row['affected_sessions'])
        return f"**占用已登记 · 占用{row['id']}**\n\n场地{venue} {row['date']} {row['start']}–{row['end']}\n\n日常场次暂停新增报名，已有{affected}条报名保留。"
    if text.startswith(('占用','释放占用')):
        return '管理示例：场地1 明天 占用 17:30-20:30 校队训练；查询：场地1 明天 场地占用；释放：场地1 释放占用12。'
    return None
