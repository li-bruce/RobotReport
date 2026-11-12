"""One queue policy for rosters, calendars and competition participant selection."""


def ranked_entries(rows, session):
    entries = sorted((dict(row) for row in rows), key=lambda row: (row['queue_seq'], row['id']))
    active = [row for row in entries if row['status'] == 'active']
    started = dict(session).get('queue_started_at')
    formal = [row for row in active if (row.get('formal_admitted_at') if started else not row.get('substitute_only'))][:session['capacity']]
    formal_ids = {row['id'] for row in formal}
    reserve = [row for row in active if row['id'] not in formal_ids]
    result = []
    for index, row in enumerate(formal + reserve, 1):
        category = '正式' if row['id'] in formal_ids else ('替补' if index-len(formal) <= session['substitute_capacity'] else '候补')
        result.append(row | {'rank': index, 'category': category})
    return result + [row | {'rank': None, 'category': '已取消'} for row in entries if row['status'] != 'active']


def queue_counts(rows, session):
    entries = ranked_entries(rows, session)
    return {key: sum(row['category'] == label for row in entries)
            for key, label in [('formal_count','正式'), ('substitute_count','替补'), ('waitlist_count','候补')]}


def backfill_source_bots(db):
    """Recover only unambiguous booking origins; never guess across robots."""
    import json
    from collections import defaultdict
    events = defaultdict(list)
    for row in db.execute("SELECT detail,created_at FROM audit WHERE action='registration.enroll'"):
        try:
            detail = json.loads(row['detail'])
        except (ValueError, TypeError):
            continue
        if not isinstance(detail, dict) or not detail.get('substitute_only') or not detail.get('bot_id'):
            continue
        key = (detail.get('chat_id'), detail.get('session_id'), detail.get('user_id'))
        events[key].append((row['created_at'], detail['bot_id']))
    for row in db.execute('SELECT * FROM registrations WHERE substitute_only=1'):
        matches = events[(row['chat_id'], row['session_id'], row['user_id'])]
        exact = {bot for at, bot in matches if at in (row['created_at'], row['updated_at'])}
        candidates = exact or {bot for _, bot in matches}
        if len(candidates) == 1:
            db.execute('UPDATE registrations SET source_bot_id=? WHERE id=?', (candidates.pop(), row['id']))


def promotion_policies(platform_root):
    """Existing bookings follow their originating robot, regardless of the reader."""
    from .db import connect
    if not platform_root:
        return {}
    registry = platform_root / 'registry.sqlite3'
    if not registry.exists():
        return {}
    with connect(registry) as db:
        return {row['id']: bool(row['external_auto_promote']) for row in db.execute(
            "SELECT id,external_auto_promote FROM bots WHERE deleted_at=''")}


def promote_today(db, session, scope_id, at, platform_root=None):
    """Admit the oldest reserve into today's vacancies in the caller's transaction.

    Keep the booking's external origin and completed-day history unchanged.
    Existing formal reservations have priority when the day's queue starts.
    After that point, a new primary booking cannot jump an older eligible reserve.
    """
    from datetime import datetime
    from .db import TZ, audit, stamp
    at = at.astimezone(TZ)
    if session['date'] != at.date().isoformat() or at >= datetime.fromisoformat(session['date'] + 'T' + session['end']).replace(tzinfo=TZ):
        return []
    rows = [dict(row) for row in db.execute(
        "SELECT * FROM registrations WHERE session_id=? AND chat_id=? AND status='active' ORDER BY queue_seq,id",
        (session['id'], scope_id))]
    timestamp = stamp(at)
    if not session.get('queue_started_at'):
        existing_formal = [row for row in rows if not row['substitute_only']][:session['capacity']]
        for row in existing_formal:
            row['formal_admitted_at'] = timestamp
            db.execute('UPDATE registrations SET formal_admitted_at=? WHERE id=?', (timestamp, row['id']))
        db.execute('UPDATE sessions SET queue_started_at=? WHERE id=?', (timestamp, session['id']))
        session['queue_started_at'] = timestamp
    admitted = [row for row in rows if row['formal_admitted_at']]
    # A manually reduced capacity returns the excess entries to the same queue.
    for row in admitted[session['capacity']:]:
        db.execute("UPDATE registrations SET formal_admitted_at='' WHERE id=?", (row['id'],))
        row['formal_admitted_at'] = ''
    vacancies = max(0, session['capacity'] - min(len(admitted), session['capacity']))
    policies = promotion_policies(platform_root)
    promoted = [row for row in rows if not row['formal_admitted_at']
                and (not row['substitute_only'] or policies.get(row.get('source_bot_id'), False))][:vacancies]
    for row in promoted:
        db.execute('UPDATE registrations SET formal_admitted_at=? WHERE id=?', (timestamp, row['id']))
    if promoted:
        audit(db, 'system', 'registration.promote', {'session_id': session['id'], 'registration_ids': [row['id'] for row in promoted]})
    return [row['id'] for row in promoted]
