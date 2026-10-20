"""Reconcile imported people with existing accounts, preserving approved records."""

from .db import audit, stamp
from .member_matching import normalized


def name_candidate(db, scope, name):
    """Only an unambiguous, unbound entry can be offered for confirmation."""
    if not normalized(name):
        return None
    rows = [dict(r) for r in db.execute(
        'SELECT * FROM pre_registered_members WHERE scope_id=? AND merged_into_id IS NULL',
        (scope,)) if normalized(r['name']) == normalized(name)]
    return rows[0] if len(rows) == 1 and not rows[0]['linked_user_id'] else None


def existing_account(db, scope, record):
    from .member_imports import normalize_phone, phone_accounts
    name=normalized(record['name']);phone=normalize_phone(record['phone'])
    accounts=[dict(r) for r in db.execute("SELECT m.*,p.name FROM member_records m JOIN profiles p ON m.chat_id=p.chat_id AND m.user_id=p.user_id WHERE m.chat_id=?",(scope,)) if name and normalized(r['name'])==name]
    owners=phone_accounts(db,scope,phone)
    if len(accounts)>1 and phone:
        accounts=[r for r in accounts if any(o['user_id']==r['user_id'] for o in owners)]
    if not accounts:
        return None, '手机号相同，姓名不一致，需核实' if owners else '待本人在群里发送“注册确认”'
    if len(accounts)!=1:
        return None, '已有会员重名，需补手机号区分'
    account=accounts[0]
    if phone and (len(owners)>1 or any(o['user_id']!=account['user_id'] for o in owners)):
        return None, '手机号已属于其他账号，需核实'
    if phone and account['phone'] and normalize_phone(account['phone'])!=phone:
        return None, '姓名相同，手机号不一致，需核实'
    if account['status']!='approved':
        return None, '已有账号尚未通过会员审核'
    prior=db.execute('SELECT * FROM pre_registered_members WHERE scope_id=? AND linked_user_id=?',
                     (scope,account['user_id'])).fetchone()
    if prior and prior['id']!=record['id']:
        if (normalized(prior['name'])!=name or phone and prior['phone'] and normalize_phone(prior['phone'])!=phone):
            return None, '该账号已关联另一份名册，需核实'
        account['_canonical_id']=prior['id']
    account['_basis']='姓名、手机号一致' if phone and account['phone'] else '姓名唯一匹配已有会员'
    return account,''


def link_existing(db, ctx, record, at):
    account, reason = existing_account(db, ctx.scope_id, record)
    if not account:
        return False, reason
    if account.get('_canonical_id'):
        count=db.execute('''UPDATE pre_registered_members SET merged_into_id=?,linked_at=?
            WHERE scope_id=? AND id=? AND linked_user_id IS NULL AND merged_into_id IS NULL''',
            (account['_canonical_id'],stamp(at),ctx.scope_id,record['id'])).rowcount
    else:
        count = db.execute('''UPDATE pre_registered_members SET linked_user_id=?,linked_at=?
            WHERE scope_id=? AND id=? AND linked_user_id IS NULL AND merged_into_id IS NULL''',
            (account['user_id'], stamp(at), ctx.scope_id, record['id'])).rowcount
    if count:
        audit(db, ctx.actor, 'member_import.reconcile',
              {'id': record['id'], 'user_id': account['user_id'], 'basis': account['_basis']})
    return bool(count), ''


def confirm_existing_member(db, ctx, member, at):
    """An approved caller may reconcile their own unique imported name only."""
    from .member_imports import ensure_schema
    ensure_schema(db)
    candidate=name_candidate(db,ctx.scope_id,member['name'])
    if candidate:
        account,_=existing_account(db,ctx.scope_id,candidate)
        if account and account['user_id']==member['user_id']:
            link_existing(db,ctx,candidate,at)
