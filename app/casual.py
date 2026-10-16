"""Participant-owned one-off match records and unified personal match history.

Every write is authorized from BotContext. Names, genders, venue and session
labels are snapshots; changing a member or a schedule never rewrites history.
"""
import hashlib
import json
import uuid

from .db import connect, dumps, stamp, now, TZ, audit
from .members import Members, display_name
from .security import BotContext, identifier
from .service import Service, day_value, integer


SCHEMA = '''
CREATE TABLE IF NOT EXISTS casual_matches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scope_id TEXT NOT NULL,
 bot_id TEXT NOT NULL, chat_id TEXT NOT NULL, date TEXT NOT NULL,
 venue_id INTEGER NOT NULL, venue_name TEXT NOT NULL, session_json TEXT,
 discipline TEXT NOT NULL, doubles_category TEXT NOT NULL,
 team_a TEXT NOT NULL, team_b TEXT NOT NULL, score_a INTEGER NOT NULL,
 score_b INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'completed',
 created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS casual_scope_day ON casual_matches(scope_id,date,venue_id);
CREATE INDEX IF NOT EXISTS casual_duplicate ON casual_matches(scope_id,fingerprint,status);
CREATE TABLE IF NOT EXISTS casual_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, match_id INTEGER NOT NULL REFERENCES casual_matches(id),
 actor TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT, after_json TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS casual_dialogs (
 scope_id TEXT NOT NULL, bot_id TEXT NOT NULL, chat_id TEXT NOT NULL,
 user_id TEXT NOT NULL, payload TEXT NOT NULL, expires_at TEXT NOT NULL,
 PRIMARY KEY(scope_id,bot_id,chat_id,user_id)
);
'''


def initialize(path):
    with connect(path) as db:
        db.executescript(SCHEMA)


def person_key(person):
    return 'member:' + person['user_id'] if person.get('kind') == 'member' else 'guest:' + person['guest_id']


def _safe(value):
    return str(value).replace('|', '｜').replace('<', '＜').replace('>', '＞').replace('[', '［').replace(']', '］').replace('*', '＊').replace('`', '｀').replace('\n', ' ').replace('\r', ' ')


def table(headers, rows):
    return '\n'.join('| ' + ' | '.join(_safe(x) for x in row) + ' |' for row in [headers, ['---'] * len(headers), *rows])


def team_name(team):
    return '、'.join(person['name'] + ('（访客）' if person.get('kind') == 'guest' else '') for person in team)


def category_label(record):
    if record['discipline'] == 'singles':
        return '单打'
    return {'open': '双打', 'mens': '男双', 'womens': '女双', 'mixed': '混双'}[record['doubles_category']]


