"""Preview and atomically record a shared validity period for selected members."""
import hashlib
import hmac
import re

from .db import connect, dumps, now
from .membership_terms import MembershipTerms, _day, _amount, _note
from .security import identifier


class MembershipBatch:
    def __init__(self, ctx, clock=now):
        self.ctx, self.terms = ctx, MembershipTerms(ctx, clock)

    def _input(self, data, *, applying=False):
        self.ctx.require('member.manage')
        allowed = {'user_ids', 'period'} | ({'expected_revision'} if applying else set())
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError('批量有效期字段无效')
        users, period = data.get('user_ids'), data.get('period')
        if not isinstance(users, list) or not 1 <= len(users) <= 200 or any(not isinstance(u, str) for u in users):
            raise ValueError('请勾选1至200位会员')
        users = sorted(identifier(u) for u in users)
        if len(users) != len(set(users)):
            raise ValueError('会员名单不能重复')
        if not isinstance(period, dict) or set(period) - {'start_date', 'end_date', 'amount', 'note'}:
            raise ValueError('会员有效期字段无效')
        start, end = _day(period.get('start_date')), _day(period.get('end_date'))
        if start > end:
            raise ValueError('有效期结束日期不能早于开始日期')
        cents = _amount(period.get('amount'))
        period = {'start_date': start, 'end_date': end, 'note': _note(period.get('note', '')),
                  'amount': None if cents is None else f'{cents // 100}.{cents % 100:02d}'}
        return users, period

    def _preview(self, db, users, period):
        rows, snapshot = [], []
        for user in users:
            member = db.execute('''SELECT m.status,p.name FROM member_records m
                LEFT JOIN profiles p ON p.chat_id=m.chat_id AND p.user_id=m.user_id
                WHERE m.chat_id=? AND m.user_id=?''', (self.ctx.scope_id, user)).fetchone()
            terms = self.terms._rows(db, user)
            valid = [row for row in terms if row['status'] == 'valid']
            error = ''
            if not member:
                error = '会员不存在'
            elif member['status'] not in ('approved', 'suspended'):
                error = '请先审核会员资料'
            elif any(row['start_date'] <= period['end_date'] and row['end_date'] >= period['start_date'] for row in valid):
                error = '与已有有效期重叠'
            rows.append({'user_id': user, 'name': member['name'] if member else '会员不存在',
                         'status': member['status'] if member else '', 'error': error,
                         'last_end_date': max((row['end_date'] for row in valid), default='')})
            snapshot.append({'user_id': user, 'member': dict(member) if member else None, 'terms': terms})
        revision = hashlib.sha256(dumps({'scope': self.ctx.scope_id, 'rows': snapshot, 'period': period}).encode()).hexdigest()
        return {'rows': rows, 'period': period, 'revision': revision, 'can_apply': all(not row['error'] for row in rows)}

    def preview(self, data):
        users, period = self._input(data)
        with connect(self.ctx.db_path) as db:
            db.execute('BEGIN')
            return self._preview(db, users, period)

    def apply(self, data):
        users, period = self._input(data, applying=True)
        expected = data.get('expected_revision')
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('请先预览批量有效期')
        with connect(self.ctx.db_path, True) as db:
            self.ctx.require('member.manage', db=db)
            preview = self._preview(db, users, period)
            if not hmac.compare_digest(expected, preview['revision']):
                raise ValueError('人员或有效期记录已变化，请重新预览')
            if not preview['can_apply']:
                raise ValueError('存在未审核或有效期冲突的人员，请调整名单后重试')
            records = [self.terms.create(user, period, db=db) for user in users]
        return {'count': len(records), 'periods': records}
