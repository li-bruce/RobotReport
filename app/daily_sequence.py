"""Dynamic daily roster coordination inside the registration write transaction."""
import json
from .formats.match_sequence import build
from .formats.common import group, sides


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _plan(engine, db, event):
    from .competitions import _participant
    people = [_participant(p) for p in db.execute(
        'SELECT * FROM tour_participants WHERE tournament_id=? ORDER BY registered_at,id', (event['id'],))]
    history = [m for m in engine._matches(db, event['id']) if m['status'] == 'completed']
    active = [p for p in people if p['status'] == 'active']
    plan = build(active, event['options'], event['seed'], history, event['plan'].get('sequence'))
    # Include departed people in standings so their completed results survive.
    plan['groups'] = [group('A', sides(people))]
    return plan


def _apply(engine, db, event, plan, actor, reason):
    before = engine._matches(db, event['id'])
    old_pending = [m for m in before if m['status'] != 'completed']
    start = max([m['number'] for m in before]+[event['plan'].get('sequence', {}).get('last_number', 0)])
    for offset, item in enumerate(plan['matches'], 1):
        item['number'] = start+offset
        item['round'] = plan['sequence']['batch']
    plan['sequence']['last_number'] = start+len(plan['matches'])
    db.execute("DELETE FROM tour_matches WHERE tournament_id=? AND status<>'completed'", (event['id'],))
    engine._insert_matches(db, event['id'], plan['matches'])
    stored = {**event['plan'], **{k: v for k, v in plan.items() if k not in ('groups', 'matches')}}
    db.execute("UPDATE tour_events SET groups_json=?,plan_json=?,champion_json='[]' WHERE id=?",
               (_dump(plan['groups']), _dump(stored), event['id']))
    engine._refresh(db, event['id'])
    engine._touch(db, event['id'])
    engine._audit(db, event['id'], actor, 'sequence.'+reason,
                  {'pending': old_pending}, {'numbers': [m['number'] for m in plan['matches']],
                    'active_ids': [p[0] for p in db.execute("SELECT id FROM tour_participants WHERE tournament_id=? AND status='active' ORDER BY id", (event['id'],))],
                    'sequence': plan['sequence']})


def replan(engine, db, event, actor, reason):
    plan = _plan(engine, db, event)
    _apply(engine, db, event, plan, actor, reason)
    return bool(plan['matches'])


def advance_batch(engine, db, event):
    plan = _plan(engine, db, event)
    if not plan['matches']:
        return False
    _apply(engine, db, event, plan, 'system', 'next_batch')
    return True


def sync_registration(db, service, session):
    """Only a live dynamic daily event follows the shared activity signup list."""
    from .service import session_time, registration_name
    from .competitions import TournamentService, _participant
    from .registration_queue import ranked_entries
    from .members import Members
    from .gender import eligible
    if not session_time(session, 'start') <= service.clock() < session_time(session, 'end'):
        return
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tour_events'").fetchone():
        return
    row = db.execute("""SELECT id FROM tour_events WHERE kind='daily' AND format='match_sequence'
        AND chat_id=? AND venue_id=? AND status IN ('running','paused')
        AND json_extract(options,'$.session_id')=?""", (service.ctx.scope_id, session['venue_id'], session['id'])).fetchone()
    if not row:
        return
    engine = TournamentService(service.path, initialize=False)
    event = engine._event(db, row[0])
    excluded = set(event['options'].get('excluded_participant_ids', []))
    old = {p['id']: _participant(p) for p in db.execute('SELECT * FROM tour_participants WHERE tournament_id=?', (event['id'],))}
    rows = db.execute("SELECT * FROM registrations WHERE session_id=? AND chat_id=? AND status='active' ORDER BY queue_seq,id",
                      (session['id'], service.ctx.scope_id)).fetchall()
    people = []
    members = Members(service.ctx, service.clock)
    for registration in ranked_entries(rows, session):
        if registration['category'] not in ('正式', '替补'):
            continue
        guest = registration['guest_index']
        pid = f"{registration['user_id']}:{guest}"
        if pid in excluded:
            continue
        try:
            profile = members.assert_eligible(registration['user_id'], session, int(guest > 0), db=db)
            members.assert_eligible(registration['user_id'], event['options'].get('eligibility', {}), int(guest > 0), db=db)
        except (PermissionError, ValueError):
            continue
        person = {'id': pid, 'user_id': registration['user_id'], 'guest_index': guest,
                  'name': registration_name(registration, profile['name']), 'level': 0 if guest else profile['level'] or 0,
                  'gender': 'unknown' if guest else profile['gender'], 'group_ids': [] if guest else profile['group_ids'],
                  'source_category': registration['category'], 'is_proxy': int(guest > 0)}
        if eligible(person, event['options'].get('doubles_category', 'open')):
            people.append(person)
    # Retain existing participants when a full roster gets another booking.
    people.sort(key=lambda p: (p['id'] not in old or old[p['id']]['status'] != 'active'))
    people = people[:40]
    ids = {p['id'] for p in people}
    if ids == {pid for pid, p in old.items() if p['status'] == 'active'}:
        return
    # A strict mixed event waits for a compatible gender-balanced roster;
    # ordinary doubles is the recommended dynamic mode.
    if event['options'].get('doubles_category') == 'mixed':
        men = [p for p in people if p['gender'] == 'male']
        women = [p for p in people if p['gender'] == 'female']
        size = min(len(men), len(women))
        people = men[:size]+women[:size]
        ids = {p['id'] for p in people}
    for pid, person in old.items():
        if person['status'] == 'active' and pid not in ids:
            db.execute("UPDATE tour_participants SET status='withdrawn',cancelled_at=? WHERE tournament_id=? AND id=?",
                       (service.clock().isoformat(), event['id'], pid))
    for person in people:
        if person['id'] in old:
            db.execute("UPDATE tour_participants SET status='active',cancelled_at=NULL WHERE tournament_id=? AND id=?", (event['id'], person['id']))
        else:
            db.execute('''INSERT INTO tour_participants(tournament_id,id,user_id,name,guest_index,registered_at,level,group_ids,source_category,is_proxy,gender)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (event['id'], person['id'], person['user_id'], person['name'], person['guest_index'],
                  service.clock().isoformat(), person['level'], _dump(person['group_ids']), person['source_category'], person['is_proxy'], person['gender']))
    replan(engine, db, engine._event(db, event['id']), service.ctx.actor, 'roster')
