"""Small deterministic dialogue for casual matches; no model authority."""
from datetime import datetime, timedelta
import json
import re

from .casual import CasualMatches, render_match, render_history, render_statistics, table, _safe
from .db import connect, dumps, now, stamp


GUIDE = '''**🏸 记录今天的一次对局**

直接发送：

- `记录 单打 我 VS 张三 21:19`
- `记录 双打 我、张三 VS 李四、王五 21-19`
- `记录 混双 我、张三 VS 李四、王五 21：19`
- 指定场次：`记录 第2场 单打 我 VS 张三 21:19`

支持单打、双打、男双、女双、混双。男双、女双、混双需参赛人性别齐全；具名访客可写 `访客：小王（女）`。

记录者须参赛，提交立即生效。同名会员会给出选择；重复对局会请你确认。

`我的对战历史` · `我的对战统计`
`修改记录 12 21:18` · `作废记录 12`

查询可加日期范围：`我的对战历史 2026-10-01 至 2026-10-31`。'''

CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


def _number(text):
    return int(text) if text.isdigit() else CN.get(text, 0)


def _pending(service):
    with connect(service.path) as db:
        row = db.execute('''SELECT payload,expires_at FROM casual_dialogs
            WHERE scope_id=? AND bot_id=? AND chat_id=? AND user_id=?''', (service.ctx.scope_id, service.ctx.id, service.ctx.chat_id, service.ctx.actor)).fetchone()
    if row and datetime.fromisoformat(row['expires_at']) > service.clock():
        return json.loads(row['payload'])
    return None


def _save(service, payload):
    with connect(service.path, True) as db:
        db.execute('''INSERT INTO casual_dialogs VALUES(?,?,?,?,?,?) ON CONFLICT(scope_id,bot_id,chat_id,user_id)
            DO UPDATE SET payload=excluded.payload,expires_at=excluded.expires_at''',
            (service.ctx.scope_id, service.ctx.id, service.ctx.chat_id, service.ctx.actor, dumps(payload), stamp(service.clock() + timedelta(minutes=20))))


def _clear(service):
    with connect(service.path, True) as db:
        db.execute('DELETE FROM casual_dialogs WHERE scope_id=? AND bot_id=? AND chat_id=? AND user_id=?',
                   (service.ctx.scope_id, service.ctx.id, service.ctx.chat_id, service.ctx.actor))


def _resolve(service, text):
    text = text.strip()
    if text in ('我', '本人', '自己'):
        return {'user_id': service.ctx.actor}
    visitor = re.match(r'^(?:访客|客人)\s*[:：]?\s*(.+)$', text)
    value = visitor.group(1) if visitor else text
    gender = 'unknown'
    specified = re.search(r'[（(]\s*(男|女)\s*[）)]$', value)
    if specified:
        gender = 'male' if specified[1] == '男' else 'female'
        value = value[:specified.start()].strip()
    if not value or len(value) > 80:
        raise ValueError('请填写参赛人姓名')
    if visitor:
        return {'kind': 'guest', 'name': value, 'gender': gender}
    candidates = [p for p in service.participants(value) if p['name'] == value]
    if len(candidates) == 1:
        return {'user_id': candidates[0]['user_id']}
    if len(candidates) > 1:
        return {'ambiguous_name': value, 'candidates': candidates}
    return {'kind': 'guest', 'name': value, 'gender': gender}


def _choose(service, state):
    for side in ('team_a', 'team_b'):
        for index, person in enumerate(state['data'][side]):
            if 'candidates' in person:
                state['choice'] = {'side': side, 'index': index}
                _save(service, state)
                rows = [[n, p['name'], {'male': '男', 'female': '女'}.get(p.get('gender'), '未填写'), f"{p['level']}级" if p.get('level') else '无级别', '、'.join(p.get('groups') or []) or '未分组'] for n, p in enumerate(person['candidates'], 1)]
                return f"**请选择“{_safe(person['ambiguous_name'])}”对应的会员**\n\n" + table(['序号', '姓名', '性别', '等级', '组别'], rows) + '\n\n回复 `选择1`（替换为序号）。无法确定时请联系本人或管理员核对；20分钟内有效。'
    result = service.create(state['data'])
    if result.get('duplicate'):
        state['duplicate_id'] = result['id']
        state.pop('choice', None)
        _save(service, state)
        return render_match(result) + '\n\n⚠️ 已有相同日期、对手与比分的记录，本次未重复记入。若确实又打了一局，请回复 `重复记录`；否则无需操作。'
    _clear(service)
    return render_match(result) + f"\n\n✅ 已记录并计入统计。修改：`修改记录 {result['id']} 21:18`；作废：`作废记录 {result['id']}`。"


