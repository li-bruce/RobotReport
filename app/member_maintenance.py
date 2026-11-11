"""Explicit offline maintenance; preview, backup and a matching digest are required.

Not exposed through a bot or HTTP route. The caller owns the database transaction.
"""
import hashlib
import json
from .db import dumps, stamp


def preview(db, scope='club'):
    groups = {r['name']: r['id'] for r in db.execute(
        'SELECT id,name FROM member_groups WHERE chat_id=?', (scope,))}
    if not {'一组', '二组'} <= groups.keys():
        raise ValueError('请先创建一组和二组')
    targets = {'male': groups['一组'], 'female': groups['二组']}
    managed = set(targets.values())
    members = [dict(r) for r in db.execute('SELECT user_id,gender FROM member_records WHERE chat_id=? ORDER BY user_id', (scope,))]
    gender_by_user = {m['user_id']: m['gender'] for m in members}
    changes, imported = [], []
    for member in members:
        current = sorted(r[0] for r in db.execute('SELECT group_id FROM member_group_links WHERE chat_id=? AND user_id=?', (scope, member['user_id'])))
        if member['gender'] not in targets:
            continue
        desired = sorted((set(current) - managed) | {targets[member['gender']]})
        if current != desired:
            changes.append({'user_id': member['user_id'], 'before': current, 'after': desired})
    for row in db.execute('SELECT * FROM pre_registered_members WHERE scope_id=? AND merged_into_id IS NULL ORDER BY id', (scope,)):
        gender = gender_by_user.get(row['linked_user_id'], row['gender'])
        if gender not in targets:
            continue
        current = json.loads(row['group_ids'])
        desired = sorted((set(current) - managed) | {targets[gender]})
        if sorted(current) != desired:
            imported.append({'id': row['id'], 'before': current, 'after': desired})
    guests = [dict(r) for r in db.execute('SELECT * FROM registrations WHERE chat_id=? AND guest_index>0 ORDER BY id', (scope,))]
    plan = {'scope': scope, 'members': changes, 'imports': imported, 'guest_ids': [r['id'] for r in guests]}
    # Include complete targeted registrations so a changed booking cannot be
    # deleted under an old preview. No names are returned in the public summary.
    fingerprint = {'plan': plan, 'guest_rows': guests, 'genders': members}
    digest = hashlib.sha256(dumps(fingerprint).encode()).hexdigest()
    return plan | {'digest': digest, 'summary': {
        'members_changed': len(changes), 'imported_changed': len(imported),
        'guests_deleted': len(guests), 'male_members': sum(m['gender']=='male' for m in members),
        'female_members': sum(m['gender']=='female' for m in members),
        'unknown_gender_unchanged': sum(m['gender'] not in targets for m in members)}}


def apply(db, expected_digest, backup_sha256, scope='club'):
    if not db.in_transaction:
        raise ValueError('数据整理必须在事务中执行')
    if len(backup_sha256) != 64 or any(c not in '0123456789abcdef' for c in backup_sha256):
        raise ValueError('需要已校验的恢复备份')
    plan = preview(db, scope)
    if plan['digest'] != expected_digest:
        raise ValueError('资料或报名已变化，请重新预览')
    for item in plan['members']:
        db.execute('DELETE FROM member_group_links WHERE chat_id=? AND user_id=?', (scope, item['user_id']))
        db.executemany('INSERT INTO member_group_links(chat_id,user_id,group_id) VALUES(?,?,?)',
                       [(scope, item['user_id'], g) for g in item['after']])
    for item in plan['imports']:
        db.execute('UPDATE pre_registered_members SET group_ids=? WHERE id=? AND scope_id=?', (dumps(item['after']), item['id'], scope))
    for guest_id in plan['guest_ids']:
        db.execute('DELETE FROM imports WHERE registration_id=?', (guest_id,))
        db.execute('DELETE FROM registrations WHERE id=? AND chat_id=? AND guest_index>0', (guest_id, scope))
    db.execute('INSERT INTO audit(actor,action,detail,created_at) VALUES(?,?,?,?)',
               ('offline-admin', 'maintenance.gender_groups_and_clear_guests',
                dumps({'summary':plan['summary'], 'preview_sha256':expected_digest, 'backup_sha256':backup_sha256}), stamp()))
    return plan['summary']
