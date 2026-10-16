"""Shared people directory: applications, club roles, and proxy guests."""
from .db import connect, audit, stamp
from .members import Members
from .security import identifier, ROLE_DEFINITIONS, PERMISSION_DEFINITIONS


class People(Members):
    def applications(self, user_id=None):
        if user_id is None:
            self._admin()
        else:
            user_id = self._subject(user_id)
        with connect(self.path) as db:
            sql = 'SELECT * FROM membership_applications WHERE scope_id=?'
            args = [self.ctx.scope_id]
            if user_id:
                sql += ' AND user_id=?'; args.append(user_id)
            rows = [dict(r) for r in db.execute(sql+' ORDER BY id DESC LIMIT 500', args)]
        for row in rows:
            phone = row.pop('phone','')
            row['phone_masked'] = phone[:3]+'****'+phone[-4:] if phone else ''
        return rows

    def set_roles(self, user_id, roles):
        self.ctx.require('roles.manage')
        user_id = identifier(user_id)
        valid_roles = {item['id'] for item in ROLE_DEFINITIONS}
        if not isinstance(roles, list) or any(not isinstance(role, str) or role not in valid_roles for role in roles):
            raise ValueError('后台角色无效；赛事职务请在对应赛事中配置')
        with connect(self.path,True) as db:
            self.ctx.require('roles.manage', db=db)
            before = self._record(db, user_id)
            if not before:
                raise ValueError('人员档案不存在')
            if roles:
                self.require_approved(user_id,db,allow_admin=False)
            db.execute("DELETE FROM user_roles WHERE user_id=? AND scope_type='club' AND scope_id=?", (user_id,self.ctx.scope_id))
            for role in set(roles):
                db.execute('INSERT INTO user_roles VALUES(?,?,?,?,?,?)',(user_id,role,'club',self.ctx.scope_id,stamp(self.clock()),self.ctx.actor))
            audit(db,self.ctx.actor,'member.roles',{'user_id':user_id,'roles':sorted(set(roles))})
            result = self._record(db,user_id)
            from .identity_notifications import record_identity_change
            record_identity_change(db, self.ctx, user_id, kind='roles',
                                   before={'roles': before['roles']}, after={'roles': result['roles']}, at=self.clock())
            return result

    def role_directory(self):
        self._admin()
        return {'definitions':[dict(item) for item in ROLE_DEFINITIONS],
                'permissions':[dict(item) for item in PERMISSION_DEFINITIONS],
                'assignments':[{'user_id':m['user_id'],'name':m['name'],'roles':m['roles'],'status':m['status']} for m in self.list() if m['roles']]}

    def guests(self):
        self._admin()
        with connect(self.path) as db:
            rows = [dict(r) for r in db.execute('''SELECT r.id AS registration_id,r.session_id,r.user_id AS sponsor_user_id,
                COALESCE(p.name,'待补姓名') AS sponsor_name,r.guest_name,r.name,r.guest_index,r.status,s.date,s.venue_id
                FROM registrations r JOIN sessions s ON s.id=r.session_id
                LEFT JOIN profiles p ON p.chat_id=r.chat_id AND p.user_id=r.user_id
                WHERE r.chat_id=? AND r.guest_index>0 ORDER BY s.date DESC,r.id DESC LIMIT 500''',(self.ctx.scope_id,))]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='tour_participants'").fetchone():
                rows.extend(dict(r) for r in db.execute('''SELECT p.tournament_id,e.title,e.date,e.venue_id,p.user_id AS sponsor_user_id,
                    COALESCE(n.name,'待补姓名') AS sponsor_name,p.name,p.guest_index,p.status
                    FROM tour_participants p JOIN tour_events e ON e.id=p.tournament_id
                    LEFT JOIN profiles n ON n.user_id=p.user_id AND n.chat_id=e.chat_id
                    WHERE e.chat_id=? AND p.guest_index>0 ORDER BY e.date DESC LIMIT 500''',(self.ctx.scope_id,)))
        for row in rows:
            row['name'] = row.get('guest_name') or row.get('name') or f"{row['sponsor_name']}+{row['guest_index']}"
            row['member'] = False
            row['source'] = '赛事代报' if row.get('tournament_id') else '活动代报'
            row['identity_kind'] = 'guest'
        return rows
