"""Registration review by a trusted member manager, shared by chat and AI."""
import re

from .db import connect, now
from .members import Members
from .messages import escape, table
from .dialogue import render, command, Markup


class MemberReview:
    def __init__(self, ctx, clock=now):
        self.ctx, self.clock = ctx, clock

    def _require(self):
        self.ctx.require('member.manage')
        Members(self.ctx, self.clock).require_approved()

    def _rows(self, db):
        # Return no phone numbers or internal review notes to a group conversation.
        return [dict(row) for row in db.execute('''
            SELECT a.id,a.user_id,a.name,a.requested_level,a.gender,a.submitted_at,
                   a.status,m.status AS member_status
            FROM membership_applications a
            JOIN member_records m ON m.chat_id=a.scope_id AND m.user_id=a.user_id
            WHERE a.scope_id=? AND a.id=(SELECT MAX(b.id) FROM membership_applications b
                WHERE b.scope_id=a.scope_id AND b.user_id=a.user_id)
            ORDER BY a.id DESC''', (self.ctx.scope_id,))]

    def pending(self):
        self._require()
        with connect(self.ctx.db_path) as db:
            return [row for row in self._rows(db) if row['status']=='pending' and row['member_status']=='pending']

    def describe_pending(self):
        rows = self.pending()
        if not rows:
            return render(self.ctx,'review.empty')
        result = '**待审核注册 · ' + str(len(rows)) + '人**\n\n' + table(
            ['申请编号','姓名','性别','自评级别','提交时间'],
            [[f"申请{r['id']}",r['name'],{'male':'男','female':'女'}.get(r['gender'],'待补齐'),
              str(r['requested_level'])+'级' if r['requested_level'] else '未填写',r['submitted_at'][:16].replace('T',' ')] for r in rows[:50]])
        if len(rows)>50:
            result += '\n\n仅显示最新50条，完整申请可在人员中心查看。'
        return render(self.ctx,'review.list',content=Markup(result),approve_command=command(self.ctx,'审核通过 申请12'),reject_command=command(self.ctx,'审核拒绝 申请12'))

    def resolve(self, member_name=None, application_id=None, *, decision=None, db=None):
        self._require()
        if db is None:
            with connect(self.ctx.db_path) as connection:
                return self.resolve(member_name, application_id, decision=decision, db=connection)
        if (application_id is None)==(not member_name):
            raise ValueError('请指定姓名或申请编号中的一种，例如：审核通过 申请12')
        rows = self._rows(db)
        if application_id is not None:
            if type(application_id) is not int or application_id<1:
                raise ValueError('申请编号无效')
            found=[row for row in rows if row['id']==application_id]
        else:
            if not isinstance(member_name,str) or not 1<=len(member_name.strip())<=80:
                raise ValueError('请提供完整姓名')
            found=[row for row in rows if row['name']==member_name.strip()]
            # Names never silently select one account, even if only one is pending.
            if len(found)>1:
                choices=table(['申请编号','姓名','申请状态'],[[f"申请{r['id']}",r['name'],
                    {'pending':'待审核','approved':'已通过','rejected':'已拒绝','superseded':'已更新'}.get(r['status'],r['status'])] for r in found[:20]])
                raise ValueError('存在同名申请，本次未操作，请用申请编号选择。\n\n'+choices)
        if not found:
            raise ValueError('未找到当前注册申请，请发送 待审核 查看申请编号；旧申请被重新提交后不能继续审核。')
        row=found[0]
        if row['status']=='pending' and row['member_status']=='pending':
            return row | {'already_reviewed':False}
        if decision in ('approved','rejected') and row['status']==row['member_status']==decision:
            return row | {'already_reviewed':True}
        raise ValueError('该申请已处理或会员状态已改变，本次未操作。停用、恢复等资料维护请在人员中心办理。')

    def review(self, decision, member_name=None, application_id=None):
        if decision not in ('approved','rejected'):
            raise ValueError('审核结果仅支持通过或拒绝')
        with connect(self.ctx.db_path, True) as db:
            row=self.resolve(member_name,application_id,decision=decision,db=db)
            label='通过' if decision=='approved' else '拒绝'
            if row['already_reviewed']:
                return render(self.ctx,'review.already',number=row['id'],name=row['name'],decision=label)
            Members(self.ctx,self.clock).review(row['user_id'],decision,db=db)
        return render(self.ctx,'review.done',number=row['id'],name=row['name'],decision=label)

    def handle(self, text):
        if text in ('待审核','待审批','审核列表','注册审核列表'):
            return self.describe_pending()
        match=re.fullmatch(r'(审核通过|通过审核|审核批准|审核同意|审核拒绝|拒绝审核|审核退回)(?:\s*[：:]?\s*(.*))?',text)
        if not match:
            return None
        self._require()
        target=(match[2] or '').strip()
        if not target:
            return render(self.ctx,'review.guide',approve_command=command(self.ctx,'审核通过 申请12'),pending_command=command(self.ctx,'待审核'))
        decision='rejected' if any(v in match[1] for v in ('拒绝','退回')) else 'approved'
        number=re.fullmatch(r'(?:申请|编号|申请编号)\s*[#＃]?\s*(\d+)',target)
        return self.review(decision,application_id=int(number[1])) if number else self.review(decision,member_name=target)
