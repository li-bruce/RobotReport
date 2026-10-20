"""Self-service profile requests; approval and the resulting change commit together."""
import json
import re

from .db import connect, audit, dumps, now, stamp
from .members import Members, display_name
from .gender import normalize_gender
from .messages import escape, table


FIELDS = {'name': '姓名', 'gender': '性别', 'phone': '手机号', 'level': '等级', 'group_ids': '分组'}
STATUSES = {'pending': '待审核', 'approved': '已通过', 'rejected': '已退回',
            'cancelled': '已撤回', 'superseded': '已更新'}


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS member_profile_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,scope_id TEXT NOT NULL,user_id TEXT NOT NULL,
        member_name TEXT NOT NULL,before_json TEXT NOT NULL,changes_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',submitted_at TEXT NOT NULL,
        reviewed_at TEXT,reviewed_by TEXT,note TEXT NOT NULL DEFAULT '')''')
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_pending_profile_request ON member_profile_requests(scope_id,user_id) WHERE status='pending'")


class ProfileChanges:
    def __init__(self, ctx, clock=now):
        self.ctx, self.clock = ctx, clock
        self.members = Members(ctx, clock)
        with connect(ctx.db_path, True) as db:
            ensure_schema(db)

    def _manager(self, db=None):
        self.ctx.require('member.manage', db=db)
        self.members.require_approved(db=db)

    def groups(self, db=None):
        with self.members._db(db) as connection:
            self.members.require_approved(db=connection, allow_admin=False, bot_access=False)
            return [dict(r) for r in connection.execute('SELECT id,name FROM member_groups WHERE chat_id=? ORDER BY id', (self.ctx.scope_id,))]

    def _validate(self, changes, user_id, db):
        if not isinstance(changes, dict) or not changes or set(changes) - FIELDS.keys():
            raise ValueError('仅可申请修改姓名、性别、手机号、等级和分组')
        result = dict(changes)
        if 'name' in result:
            result['name'] = display_name(result['name'], user_id)
        if 'gender' in result:
            result['gender'] = normalize_gender(result['gender'], required=True)
        if 'level' in result and result['level'] is not None:
            result['level'] = Members._level(result['level'])
        if 'group_ids' in result:
            result['group_ids'] = self.members.validate_group_ids(result['group_ids'], db)
        if 'phone' in result:
            result['phone'] = Members._phone(result['phone'])
            if result['phone']:
                from .member_imports import registration_match
                if registration_match(db, self.ctx.scope_id, result['phone'], user_id):
                    raise ValueError('该手机号属于预登记档案，请联系会员管理员核实')
        return result

    @staticmethod
    def _public(row, groups):
        result = dict(row)
        result['before'] = json.loads(result.pop('before_json'))
        result['changes'] = json.loads(result.pop('changes_json'))
        # Chat, list and status responses never reveal a full phone number.
        for values in (result['before'], result['changes']):
            if values.get('phone'):
                phone = values['phone']
                values['phone'] = phone[:3] + '****' + phone[-4:]
        result['items'] = [{'field': key, 'label': FIELDS[key],
                            'before': ProfileChanges._display(key, result['before'].get(key), groups),
                            'after': ProfileChanges._display(key, value, groups)}
                           for key, value in result['changes'].items()]
        return result

    @staticmethod
    def _display(key, value, groups):
        if key == 'group_ids':
            return '、'.join(groups.get(i, '已移除分组') for i in value or []) or '未分组'
        if key == 'gender':
            return {'male': '男', 'female': '女'}.get(value, '待补齐')
        if key == 'level':
            return f'{value}级' if value else '无级别'
        return str(value or '未填写')

    def _view(self, row, db):
        groups = {r['id']: r['name'] for r in db.execute('SELECT id,name FROM member_groups WHERE chat_id=?', (self.ctx.scope_id,))}
        return self._public(row, groups)

    def list(self, *, all_users=False, status=None, user_id=None):
        if status is not None and status not in STATUSES:
            raise ValueError('申请状态无效')
        if user_id is not None:
            from .security import identifier
            user_id=identifier(user_id)
            if not all_users and user_id!=self.ctx.actor:raise PermissionError('只能查看本人的资料申请')
        with connect(self.ctx.db_path) as db:
            if all_users:
                self._manager(db)
            else:
                self.members.require_approved(db=db, allow_admin=False, bot_access=False)
            rows = db.execute('''SELECT * FROM member_profile_requests WHERE scope_id=?
                AND (? OR user_id=?) AND (? IS NULL OR status=?) AND (? IS NULL OR user_id=?)
                ORDER BY id DESC LIMIT 1000''',
                (self.ctx.scope_id, int(all_users), self.ctx.actor, status, status,user_id,user_id))
            return [self._view(r, db) for r in rows]

    def submit(self, changes, db=None):
        with self.members._db(db, write=True) as connection:
            member = self.members.require_approved(db=connection, allow_admin=False, bot_access=False)
            changes = self._validate(changes, self.ctx.actor, connection)
            old = connection.execute("SELECT * FROM member_profile_requests WHERE scope_id=? AND user_id=? AND status='pending'", (self.ctx.scope_id, self.ctx.actor)).fetchone()
            combined = (json.loads(old['changes_json']) if old else {}) | changes
            combined = self._validate(combined, self.ctx.actor, connection)
            combined = {key: value for key, value in combined.items() if member.get(key) != value}
            if not combined:
                raise ValueError('申请内容与当前资料一致，无需修改；已有申请可发送 取消资料申请 撤回')
            before = {key: member.get(key) for key in combined}
            if 'level' in combined:
                before.update({key: member.get(key) for key in ('requested_level', 'level_status', 'level_reviewed_at')})
            if old and json.loads(old['changes_json']) == combined and json.loads(old['before_json']) == before:
                return self._view(old, connection)
            if old:
                connection.execute("UPDATE member_profile_requests SET status='superseded' WHERE id=?", (old['id'],))
            request_id = connection.execute('''INSERT INTO member_profile_requests
                (scope_id,user_id,member_name,before_json,changes_json,submitted_at) VALUES(?,?,?,?,?,?)''',
                (self.ctx.scope_id, self.ctx.actor, member['name'], dumps(before), dumps(combined), stamp(self.clock()))).lastrowid
            audit(connection, self.ctx.actor, 'member.profile_request', {'request_id': request_id, 'fields': sorted(combined), 'scope_id': self.ctx.scope_id})
            from .identity_notifications import remember_request_source
            remember_request_source(connection, self.ctx, self.ctx.actor, 'profile_request', self.clock())
            return self._view(connection.execute('SELECT * FROM member_profile_requests WHERE id=?', (request_id,)).fetchone(), connection)

    def cancel(self):
        with connect(self.ctx.db_path, True) as db:
            self.members.require_approved(db=db, allow_admin=False, bot_access=False)
            changed = db.execute("UPDATE member_profile_requests SET status='cancelled',reviewed_at=?,reviewed_by=? WHERE scope_id=? AND user_id=? AND status='pending'", (stamp(self.clock()), self.ctx.actor, self.ctx.scope_id, self.ctx.actor)).rowcount
            if changed:
                audit(db, self.ctx.actor, 'member.profile_cancel', {'scope_id': self.ctx.scope_id})
        return '已撤回本人的资料申请。' if changed else '没有待审核的资料申请。'

    def review(self, request_id, approved, note=''):
        if type(request_id) is not int or request_id < 1 or type(approved) is not bool:
            raise ValueError('请指定资料申请编号和审核结果')
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError('审核备注最多500字')
        with connect(self.ctx.db_path, True) as db:
            self._manager(db)
            row = db.execute('SELECT * FROM member_profile_requests WHERE id=? AND scope_id=?', (request_id, self.ctx.scope_id)).fetchone()
            if not row:
                raise ValueError('资料申请不存在')
            decision = 'approved' if approved else 'rejected'
            if row['status'] == decision:
                return self._view(row, db) | {'already_reviewed': True}
            if row['status'] != 'pending':
                raise ValueError('申请已处理或已被新申请替换，请刷新列表')
            if row['user_id'] == self.ctx.actor:
                raise PermissionError('不能审核本人的资料调整申请，请由另一位会员管理员审核')
            member = self.members.require_approved(row['user_id'], db=db, allow_admin=False, bot_access=False)
            changes, before = json.loads(row['changes_json']), json.loads(row['before_json'])
            if approved:
                if any(member.get(key) != value for key, value in before.items()):
                    raise ValueError('申请涉及的资料已被修改，请退回后由会员重新申请')
                changes = self._validate(changes, row['user_id'], db)
                if 'name' in changes:
                    db.execute('UPDATE profiles SET name=? WHERE chat_id=? AND user_id=?', (changes['name'], self.ctx.scope_id, row['user_id']))
                    db.execute("UPDATE member_records SET name_source='admin' WHERE chat_id=? AND user_id=?", (self.ctx.scope_id, row['user_id']))
                for key in ('phone', 'gender'):
                    if key in changes:
                        db.execute(f'UPDATE member_records SET {key}=? WHERE chat_id=? AND user_id=?', (changes[key], self.ctx.scope_id, row['user_id']))
                if 'level' in changes:
                    db.execute("UPDATE member_records SET level=?,requested_level=?,level_status=?,level_note=?,level_reviewed_at=? WHERE chat_id=? AND user_id=?", (changes['level'], changes['level'], 'approved' if changes['level'] else 'none', note, stamp(self.clock()), self.ctx.scope_id, row['user_id']))
                if 'group_ids' in changes:
                    db.execute('DELETE FROM member_group_links WHERE chat_id=? AND user_id=?', (self.ctx.scope_id, row['user_id']))
                    db.executemany('INSERT INTO member_group_links(chat_id,user_id,group_id) VALUES(?,?,?)', [(self.ctx.scope_id, row['user_id'], group) for group in changes['group_ids']])
                db.execute('UPDATE member_records SET updated_at=? WHERE chat_id=? AND user_id=?', (stamp(self.clock()), self.ctx.scope_id, row['user_id']))
            db.execute('UPDATE member_profile_requests SET status=?,reviewed_at=?,reviewed_by=?,note=? WHERE id=?', (decision, stamp(self.clock()), self.ctx.actor, note, request_id))
            audit(db, self.ctx.actor, 'member.profile_review', {'request_id': request_id, 'approved': approved, 'fields': sorted(changes), 'scope_id': self.ctx.scope_id})
            from .identity_notifications import record_identity_change
            record_identity_change(db, self.ctx, row['user_id'], 'profile_request',
                {'request_id': request_id, 'status': 'pending'},
                {'request_id': request_id, 'status': decision, 'fields': [FIELDS[k] for k in changes]}, self.clock())
            return self._view(db.execute('SELECT * FROM member_profile_requests WHERE id=?', (request_id,)).fetchone(), db)

    @staticmethod
    def describe(row):
        return f"**资料申请{row['id']} · {STATUSES[row['status']]}**\n\n" + table(['项目', '当前资料', '申请调整为'], [[r['label'], r['before'], r['after']] for r in row['items']])

    def handle(self, text):
        if text in ('修改资料', '申请修改资料', '修改个人信息', '申请调整资料'):
            self.members.require_approved(allow_admin=False, bot_access=False)
            from .registration import create_form
            url = create_form(self.ctx, self.clock, purpose='profile')
            return '[填写资料调整申请](' + url + ')' if url else '发送：申请分组 提高组 / 申请等级 5级。'
        if text in ('可选分组', '分组列表'):
            groups = self.groups()
            return '**可申请分组**\n\n' + ('、'.join(escape(g['name']) for g in groups) or '暂无分组，请联系管理员。')
        if text in ('我的资料申请', '资料申请进度'):
            rows = self.list()
            return '\n\n'.join(self.describe(r) for r in rows[:5]) if rows else '尚未提交资料调整申请。发送 修改资料 开始。'
        if text == '取消资料申请':
            return self.cancel()
        match = re.fullmatch(r'(?:申请|修改|调整)分组\s*[:：]?\s*(.+)', text)
        if match:
            names = [v.strip() for v in re.split(r'[,，、;；]', match[1]) if v.strip()]
            available = {g['name']: g['id'] for g in self.groups()}
            if names != ['无'] and (not names or set(names) - available.keys()):
                raise ValueError('分组名称未找到，请发送 可选分组；多个分组用逗号分隔')
            row = self.submit({'group_ids': [] if names == ['无'] else [available[n] for n in names]})
            return self.describe(row) + '\n\n等待会员管理员审核；申请分组为完整名单。'
        match = re.fullmatch(r'(?:申请|修改|调整)等级\s*[:：]?\s*(\d+|[一二三四五六七八九])\s*级?', text)
        if match:
            level = int(match[1]) if match[1].isdigit() else '一二三四五六七八九'.index(match[1]) + 1
            return self.describe(self.submit({'level': level})) + '\n\n等待会员管理员审核。'
        if text in ('资料待审核', '资料审核列表'):
            rows = self.list(all_users=True, status='pending')
            return ('**待审核资料**\n\n' + table(['资料申请', '姓名', '修改项目'], [[f"资料申请{r['id']}", r['member_name'], '、'.join(i['label'] for i in r['items'])] for r in rows]) + '\n\n发送 资料查看 申请12 / 资料通过 申请12 / 资料拒绝 申请12。') if rows else '目前没有待审核的资料调整申请。'
        match = re.fullmatch(r'资料(查看|通过|拒绝)\s*(?:资料)?(?:申请)?\s*(\d+)', text)
        if match:
            if match[1] == '查看':
                row = next((r for r in self.list(all_users=True) if r['id'] == int(match[2])), None)
                if not row:
                    raise ValueError('资料申请不存在')
                return self.describe(row)
            row = self.review(int(match[2]), match[1] == '通过')
            return self.describe(row) + '\n\n' + ('该申请已处理，无需重复操作。' if row.get('already_reviewed') else '审核结果将通知本人。')
        return None