class CasualMatches:
    def __init__(self, context: BotContext, clock=now):
        if not isinstance(context, BotContext):
            raise PermissionError('缺少可信机器人上下文')
        self.ctx, self.clock, self.path = context, clock, context.db_path
        self.service = Service(context, clock)
        initialize(self.path)

    def _approved(self, db=None):
        return Members(self.ctx, self.clock).require_approved(db=db)

    def _today(self):
        return self.clock().astimezone(TZ).date().isoformat()

    def _subject(self, user_id=None, *, all_default=False):
        if self.ctx.can('event.manage') and (user_id == '*' or (user_id is None and all_default)):
            return None
        user_id = identifier(user_id or self.ctx.actor)
        if not self.ctx.can('event.manage') and user_id != self.ctx.actor:
            raise PermissionError('只能查询本人的对战历史与统计')
        return user_id

    def participants(self, query):
        """Names resolve only to approved profiles; ambiguous names stay ambiguous."""
        self._approved()
        query = str(query or '').strip()
        if len(query) > 80:
            raise ValueError('姓名过长')
        with connect(self.path) as db:
            people = []
            rows = db.execute('''SELECT m.*,p.name FROM member_records m JOIN profiles p
                ON p.chat_id=m.chat_id AND p.user_id=m.user_id
                WHERE m.chat_id=? AND m.status='approved' AND p.name<>'' ORDER BY p.name,m.user_id''', (self.ctx.scope_id,))
            for row in rows:
                row = dict(row)
                if query and query not in row['name']:
                    continue
                groups = [r[0] for r in db.execute('''SELECT g.name FROM member_groups g
                    JOIN member_group_links l ON l.group_id=g.id WHERE l.chat_id=? AND l.user_id=?''', (self.ctx.scope_id, row['user_id']))]
                people.append({'kind': 'member', 'user_id': row['user_id'], 'name': row['name'],
                               'gender': row.get('gender', 'unknown'), 'level': row['level'], 'groups': groups})
        return people

    def _person(self, db, value, old_guests=None):
        if not isinstance(value, dict) or set(value) - {'kind', 'user_id', 'guest_id', 'name', 'gender', 'level', 'groups'}:
            raise ValueError('参赛人信息无效')
        user_id = value.get('user_id')
        if user_id:
            if value.get('kind') == 'guest' or value.get('guest_id'):
                raise ValueError('会员和访客身份不能混用')
            user_id = identifier(user_id)
            member = Members(self.ctx, self.clock)._record(db, user_id)
            if not member or member['status'] != 'approved' or not member['name']:
                raise ValueError('参赛会员不存在或尚未通过审核；访客请使用姓名单独记录')
            return {'kind': 'member', 'user_id': user_id, 'name': member['name'], 'gender': member.get('gender', 'unknown')}
        name = display_name(value.get('name'), '')
        gender = value.get('gender', 'unknown')
        if gender not in ('male', 'female', 'unknown'):
            raise ValueError('访客性别仅支持男、女或未填写')
        guest_id = value.get('guest_id')
        if guest_id:
            old = (old_guests or {}).get(str(guest_id))
            if not old:
                # A guest ID may only be reused from a record already visible to
                # this actor; arbitrary input cannot claim another guest.
                raise ValueError('访客身份不能直接指定，请从已有记录选择或输入姓名')
            return dict(old) | {'name': name, 'gender': gender}
        return {'kind': 'guest', 'guest_id': uuid.uuid4().hex, 'name': name, 'gender': gender}

    def _teams(self, db, data, old=None, preserve_sides=()):
        discipline = data.get('discipline', 'doubles')
        category = data.get('doubles_category', 'open')
        if discipline not in ('singles', 'doubles') or category not in ('open', 'mens', 'womens', 'mixed'):
            raise ValueError('请选择单打、双打、男双、女双或混双')
        if discipline == 'singles' and category != 'open':
            raise ValueError('单打不能设置双打类别')
        size = 1 if discipline == 'singles' else 2
        old_guests = {p['guest_id']: p for side in ('team_a', 'team_b') for p in (old or {}).get(side, []) if p['kind'] == 'guest'}
        teams = []
        for side in ('team_a', 'team_b'):
            values = data.get(side)
            if not isinstance(values, list) or len(values) != size:
                raise ValueError(f'{"单打" if size == 1 else "双打"}每队须有{size}人')
            team = [dict(value) for value in values] if side in preserve_sides else [self._person(db, value, old_guests) for value in values]
            if category != 'open':
                genders = sorted(p['gender'] for p in team)
                expected = {'mens': ['male', 'male'], 'womens': ['female', 'female'], 'mixed': ['female', 'male']}[category]
                if genders != expected:
                    raise ValueError('男双、女双或混双队伍性别不符合要求；未填写性别的会员请先补齐资料')
            teams.append(team)
        keys = [person_key(p) for team in teams for p in team]
        if len(set(keys)) != len(keys):
            raise ValueError('同一位参赛者不能在一次对局中重复出现')
        # Separate anonymous guests must not disguise the recorder by name.
        if not self.ctx.can('event.manage') or old is None:
            if 'member:' + self.ctx.actor not in keys:
                raise PermissionError('记录者必须是本次对局的参赛会员，请用“我”表示自己')
        return discipline, category, teams

    def _location(self, data):
        venue_id = integer(data.get('venue_id', self.ctx.default_venue_id), 1, 1000000000, '场地编号')
        venue = self.service.venue(venue_id, active=True)
        day = data.get('date', self._today())
        day_value(day)
        if not self.ctx.can('event.manage') and day != self._today():
            raise ValueError('个人零散对局请在当天记录；历史补录请联系管理员')
        if day > self._today():
            raise ValueError('尚未发生的对局不能记录')
        rows = self.service.sessions(day, venue_id)
        session_id = data.get('session_id')
        session = None
        if session_id is not None:
            integer(session_id, 1, 1000000000, '场次编号')
            session = next((r for r in rows if r['id'] == session_id), None)
            if session is None:
                raise ValueError('场次不属于指定日期、场地或机器人可用时段')
        else:
            at = self.clock().astimezone(TZ).strftime('%H:%M')
            current = [r for r in rows if day == self._today() and r['start'] <= at < r['end']]
            if len(current) == 1:
                session = current[0]
        scoped = self.service.scope_context() if hasattr(self.service, 'scope_context') else self.ctx
        restricted = getattr(scoped, 'slot_keys', None) is not None
        if restricted and session is None:
            raise ValueError('本机器人仅负责指定时段，请选择该时段场次再记录')
        if session is not None and hasattr(self.service, 'assert_session_scope'):
            self.service.assert_session_scope(session)
        snapshot = None if session is None else {k: v for k, v in session.items() if k in ('id', 'name', 'start', 'end', 'date', 'venue_id', 'number', 'slot_key', 'rule_key', 'slot_id')}
        return day, venue_id, venue['name'], snapshot

    @staticmethod
    def _scores(data):
        a = integer(data.get('score_a'), 0, 999, '甲队比分')
        b = integer(data.get('score_b'), 0, 999, '乙队比分')
        if a == b:
            raise ValueError('已完成的对局不能记录平分')
        return a, b

    @staticmethod
    def _fingerprint(data):
        # Guest names intentionally enter only duplicate detection, never member
        # identity or statistics grouping. A repeat game can be confirmed.
        def side(team, score):
            return [sorted(person_key(p) if p['kind'] == 'member' else 'guest-name:' + p['name'] for p in team), score]
        teams = sorted([side(data['team_a'], data['score_a']), side(data['team_b'], data['score_b'])], key=dumps)
        raw = [data['date'], data['venue_id'], (data.get('session') or {}).get('id'), data['discipline'], data['doubles_category'], teams]
        return hashlib.sha256(dumps(raw).encode()).hexdigest()

    def _row(self, row):
        row = dict(row)
        row['team_a'], row['team_b'] = json.loads(row['team_a']), json.loads(row['team_b'])
        session = row.pop('session_json')
        row['session'] = json.loads(session) if session else None
        row['source'], row['source_id'] = 'casual', row['id']
        row['scores'] = [[row['score_a'], row['score_b']]]
        row['winner'] = 'a' if row['score_a'] > row['score_b'] else 'b'
        row['can_edit'] = row['status'] == 'completed' and (self.ctx.can('event.manage') or row['created_by'] == self.ctx.actor)
        row['can_void'] = row['can_edit']
        row.pop('fingerprint', None)
        return row

    def _scope(self, row):
        self.service.venue(row['venue_id'])
        if row.get('session') is not None and hasattr(self.service, 'assert_session_scope'):
            self.service.assert_session_scope(row['session'])
        elif getattr(self.service.ctx, 'slot_keys', None) is not None:
            raise PermissionError('本对局不在机器人的授权时段内')

    def _get(self, db, match_id):
        integer(match_id, 1, 1000000000, '记录编号')
        row = db.execute('SELECT * FROM casual_matches WHERE scope_id=? AND id=?', (self.ctx.scope_id, match_id)).fetchone()
        if row is None:
            raise ValueError('零散对局记录不存在')
        row = self._row(row)
        self._scope(row)
        if not self.ctx.can('event.manage') and not any(p.get('user_id') == self.ctx.actor for side in ('team_a', 'team_b') for p in row[side]):
            raise PermissionError('只能查看本人参与的对局')
        return row

    def get(self, match_id):
        self._approved()
        with connect(self.path) as db:
            return self._get(db, match_id)

    def create(self, data):
        self._approved()
        allowed = {'discipline', 'doubles_category', 'team_a', 'team_b', 'score_a', 'score_b', 'date', 'venue_id', 'session_id', 'allow_duplicate'}
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError('零散对局字段不支持')
        if type(data.get('allow_duplicate', False)) is not bool:
            raise ValueError('重复对局确认须为开关')
        day, venue_id, venue_name, session = self._location(data)
        a, b = self._scores(data)
        with connect(self.path, True) as db:
            self._approved(db)
            discipline, category, teams = self._teams(db, data)
            current = {'date': day, 'venue_id': venue_id, 'venue_name': venue_name, 'session': session,
                       'discipline': discipline, 'doubles_category': category, 'team_a': teams[0], 'team_b': teams[1], 'score_a': a, 'score_b': b}
            fingerprint = self._fingerprint(current)
            duplicate = db.execute("SELECT * FROM casual_matches WHERE scope_id=? AND fingerprint=? AND status='completed' ORDER BY id LIMIT 1", (self.ctx.scope_id, fingerprint)).fetchone()
            if duplicate and not data.get('allow_duplicate'):
                return self._row(duplicate) | {'duplicate': True}
            if not data.get('allow_duplicate'):
                for official in self._formal(db):
                    if len(official['scores']) == 1 and self._fingerprint(official) == fingerprint:
                        return official | {'duplicate': True}
            timestamp = stamp(self.clock())
            match_id = db.execute('''INSERT INTO casual_matches(scope_id,bot_id,chat_id,date,venue_id,venue_name,session_json,discipline,doubles_category,
                team_a,team_b,score_a,score_b,created_by,created_at,updated_at,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (self.ctx.scope_id, self.ctx.id, self.ctx.chat_id, day, venue_id, venue_name, dumps(session) if session else None, discipline, category,
                 dumps(teams[0]), dumps(teams[1]), a, b, self.ctx.actor, timestamp, timestamp, fingerprint)).lastrowid
            result = self._get(db, match_id)
            db.execute('INSERT INTO casual_audit(match_id,actor,action,after_json,created_at) VALUES(?,?,?,?,?)', (match_id, self.ctx.actor, 'create', dumps(result), timestamp))
            audit(db, self.ctx.actor, 'casual.create', {'match_id': match_id})
        return result

    def update(self, match_id, data):
        self._approved()
        if not isinstance(data, dict) or set(data) - {'discipline', 'doubles_category', 'team_a', 'team_b', 'score_a', 'score_b', 'expected_version'}:
            raise ValueError('仅可修正参赛人、类型和比分，历史场地与时段快照不能改写')
        with connect(self.path, True) as db:
            self._approved(db)
            old = self._get(db, match_id)
            if not self.ctx.can('event.manage') and old['created_by'] != self.ctx.actor:
                raise PermissionError('只能修改本人提交的对局；其他记录请联系管理员修正')
            if old['status'] != 'completed':
                raise ValueError('已作废记录不能修改')
            expected = data.get('expected_version')
            if expected is not None and (type(expected) is not int or expected != old['version']):
                raise ValueError('记录已被更新，请刷新后重试')
            updated = old | data
            a, b = self._scores(updated)
            discipline, category, teams = self._teams(db, updated, old, preserve_sides=[side for side in ('team_a', 'team_b') if side not in data])
            updated.update(discipline=discipline, doubles_category=category, team_a=teams[0], team_b=teams[1], score_a=a, score_b=b)
            timestamp = stamp(self.clock())
            db.execute('''UPDATE casual_matches SET discipline=?,doubles_category=?,team_a=?,team_b=?,score_a=?,score_b=?,
                version=version+1,updated_at=?,fingerprint=? WHERE id=?''', (discipline, category, dumps(teams[0]), dumps(teams[1]), a, b, timestamp, self._fingerprint(updated), match_id))
            result = self._get(db, match_id)
            db.execute('INSERT INTO casual_audit(match_id,actor,action,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)', (match_id, self.ctx.actor, 'update', dumps(old), dumps(result), timestamp))
            audit(db, self.ctx.actor, 'casual.update', {'match_id': match_id, 'version': result['version']})
        return result

    def void(self, match_id, expected_version=None):
        self._approved()
        with connect(self.path, True) as db:
            self._approved(db)
            old = self._get(db, match_id)
            if not self.ctx.can('event.manage') and old['created_by'] != self.ctx.actor:
                raise PermissionError('只能作废本人提交的对局')
            if expected_version is not None and (type(expected_version) is not int or expected_version != old['version']):
                raise ValueError('记录已被更新，请刷新后重试')
            if old['status'] == 'void':
                return old
            timestamp = stamp(self.clock())
            db.execute("UPDATE casual_matches SET status='void',version=version+1,updated_at=? WHERE id=?", (timestamp, match_id))
            result = self._get(db, match_id)
            db.execute('INSERT INTO casual_audit(match_id,actor,action,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)', (match_id, self.ctx.actor, 'void', dumps(old), dumps(result), timestamp))
            audit(db, self.ctx.actor, 'casual.void', {'match_id': match_id})
        return result

    def _formal(self, db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tour_matches'").fetchone():
            return []
        rows = db.execute('''SELECT m.*,e.title,e.venue_id,e.date,e.options,e.status AS event_status,e.chat_id AS scope_id,e.creator,
            v.name AS venue_name FROM tour_matches m JOIN tour_events e ON e.id=m.tournament_id
            JOIN venues v ON v.id=e.venue_id WHERE e.chat_id=? AND m.status='completed' AND e.status<>'cancelled' ''', (self.ctx.scope_id,))
        result = []
        for source in rows:
            row = dict(source)
            sides = [json.loads(row['side_a'] or 'null'), json.loads(row['side_b'] or 'null')]
            scores = json.loads(row['scores'] or '[]')
            if not all(sides) or not scores or row['winner'] not in {side['id'] for side in sides}:
                continue
            people = {p['id']: dict(p) for p in db.execute('SELECT * FROM tour_participants WHERE tournament_id=?', (row['tournament_id'],))}
            teams = []
            for side in sides:
                team = []
                for key in side.get('members', []):
                    p = people.get(str(key))
                    if not p:
                        continue
                    member = bool(p.get('user_id')) and not p.get('guest_index', 0)
                    name = p['name'] if p['name'] and p['name'] not in (p['id'], p['user_id'], f"{p['user_id']}+{p.get('guest_index', 0)}") else '待完善姓名'
                    person = {'kind': 'member' if member else 'guest', 'name': name, 'gender': p.get('gender', 'unknown')}
                    if member:
                        person['user_id'] = p['user_id']
                    else:
                        person['guest_id'] = f"tournament:{row['tournament_id']}:{p['id']}"
                    team.append(person)
                teams.append(team)
            if not all(teams):
                continue
            options = json.loads(row['options'])
            discipline = options.get('discipline', 'singles' if len(teams[0]) == 1 else 'doubles')
            # Multiple games are one match; winner comes from the tournament's
            # rules, not summed points (a best-of-three may win fewer points).
            item = {'id': f"tournament:{row['id']}", 'source': 'tournament', 'source_id': row['id'], 'event_id': row['tournament_id'],
                    'title': row['title'], 'match_number': row['number'], 'date': row['date'], 'venue_id': row['venue_id'],
                    'venue_name': (options.get('session_snapshot') or {}).get('venue_name') or row['venue_name'], 'session': options.get('session_snapshot'), 'discipline': discipline,
                    'doubles_category': options.get('doubles_category', 'open'), 'team_a': teams[0], 'team_b': teams[1],
                    'scores': scores, 'score_a': sum(x[0] for x in scores), 'score_b': sum(x[1] for x in scores),
                    'winner': 'a' if sides[0]['id'] == row['winner'] else 'b', 'status': 'completed', 'version': row['version'],
                    'created_by': row['creator'], 'created_at': row['updated_at'], 'updated_at': row['updated_at'], 'can_edit': False, 'can_void': False}
            if hasattr(self.service, 'event_allowed'):
                event = {'id': row['tournament_id'], 'venue_id': row['venue_id'], 'date': row['date'], 'options': options}
                if not self.service.event_allowed(event):
                    continue
            result.append(item)
        return result

    def history(self, user_id=None, date_from=None, date_to=None, venue_id=None, include_formal=True, include_void=False):
        self._approved()
        user_id = self._subject(user_id, all_default=True)
        if date_from:
            day_value(date_from)
        if date_to:
            day_value(date_to)
        if date_from and date_to and date_from > date_to:
            raise ValueError('开始日期不能晚于结束日期')
        if venue_id is not None:
            self.service.venue(venue_id)
        with connect(self.path) as db:
            rows = [self._row(row) for row in db.execute('SELECT * FROM casual_matches WHERE scope_id=?', (self.ctx.scope_id,))]
            if include_formal:
                rows.extend(self._formal(db))
        result = []
        for row in rows:
            if not include_void and row['status'] != 'completed':
                continue
            if (date_from and row['date'] < date_from) or (date_to and row['date'] > date_to) or (venue_id is not None and row['venue_id'] != int(venue_id)):
                continue
            if user_id and not any(p.get('user_id') == user_id for side in ('team_a', 'team_b') for p in row[side]):
                continue
            try:
                if row['source'] == 'casual':
                    self._scope(row)
                else:
                    self.service.venue(row['venue_id'])
            except PermissionError:
                continue
            result.append(row)
        result.sort(key=lambda row: (row['date'], row['updated_at'], str(row['id'])), reverse=True)
        return {'matches': result, 'count': len(result), 'user_id': user_id}

    def statistics(self, user_id=None, date_from=None, date_to=None, venue_id=None, include_formal=True):
        target = self._subject(user_id)
        rows = self.history(target or '*', date_from, date_to, venue_id, include_formal)['matches']
        def bucket():
            return {'played': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0}
        total = bucket()
        groups = {key: {} for key in ('by_discipline', 'partners', 'opponents', 'opponent_teams')}
        sources = {'casual': 0, 'tournament': 0}
        def add(container, key, metadata, won):
            item = container.setdefault(key, bucket() | metadata)
            item['played'] += 1
            item['wins' if won else 'losses'] += 1
        for row in rows:
            sources[row['source']] += 1
            # An all-member overview counts each match once, and does not invent
            # a club-wide win rate when both sides belong to the same club.
            if target is None:
                total['played'] += 1
                add(groups['by_discipline'], row['discipline'], {'discipline': row['discipline'], 'label': '单打' if row['discipline'] == 'singles' else '双打'}, False)
                continue
            side = 'a' if any(p.get('user_id') == target for p in row['team_a']) else 'b'
            own, opposite = row['team_' + side], row['team_' + ('b' if side == 'a' else 'a')]
            won = row['winner'] == side
            total['played'] += 1
            total['wins' if won else 'losses'] += 1
            add(groups['by_discipline'], row['discipline'], {'discipline': row['discipline'], 'label': '单打' if row['discipline'] == 'singles' else '双打'}, won)
            partners = [p for p in own if p.get('user_id') != target]
            if partners:
                add(groups['partners'], tuple(sorted(person_key(p) for p in partners)), {'people': partners}, won)
            for person in opposite:
                add(groups['opponents'], person_key(person), {'people': [person]}, won)
            add(groups['opponent_teams'], tuple(sorted(person_key(p) for p in opposite)), {'people': opposite}, won)
        for item in [total, *(item for group in groups.values() for item in group.values())]:
            item['win_rate'] = round(item['wins'] * 100 / item['played'], 1) if item['played'] else 0.0
        if target is None:
            total.update(wins=None, losses=None, win_rate=None)
            for item in groups['by_discipline'].values():
                item.update(wins=None, losses=None, win_rate=None)
        return total | {key: sorted(group.values(), key=lambda x: (-x['played'], -x['wins'] if x['wins'] is not None else 0)) for key, group in groups.items()} | {'sources': sources, 'user_id': target}


def render_match(row):
    location = row['venue_name']
    if row.get('session'):
        s = row['session']
        location += f" · {s['name']} {s['start']}–{s['end']}"
    title = f"对局 #{row['id']}" if row['source'] == 'casual' else f"{row['title']} 第{row['match_number']}场"
    return f"**🏸 {_safe(title)} · {category_label(row)}**\n\n{_safe(row['date'])} · {_safe(location)}\n\n" + table(['甲方', '比分', '乙方'], [[team_name(row['team_a']), f"{row['score_a']} : {row['score_b']}", team_name(row['team_b'])]])


def render_history(result):
    rows = result['matches']
    if not rows:
        return '**🏸 我的对战历史**\n\n还没有已完成的对局。发送“记录”记下今天的比赛。'
    def versus(row):
        if len(row['scores']) == 1:
            return f"{team_name(row['team_a'])}（{row['score_a']}） VS {team_name(row['team_b'])}（{row['score_b']}）"
        games = ' / '.join(f'{a}:{b}' for a, b in row['scores'])
        winner = team_name(row['team_' + row['winner']])
        return f"{team_name(row['team_a'])} VS {team_name(row['team_b'])} · {games} · {winner}胜"
    body = table(['日期', '类型', '对战与比分', '来源'], [[r['date'], category_label(r), versus(r), f"零散 #{r['id']}" if r['source'] == 'casual' else f"{r['title']} 第{r['match_number']}场"] for r in rows[:30]])
    return f"**🏸 我的对战历史 · 共 {result['count']} 场**\n\n" + body + ('\n\n仅显示最近30场，可加日期范围查询。' if len(rows) > 30 else '')


def render_statistics(result):
    body = f"**📊 我的对战统计**\n\n共 **{result['played']}** 场 · **{result['wins']}** 胜 / **{result['losses']}** 负 · 胜率 **{result['win_rate']}%**"
    body += '\n\n' + table(['类型', '场次', '胜 / 负', '胜率'], [[r['label'], r['played'], f"{r['wins']} / {r['losses']}", f"{r['win_rate']}%"] for r in result['by_discipline']])
    for key, label in [('partners', '搭档组合'), ('opponents', '对手个人'), ('opponent_teams', '对手组合')]:
        if result[key]:
            body += f'\n\n**{label}**\n\n' + table([label, '场次', '我方胜率'], [[team_name(r['people']), r['played'], f"{r['win_rate']}%"] for r in result[key][:15]])
    return body + '\n\n统计包括已记分赛事和零散对局；作废与未完成记录不计入。'