def _parse_record(service, text, venue_id):
    body = re.sub(r'^记录\s*', '', text, count=1)
    data = {'venue_id': venue_id, 'date': service._today()}
    session_match = re.match(r'^第?\s*(\d+|[一二三四五六七八九十])\s*场\s*', body)
    if session_match:
        number = _number(session_match[1])
        rows = service.service.sessions(data['date'], venue_id)
        session = next((r for r in rows if r['number'] == number), None)
        if not session:
            raise ValueError('此场次不在当前机器人的可用范围内，请检查场次编号')
        data['session_id'] = session['id']
        body = body[session_match.end():]
    kind = re.match(r'^(单打|双打|男双|女双|混双)\s*', body)
    label = kind[1] if kind else '双打'
    if kind:
        body = body[kind.end():]
    score = re.search(r'(\d{1,3})\s*[:：\-－—–]\s*(\d{1,3})\s*$', body)
    if not score:
        raise ValueError('请在最后填写比分，例如：记录 单打 我 VS 张三 21:19')
    data.update(discipline='singles' if label == '单打' else 'doubles', doubles_category={'男双': 'mens', '女双': 'womens', '混双': 'mixed'}.get(label, 'open'), score_a=int(score[1]), score_b=int(score[2]))
    teams = re.split(r'\s*(?:VS|ＶＳ|对阵|对战)\s*', body[:score.start()].strip(), flags=re.I)
    if len(teams) != 2:
        raise ValueError('请用 VS 分隔两队，例如：我、张三 VS 李四、王五')
    for side, raw in zip(('team_a', 'team_b'), teams):
        names = [name.strip() for name in re.split(r'[、,，/／+＋]|\s+', raw.strip()) if name.strip()]
        required = 1 if data['discipline'] == 'singles' else 2
        if len(names) != required:
            raise ValueError(f'{label}每队须有{required}人，请用顿号或逗号分隔姓名')
        data[side] = [_resolve(service, name) for name in names]
    return {'data': data}


def handle_casual(ctx, text, venue_id=None, clock=now, *, explicit_venue=False):
    """Return a reply, or None when another command family owns the text."""
    text = text.strip()
    history = re.fullmatch(r'(?:我的)?(?:对战历史|对局历史|对战记录)(?:\s+(.+))?', text)
    stats = re.fullmatch(r'(?:我的)?(?:对战统计|对局统计)(?:\s+(.+))?', text)
    change = re.fullmatch(r'修改记录\s*[#＃]?\s*(\d+)\s*[,，:：]?\s*(\d{1,3})\s*[:：\-－—–]\s*(\d{1,3})', text)
    remove = re.fullmatch(r'(?:作废|撤销)记录\s*[#＃]?\s*(\d+)', text)
    choose = re.fullmatch(r'(?:选择|选)\s*(\d+)', text)
    relevant = history or stats or change or remove or choose or text == '重复记录' or text == '取消记录' or text.startswith('记录')
    if not relevant:
        return None
    service = CasualMatches(ctx, clock)
    service._approved()
    venue_id = venue_id if venue_id is not None else ctx.default_venue_id
    if history or stats:
        query = (history or stats)[1]
        date_from = date_to = None
        if query:
            dates = re.fullmatch(r'(\d{4}-\d{2}-\d{2})(?:\s*(?:至|到|~|～)\s*(\d{4}-\d{2}-\d{2}))?', query)
            if not dates:
                raise ValueError('日期示例：我的对战历史 2026-10-01 至 2026-10-31')
            date_from, date_to = dates[1], dates[2] or dates[1]
        kwargs = dict(user_id=ctx.actor, date_from=date_from, date_to=date_to)
        # The caller's scope is always enforced, but no explicit venue prefix
        # means personal history may span all venues served by this bot.
        if explicit_venue:
            kwargs['venue_id'] = venue_id
        return render_history(service.history(**kwargs)) if history else render_statistics(service.statistics(**kwargs))
    if change:
        row = service.update(int(change[1]), {'score_a': int(change[2]), 'score_b': int(change[3])})
        return render_match(row) + '\n\n✅ 比分已修正，统计已同步更新。'
    if remove:
        row = service.void(int(remove[1]))
        return f"✅ 对局 #{row['id']} 已作废，不再计入统计；修改痕迹已保留。"
    if text == '取消记录':
        _clear(service)
        return '已取消当前记录草稿，已提交的对局不会受影响。'
    if choose or text == '重复记录':
        state = _pending(service)
        if not state:
            return None if choose else '当前没有待确认的重复对局，请重新发送记录内容。'
        if choose:
            choice = state.get('choice')
            if not choice:
                return None
            person = state['data'][choice['side']][choice['index']]
            index = int(choose[1]) - 1
            if not 0 <= index < len(person['candidates']):
                raise ValueError('请从当前候选序号中选择')
            state['data'][choice['side']][choice['index']] = {'user_id': person['candidates'][index]['user_id']}
            state.pop('choice', None)
            return _choose(service, state)
        if not state.get('duplicate_id'):
            return '请先完成同名会员选择。'
        state['data']['allow_duplicate'] = True
        return _choose(service, state)
    if text in ('记录', '记录帮助'):
        return GUIDE
    return _choose(service, _parse_record(service, text, venue_id))
