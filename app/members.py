"""Workspace membership, reviewed identity and participation policies."""
from contextlib import contextmanager
import json
import re

from .db import connect, audit, stamp, now
from .security import BotContext, identifier, permissions_for_roles
from .gender import normalize_gender


def display_name(value, user_id):
    value = re.sub(r'[\x00-\x1f\x7f]', '', str(value or '')).strip()
    if not value or len(value) > 80 or value == str(user_id):
        raise ValueError('请提供真实用户名；用户 ID 不能作为姓名。发送 注册 打开本人注册表单')
    if re.fullmatch(re.escape(str(user_id)) + r'\+\d+', value):
        raise ValueError('用户 ID 不能作为姓名')
    return value


class Members:
    def __init__(self, context: BotContext, clock=now):
        if not isinstance(context, BotContext):
            raise PermissionError('缺少可信机器人上下文')
        self.ctx, self.path, self.clock = context, context.db_path, clock

    @contextmanager
    def _db(self, db=None, write=False):
        if db is not None:
            yield db
        else:
            with connect(self.path, write) as connection:
                yield connection

    def _admin(self):
        self.ctx.require('member.manage')

    def _subject(self, user_id=None):
        user_id = identifier(user_id or self.ctx.actor)
        if not self.ctx.can('member.manage') and user_id != self.ctx.actor:
            raise PermissionError('只能操作本人会员资料')
        return user_id

    def _record(self, db, user_id):
        row = db.execute('''SELECT m.*,COALESCE(p.name,'') AS name FROM member_records m
                            LEFT JOIN profiles p ON p.chat_id=m.chat_id AND p.user_id=m.user_id
                            WHERE m.chat_id=? AND m.user_id=?''', (self.ctx.scope_id, user_id)).fetchone()
        if not row:
            return None
        result = dict(row)
        # Never let an old ID-as-name bug escape through a display API.
        if result['name'] == user_id:
            result['name'] = ''
        result['groups'] = [dict(r) for r in db.execute('''SELECT g.id,g.name FROM member_groups g
                            JOIN member_group_links l ON l.group_id=g.id AND l.chat_id=g.chat_id
                            WHERE l.chat_id=? AND l.user_id=? ORDER BY g.id''', (self.ctx.scope_id, user_id))]
        result['group_ids'] = [r['id'] for r in result['groups']]
        result['roles'] = [r[0] for r in db.execute("SELECT role FROM user_roles WHERE user_id=? AND scope_type='club' AND scope_id=? ORDER BY role", (user_id, self.ctx.scope_id))]
        result['permissions'] = list(permissions_for_roles(result['roles'])) if result['status'] == 'approved' else []
        result['identity_kind'] = 'member' if result['status'] == 'approved' else 'account'
        from .membership_terms import MembershipTerms
        result['membership'] = MembershipTerms(self.ctx, self.clock).summary(user_id, db=db, trusted=True)
        return result

    def get(self, user_id=None, db=None):
        user_id = self._subject(user_id)
        with self._db(db) as connection:
            result = self._record(connection, user_id)
        return result

    def list(self, status=None, group_id=None):
        self._admin()
        if status is not None and status not in ('unregistered', 'pending', 'approved', 'rejected', 'suspended'):
            raise ValueError('会员状态无效')
        with connect(self.path) as db:
            users = [r[0] for r in db.execute('SELECT user_id FROM member_records WHERE chat_id=? ORDER BY created_at,user_id', (self.ctx.scope_id,))]
            rows = [self._record(db, user) for user in users]
        for row in rows:
            phone = row.pop('phone', '')
            row['phone_masked'] = phone[:3] + '****' + phone[-4:] if phone else ''
        return [r for r in rows if (status is None or r['status'] == status) and (group_id is None or int(group_id) in r['group_ids'])]

    def _touch(self, db, user_id, name=None, source='callback'):
        timestamp = stamp(self.clock())
        member = self._record(db, user_id)
        normalized = display_name(name, user_id) if name else None
        if member is None:
            db.execute('INSERT OR IGNORE INTO profiles(chat_id,user_id,name) VALUES(?,?,?)', (self.ctx.scope_id, user_id, normalized or ''))
            db.execute("INSERT INTO member_records(chat_id,user_id,status,name_source,created_at,updated_at) VALUES(?,?,'unregistered',?,?,?)",
                       (self.ctx.scope_id, user_id, source, timestamp, timestamp))
            member = self._record(db, user_id)
        # A later callback must not silently rename an identity already reviewed.
        callback_editable = member['status'] != 'approved' and member['name_source'] not in ('self', 'admin')
        if normalized and normalized != member['name'] and (callback_editable or source in ('self', 'admin')):
            db.execute('UPDATE profiles SET name=? WHERE chat_id=? AND user_id=?', (normalized, self.ctx.scope_id, user_id))
            if source == 'self':
                db.execute("UPDATE member_records SET status='pending',reviewed_at=NULL,review_note='' WHERE chat_id=? AND user_id=?", (self.ctx.scope_id, user_id))
            db.execute('UPDATE member_records SET name_source=?,updated_at=? WHERE chat_id=? AND user_id=?', (source, timestamp, self.ctx.scope_id, user_id))
            audit(db, self.ctx.actor, 'member.name', {'user_id': user_id, 'name': normalized, 'source': source, 'chat_id': self.ctx.scope_id})
        return self._record(db, user_id)

    def touch(self, name=None, user_id=None, source='callback'):
        user_id = self._subject(user_id)
        if source not in ('callback', 'self', 'admin'):
            raise ValueError('姓名来源无效')
        if source == 'admin':
            self._admin()
        with connect(self.path, True) as db:
            result = self._touch(db, user_id, name, source)
            # A profile's name source is not proof that its owner is present in
            # this group. Only a trusted callback/form for the acting person
            # may establish a real notification destination. Admin edits and
            # the simulator must not manufacture group membership evidence.
            if (self.ctx.source in ('callback', 'registration_form') and
                    user_id == self.ctx.actor and self.ctx.chat_id and self.ctx.id != 'workspace'):
                db.execute('''INSERT INTO identity_observations VALUES(?,?,?,?,?,?)
                    ON CONFLICT(scope_id,user_id,bot_id,chat_id) DO UPDATE SET last_seen_at=excluded.last_seen_at''',
                    (self.ctx.scope_id,user_id,self.ctx.id,self.ctx.chat_id,stamp(self.clock()),stamp(self.clock())))
            return result

    @staticmethod
    def _phone(phone):
        from .member_imports import normalize_phone
        return normalize_phone(phone)

    def apply(self, name, level=None, phone=None, db=None, *, user_id=None, source='self', gender=None):
        user_id = self._subject(user_id)
        if source == 'admin':
            self._admin()
        elif source != 'self' or user_id != self.ctx.actor:
            raise PermissionError('只能提交本人的注册申请')
        with self._db(db, True) as db:
            existing = self._record(db, user_id)
            if existing and existing['status'] == 'suspended':
                raise PermissionError('会员已停用，请联系管理员处理后再提交资料')
            # Registration is not a member-profile editor. Repeated submissions,
            # including those from another connected group, are read-only.
            if source == 'self' and existing and existing['status'] == 'approved':
                return existing | {'registration_result': 'already_registered'}
            gender = normalize_gender(gender, required=source == 'self')
            name = display_name(name, user_id)
            if level is not None:
                self._level(level)
            if phone is not None:
                phone = self._phone(phone)
            imported = None
            if phone:
                from .member_imports import registration_match
                imported = registration_match(db, self.ctx.scope_id, phone, user_id)
            if imported:
                return self._link_import(db, user_id, imported, gender)
            member = self._touch(db, user_id, name, source)
            db.execute('UPDATE member_records SET gender=? WHERE chat_id=? AND user_id=?', (gender, self.ctx.scope_id, user_id))
            if phone is not None:
                db.execute('UPDATE member_records SET phone=? WHERE chat_id=? AND user_id=?', (phone, self.ctx.scope_id, user_id))
            if level is not None and not (member['level_status'] == 'approved' and member['level'] == level):
                db.execute("UPDATE member_records SET requested_level=?,level_status='pending' WHERE chat_id=? AND user_id=?", (level, self.ctx.scope_id, user_id))
            if member['status'] != 'approved':
                db.execute("UPDATE member_records SET status='pending',updated_at=?,review_note='',reviewed_at=NULL WHERE chat_id=? AND user_id=?", (stamp(self.clock()), self.ctx.scope_id, user_id))
                audit(db, self.ctx.actor, 'member.apply', {'user_id': user_id, 'scope_id': self.ctx.scope_id, 'source': source})
                db.execute("UPDATE membership_applications SET status='superseded' WHERE scope_id=? AND user_id=? AND status='pending'", (self.ctx.scope_id,user_id))
                current = self._record(db, user_id)
                db.execute('''INSERT INTO membership_applications(scope_id,user_id,name,requested_level,phone,gender,submitted_at)
                    VALUES(?,?,?,?,?,?,?)''',(self.ctx.scope_id,user_id,current['name'],current.get('requested_level'),current.get('phone',''),gender,stamp(self.clock())))
            result = 'submitted' if member['status'] != 'approved' else 'already_registered'
            return self._record(db, user_id) | {'registration_result': result}

    def _link_import(self, db, user_id, imported, gender='unknown'):
        """Attach an imported, admin-reviewed profile in the caller's transaction."""
        before = self._record(db, user_id) or {'status': 'unregistered', 'name': ''}
        group_ids = self.validate_group_ids(json.loads(imported['group_ids']), db)
        timestamp = stamp(self.clock())
        # Keep imported labels even if the registration form supplied different
        # names or self-ratings. The submitted phone is an exact lookup key, not
        # a claim of SMS verification.
        self._touch(db, user_id, imported['name'], 'admin')
        gender = normalize_gender(imported.get('gender')) if imported.get('gender') in ('male', 'female') else gender
        level = imported['level']
        if level is not None:
            self._level(level)
        db.execute('''UPDATE member_records SET status='approved',phone=?,level=?,requested_level=?,gender=?,
            level_status=?,name_source='admin',review_note=?,level_note=?,reviewed_at=?,
            level_reviewed_at=?,updated_at=? WHERE chat_id=? AND user_id=?''',
            (imported['phone'],level,level,gender,'approved' if level is not None else 'none',
             '手机号与管理员预登记档案一致，已关联', '沿用管理员导入级别' if level is not None else '',
             timestamp,timestamp if level is not None else None,timestamp,self.ctx.scope_id,user_id))
        db.execute('DELETE FROM member_group_links WHERE chat_id=? AND user_id=?', (self.ctx.scope_id,user_id))
        db.executemany('INSERT INTO member_group_links(chat_id,user_id,group_id) VALUES(?,?,?)',
                       [(self.ctx.scope_id,user_id,gid) for gid in group_ids])
        changed = db.execute('''UPDATE pre_registered_members SET linked_user_id=?,linked_at=?
            WHERE id=? AND scope_id=? AND linked_user_id IS NULL''',
            (user_id,timestamp,imported['id'],self.ctx.scope_id)).rowcount
        if changed != 1:
            raise PermissionError('预登记档案已关联其他账号，请联系管理员核实')
        db.execute("UPDATE membership_applications SET status='superseded' WHERE scope_id=? AND user_id=? AND status='pending'",
                   (self.ctx.scope_id,user_id))
        db.execute('''INSERT INTO membership_applications(scope_id,user_id,name,requested_level,phone,gender,status,
            submitted_at,reviewed_at,reviewed_by,note) VALUES(?,?,?,?,?,?,'approved',?,?,?,?)''',
            (self.ctx.scope_id,user_id,imported['name'],level,imported['phone'],gender,timestamp,timestamp,
             imported['created_by'],'手机号精确关联管理员预登记档案'))
        audit(db,self.ctx.actor,'member.import_link',{'user_id':user_id,'scope_id':self.ctx.scope_id,'pre_registration_id':imported['id']})
        result = self._record(db,user_id)
        from .identity_notifications import record_identity_change
        record_identity_change(db, self.ctx, user_id, kind='membership',
                               before={key: before[key] for key in ('status', 'name')},
                               after={key: result[key] for key in ('status', 'name')}, at=self.clock())
        return result | {'registration_result':'linked'}

    def update_details(self, user_id, phone=None, requested_level=None, db=None, gender=None):
        self._admin()
        user_id = identifier(user_id)
        if requested_level is not None:
            self._level(requested_level)
        phone = self._phone(phone) if phone is not None else None
        with self._db(db, True) as db:
            member = self._record(db, user_id)
            if not member:
                raise ValueError('会员不存在')
            if gender is not None:
                gender = normalize_gender(gender)
                db.execute('UPDATE member_records SET gender=?,updated_at=? WHERE chat_id=? AND user_id=?', (gender, stamp(self.clock()), self.ctx.scope_id, user_id))
            if phone is not None:
                if phone:
                    from .member_imports import registration_match
                    if registration_match(db, self.ctx.scope_id, phone, user_id):
                        raise ValueError('该手机号属于尚未关联的预登记档案，请由本人通过注册关联；不能在资料编辑中占用')
                db.execute('UPDATE member_records SET phone=?,updated_at=? WHERE chat_id=? AND user_id=?', (phone, stamp(self.clock()), self.ctx.scope_id, user_id))
            if requested_level is not None and not (member['level_status'] == 'approved' and member['level'] == requested_level):
                db.execute("UPDATE member_records SET requested_level=?,level_status='pending',updated_at=? WHERE chat_id=? AND user_id=?", (requested_level, stamp(self.clock()), self.ctx.scope_id, user_id))
            audit(db, self.ctx.actor, 'member.details', {'user_id': user_id, 'phone_changed': phone is not None, 'gender_changed': gender is not None, 'gender': gender, 'requested_level': requested_level, 'chat_id': self.ctx.scope_id})
            return self._record(db, user_id)

    def set_gender(self, gender, user_id=None):
        user_id = self._subject(user_id)
        gender = normalize_gender(gender, required=not self.ctx.can('member.manage'))
        with connect(self.path, True) as db:
            member = self.require_approved(user_id, db=db, allow_admin=False, bot_access=False)
            db.execute('UPDATE member_records SET gender=?,updated_at=? WHERE chat_id=? AND user_id=?', (gender, stamp(self.clock()), self.ctx.scope_id, user_id))
            audit(db, self.ctx.actor, 'member.gender', {'user_id': user_id, 'before': member['gender'], 'gender': gender, 'chat_id': self.ctx.scope_id})
            return self._record(db, user_id)

    def review(self, user_id, status, name=None, note='', db=None):
        self._admin()
        user_id = identifier(user_id)
        if status not in ('unregistered', 'approved', 'rejected', 'pending', 'suspended'):
            raise ValueError('审核结果无效')
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError('审核备注最多500字')
        with self._db(db, True) as db:
            self.ctx.require('member.manage', db=db)
            before = self._record(db, user_id) or {'status': 'unregistered', 'name': '', 'roles': []}
            if before['roles'] and status != 'approved' and not self.ctx.can('roles.manage', db=db):
                raise PermissionError('变更管理角色人员的会员状态需要角色授权权限')
            if status == 'unregistered':
                current = self._record(db, user_id)
                if not current or current['status'] != 'unregistered':
                    raise ValueError('已提交申请的账号不能退回未注册；可退回申请或停用会员')
                return self._touch(db, user_id, name, 'admin')
            member = self._touch(db, user_id, name, 'admin')
            if status == 'approved':
                display_name(member['name'], user_id)
                # Repair only missing/ID-derived historical display labels, leaving
                # existing real names and the immutable imported evidence intact.
                db.execute('''UPDATE registrations SET name=? || CASE WHEN guest_index>0 THEN '+' || guest_index ELSE '' END
                              WHERE chat_id=? AND user_id=? AND guest_name='' AND (name='' OR name=user_id OR name=user_id || '+' || guest_index)''',
                           (member['name'], self.ctx.scope_id, user_id))
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tour_participants'").fetchone():
                    db.execute('''UPDATE tour_participants SET name=? || CASE WHEN guest_index>0 THEN '+' || guest_index ELSE '' END
                                  WHERE user_id=? AND tournament_id IN (SELECT id FROM tour_events WHERE chat_id=?)
                                  AND (name='' OR name=user_id OR name=user_id || '+' || guest_index)''',
                               (member['name'], user_id, self.ctx.scope_id))
            db.execute('UPDATE member_records SET status=?,review_note=?,reviewed_at=?,updated_at=? WHERE chat_id=? AND user_id=?',
                       (status, note, stamp(self.clock()), stamp(self.clock()), self.ctx.scope_id, user_id))
            if status in ('approved','rejected','suspended'):
                db.execute("UPDATE membership_applications SET status=?,reviewed_at=?,reviewed_by=?,note=? WHERE scope_id=? AND user_id=? AND status='pending'", (status,stamp(self.clock()),self.ctx.actor,note,self.ctx.scope_id,user_id))
            if status in ('rejected','suspended'):
                db.execute("DELETE FROM user_roles WHERE user_id=? AND scope_type='club' AND scope_id=?", (user_id,self.ctx.scope_id))
            audit(db, self.ctx.actor, 'member.review', {'user_id': user_id, 'status': status, 'note': note, 'chat_id': self.ctx.scope_id})
            result = self._record(db, user_id)
            from .identity_notifications import record_identity_change
            record_identity_change(db, self.ctx, user_id, kind='membership',
                                   before={key: before[key] for key in ('status', 'name')},
                                   after={key: result[key] for key in ('status', 'name')}, at=self.clock())
            record_identity_change(db, self.ctx, user_id, kind='roles',
                                   before={'roles': before['roles']}, after={'roles': result['roles']}, at=self.clock())
            return result

    def request_level(self, level):
        self._level(level)
        with connect(self.path, True) as db:
            member = self.require_approved(db=db, allow_admin=False, bot_access=False)
            if member['level_status'] == 'approved' and member['level'] == level:
                return member
            db.execute("UPDATE member_records SET requested_level=?,level_status='pending',level_note='',updated_at=? WHERE chat_id=? AND user_id=?", (level, stamp(self.clock()), self.ctx.scope_id, self.ctx.actor))
            audit(db, self.ctx.actor, 'member.level_request', {'level': level, 'chat_id': self.ctx.scope_id})
            return self._record(db, self.ctx.actor)

    @staticmethod
    def _level(level):
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 9:
            raise ValueError('等级须为1至9级整数')
        return level

    def review_level(self, user_id, approved, level=None, note=''):
        self._admin()
        user_id = identifier(user_id)
        if not isinstance(approved, bool):
            raise ValueError('等级审核结果须为布尔值')
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError('审核备注最多500字')
        with connect(self.path, True) as db:
            member = self.require_approved(user_id, db=db, allow_admin=False)
            accepted = self._level(level if level is not None else member['requested_level']) if approved else None
            db.execute('UPDATE member_records SET level=?,level_status=?,level_note=?,level_reviewed_at=?,updated_at=? WHERE chat_id=? AND user_id=?',
                       (accepted, 'approved' if approved else 'rejected', note, stamp(self.clock()), stamp(self.clock()), self.ctx.scope_id, user_id))
            audit(db, self.ctx.actor, 'member.level_review', {'user_id': user_id, 'approved': approved, 'level': accepted, 'chat_id': self.ctx.scope_id})
            result = self._record(db, user_id)
            from .identity_notifications import record_identity_change
            record_identity_change(db, self.ctx, user_id, kind='level',
                                   before={key: member[key] for key in ('level', 'level_status')},
                                   after={key: result[key] for key in ('level', 'level_status')}, at=self.clock())
            return result

    def groups(self):
        self._admin()
        with connect(self.path) as db:
            return [dict(r) for r in db.execute('SELECT id,name FROM member_groups WHERE chat_id=? ORDER BY id', (self.ctx.scope_id,))]

    def save_group(self, name, group_id=None):
        self._admin()
        name = re.sub(r'[\x00-\x1f\x7f]', '', str(name or '')).strip()
        if not name or len(name) > 60:
            raise ValueError('组名须为1至60字')
        with connect(self.path, True) as db:
            duplicate = db.execute('SELECT id FROM member_groups WHERE chat_id=? AND name=?', (self.ctx.scope_id, name)).fetchone()
            if duplicate and duplicate[0] != group_id:
                raise ValueError('分组名称已存在')
            if group_id is None:
                group_id = db.execute('INSERT INTO member_groups(chat_id,name,created_at) VALUES(?,?,?)', (self.ctx.scope_id, name, stamp(self.clock()))).lastrowid
            elif not db.execute('UPDATE member_groups SET name=? WHERE id=? AND chat_id=?', (name, group_id, self.ctx.scope_id)).rowcount:
                raise ValueError('会员分组不存在')
            audit(db, self.ctx.actor, 'member.group_save', {'id': group_id, 'name': name, 'chat_id': self.ctx.scope_id})
            return {'id': group_id, 'name': name}

    def validate_group_ids(self, group_ids, db=None):
        if not isinstance(group_ids, list) or len(group_ids) > 100 or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in group_ids):
            raise ValueError('分组须为有效分组编号列表')
        group_ids = sorted(set(group_ids))
        with self._db(db) as connection:
            available = {r[0] for r in connection.execute('SELECT id FROM member_groups WHERE chat_id=?', (self.ctx.scope_id,))}
        if set(group_ids) - available:
            raise ValueError('包含不存在的后台会员分组')
        return group_ids

    def set_groups(self, user_id, group_ids):
        self._admin()
        user_id = identifier(user_id)
        with connect(self.path, True) as db:
            before = self._record(db, user_id)
            if not before:
                raise ValueError('会员不存在')
            group_ids = self.validate_group_ids(group_ids, db)
            db.execute('DELETE FROM member_group_links WHERE chat_id=? AND user_id=?', (self.ctx.scope_id, user_id))
            db.executemany('INSERT INTO member_group_links(chat_id,user_id,group_id) VALUES(?,?,?)', [(self.ctx.scope_id, user_id, group) for group in group_ids])
            audit(db, self.ctx.actor, 'member.groups', {'user_id': user_id, 'group_ids': group_ids, 'chat_id': self.ctx.scope_id})
            result = self._record(db, user_id)
            from .identity_notifications import record_identity_change
            record_identity_change(db, self.ctx, user_id, kind='groups',
                                   before={'groups': before['groups']}, after={'groups': result['groups']}, at=self.clock())
            return result

    def require_approved(self, user_id=None, db=None, allow_admin=True, *, bot_access=True):
        user_id = identifier(user_id or self.ctx.actor)
        with self._db(db) as connection:
            if bot_access:
                from .bot_access import require_access
                require_access(self.ctx,self.clock,connection)
            member = self._record(connection, user_id)
            admin = self.ctx.can('roles.manage', db=connection)
        if allow_admin and admin and user_id == self.ctx.actor:
            return member
        if not member or not member['name'] or member['status'] == 'unregistered':
            raise PermissionError('请先发送 注册，打开本人注册表单；审核通过后即可使用会员功能')
        if member['status'] != 'approved':
            status = {'rejected':'未通过，请修改资料重新提交','suspended':'已停用，请联系管理员','pending':'待审核'}.get(member['status'],'尚未通过')
            raise PermissionError(f'会员资料{status}，请等待管理员审核后使用会员功能')
        return member

    def validate_rules(self, rules, db=None):
        if not isinstance(rules, dict) or set(rules) - {'min_level', 'max_level', 'allowed_group_ids'}:
            raise ValueError('报名限制字段无效')
        low, high = rules.get('min_level'), rules.get('max_level')
        if low is not None:
            self._level(low)
        if high is not None:
            self._level(high)
        if low is not None and high is not None and low > high:
            raise ValueError('最低等级不能高于最高等级')
        return {'min_level': low, 'max_level': high, 'allowed_group_ids': self.validate_group_ids(rules.get('allowed_group_ids', []), db)}

    def assert_eligible(self, user_id, session, guests=0, db=None):
        rules = {key: session.get(key) for key in ('min_level', 'max_level')}
        rules['allowed_group_ids'] = session.get('allowed_group_ids') or []
        if isinstance(rules['allowed_group_ids'], str):
            rules['allowed_group_ids'] = json.loads(rules['allowed_group_ids'])
        return self.assert_rules(user_id, rules, guests, db)

    def assert_rules(self, user_id, rules, guests=0, db=None):
        with self._db(db) as connection:
            member = self.require_approved(user_id, db=connection, allow_admin=False)
            rules = self.validate_rules(rules, connection)
            low, high, group_ids = rules['min_level'], rules['max_level'], rules['allowed_group_ids']
            if guests and (low is not None or high is not None or group_ids):
                raise PermissionError('本场活动有等级或组别限制，同行人员须自行实名注册、审核后独立报名')
            level = member['level']
            if (low is not None or high is not None) and level is None:
                raise PermissionError('本场活动有等级限制，您尚无已审核等级，请发送您的自评级别（1至9级，例如 自评6级）并等待审核')
            if low is not None and level < low:
                raise PermissionError(f'本场活动要求最低{low}级，您的已审核等级为{level}级')
            if high is not None and level > high:
                raise PermissionError(f'本场活动要求最高{high}级，您的已审核等级为{level}级')
            if group_ids and not set(group_ids) & set(member['group_ids']):
                placeholders = ','.join('?' for _ in group_ids)
                names = [r[0] for r in connection.execute(f'SELECT name FROM member_groups WHERE chat_id=? AND id IN ({placeholders})', [self.ctx.scope_id] + group_ids)]
                raise PermissionError('本场活动仅限以下组别：' + '、'.join(names))
            return member
