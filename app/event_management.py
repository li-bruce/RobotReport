"""Event organization, staff appointments and notification destinations.

An event belongs to the shared club database. Bot/group delivery credentials
remain in Platform; a caller must validate channel bindings there before save.
"""
from contextlib import closing

from .competitions import TournamentService, _dump, _time
from .db import now
from .members import Members
from .service import Service


TEXT_FIELDS = {'description', 'rules_text', 'meeting_instructions', 'coordination'}
TEAM_METHODS = {'random', 'seeded', 'balanced', 'high_low'}


class EventManagement:
    def __init__(self, ctx, clock=now, channel_validator=None):
        self.ctx, self.clock = ctx, clock
        self.scope_id = getattr(ctx, 'scope_id', ctx.chat_id)
        self.engine = TournamentService(ctx.db_path)
        self.members = Members(ctx, clock)
        self.service = Service(ctx, clock)
        self.channel_validator = channel_validator

    def _event(self, db, event_id, write=False):
        self.members.require_approved(db=db)
        event = self.engine._event(db, event_id)
        if event['chat_id'] != self.scope_id:
            raise PermissionError('赛事不属于当前后台')
        self.service.venue(event['venue_id'])
        self.service.assert_event_scope(event)
        if write and event['status'] in ('archived', 'cancelled'):
            raise ValueError('已关闭或归档赛事不可修改')
        return event

    def _read(self, db, event):
        row = db.execute('SELECT * FROM tour_organization WHERE tournament_id=?', (event['id'],)).fetchone()
        result = {key: row[key] if row else '' for key in sorted(TEXT_FIELDS)}
        result['team_method'] = event['options'].get('assignment_strategy', 'random')
        result['channel_ids'] = [r[0] for r in db.execute('SELECT channel_id FROM tour_event_channels WHERE tournament_id=? ORDER BY channel_id', (event['id'],))]
        result['staff'] = []
        for person in event['staff']:
            profile = db.execute('SELECT name FROM profiles WHERE chat_id=? AND user_id=?', (self.scope_id, person['user_id'])).fetchone()
            result['staff'].append({**person, 'name': profile['name'] if profile and profile['name'] != person['user_id'] else '待完善姓名',
                                    'active': _time(person['starts_at']) <= self.clock() < _time(person['expires_at'])})
        profile = db.execute('SELECT name FROM profiles WHERE chat_id=? AND user_id=?', (self.scope_id, event['creator'])).fetchone()
        result['organizer'] = {'user_id': event['creator'], 'name': profile['name'] if profile and profile['name'] else event['options'].get('organizer_name', '管理员')}
        result['permissions'] = self.engine.permissions(event, self.ctx.actor, self.ctx.can('event.manage'), self.clock())
        result['version'] = event['version']
        return result

    def get_organization(self, event_id):
        with closing(self.engine._connect()) as db:
            return self._read(db, self._event(db, event_id))

    def permissions(self, event_id):
        return self.get_organization(event_id)['permissions']

    def get_channels(self, event_id):
        return self.get_organization(event_id)['channel_ids']

    def save_channels(self, event_id, channel_ids, channel_validator=None):
        return self.save_organization(event_id, {'channel_ids': channel_ids}, channel_validator)['channel_ids']

    def save_organization(self, event_id, data, channel_validator=None):
        allowed = TEXT_FIELDS | {'team_method', 'staff', 'channel_ids', 'expected_version', 'match_schedule'}
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError('赛事组织字段无效')
        with self.engine._transaction() as db:
            event = self._event(db, event_id, write=True)
            before = self._read(db, event)
            rights = before['permissions']
            if not rights['coordinate']:
                raise PermissionError('请由赛事负责人或协调员管理赛事安排')
            if 'expected_version' in data and (type(data['expected_version']) is not int or data['expected_version'] != event['version']):
                raise ValueError('赛事已更新，请刷新后重试')
            changed = {key for key in data if key != 'expected_version'}
            if not rights['manage'] and changed - {'description', 'meeting_instructions', 'coordination', 'match_schedule'}:
                raise PermissionError('协调员只能维护公开说明、集合说明和赛程协调')
            texts = {key: before[key] for key in TEXT_FIELDS}
            for key in TEXT_FIELDS & changed:
                value = data[key]
                if not isinstance(value, str) or len(value) > 10000 or any(ord(char) < 32 and char not in '\n\r\t' for char in value):
                    raise ValueError('赛事说明应为不超过10000字的文本')
                texts[key] = value.strip()
            if 'team_method' in data:
                method = data['team_method']
                if not isinstance(method, str) or method not in TEAM_METHODS:
                    raise ValueError('组队方法应为随机、种子分布、平均分布或高带低')
                if method != before['team_method']:
                    if db.execute('SELECT 1 FROM tour_matches WHERE tournament_id=? LIMIT 1', (event_id,)).fetchone():
                        raise ValueError('已生成对阵后不能更换组队方法；历史对阵保持不变')
                    options = self.engine._options(event['format'], {**event['options'], 'assignment_strategy': method})
                    db.execute('UPDATE tour_events SET options=? WHERE id=?', (_dump(options), event_id))
            if 'staff' in data:
                staff = self.engine._validate_staff_change(db, event, data['staff'], self.ctx.actor, self.ctx.can('event.manage'))
                db.execute('UPDATE tour_events SET staff=? WHERE id=?', (_dump(staff), event_id))
            if 'channel_ids' in data:
                ids = data['channel_ids']
                if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(item, str) or not item or len(item) > 100 for item in ids) or len(set(ids)) != len(ids):
                    raise ValueError('通知通道列表无效或重复')
                validator = channel_validator or self.channel_validator
                if ids and validator is None:
                    raise ValueError('保存通知通道必须验证所属后台、启用状态和可用场地')
                if validator:
                    validator(ids, event['venue_id'])
                db.execute('DELETE FROM tour_event_channels WHERE tournament_id=?', (event_id,))
                db.executemany('INSERT INTO tour_event_channels(tournament_id,channel_id,created_by,created_at) VALUES(?,?,?,?)',
                               [(event_id, channel, self.ctx.actor, self.clock().isoformat()) for channel in ids])
            if 'match_schedule' in data:
                self.engine._schedule(db, event, data['match_schedule'], self.ctx.actor)
            db.execute('''INSERT INTO tour_organization(tournament_id,description,rules_text,meeting_instructions,coordination,updated_by,updated_at)
                          VALUES(?,?,?,?,?,?,?) ON CONFLICT(tournament_id) DO UPDATE SET
                          description=excluded.description,rules_text=excluded.rules_text,
                          meeting_instructions=excluded.meeting_instructions,coordination=excluded.coordination,
                          updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                       (event_id, texts['description'], texts['rules_text'], texts['meeting_instructions'], texts['coordination'], self.ctx.actor, self.clock().isoformat()))
            self.engine._touch(db, event_id)
            result = self._read(db, self.engine._event(db, event_id))
            self.engine._audit(db, event_id, self.ctx.actor, 'organization.update', before, result)
        return result
