"""Stable activity identities and additive historical snapshots."""
import json
import hashlib
from dataclasses import replace
from .db import connect, dumps, stamp
from .bot_capabilities import normalize_capabilities


def intersect(left, right):
    if left is None:
        return None if right is None else tuple(right)
    if right is None:
        return tuple(left)
    return tuple(v for v in left if v in right)


def refresh_context(ctx):
    if not ctx.platform_root or ctx.id == 'workspace':
        return ctx
    with connect(ctx.platform_root / 'registry.sqlite3') as db:
        bot = db.execute('SELECT * FROM bots WHERE id=?', (ctx.id,)).fetchone()
        if not bot or not bot['active']:
            raise PermissionError('机器人已停用')
        bot_venues = json.loads(bot['venue_ids']) if 'venue_ids' in bot.keys() else None
        bot_slots = json.loads(bot['slot_keys']) if 'slot_keys' in bot.keys() else None
        capabilities = normalize_capabilities(json.loads(bot['capabilities']) if 'capabilities' in bot.keys() else None)
        channel = db.execute('SELECT * FROM bot_channels WHERE id=?', (ctx.channel_id,)).fetchone() if ctx.channel_id else None
        if ctx.channel_id and (not channel or not channel['active'] or ('archived' in channel.keys() and channel['archived']) or channel['bot_id'] != ctx.id or channel['chat_id'] != ctx.chat_id):
            raise PermissionError('群通道已停用或绑定已变更')
        # Scope belongs to the robot. Old channel scope columns remain historical data.
        venues = None if bot_venues is None else tuple(bot_venues)
        slots = None if bot_slots is None else tuple(bot_slots)
        default = venues[0] if venues else 1
        if venues and default not in venues:
            default = venues[0]
        if slots:
            # A bot serving only a time slot at venue 2 should use that venue
            # for plain "报名", even if the group's old default was venue 1.
            with connect(ctx.db_path) as business:
                slot_venues = {r['venue_id'] for r in business.execute('SELECT id,venue_id FROM slots') if 'slot:'+str(r['id']) in slots}
                for row in business.execute('SELECT * FROM special_dates'):
                    if any(rule['slot_key'] in slots for rule in special_rules(row['venue_id'],row['date'],json.loads(row['slots']))):
                        slot_venues.add(row['venue_id'])
                from .holidays import all_slots as holiday_slots
                for venue in business.execute('SELECT id FROM venues'):
                    if any(rule['slot_key'] in slots for rule in holiday_slots(business,venue[0])):
                        slot_venues.add(venue[0])
            candidates = sorted(v for v in slot_venues if venues is None or v in venues)
            if candidates and default not in candidates:
                default = candidates[0]
        return replace(ctx, venue_ids=venues or (), venue_restricted=venues is not None, slot_keys=slots,
                       default_venue_id=default, capabilities=capabilities, bot_name=bot['name'],
                       capability_rules=tuple(json.loads(bot['capability_rules'])) if 'capability_rules' in bot.keys() else (),
                       receive_commands=bool(channel['receive_commands']) if channel else ctx.receive_commands,
                       daily_notifications=bool(channel['daily_notifications']) if channel else ctx.daily_notifications)


def snapshot(session):
    return {k: session[k] for k in ('id','venue_id','venue_name','date','name','start','end','slot_key') if k in session.keys()}


def special_rules(venue_id, day, rules):
    return [dict(rule, slot_key=rule.get('slot_key') or 'special:' + hashlib.md5(
        dumps([venue_id, day, rule.get('name'), rule.get('start'), rule.get('end')]).encode()).hexdigest()) for rule in rules]


def initialize_scope(path):
    with connect(path) as check:
        if check.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_changes'").fetchone():
            return
    with connect(path, True) as db:
        session_columns = {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}
        for name, definition in (('slot_key', "TEXT NOT NULL DEFAULT ''"), ('venue_name', "TEXT NOT NULL DEFAULT ''")):
            if name not in session_columns:
                db.execute(f'ALTER TABLE sessions ADD COLUMN {name} {definition}')
        reg_columns = {r['name'] for r in db.execute('PRAGMA table_info(registrations)')}
        if 'session_snapshot' not in reg_columns:
            db.execute("ALTER TABLE registrations ADD COLUMN session_snapshot TEXT NOT NULL DEFAULT '{}'")
        db.execute('''CREATE TABLE IF NOT EXISTS session_changes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER NOT NULL,
            before_json TEXT NOT NULL,after_json TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')
        # Recover a stable legacy key only from an unambiguous matching rule.
        rules = [(r['venue_id'], 'slot:' + str(r['id']), json.loads(r['definition'])) for r in db.execute('SELECT * FROM slots')]
        specials = {(r['venue_id'], r['date']):special_rules(r['venue_id'],r['date'],json.loads(r['slots'])) for r in db.execute('SELECT * FROM special_dates')}
        for row in list(db.execute("SELECT * FROM sessions WHERE slot_key='' OR venue_name=''")):
            key = row['slot_key']
            candidates = [(row['venue_id'],r['slot_key'],r) for r in specials[(row['venue_id'],row['date'])]] if (row['venue_id'],row['date']) in specials else rules
            matches = [item[1] for item in candidates if item[0] == row['venue_id'] and all(item[2].get(k) == row[k] for k in ('name','start','end'))]
            if not key:
                key = matches[0] if len(matches) == 1 else 'legacy:' + str(row['id'])
            venue = db.execute('SELECT name FROM venues WHERE id=?', (row['venue_id'],)).fetchone()
            db.execute('UPDATE sessions SET slot_key=?,venue_name=? WHERE id=?', (key, row['venue_name'] or (venue[0] if venue else ''),row['id']))
        for row in list(db.execute("SELECT r.id,s.* FROM registrations r JOIN sessions s ON s.id=r.session_id WHERE r.session_snapshot='{}'")):
            # r.id aliases the registration row; s.id is intentionally read separately.
            session = db.execute('SELECT s.* FROM sessions s JOIN registrations r ON r.session_id=s.id WHERE r.id=?', (row['id'],)).fetchone()
            db.execute('UPDATE registrations SET session_snapshot=? WHERE id=?', (dumps(snapshot(session)),row['id']))
