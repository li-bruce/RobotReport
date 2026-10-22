"""Transactional, robot-local editing of scheduled announcements."""
import hashlib
import json
import uuid

from .db import connect, dumps, audit
from .time_rules import broadcast_rules, normalize_broadcasts


def _mine(rules, bot_id):
    result=[]
    names={'open':'当日活动提醒','closing':'报名即将结束','roster':'报名名单'}
    for rule in rules:
        if bot_id not in rule['bot_ids'] or bot_id in rule.get('excluded_bot_ids', []):
            continue
        own=dict({k:v for k,v in rule.items() if k!='excluded_bot_ids'},bot_ids=[bot_id])
        if own['id']=='legacy-'+own['name']:
            own['name']=names.get(own['name'],own['name'])
        result.append(own)
    return result


def _version(rules):
    return hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()


def settings(platform, user, bot_id, data=None):
    platform.require_platform(user)
    platform.bot(bot_id)
    ids = [b['id'] for b in platform.list(user)]
    ctx = platform.workspace_context(actor=user.id, source='admin')
    with connect(ctx.db_path, data is not None) as db:
        config = {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM config')}
        rules = broadcast_rules(config, ids)
        own = _mine(rules, bot_id)
        if data is not None:
            if set(data) != {'rules', 'revision'}:
                raise ValueError('请提供播报规则与版本')
            if data['revision'] != _version(own):
                from fastapi import HTTPException
                raise HTTPException(409, '此机器人的播报已被修改，请重新载入后编辑')
            if isinstance(data['rules'],list) and any(isinstance(r,dict) and 'excluded_bot_ids' in r for r in data['rules']):
                raise ValueError('请在当前机器人的播报面板编辑规则')
            updated = normalize_broadcasts(data['rules'], [bot_id])
            if updated is None:
                raise ValueError('播报规则必须为列表')
            previous = {r['id']: r for r in own}
            incoming = {r['id']: r for r in updated}
            # Keep an untouched shared rule byte-for-byte. When one recipient
            # overrides it, exclude only that recipient: outstanding deliveries
            # for every other robot retain their original rule fingerprint.
            retained = []
            for rule in rules:
                rid = rule['id']
                if rid not in previous or incoming.get(rid) == normalize_broadcasts([previous[rid]], [bot_id])[0]:
                    retained.append(rule)
                    if rid in previous:incoming.pop(rid, None)
                    continue
                excluded = list(dict.fromkeys(rule.get('excluded_bot_ids', []) + [bot_id]))
                if set(rule['bot_ids']) - set(excluded):
                    retained.append(dict(rule, excluded_bot_ids=excluded))
            taken = {r['id'] for r in retained}
            for rule in incoming.values():
                if rule['id'] in taken:
                    rule = dict(rule, id=uuid.uuid4().hex)
                retained.append(rule)
                taken.add(rule['id'])
            # The editor limits each robot to 50 rules; shared legacy rules may
            # become independent over time, so the storage limit is larger.
            normalize_broadcasts(retained, limit=5000)
            rules = retained
            db.execute('INSERT OR REPLACE INTO config(key,value) VALUES(?,?)', ('broadcast_rules', dumps(rules)))
            audit(db, user.id, 'bot.broadcasts.update', {'bot_id': bot_id, 'rule_count': len(updated)})
            own = _mine(rules, bot_id)
        return {'rules': own, 'revision': _version(own), 'daily_enabled': config['daily_enabled']}
