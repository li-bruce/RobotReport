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
        from .dialogue import render
        return render(ctx,'availability.holidays',venue=venue,status='开启' if result['enabled'] else '关闭')
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
        from .dialogue import render
        return render(ctx,'availability.released')
    matched=re.fullmatch(r'占用\s*(\d{1,2}[:：]\d{2})\s*[-—–~～至]\s*(\d{1,2}[:：]\d{2})\s+(.+)',text)
    if matched:
        require_capability(ctx,'management')
        def hhmm(value):
            h,m=value.replace('：',':').split(':')
            return f'{int(h):02}:{int(m):02}'
        row=Availability(ctx,clock).save({'venue_id':venue,'date':selection.get('day') or clock().date().isoformat(),
                                        'start':hhmm(matched[1]),'end':hhmm(matched[2]),'title':matched[3]})
        affected=sum(s['count'] for s in row['affected_sessions'])
        from .dialogue import render
        return render(ctx,'availability.saved',number=row['id'],venue=venue,date=row['date'],start=row['start'],end=row['end'],count=affected)
    if text.startswith(('占用','释放占用')):
        from .dialogue import render, command
        return render(ctx,'availability.guide',reserve_example=command(ctx,'场地1 明天 占用 17:30-20:30 校队训练'),query_example=command(ctx,'场地1 明天 场地占用'),release_example=command(ctx,'场地1 释放占用12'))
    return None
