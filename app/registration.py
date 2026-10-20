"""Short-lived registration links bound to the originating chat identity."""
from datetime import timedelta
import json
import secrets
import re
from .db import connect, stamp, now, dumps
from .security import Platform, digest
from .members import Members, display_name
from .bot_capabilities import require_capability, supports


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS registration_forms(
        token_hash TEXT PRIMARY KEY,chat_id TEXT NOT NULL,user_id TEXT NOT NULL,
        expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft',
        payload TEXT,code TEXT,channel_id TEXT NOT NULL DEFAULT '')''')
    if 'channel_id' not in {row[1] for row in db.execute('PRAGMA table_info(registration_forms)')}:
        db.execute("ALTER TABLE registration_forms ADD COLUMN channel_id TEXT NOT NULL DEFAULT ''")
    if 'purpose' not in {row[1] for row in db.execute('PRAGMA table_info(registration_forms)')}:
        db.execute("ALTER TABLE registration_forms ADD COLUMN purpose TEXT NOT NULL DEFAULT 'registration'")


def _platform(ctx):
    if not ctx.platform_root or ctx.id == 'workspace':
        raise PermissionError('注册链接缺少有效的机器人身份')
    # Initialization can acquire transport locks, so run it before opening the
    # form transaction, never while holding that transaction's write lock.
    return Platform(ctx.platform_root)


def _bound_context(ctx, row, platform, clock=now):
    """The token grants only the identity saved when its link was issued."""
    if row['channel_id']:
        channel = platform.channel(row['channel_id'], active=True)
        if channel['bot_id'] != ctx.id or channel['chat_id'] != row['chat_id']:
            raise PermissionError('原注册群通道已变更，请重新发送 注册')
    else:
        # Old links stay scoped to the original bot/chat, not the first group
        # used to construct the anonymous public HTTP request context.
        channels = [c for c in platform.list_channels(bot_id=ctx.id, active_only=True)
                    if c['chat_id'] == row['chat_id']]
        if len(channels) != 1:
            raise PermissionError('原注册群通道已停用，请联系管理员')
        channel = channels[0]
    if not channel['receive_commands']:
        raise PermissionError('原注册群通道已暂停接收业务，请联系管理员')
    bound = platform.context_for_channel(channel['id'], row['user_id'], False, 'registration_form')
    from .time_rules import bind_clock
    bound=bind_clock(bound,clock)
    require_capability(bound, 'registration')
    if (bound.db_path.resolve() != ctx.db_path.resolve()
            or bound.transport_db_path.resolve() != ctx.transport_db_path.resolve()):
        raise PermissionError('注册链接不属于此机器人')
    return bound


def _receipt_schema(db):
    # Member write and receipt commit atomically. If the process dies before the
    # separate transport commit, retry returns this receipt without reapplying.
    db.execute('''CREATE TABLE IF NOT EXISTS registration_form_receipts(
        bot_id TEXT NOT NULL,token_hash TEXT NOT NULL,scope_id TEXT NOT NULL,
        chat_id TEXT NOT NULL,user_id TEXT NOT NULL,result TEXT NOT NULL,
        created_at TEXT NOT NULL,PRIMARY KEY(bot_id,token_hash))''')


def _receipt(db, ctx, row):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='registration_form_receipts'").fetchone():
        return None
    found = db.execute('''SELECT result FROM registration_form_receipts
        WHERE bot_id=? AND token_hash=? AND scope_id=? AND chat_id=? AND user_id=?''',
        (ctx.id, row['token_hash'], ctx.scope_id, row['chat_id'], row['user_id'])).fetchone()
    return json.loads(found['result']) if found else None


def _result(member, ctx=None):
    outcome = member.get('registration_result',
                         'already_registered' if member['status'] == 'approved' else 'submitted')
    messages = {
        'submitted': '注册申请已提交，等待管理员审核。',
        'linked': '已关联管理员预登记资料，注册完成。现在可以回群报名。',
        'already_registered': '您已注册，无需重复提交。可以回群使用会员功能。',
    }
    if ctx is not None and not supports(ctx, 'enrollment'):
        messages['submitted'] = '注册申请已提交，等待管理员审核。无需回群确认，审核通过后发送 帮助 查看可用功能。'
        messages['linked'] = '已关联管理员预登记资料，注册完成。回群发送 帮助 查看可用功能。'
    phone = member.get('phone') or ''
    return {'status': 'completed', 'registration_result': outcome,
            'member_status': member['status'], 'name': member['name'], 'gender': member.get('gender', 'unknown'),
            'level': member.get('requested_level') if member['status'] == 'pending' else member.get('level'),
            'phone_masked': phone[:3] + '****' + phone[-4:] if phone else '',
            'message': messages[outcome]}


def _active_member(members, db=None):
    member = members.get(db=db)
    if member and member['status'] == 'suspended':
        raise PermissionError('会员已停用，请联系管理员处理后再提交资料')
    return member


def create_form(ctx, clock=now, *, purpose='registration', hint_name=None):
    if purpose not in ('registration', 'profile'):
        raise ValueError('资料表用途无效')
    if ctx.platform_root and ctx.channel_id:
        fresh = _platform(ctx).context_for_channel(ctx.channel_id, ctx.actor, False, 'registration_form')
        if (fresh.id, fresh.chat_id) != (ctx.id, ctx.chat_id) or not fresh.receive_commands:
            raise PermissionError('原注册群通道已变更，请重新发送 注册')
        ctx = fresh
    from .time_rules import bind_clock
    ctx=bind_clock(ctx,clock)
    require_capability(ctx, 'registration')
    if purpose == 'profile':
        Members(ctx, clock).require_approved(allow_admin=False, bot_access=False)
    with connect(ctx.db_path) as business:
        base = json.loads(business.execute("SELECT value FROM config WHERE key='public_base_url'").fetchone()[0])
    if not base:
        return None
    with connect(ctx.transport_db_path, True) as db:
        schema(db)
        db.execute('DELETE FROM registration_forms WHERE expires_at<=?', (stamp(clock()),))
        db.execute("UPDATE registration_forms SET status='superseded',payload=NULL,code=NULL WHERE chat_id=? AND user_id=? AND status IN ('draft','submitted')", (ctx.chat_id, ctx.actor))
        token = secrets.token_urlsafe(32)
        hint = str(hint_name or '').strip()[:80]
        db.execute('INSERT INTO registration_forms(token_hash,chat_id,user_id,expires_at,channel_id,purpose,payload) VALUES(?,?,?,?,?,?,?)',
                   (digest(token), ctx.chat_id, ctx.actor, stamp(clock() + timedelta(minutes=20)), ctx.channel_id, purpose,
                    dumps({'hint_name':hint}) if hint else None))
    return f'{base}/register#{ctx.id}.{token}'


def form_status(ctx, token, clock=now):
    platform = _platform(ctx)
    with connect(ctx.transport_db_path) as db:
        schema(db)
        row = db.execute("SELECT * FROM registration_forms WHERE token_hash=? AND expires_at>? AND status IN ('draft','submitted','confirmed')", (digest(token), stamp(clock()))).fetchone()
    if not row:
        raise ValueError('注册链接已失效，请回群发送 注册 获取新链接')
    bound = _bound_context(ctx, row, platform, clock)
    members = Members(bound, clock)
    member = _active_member(members)
    with connect(bound.db_path) as business:
        result = _receipt(business, bound, row)
    if result:
        return result
    if row['purpose'] == 'profile':
        from .profile_changes import ProfileChanges
        changes = ProfileChanges(bound, clock)
        member = members.require_approved(allow_admin=False, bot_access=False)
        pending = changes.list(status='pending')
        values = {key: member.get(key) for key in ('name','gender','level','group_ids')}
        if pending:
            values.update({key: value for key, value in pending[0]['changes'].items() if key != 'phone'})
        return {'status': row['status'], 'purpose': 'profile', 'profile': values, 'groups': changes.groups()}
    if row['status'] == 'confirmed' and member:
        return _result(member | {'registration_result': 'already_registered' if member['status'] == 'approved' else 'submitted'}, bound)
    hint = json.loads(row['payload'] or '{}').get('hint_name','')
    suggestion = _import_suggestion(bound, hint or (member or {}).get('name',''))
    if suggestion:
        return {'status':row['status'], **suggestion}
    if not member:
        return {'status':row['status'], **({'profile':{'name':hint,'gender':'','level':None}} if hint else {})}
    values={key:member.get(key) for key in ('name','gender','requested_level')}
    if values.get('name')==bound.actor:values['name']=''
    return {'status':row['status'],'profile':{'name':values.get('name',''),'gender':values.get('gender',''),'level':values.get('requested_level')}}


def _import_suggestion(ctx, name):
    from .member_imports import ensure_schema, masked
    from .registration_reconciliation import name_candidate
    with connect(ctx.db_path) as db:
        ensure_schema(db)
        candidate = name_candidate(db,ctx.scope_id,name)
    if not candidate:
        return None
    return {'profile':{'name':candidate['name'],'gender':candidate['gender'] if candidate['gender']!='unknown' else '',
                       'level':candidate['level']},
            'import_hint':{'phone_required':False,'phone_masked':masked(candidate['phone']),
                           'message':'已找到管理员登记资料，确认后直接完成注册。'
                                     + ('已登记手机号 '+masked(candidate['phone'])+'，无需重复填写。' if candidate['phone']
                                        else '可补充本人手机号。')}}


def lookup_form(ctx, token, data, clock=now):
    """A personal token can suggest a unique roster name, without exposing a phone."""
    platform = _platform(ctx)
    with connect(ctx.transport_db_path) as db:
        schema(db)
        row=db.execute("""SELECT * FROM registration_forms WHERE token_hash=? AND expires_at>?
            AND status='draft' AND purpose='registration'""",(digest(token),stamp(clock()))).fetchone()
    if not row:raise ValueError('请重新发送 注册确认 获取本人链接')
    bound=_bound_context(ctx,row,platform,clock)
    if not isinstance(data,dict):raise ValueError('请填写姓名')
    name=display_name(data.get('name'),bound.actor)
    return _import_suggestion(bound,name) or {'profile':{'name':name},'import_hint':None}


def _apply_form(bound, row, data, clock):
    members = Members(bound, clock)
    changes = None
    if row['purpose'] == 'profile':
        from .profile_changes import ProfileChanges
        changes = ProfileChanges(bound, clock)
    with connect(bound.db_path, True) as business:
        _receipt_schema(business)
        existing = _active_member(members, business)
        saved = _receipt(business, bound, row)
        if saved:
            return existing, saved
        if changes:
            request = changes.submit(data, db=business)
            result = {'status': 'completed', 'purpose': 'profile', 'request_id': request['id'],
                      'name': existing['name'], 'message': '资料调整已提交，等待管理员审核。'}
            business.execute('INSERT INTO registration_form_receipts VALUES(?,?,?,?,?,?,?)',
                (bound.id, row['token_hash'], bound.scope_id, row['chat_id'], row['user_id'], dumps(result), stamp(clock())))
            return existing, result
        if row['status'] == 'confirmed':
            if not existing:
                raise ValueError('原注册资料已变更，请重新发送 注册')
            member = existing | {'registration_result': 'already_registered' if existing['status'] == 'approved' else 'submitted'}
        elif existing and existing['status'] == 'approved':
            member = existing | {'registration_result': 'already_registered'}
        else:
            if not isinstance(data, dict):
                raise ValueError('注册资料格式无效')
            # Only profile fields are accepted; caller-supplied account, bot,
            # chat, approval, and role claims never have authority.
            name = display_name(data.get('name'), bound.actor)
            level = data.get('level')
            if level is not None:
                Members._level(level)
            from .member_imports import normalize_phone
            phone = normalize_phone(data.get('phone', (existing or {}).get('phone')) or '')
            member = members.apply(name, level, phone, db=business, gender=data.get('gender'))
        result = _result(member, bound)
        business.execute('INSERT INTO registration_form_receipts VALUES(?,?,?,?,?,?,?)',
            (bound.id, row['token_hash'], bound.scope_id, row['chat_id'], row['user_id'], dumps(result), stamp(clock())))
    return member, result


def _mark_confirmed(db, token_hash):
    db.execute("UPDATE registration_forms SET status='confirmed',payload=NULL,code=NULL WHERE token_hash=?", (token_hash,))


def submit_form(ctx, token, data, clock=now):
    platform = _platform(ctx)
    with connect(ctx.transport_db_path, True) as db:
        schema(db)
        row = db.execute("SELECT * FROM registration_forms WHERE token_hash=? AND expires_at>? AND status IN ('draft','submitted','confirmed')", (digest(token), stamp(clock()))).fetchone()
        if not row:
            raise ValueError('注册链接已失效，请回群发送 注册 获取新链接')
        bound = _bound_context(ctx, row, platform, clock)
        member, result = _apply_form(bound, row, data, clock)
        _mark_confirmed(db, row['token_hash'])
    return result


def confirm_form(ctx, code, clock=now):
    """Accept legacy confirmation codes; new links no longer issue a code."""
    if not isinstance(code, str) or not re.fullmatch(r'[a-f0-9]{10}', code):
        raise ValueError('注册确认已失效或不属于本人，请重新发送 注册')
    platform = _platform(ctx)
    with connect(ctx.transport_db_path, True) as db:
        schema(db)
        row = db.execute("SELECT * FROM registration_forms WHERE code=? AND chat_id=? AND user_id=? AND expires_at>? AND status='submitted'", (code, ctx.chat_id, ctx.actor, stamp(clock()))).fetchone()
        if not row:
            raise ValueError('注册确认已失效或不属于本人，请重新发送 注册')
        bound = _bound_context(ctx, row, platform, clock)
        data = json.loads(row['payload'])
        member, result = _apply_form(bound, row, data, clock)
        _mark_confirmed(db, row['token_hash'])
    return member | {'registration_result': result['registration_result']}
