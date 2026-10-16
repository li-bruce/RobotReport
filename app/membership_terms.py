"""Append-only member validity periods; payment recording is not payment processing."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from .db import TZ, connect, dumps, now, stamp
from .security import BotContext, identifier


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('会员有效期日期格式为 YYYY-MM-DD')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError('会员有效期日期无效') from None


def _note(value):
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError('备注须为不超过1000字的文字')
    return value.strip()


def _amount(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('金额请以元为单位填写，最多两位小数')
    text = str(value).strip()
    if not re.fullmatch(r'\d{1,9}(?:\.\d{1,2})?', text):
        raise ValueError('金额须为非负数，最多两位小数')
    try:
        return int(Decimal(text) * 100)
    except InvalidOperation:
        raise ValueError('金额无效') from None


class MembershipTerms:
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

    def _subject(self, user_id=None):
        user_id = identifier(user_id or self.ctx.actor)
        if not self.ctx.can('member.manage') and user_id != self.ctx.actor:
            raise PermissionError('只能查询本人的会员有效期')
        return user_id

    def _admin(self):
        self.ctx.require('member.manage')

    def _member(self, db, user_id):
        row = db.execute('SELECT * FROM member_records WHERE chat_id=? AND user_id=?',
                         (self.ctx.scope_id, user_id)).fetchone()
        if not row:
            raise ValueError('会员不存在')
        return dict(row)

    @staticmethod
    def _record(row):
        result = dict(row)
        result.pop('scope_id', None)
        cents = result['amount_cents']
        result['amount'] = f'{cents // 100}.{cents % 100:02d}' if cents is not None else None
        return result

    def _rows(self, db, user_id):
        return [self._record(r) for r in db.execute(
            'SELECT * FROM membership_terms WHERE scope_id=? AND user_id=? ORDER BY start_date DESC,id DESC',
            (self.ctx.scope_id, user_id))]

    def list(self, user_id=None, db=None):
        user_id = self._subject(user_id)
        with self._db(db) as connection:
            self._member(connection, user_id)
            return self._rows(connection, user_id)

    def application_history(self, user_id=None, db=None):
        user_id = self._subject(user_id)
        with self._db(db) as connection:
            self._member(connection, user_id)
            return [dict(row) for row in connection.execute(
                '''SELECT id,name,requested_level,gender,status,submitted_at,reviewed_at,reviewed_by,note
                   FROM membership_applications WHERE scope_id=? AND user_id=? ORDER BY id DESC''',
                (self.ctx.scope_id, user_id))]

    def summary(self, user_id=None, at_date=None, db=None, *, trusted=False):
        # Members._record already controls access and also reads other participants
        # internally, so it may request the same summary without a second subject gate.
        user_id = identifier(user_id or self.ctx.actor) if trusted else self._subject(user_id)
        moment = self.clock()
        today = moment.astimezone(TZ).date().isoformat() if moment.tzinfo else moment.replace(tzinfo=TZ).date().isoformat()
        day = _day(at_date) if at_date is not None else today
        with self._db(db) as connection:
            member = self._member(connection, user_id)
            rows = self._rows(connection, user_id)
            first_application = connection.execute('''SELECT MIN(submitted_at),
                MIN(CASE WHEN status='approved' THEN reviewed_at END) FROM membership_applications
                WHERE scope_id=? AND user_id=?''', (self.ctx.scope_id, user_id)).fetchone()
        valid = [r for r in rows if r['status'] == 'valid']
        current = next((r for r in valid if r['start_date'] <= day <= r['end_date']), None)
        future = sorted((r for r in valid if r['start_date'] > day), key=lambda r: (r['start_date'], r['id']))
        past = [r for r in valid if r['end_date'] < day]
        state = ('not_configured' if not rows else 'active' if current else
                 'gap' if future and past else 'upcoming' if future else 'expired' if past else 'inactive')
        labels = {'not_configured': '待配置有效期', 'active': '有效', 'gap': '续费间隔期',
                  'upcoming': '尚未生效', 'expired': '已到期', 'inactive': '无有效期限'}
        return {'configured': bool(rows), 'status': state, 'status_label': labels[state], 'as_of': day,
                'current_term': current, 'next_term': future[0] if future else None,
                'last_end_date': max((r['end_date'] for r in past), default=None),
                'can_enroll': member['status'] == 'approved' and state in ('active', 'not_configured'),
                'period_count': len(valid), 'record_count': len(rows),
                'registration': {'created_at': member['created_at'], 'reviewed_at': member['reviewed_at'],
                                 'first_applied_at': first_application[0], 'first_approved_at': first_application[1],
                                 'status': member['status']}}

    def _audit(self, db, user_id, term_id, action, detail):
        timestamp = stamp(self.clock())
        db.execute('''INSERT INTO membership_term_audit(scope_id,user_id,term_id,actor,action,detail,created_at)
                      VALUES(?,?,?,?,?,?,?)''',
                   (self.ctx.scope_id, user_id, term_id, self.ctx.actor, action, dumps(detail), timestamp))
        db.execute('INSERT INTO audit(actor,action,detail,created_at) VALUES(?,?,?,?)',
                   (self.ctx.actor, 'membership.' + action,
                    dumps({'scope_id': self.ctx.scope_id, 'user_id': user_id, 'term_id': term_id, **detail}), timestamp))

    def create(self, user_id, data, db=None):
        self._admin()
        user_id = self._subject(user_id)
        if not isinstance(data, dict) or set(data) - {'start_date', 'end_date', 'amount', 'note'}:
            raise ValueError('会员有效期字段无效')
        start, end = _day(data.get('start_date')), _day(data.get('end_date'))
        if start > end:
            raise ValueError('有效期结束日期不能早于开始日期')
        amount, note = _amount(data.get('amount')), _note(data.get('note', ''))
        with self._db(db, True) as connection:
            member = self._member(connection, user_id)
            if member['status'] not in ('approved', 'suspended'):
                raise ValueError('请先审核会员资料，再登记有效期')
            if connection.execute('''SELECT 1 FROM membership_terms WHERE scope_id=? AND user_id=?
                AND status='valid' AND start_date<=? AND end_date>=?''',
                (self.ctx.scope_id, user_id, end, start)).fetchone():
                raise ValueError('此有效期与已有记录重叠；相邻续费请从原有效期结束的次日开始')
            cur = connection.execute('''INSERT INTO membership_terms
                (scope_id,user_id,start_date,end_date,amount_cents,note,created_at,created_by)
                VALUES(?,?,?,?,?,?,?,?)''',
                (self.ctx.scope_id, user_id, start, end, amount, note, stamp(self.clock()), self.ctx.actor))
            self._audit(connection, user_id, cur.lastrowid, 'term.add',
                        {'start_date': start, 'end_date': end, 'amount_cents': amount, 'note': note})
            return self._record(connection.execute('SELECT * FROM membership_terms WHERE id=?', (cur.lastrowid,)).fetchone())

    def void(self, user_id, term_id, expected_version, note='', db=None):
        self._admin()
        user_id = self._subject(user_id)
        if isinstance(term_id, bool) or not isinstance(term_id, int) or term_id <= 0:
            raise ValueError('会员有效期记录编号无效')
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError('作废需提交当前记录版本，请刷新后重试')
        note = _note(note)
        with self._db(db, True) as connection:
            self._member(connection, user_id)
            row = connection.execute('SELECT * FROM membership_terms WHERE id=? AND scope_id=? AND user_id=?',
                                     (term_id, self.ctx.scope_id, user_id)).fetchone()
            if not row:
                raise ValueError('会员有效期记录不存在')
            if row['version'] != expected_version:
                raise ValueError('会员有效期记录已变化，请刷新后重试')
            if row['status'] == 'void':
                raise ValueError('此记录已作废')
            connection.execute('''UPDATE membership_terms SET status='void',version=version+1,
                voided_at=?,voided_by=?,void_note=? WHERE id=?''',
                (stamp(self.clock()), self.ctx.actor, note, term_id))
            self._audit(connection, user_id, term_id, 'term.void', {'note': note, 'previous_version': expected_version})
            return self._record(connection.execute('SELECT * FROM membership_terms WHERE id=?', (term_id,)).fetchone())

    def require_enrollment(self, user_id, event_date, db=None):
        user_id = identifier(user_id)
        event_date = _day(event_date)
        with self._db(db) as connection:
            current = self.summary(user_id, db=connection, trusted=True)
            if not current['configured']:
                return current
            if current['status'] != 'active':
                raise PermissionError(f"会员{current['status_label']}，暂不能新报名；请联系管理员续费。历史记录仍可查询")
            event = self.summary(user_id, at_date=event_date, db=connection, trusted=True)
            if event['status'] != 'active':
                raise PermissionError(f'会员有效期未覆盖活动日期 {event_date}，请续费后再报名；历史记录仍可查询')
            return event
