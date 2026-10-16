"""Shared club tournament boundary; channels are transports, never owners."""
from .db import now, dumps, connect
from .service import Service, session_time, registration_name
from .members import Members
from .gender import require_person, category, eligible
from .scope import snapshot


class Tournaments:
    def __init__(self, ctx, clock=now):
        from .competitions import TournamentService
        self.ctx, self.clock = ctx, clock
        self.scope_id = getattr(ctx, 'scope_id', ctx.chat_id)
        self.service = Service(ctx, clock)
        self.members = Members(ctx, clock)
        self.engine = TournamentService(ctx.db_path)

    def formats(self, discipline='doubles', participant_count=None, options=None, substitute_count=0):
        return self.engine.formats(discipline, participant_count, options, substitute_count)

    def available_formats(self, count, discipline='doubles', options=None, substitute_count=0):
        return self.engine.available_formats(count, discipline, options, substitute_count)

    def get(self, event_id, venue_id=None):
        self.members.require_approved()
        result = self.engine.get(event_id)
        from .bot_policy import require_event
        require_event(self.service.scope_context(),result)
        if str(result['chat_id']) != self.scope_id:
            raise PermissionError('赛事不属于当前后台')
        self.service.venue(result['venue_id'])
        self.service.assert_event_scope(result)
        if venue_id is not None and int(result['venue_id']) != int(venue_id):
            raise PermissionError('赛事不属于当前场地')
        result['permissions'] = self.engine.permissions(result, self.ctx.actor, self.ctx.can('event.manage'), self.clock())
        return result

    def list(self, venue_id=None, status=None, *, _include_disabled=False):
        self.members.require_approved()
        if venue_id is not None:
            self.service.venue(venue_id)
        rows = self.engine.list(venue_id=venue_id, chat_id=self.scope_id, status=status)
        result = []
        from .bot_policy import require_event
        context = self.service.scope_context()
        for item in rows:
            try:
                if not _include_disabled:
                    require_event(context, item)
                self.service.venue(item['venue_id'])
                self.service.assert_event_scope(item)
            except PermissionError:
                continue
            item['permissions'] = self.engine.permissions(item, self.ctx.actor, self.ctx.can('event.manage'), self.clock())
            result.append(item)
        return result

    def planning_candidates(self, day=None, venue_id=1, session_id=None):
        self.members.require_approved()
        session = self.service.resolve_session(day, venue_id, session_id)
        with connect(self.ctx.db_path) as db:
            from .availability import assert_bookable
            assert_bookable(db, session)
        entries = self.service.registrations(session['date'], venue_id, session['id'])['registrations']
        players = [{'id': f"{r['user_id']}:{r['guest_index']}", 'user_id': r['user_id'], 'name': r['name'], 'guest_index': r['guest_index'],
                    'level': r.get('level'), 'group_ids': r.get('group_ids', []), 'source_category': r['category'],
                    'is_proxy': r.get('is_proxy', bool(r.get('guest_index')))}
                   for r in entries if r['status'] == 'active' and r['category'] in ('正式', '替补') and r.get('eligible', True)]
        with connect(self.ctx.db_path) as db:
            genders = {r['user_id']: r['gender'] for r in db.execute('SELECT user_id,gender FROM member_records WHERE chat_id=?', (self.scope_id,))}
        for person in players:
            person['gender'] = 'unknown' if person['guest_index'] else genders.get(person['user_id'], 'unknown')
        return {'formal': [p for p in players if p['source_category'] == '正式'],
                'substitutes': [p for p in players if p['source_category'] == '替补'], 'session': session}

    def planning_formats(self, candidates, discipline='doubles', options=None):
        options = options or {}
        rules = self.members.validate_rules(options.get('eligibility') or {})
        restricted = rules['min_level'] is not None or rules['max_level'] is not None or bool(rules['allowed_group_ids'])
        def allowed(person):
            if restricted and (person.get('is_proxy') or person.get('guest_index')):
                return False
            grade = person.get('level')
            if (rules['min_level'] is not None or rules['max_level'] is not None) and not grade:
                return False
            if rules['min_level'] is not None and grade < rules['min_level']:
                return False
            if rules['max_level'] is not None and grade > rules['max_level']:
                return False
            return not rules['allowed_group_ids'] or bool(set(rules['allowed_group_ids']) & set(person.get('group_ids', [])))
        formal = [p for p in candidates['formal'] if allowed(p)]
        subs = [p for p in candidates['substitutes'] if allowed(p)]
        rows = self.available_formats(len(formal), discipline,
            {**options, 'participants': formal, 'substitutes': subs}, len(subs))
        omitted = len(candidates['formal']) + len(candidates['substitutes']) - len(formal) - len(subs)
        for row in rows:
            row['excluded_count'] = row.get('excluded_count', 0) + omitted
            row['eligibility_excluded_count'] = omitted
        return rows

    @staticmethod
    def _selected_players(candidates, selected):
        people = candidates['formal'] + candidates['substitutes']
        ids = set(selected['selected_ids'])
        chosen = [p for p in people if p['id'] in ids]
        return chosen, [p for p in chosen if p['source_category'] == '替补']

    def participants(self, day=None, venue_id=1, session_id=None, include_substitutes=False):
        candidates = self.planning_candidates(day, venue_id, session_id)
        return candidates['formal'] + (candidates['substitutes'] if include_substitutes else []), candidates['session']

    def create(self, data):
        from .bot_policy import require_event
        require_event(self.service.scope_context(), {'kind':data.get('kind','daily')})
        actor_profile = self.members.require_approved()
        data = dict(data)
        venue_id = int(data.get('venue_id', 1))
        data['venue_id'] = venue_id
        self.service.venue(venue_id, True)
        if data.get('chat_id', self.scope_id) not in (self.scope_id, self.ctx.chat_id):
            raise PermissionError('不能创建其他后台的赛事')
        data['chat_id'] = self.scope_id
        options = dict(data.get('options') or {})
        options.pop('session_snapshot', None)  # Always derive trusted scope evidence.
        if data.get('organizer_user_id'):
            self.service.require_admin('event.manage')
            organizer = self.members.require_approved(str(data['organizer_user_id']), allow_admin=False)
        else:
            organizer = actor_profile
        options['organizer_name'] = (organizer or {}).get('name') or '管理员'
        if 'eligibility' in options:
            self.service.require_admin('event.manage')
            options['eligibility'] = self.members.validate_rules(options['eligibility'])
        category(options)
        data['options'] = options
        if data.get('kind', 'daily') == 'daily':
            data['kind'] = 'daily'
            data.pop('registration_open', None)
            data.pop('registration_close', None)
            candidates = self.planning_candidates(data.get('date'), venue_id, data.pop('session_id', None))
            players, session = candidates['formal'], candidates['session']
            discipline = options.get('discipline', 'singles' if options.get('team_size') == 1 else 'doubles')
            formats = self.planning_formats(candidates, discipline, {**options, 'group_count': data.get('group_count', 2)})
            selected = next((item for item in formats if item['id'] == data.get('format', 'round_robin')), None)
            if not selected or not selected['available']:
                raise ValueError(selected['reason'] if selected else '未知赛制')
            players, additions = self._selected_players(candidates, selected)
            data['options']['selected_substitute_names'] = [person['name'] for person in additions]
            data['participants'] = players
            data['date'] = session['date']
            config = self.service.config()
            options = dict(data.get('options') or {})
            options.setdefault('scoring_mode', config['scoring_mode'])
            options.setdefault('leader_user_ids', config['leader_user_ids'])
            if not self.ctx.can('event.manage'):
                options.update({'scoring_mode': config['scoring_mode'], 'leader_user_ids': config['leader_user_ids']})
            options['session_id'] = session['id']
            options['session_snapshot'] = snapshot(session)
            data['options'] = options
        else:
            self.service.require_admin('event.manage')
            cleaned = []
            for person in data.get('participants', []):
                profile = self.members.assert_eligible(str(person.get('user_id', person.get('id', ''))), options.get('eligibility', {}), int(person.get('guest_index', 0)))
                guest = int(person.get('guest_index', 0))
                cleaned.append({**person, 'user_id': profile['user_id'], 'name': profile['name'] + (f'+{guest}' if guest else ''),
                                'level': None if guest else profile['level'], 'gender': 'unknown' if guest else profile['gender'], 'group_ids': [] if guest else profile['group_ids']})
            data['participants'] = cleaned
            if options.get('session_id'):
                with connect(self.ctx.db_path) as db:
                    session = db.execute('SELECT * FROM sessions WHERE id=? AND venue_id=?', (options['session_id'], venue_id)).fetchone()
                if not session:
                    raise ValueError('赛事关联场次不存在或不属于此场地')
                data.setdefault('date', session['date'])
                if data['date'] != session['date']:
                    raise ValueError('赛事日期与关联场次日期不一致')
                self.service.assert_session_scope(session)
                options['session_snapshot'] = snapshot(session)
        self.service.assert_event_scope(data)
        channel = getattr(self.ctx, 'channel_id', '')
        return self.engine.create(data, self.ctx.actor, self.ctx.can('event.manage'), creation_guard=self._creation_guard,
                                  channel_ids=[channel] if channel else [])

    def _creation_guard(self, db, prepared):
        """Rebuild the selected roster under the engine's write transaction."""
        self.members.require_approved(db=db)
        creator = prepared['creator']
        if creator != self.ctx.actor or not self.ctx.can('event.manage'):
            organizer = self.members.require_approved(creator, db=db, allow_admin=False)
            prepared['options']['organizer_name'] = organizer['name']
        venue = db.execute('SELECT active FROM venues WHERE id=?', (prepared['venue_id'],)).fetchone()
        if not venue or not venue[0]:
            raise ValueError('场地已停用，请重新选择')
        self.service.assert_event_scope(prepared)
        if prepared['options'].get('session_id'):
            bound_session = db.execute('SELECT * FROM sessions WHERE id=? AND venue_id=?', (prepared['options']['session_id'], prepared['venue_id'])).fetchone()
            if not bound_session:
                raise ValueError('赛事关联场次不存在或不属于此场地')
            if prepared['date'] != bound_session['date']:
                raise ValueError('赛事日期与关联场次日期不一致')
            self.service.assert_session_scope(bound_session)
            prepared['options']['session_snapshot'] = snapshot(bound_session)
        if prepared['kind'] != 'daily':
            result = []
            for person in prepared['participants']:
                guest = person.get('guest_index', 0)
                profile = self.members.assert_eligible(person['user_id'], prepared['options'].get('eligibility', {}), guest, db=db)
                from .membership_terms import MembershipTerms
                MembershipTerms(self.ctx, self.clock).require_enrollment(person['user_id'], prepared['date'], db=db)
                result.append({**person, 'name': person['name'] if guest else profile['name'],
                               'level': 0 if guest else profile['level'], 'gender': 'unknown' if guest else profile['gender'], 'group_ids': [] if guest else profile['group_ids']})
            return {'participants': result}
        session = db.execute('SELECT * FROM sessions WHERE id=? AND venue_id=? AND date=?',
                             (prepared['options']['session_id'], prepared['venue_id'], prepared['date'])).fetchone()
        if not session or session_time(session, 'end') <= self.clock():
            raise ValueError('活动场次已调整或已结束，请重新组织比赛')
        session = dict(session)
        from .availability import assert_bookable
        assert_bookable(db, session)
        self.service.assert_session_scope(session)
        formal, substitutes = [], []
        rows = db.execute("SELECT * FROM registrations WHERE session_id=? AND chat_id=? AND status='active' ORDER BY queue_seq,id",
                          (session['id'], self.scope_id)).fetchall()
        for rank, row in enumerate(rows, 1):
            if rank > session['capacity'] + session['substitute_capacity']:
                break
            try:
                profile = self.members.assert_eligible(row['user_id'], session, int(row['guest_index'] > 0), db=db)
                self.members.assert_eligible(row['user_id'], prepared['options'].get('eligibility', {}), int(row['guest_index'] > 0), db=db)
            except (PermissionError, ValueError):
                continue
            guest = row['guest_index']
            category = '正式' if rank <= session['capacity'] else '替补'
            person = {'id': f"{row['user_id']}:{guest}", 'user_id': row['user_id'], 'guest_index': guest,
                      'name': registration_name(row, profile['name']),
                      'is_proxy': guest > 0, 'level': 0 if guest else profile['level'], 'gender': 'unknown' if guest else profile['gender'],
                      'group_ids': [] if guest else profile['group_ids'], 'source_category': category}
            (formal if category == '正式' else substitutes).append(person)
        candidates = {'formal': formal, 'substitutes': substitutes}
        formats = self.planning_formats(candidates, prepared['options']['discipline'],
                                         {**prepared['options'], 'group_count': prepared['group_count']})
        selected = next(item for item in formats if item['id'] == prepared['format'])
        if not selected['available']:
            raise ValueError('当前报名或资格已变化：' + selected['reason'])
        chosen, additions = self._selected_players(candidates, selected)
        return {'participants': chosen, 'selected_substitute_names': [person['name'] for person in additions]}

    def update(self, event_id, data):
        item = self.get(event_id)
        if data.get('chat_id', item['chat_id']) != item['chat_id'] or int(data.get('venue_id', item['venue_id'])) != item['venue_id']:
            raise ValueError('赛事创建后不可变更所属后台或场地')
        data = dict(data)
        changed_options = data.get('options') or {}
        if 'session_snapshot' in changed_options:
            raise ValueError('赛事场次快照由系统记录，不可手动修改')
        if 'session_id' in changed_options:
            if changed_options['session_id'] is None:
                unbound = {k: v for k, v in item['options'].items() if k not in ('session_id', 'session_snapshot')}
                self.service.assert_event_scope({**item, 'options': unbound})
                data['options'] = {**changed_options, 'session_snapshot': None}
            else:
                with connect(self.ctx.db_path) as db:
                    session = db.execute('SELECT * FROM sessions WHERE id=? AND venue_id=?', (changed_options['session_id'], item['venue_id'])).fetchone()
                if not session:
                    raise ValueError('赛事关联场次不存在或不属于此场地')
                if item['date'] != session['date']:
                    raise ValueError('赛事日期与关联场次日期不一致')
                self.service.assert_session_scope(session)
                data['options'] = {**changed_options, 'session_snapshot': snapshot(session)}
        if 'eligibility' in (data.get('options') or {}):
            data['options'] = {**data['options'], 'eligibility': self.members.validate_rules(data['options']['eligibility'])}
        for key in ('chat_id', 'venue_id', 'kind', 'format', 'date'):
            if key in data:
                if str(data[key]) != str(item[key]):
                    raise ValueError(f'赛事创建后不可变更 {key}')
                data.pop(key)
        if item['kind'] == 'daily':
            data.pop('registration_open', None)
            data.pop('registration_close', None)
            data.pop('keyword', None)
        return self.engine.update(event_id, data, self.ctx.actor, self.ctx.can('event.manage'))

    def transition(self, event_id, action, venue_id=None):
        item = self.get(event_id, venue_id)
        if not item['permissions']['manage']:
            raise PermissionError('请由赛事组织者或管理员操作')
        def guard(db, event):
            people = [dict(p) for p in db.execute("SELECT * FROM tour_participants WHERE tournament_id=? AND status='active'", (event_id,))]
            for uid in dict.fromkeys(p['user_id'] for p in people):
                group = [p for p in people if p['user_id'] == uid]
                guests = sum(p['guest_index'] > 0 for p in group)
                profile = self.members.assert_eligible(uid, event.get('options', {}).get('eligibility', {}), guests, db=db)
                for person in group:
                    if person['guest_index'] == 0:
                        db.execute('UPDATE tour_participants SET name=?,level=?,group_ids=?,gender=? WHERE tournament_id=? AND id=?',
                                   (profile['name'], profile['level'] or 0, dumps(profile['group_ids']), profile['gender'], event_id, person['id']))
        return self.engine.transition(event_id, action, self.ctx.actor, self.ctx.can('event.manage'), eligibility_guard=guard)

    def can_score(self, item):
        return self.engine.permissions(item, self.ctx.actor, self.ctx.can('event.manage'), self.clock())['score']

    def score(self, event_id, match_id, scores, version=None, venue_id=None, by_number=False):
        item = self.get(event_id, venue_id)
        if not self.can_score(item):
            raise PermissionError('无本赛事记分权限或临时授权已过期')
        matches = [m for m in item['matches'] if int(m.get('number') if by_number else m['id']) == int(match_id)]
        if len(matches) != 1:
            raise ValueError('赛事场次不存在')
        match = matches[0]
        if match.get('scores') and version is None:
            raise ValueError('修改已记分比赛需要 expected_version，请在管理页刷新后操作')
        return self.engine.score(event_id, match['id'], scores, self.ctx.actor, self.ctx.can('event.manage'), expected_version=match['version'] if version is None else version)

    def enroll(self, event_id, user_id, name, guests=0, venue_id=None, guest_names=None):
        self.get(event_id, venue_id)
        if user_id != self.ctx.actor and (self.ctx.source in ('callback', 'simulator') or not self.ctx.can('event.manage')):
            raise PermissionError('只能操作本人及同行人员')
        profile = self.members.require_approved(user_id, allow_admin=False)
        def guard(db, event):
            active_guests = {row[0] for row in db.execute(
                "SELECT guest_index FROM tour_participants WHERE tournament_id=? AND user_id=? AND status='active'",
                (event_id, user_id))}
            if guests or 0 not in active_guests:
                from .membership_terms import MembershipTerms
                MembershipTerms(self.ctx, self.clock).require_enrollment(user_id, event['date'], db=db)
            return self.members.assert_eligible(user_id, event.get('options', {}).get('eligibility', {}), guests, db=db)
        return self.engine.enroll(event_id, user_id, profile['name'], guests, now=self.clock(), profile=profile, eligibility_guard=guard, guest_names=guest_names)

    def cancel(self, event_id, user_id, venue_id=None):
        self.get(event_id, venue_id)
        if user_id != self.ctx.actor and (self.ctx.source in ('callback', 'simulator') or not self.ctx.can('event.manage')):
            raise PermissionError('只能取消本人报名；同行人员请按姓名单独取消')
        def guard(db, event):
            self.members.require_approved(db=db)
            self.service.assert_event_scope(event)
        return self.engine.cancel(event_id, user_id, now=self.clock(), eligibility_guard=guard)

    def cancel_guests(self, event_id, user_id, guest_names, venue_id=None):
        self.get(event_id, venue_id)
        if user_id != self.ctx.actor:
            raise PermissionError('只能取消本人代报名的同行人员')
        def guard(db, event):
            self.members.require_approved(user_id, db=db, allow_admin=False)
            self.service.assert_event_scope(event)
        return self.engine.cancel_guests(event_id, user_id, guest_names, now=self.clock(), eligibility_guard=guard)

    def cancel_entry(self, event_id, participant_id, venue_id=None):
        self.ctx.require('event.manage')
        if self.ctx.source in ('callback', 'simulator'):
            raise PermissionError('按报名条目移除参赛者请在管理页面操作')
        self.get(event_id, venue_id)
        def guard(db, event):
            self.ctx.require('event.manage', db=db)
            self.service.assert_event_scope(event)
        return self.engine.cancel_entry(event_id, participant_id, self.ctx.actor, is_admin=True,
                                        now=self.clock(), eligibility_guard=guard)

    def resume_candidates(self, venue_id=1, day=None):
        players, _ = self.participants(day, venue_id, include_substitutes=True)
        return [item for item in self.engine.resume_candidates(venue_id, self.scope_id, players)
                if self.service.event_allowed(item)]

    def describe(self, event_id, mode='schedule'):
        item = self.get(event_id)
        result = self.engine.render(event_id, mode, actor=self.ctx.actor)
        if item['status'] == 'completed' and mode == 'schedule':
            result += '\n\n' + self.engine.render(event_id, 'results')
        return result
