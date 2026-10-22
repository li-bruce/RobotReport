"""FastAPI entrypoint. Default run is local simulation; opt in to real sending."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import uuid
from fastapi import FastAPI, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from .db import connect, dumps, stamp, now, audit
from .security import Platform, Principal, BotContext, identifier
from .service import Service
from .members import Members
from .membership_terms import MembershipTerms
from .password_auth import PasswordAuth
from .commands import Commands
from .message_parser import message_input
from .tournaments import Tournaments
from .messages import Outbox, tournament_text
from .media import Media, MAX_BYTES
from .scheduler import Scheduler
from .middleware import BoundaryMiddleware, BodyTooLarge
from . import env_settings

ROOT = Path(__file__).resolve().parents[1]


def create_app(state_dir=None, bootstrap_token=None, start_scheduler=True, live=None, clock=now, env_file=None):
    settings = env_settings.load(env_file, explicit_state=state_dir is not None)
    state_dir = Path(state_dir or settings.get('ROBORT_STATE_DIR', str(ROOT / 'state' / 'local'))).resolve()
    env_settings.bind(state_dir, settings)
    live = env_settings.boolean(settings.get('ROBORT_LIVE_SEND', '0'), 'ROBORT_LIVE_SEND') if live is None else live
    platform = Platform(state_dir, bootstrap_token or settings.get('ROBORT_ADMIN_TOKEN'))
    env_settings.apply(platform, settings)
    scheduler = Scheduler(platform, live=live, clock=clock)
    passwords = PasswordAuth(platform, clock)
    if settings.get('ROBORT_INITIAL_ADMIN_PASSWORD') and not passwords.initialized():
        passwords.set_initial(settings.get('ROBORT_INITIAL_ADMIN_PASSWORD'))

    @asynccontextmanager
    async def lifespan(app):
        from .ai_conversation import recover_interrupted
        recover_interrupted(platform.workspace_context(actor='startup',is_admin=False,source='system').db_path,clock)
        from .bot_connection import recover_interrupted as recover_connections
        recover_connections(platform)
        task = asyncio.create_task(scheduler.run()) if start_scheduler else None
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title='聚场 GatherSpace · 场地与活动管理', version='1.0.0', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.platform = platform
    app.state.scheduler = scheduler
    app.state.live = live
    app.state.passwords = passwords
    app.add_middleware(BoundaryMiddleware, platform=platform, passwords=passwords)

    @app.exception_handler(BodyTooLarge)
    async def body_too_large(request, exc):
        return JSONResponse({'detail': '请求正文超出大小限制'}, status_code=413)

    @app.exception_handler(ValueError)
    async def bad_request(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(PermissionError)
    async def forbidden(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=403)

    @app.exception_handler(KeyError)
    async def missing_field(request, exc):
        return JSONResponse({'detail': '缺少必要字段或记录不存在'}, status_code=400)

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, exc):
        return JSONResponse({'detail': '操作与现有记录冲突，请刷新后重试'}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        # Never echo rejected request bodies: they can contain credentials.
        return JSONResponse({'detail': '请求参数格式无效，请检查必填字段'}, status_code=422)

    def check_origin(request):
        origin = request.headers.get('origin')
        expected = str(request.base_url).rstrip('/')
        if origin and origin != expected:
            raise PermissionError('请求来源无效')

    @app.post('/auth/login')
    def password_login(data: dict, request: Request):
        check_origin(request)
        result = passwords.login(data.get('password'), request.client.host if request.client else 'unknown')
        response = JSONResponse({'csrf_token':result['csrf_token'], 'role':'platform_admin', 'expires_at':result['expires_at']})
        response.set_cookie('robort_session', result['session_token'], max_age=86400, httponly=True,
                            secure=request.url.scheme == 'https', samesite='strict', path='/')
        return response

    @app.get('/auth/session')
    def password_session(request: Request):
        current = passwords.session(request.cookies.get('robort_session', ''))
        return {'csrf_token':current['csrf_token'], 'role':current['principal'].role, 'expires_at':current['expires_at']}

    @app.post('/auth/logout')
    def password_logout(request: Request):
        check_origin(request)
        passwords.logout(request.cookies.get('robort_session', ''), request.headers.get('x-csrf-token', ''))
        response = JSONResponse({'ok': True})
        response.delete_cookie('robort_session', path='/')
        return response

    @app.post('/auth/password')
    def password_change(data: dict, request: Request):
        check_origin(request)
        passwords.change(request.cookies.get('robort_session', ''), data.get('current_password'), data.get('new_password'), request.headers.get('x-csrf-token', ''))
        response = JSONResponse({'ok': True, 'reauthenticate': True})
        response.delete_cookie('robort_session', path='/')
        return response

    def principal(request: Request):
        cached = getattr(request.state, 'principal', None)
        if cached:
            return cached
        auth = request.headers.get('authorization', '')
        if auth.startswith('Bearer ') and auth[7:]:
            return platform.authenticate(auth[7:])
        try:
            return passwords.session(request.cookies.get('robort_session', ''))['principal']
        except PermissionError:
            raise HTTPException(401, '请先登录管理后台') from None

    def context(request: Request, user=Depends(principal)):
        platform.require_platform(user)
        return platform.context(user)

    def svc(ctx):
        return Service(ctx, clock)

    def events(ctx):
        return Tournaments(ctx, clock)

    @app.get('/healthz')
    def healthz():
        return {'status': 'ok', 'version': app.version, 'mode': 'live' if live else 'simulation'}

    @app.get('/')
    def index():
        return RedirectResponse('/admin')

    @app.get('/admin')
    def admin():
        return FileResponse(ROOT / 'app' / 'static' / 'index.html')

    @app.get('/guide')
    @app.get('/guide/member')
    def user_guide():
        return FileResponse(ROOT / 'app' / 'static' / 'guide.html', headers={'Cache-Control': 'no-store'})

    @app.get('/guide/admin')
    def admin_guide():
        return FileResponse(ROOT / 'app' / 'static' / 'guide-admin.html', headers={'Cache-Control': 'no-store'})

    @app.get('/api/bots')
    def list_bots(user=Depends(principal)):
        return {'bots': platform.list(user), 'is_platform_admin': user.role == 'platform_admin'}

    @app.get('/api/bot-capabilities')
    def bot_capabilities(user=Depends(principal)):
        from .bot_capabilities import capability_catalog
        return {'capabilities': capability_catalog()}

    @app.get('/api/bot-user-attributes')
    def bot_user_attributes(user=Depends(principal)):
        platform.require_platform(user)
        from .bot_access import catalog
        return catalog(platform.workspace_context())

    @app.get('/api/bots/{bot_id}/help-preview')
    def bot_help_preview(bot_id: str, audience: str = 'member', user=Depends(principal)):
        from .help import scoped_help
        from .scope import refresh_context
        platform._require_bot(user, bot_id)
        platform.bot(bot_id)
        if audience not in ('member', 'admin', 'unregistered'):
            raise ValueError('请选择会员、管理员或未注册用户')
        from .security import BotContext
        ctx=refresh_context(BotContext(bot_id,platform.workspace_root,'',user.id,audience=='admin','help_preview',platform_root=platform.root))
        reply = scoped_help(ctx,{'status': 'unregistered' if audience == 'unregistered' else 'approved'},clock=clock)
        return {'reply': reply, 'audience': audience, 'bot_id': bot_id}

    @app.get('/api/bots/{bot_id}/callback')
    def bot_callback_url(bot_id: str, request: Request, user=Depends(principal)):
        platform.require_platform(user)
        bot = platform.bot(bot_id)
        path = platform.root_for(bot_id) / 'callback.local.json'
        if not path.exists():
            raise ValueError('本地未保留回调地址，请使用重新生成回调功能')
        token = json.loads(path.read_text(encoding='utf-8')).get('callback_token', '')
        if hashlib.sha256(token.encode()).hexdigest() != bot['callback_hash']:
            raise ValueError('回调记录已变更，请重新生成回调地址')
        origin = env_settings.runtime_value(platform.root, 'ROBORT_PUBLIC_BASE_URL').rstrip('/') or str(request.base_url).rstrip('/')
        from .integrations import callback_path
        return {'callback_url': origin + callback_path(platform, bot_id, token)}

    @app.get('/api/bots/{bot_id}/connection')
    def bot_connection(bot_id:str,request:Request,channel_id:str=None,user=Depends(principal)):
        # Only the explicit administrator editor receives connection addresses.
        # Public bot/channel lists continue to omit all credentials.
        result=bot_callback_url(bot_id,request,user)
        webhook='';status={}
        if channel_id:
            channel=platform.channel(channel_id)
            if channel['bot_id']!=bot_id or channel['archived']:
                raise PermissionError('连接不属于此机器人或已移除')
            webhook=channel['webhook_url']
            from .bot_connection import summary
            status=summary(platform,channel)
        return result | {'webhook_url':webhook} | status

    @app.post('/api/bots/{bot_id}/connection/test')
    async def bot_connection_test(bot_id:str,data:dict,user=Depends(principal)):
        from .bot_connection import send_test
        return await send_test(platform,user,bot_id,data.get('channel_id'),data.get('request_id'),live=live,clock=clock)

    @app.post('/api/bots')
    def create_bot(data: dict, request: Request, user=Depends(principal)):
        result = platform.create(user, data)
        origin = env_settings.runtime_value(platform.root, 'ROBORT_PUBLIC_BASE_URL').rstrip('/') or str(request.base_url).rstrip('/')
        result['callback_url'] = origin + result['callback_path']
        return result

    @app.patch('/api/bots/{bot_id}')
    def update_bot(bot_id: str, data: dict, user=Depends(principal)):
        return platform.update(user, bot_id, data)

    @app.get('/api/bots/{bot_id}/broadcasts')
    def bot_broadcasts(bot_id: str, user=Depends(principal)):
        from .bot_broadcasts import settings
        return settings(platform, user, bot_id)

    @app.put('/api/bots/{bot_id}/broadcasts')
    def save_bot_broadcasts(bot_id: str, data: dict, user=Depends(principal)):
        from .bot_broadcasts import settings
        return settings(platform, user, bot_id, data)

    @app.delete('/api/bots/{bot_id}')
    def delete_bot(bot_id: str, user=Depends(principal)):
        return platform.remove(user,bot_id)

    @app.post('/api/bots/{bot_id}/rotate-callback')
    def rotate_callback(bot_id: str, request: Request, user=Depends(principal)):
        token = secrets.token_urlsafe(36)
        result = platform.update(user, bot_id, {'callback_token': token})
        origin = env_settings.runtime_value(platform.root, 'ROBORT_PUBLIC_BASE_URL').rstrip('/') or str(request.base_url).rstrip('/')
        from .integrations import callback_path
        return result | {'callback_url': origin + callback_path(platform, bot_id, token)}

    @app.post('/api/grants')
    def create_grant(data: dict, user=Depends(principal)):
        return platform.grant(user, data.get('bot_ids', []))

    @app.get('/api/grants')
    def grants(user=Depends(principal)):
        platform.require_platform(user)
        with connect(platform.path) as db:
            return [dict(r) | {'bot_ids': json.loads(r['bot_ids'])} for r in db.execute('SELECT id,role,bot_ids,active,created_at FROM grants')]

    @app.delete('/api/grants/{grant_id}')
    def revoke_grant(grant_id: str, user=Depends(principal)):
        platform.require_platform(user)
        if user.id == grant_id:
            raise ValueError('不能撤销当前使用的令牌')
        with connect(platform.path, True) as db:
            db.execute('UPDATE grants SET active=0 WHERE id=?', (grant_id,))
        return {'ok': True}

    @app.get('/api/config')
    def config(ctx=Depends(context)):
        return svc(ctx).config() | {'delivery_mode': 'live' if live else 'simulation'}

    @app.patch('/api/config')
    def save_config(data: dict, ctx=Depends(context)):
        return svc(ctx).update_config(data)

    @app.get('/api/profile-requests')
    def profile_requests(status: str = None, user_id: str = None, ctx=Depends(context)):
        from .profile_changes import ProfileChanges
        return ProfileChanges(ctx, clock).list(all_users=True, status=status,user_id=user_id)

    @app.post('/api/profile-requests/{request_id}/review')
    def profile_request_review(request_id: int, data: dict, ctx=Depends(context)):
        from .profile_changes import ProfileChanges
        if set(data) - {'approved', 'note'}:
            raise ValueError('资料审核字段无效')
        return ProfileChanges(ctx, clock).review(request_id, data.get('approved'), data.get('note', ''))

    @app.get('/api/members')
    def members(status: str = None, group_id: int = None, q: str = None, ctx=Depends(context)):
        rows = Members(ctx, clock).list(status, group_id)
        return [row for row in rows if not q or q.casefold() in row['name'].casefold()]

    @app.get('/api/members/{user_id}')
    def member_detail(user_id: str, ctx=Depends(context)):
        svc(ctx).require_admin()
        row = Members(ctx, clock).get(user_id)
        if not row:
            raise ValueError('会员不存在')
        return row

    @app.get('/api/members/{user_id}/membership')
    def membership_detail(user_id: str, ctx=Depends(context)):
        svc(ctx).require_admin()
        terms = MembershipTerms(ctx, clock)
        return {'summary': terms.summary(user_id), 'periods': terms.list(user_id),
                'applications': terms.application_history(user_id)}

    @app.post('/api/members/{user_id}/membership/periods')
    def membership_create_period(user_id: str, data: dict, ctx=Depends(context)):
        svc(ctx).require_admin()
        return MembershipTerms(ctx, clock).create(user_id, data)

    @app.post('/api/members/{user_id}/membership/periods/{term_id}/void')
    def membership_void_period(user_id: str, term_id: int, data: dict, ctx=Depends(context)):
        svc(ctx).require_admin()
        if set(data) - {'expected_version', 'note'}:
            raise ValueError('作废记录字段无效')
        return MembershipTerms(ctx, clock).void(user_id, term_id, data.get('expected_version'), data.get('note', ''))

    @app.post('/api/members')
    def create_member(data: dict, ctx=Depends(context)):
        ctx.require('member.manage')
        if not data.get('user_id'):
            from .member_imports import MemberImports
            return MemberImports(ctx,clock).create(data)
        manager = Members(ctx, clock)
        with connect(ctx.db_path, True) as db:
            user_id = identifier(data['user_id'])
            old = manager.get(user_id, db=db)
            if old and old['status'] == 'approved':
                raise ValueError('该账号已是正式会员，请在会员资料中修改')
            row = manager.apply(data['name'], data.get('requested_level'), data.get('phone'), db=db, user_id=user_id, source='admin', gender=data.get('gender'))
        return manager.get(row['user_id'])

    @app.patch('/api/members/{user_id}')
    def update_member(user_id: str, data: dict, ctx=Depends(context)):
        manager = Members(ctx, clock)
        svc(ctx).require_admin()
        if set(data) - {'status','name','note','phone','requested_level','gender'}:
            raise ValueError('会员资料字段无效')
        with connect(ctx.db_path, True) as db:
            if 'status' in data or 'name' in data:
                old = manager.get(user_id, db=db)
                if not old:
                    raise ValueError('会员不存在')
                manager.review(user_id, data.get('status', old['status']), data.get('name'), data.get('note', ''), db=db)
            if 'phone' in data or 'requested_level' in data or 'gender' in data:
                manager.update_details(user_id, data.get('phone'), data.get('requested_level'), db=db, gender=data.get('gender'))
        return manager.get(user_id)

    @app.put('/api/members/{user_id}/groups')
    def member_groups(user_id: str, data: dict, ctx=Depends(context)):
        return Members(ctx, clock).set_groups(user_id, data['group_ids'])

    @app.post('/api/members/{user_id}/level-review')
    def member_level(user_id: str, data: dict, ctx=Depends(context)):
        if type(data.get('approved')) is not bool:
            raise ValueError('请选择通过或不通过')
        return Members(ctx, clock).review_level(user_id, data['approved'], data.get('level'), data.get('note', ''))

    @app.get('/api/member-groups')
    def groups(ctx=Depends(context)):
        svc(ctx).require_admin()
        return Members(ctx, clock).groups()

    @app.post('/api/member-groups')
    def create_group(data: dict, ctx=Depends(context)):
        return Members(ctx, clock).save_group(data['name'])

    @app.patch('/api/member-groups/{group_id}')
    def update_group(group_id: int, data: dict, ctx=Depends(context)):
        return Members(ctx, clock).save_group(data['name'], group_id)

    @app.patch('/api/sessions/{session_id}/constraints')
    def session_constraints(session_id: int, data: dict, ctx=Depends(context)):
        return svc(ctx).save_session_constraints(session_id, data)

    @app.get('/api/venues')
    def venues(ctx=Depends(context)):
        return svc(ctx).venues()

    @app.post('/api/venues')
    def create_venue(data: dict, ctx=Depends(context)):
        return svc(ctx).save_venue(data)

    @app.patch('/api/venues/{venue_id}')
    def update_venue(venue_id: int, data: dict, ctx=Depends(context)):
        return svc(ctx).save_venue(data, venue_id)

    @app.get('/api/slots')
    def slots(venue_id: int = 1, ctx=Depends(context)):
        return svc(ctx).slots(venue_id)

    @app.get('/api/occupancies')
    def occupancies(venue_id: int = 1, start_date: str = None, end_date: str = None, history: bool = False, ctx=Depends(context)):
        from .availability import Availability
        return Availability(ctx,clock).list(venue_id,start_date,end_date,history)

    @app.post('/api/occupancies')
    def reserve_venue(data: dict, ctx=Depends(context)):
        from .availability import Availability
        return Availability(ctx,clock).save(data)

    @app.patch('/api/occupancies/{reservation_id}')
    def change_occupancy(reservation_id: int, data: dict, ctx=Depends(context)):
        from .availability import Availability
        return Availability(ctx,clock).save(data,reservation_id)

    @app.post('/api/occupancies/{reservation_id}/release')
    def release_occupancy(reservation_id: int, data: dict, ctx=Depends(context)):
        from .availability import Availability
        return Availability(ctx,clock).release(reservation_id,data.get('expected_version'))

    @app.get('/api/holiday-schedule')
    def holiday_schedule(venue_id: int = 1, ctx=Depends(context)):
        from .holidays import HolidaySchedule
        return HolidaySchedule(ctx,clock).get(venue_id)

    @app.get('/api/holiday-plan')
    def holiday_plan(year: int, venue_id: int = 1, ctx=Depends(context)):
        from .holiday_planning import HolidayPlanning
        return HolidayPlanning(ctx,clock).get(venue_id,year)

    @app.put('/api/holiday-plan/{year}/{holiday_id}')
    def change_holiday_plan(year: int, holiday_id: str, data: dict, venue_id: int = 1, ctx=Depends(context)):
        from .holiday_planning import HolidayPlanning
        return HolidayPlanning(ctx,clock).save(venue_id,year,holiday_id,data)

    @app.post('/api/holiday-calendar/{year}/refresh')
    def refresh_holiday_calendar(year: int, ctx=Depends(context)):
        from .holiday_planning import HolidayPlanning
        return HolidayPlanning(ctx,clock).refresh(year)

    @app.get('/api/activity/date-plans')
    def date_plans(year: int, ctx=Depends(context)):
        from .activity_operations import ActivityOperations
        return ActivityOperations(ctx,clock).date_plans(year)

    @app.put('/api/activity/date-plans/{day}')
    def save_date_plan(day: str, data: dict, venue_id: int = 1, ctx=Depends(context)):
        from .activity_operations import ActivityOperations
        return ActivityOperations(ctx,clock).save_date_plan(venue_id,day,data)

    @app.delete('/api/activity/date-plans/{day}')
    def delete_date_plan(day: str, data: dict, venue_id: int = 1, ctx=Depends(context)):
        from .activity_operations import ActivityOperations
        return ActivityOperations(ctx,clock).save_date_plan(venue_id,day,data,remove=True)

    @app.put('/api/holiday-schedule')
    def change_holiday_schedule(data: dict, venue_id: int = 1, ctx=Depends(context)):
        from .holidays import HolidaySchedule
        return HolidaySchedule(ctx,clock).save(venue_id,data)

    @app.put('/api/slots')
    def save_slots(data: dict, venue_id: int = 1, ctx=Depends(context)):
        return svc(ctx).save_slots(venue_id, data.get('slots', []))

    @app.get('/api/special-dates')
    def special_dates(venue_id: int = 1, ctx=Depends(context)):
        scoped = svc(ctx)
        scoped.venue(venue_id)
        with connect(ctx.db_path) as db:
            from .scope import special_rules
            rows = [dict(r) | {'slots': special_rules(venue_id, r['date'], json.loads(r['slots']))} for r in db.execute('SELECT * FROM special_dates WHERE venue_id=? ORDER BY date', (venue_id,))]
        if scoped.ctx.slot_keys is not None:
            for row in rows:
                row['slots'] = [slot for slot in row['slots'] if slot['slot_key'] in scoped.ctx.slot_keys]
            rows = [row for row in rows if row['slots']]
        return rows

    @app.put('/api/special-dates/{day}')
    def special_date(day: str, data: dict, venue_id: int = 1, ctx=Depends(context)):
        return svc(ctx).save_slots(venue_id, data.get('slots', []), day)

    @app.get('/api/dashboard')
    def dashboard(venue_id: int = 1, ctx=Depends(context)):
        return svc(ctx).dashboard(venue_id)

    @app.get('/api/activity/calendar')
    def activity_calendar(month: str, ctx=Depends(context)):
        from .activity_operations import ActivityOperations
        return ActivityOperations(ctx, clock).month(month)

    @app.get('/api/activity/day')
    def activity_day(date: str, ctx=Depends(context)):
        from .activity_operations import ActivityOperations
        return ActivityOperations(ctx, clock).day(date)

    @app.get('/api/activity/slot-plan')
    def activity_slot_plan(ctx=Depends(context)):
        from .activity_operations import ActivityOperations
        return ActivityOperations(ctx, clock).plan()

    @app.put('/api/activity/slot-plan')
    def update_activity_slot_plan(data: dict, ctx=Depends(context)):
        from .activity_operations import ActivityOperations
        return ActivityOperations(ctx, clock).save_plan(data)

    @app.get('/api/calendar')
    def calendar(month: str, venue_id: int = 1, ctx=Depends(context)):
        return svc(ctx).calendar(month, venue_id)

    @app.get('/api/registrations/export')
    def export(date: str = None, venue_id: int = 1, session_id: int = None, ctx=Depends(context)):
        if date:
            result = svc(ctx).registrations(date, venue_id, session_id)
        else:
            svc(ctx).venue(venue_id)
            with connect(ctx.db_path) as db:
                dates = [r[0] for r in db.execute('SELECT DISTINCT date FROM sessions WHERE venue_id=? ORDER BY date', (venue_id,))]
            result = {'registrations': []}
            for day in dates:
                result['registrations'].extend(svc(ctx).registrations(day, venue_id)['registrations'])
            if session_id is not None:
                result['registrations'] = [r for r in result['registrations'] if r['session_id'] == session_id]
        output = io.StringIO(newline='')
        keys = ['id','date','session_id','user_id','name','guest_index','status','rank','category','created_at']
        writer = csv.DictWriter(output, fieldnames=keys)
        writer.writeheader()
        for item in result['registrations']:
            row = {key: item[key] for key in keys}
            for key, value in row.items():
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
                    row[key] = "'" + value
            writer.writerow(row)
        return Response('\ufeff' + output.getvalue(), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="registrations-{date or "all"}.csv"'})

    @app.get('/api/registrations')
    def registrations(date: str = None, venue_id: int = 1, session_id: int = None, ctx=Depends(context)):
        return svc(ctx).registrations(date, venue_id, session_id)

    @app.post('/api/registrations')
    def enroll(data: dict, ctx=Depends(context)):
        return svc(ctx).enroll(data['user_id'], data['name'], data.get('guests', 0), data.get('date'), int(data.get('venue_id', 1)), data.get('session_id'), guest_names=data.get('guest_names'))

    @app.delete('/api/registrations/{registration_id}')
    def delete_registration(registration_id: int, ctx=Depends(context)):
        return svc(ctx).cancel_person(registration_id)

    @app.get('/api/stats')
    def stats(venue_id: int = None, ctx=Depends(context)):
        return svc(ctx).stats(venue_id)

    @app.get('/api/formats')
    def formats(discipline: str = 'doubles', doubles_category: str = 'open', participant_count: int = None,
                substitute_count: int = 0, date: str = None, session_id: int = None, venue_id: int = 1,
                min_level: int = None, max_level: int = None, allowed_group_ids: str = '',
                group_count: int = 2, advance_per_group: int = 2, assignment_strategy: str = 'random',
                ctx=Depends(context)):
        group_ids = [int(value) for value in allowed_group_ids.split(',') if value.strip()]
        options = {'doubles_category': doubles_category, 'group_count': group_count,
                   'advance_per_group': advance_per_group, 'assignment_strategy': assignment_strategy,
                   'eligibility': {'min_level': min_level, 'max_level': max_level, 'allowed_group_ids': group_ids}}
        if date and session_id:
            candidates = events(ctx).planning_candidates(date, venue_id, session_id)
            return events(ctx).planning_formats(candidates, discipline, options)
        if participant_count is not None:
            return events(ctx).available_formats(participant_count, discipline, options, substitute_count=substitute_count)
        return events(ctx).formats(discipline, options=options)

    @app.get('/api/casual/history')
    def casual_history(user_id: str = None, date_from: str = None, date_to: str = None,
                       venue_id: int = None, include_formal: bool = True, include_void: bool = False, ctx=Depends(context)):
        from .casual import CasualMatches
        return CasualMatches(ctx, clock).history(user_id=user_id, date_from=date_from, date_to=date_to,
                                                venue_id=venue_id, include_formal=include_formal, include_void=include_void)

    @app.get('/api/casual/statistics')
    def casual_statistics(user_id: str = '*', date_from: str = None, date_to: str = None,
                          venue_id: int = None, include_formal: bool = True, ctx=Depends(context)):
        from .casual import CasualMatches
        return CasualMatches(ctx, clock).statistics(user_id=user_id, date_from=date_from, date_to=date_to,
                                                   venue_id=venue_id, include_formal=include_formal)

    @app.post('/api/casual/matches')
    def casual_create(data: dict, ctx=Depends(context)):
        from .casual import CasualMatches
        return CasualMatches(ctx, clock).create(data)

    @app.patch('/api/casual/matches/{match_id}')
    def casual_update(match_id: int, data: dict, ctx=Depends(context)):
        from .casual import CasualMatches
        return CasualMatches(ctx, clock).update(match_id, data)

    @app.post('/api/casual/matches/{match_id}/void')
    def casual_void(match_id: int, data: dict, ctx=Depends(context)):
        from .casual import CasualMatches
        return CasualMatches(ctx, clock).void(match_id, data.get('expected_version'))

    @app.get('/api/tournaments')
    def tournaments(venue_id: int = None, status: str = None, ctx=Depends(context)):
        return events(ctx).list(venue_id, status)

    @app.post('/api/tournaments')
    def create_tournament(data: dict, ctx=Depends(context)):
        return events(ctx).create(data)

    @app.get('/api/tournaments/{event_id}')
    def tournament(event_id: int, venue_id: int = None, ctx=Depends(context)):
        return events(ctx).get(event_id, venue_id)

    @app.patch('/api/tournaments/{event_id}')
    def update_tournament(event_id: int, data: dict, ctx=Depends(context)):
        return events(ctx).update(event_id, data)

    @app.post('/api/tournaments/{event_id}/actions')
    def transition(event_id: int, data: dict, ctx=Depends(context)):
        return events(ctx).transition(event_id, data['action'])

    @app.post('/api/tournaments/{event_id}/scores')
    def score(event_id: int, data: dict, ctx=Depends(context)):
        return events(ctx).score(event_id, int(data['match_id']), data['scores'], data.get('expected_version'))

    @app.post('/api/tournaments/{event_id}/participants')
    def participants(event_id: int, data: dict, ctx=Depends(context)):
        return events(ctx).enroll(event_id, identifier(data['user_id']), data['name'], data.get('guests', 0), guest_names=data.get('guest_names'))

    @app.delete('/api/tournaments/{event_id}/participants/{user_id}')
    def cancel_participant(event_id: int, user_id: str, ctx=Depends(context)):
        return events(ctx).cancel(event_id, user_id)

    @app.delete('/api/tournaments/{event_id}/entries/{participant_id}')
    def cancel_participant_entry(event_id: int, participant_id: str, ctx=Depends(context)):
        return events(ctx).cancel_entry(event_id, participant_id)

    @app.post('/api/tournaments/{event_id}/notify')
    def notify(event_id: int, data: dict, ctx=Depends(context)):
        mode = data.get('type', 'schedule')
        if mode not in ('registration', 'closed', 'groups', 'schedule', 'results'):
            raise ValueError('通知类型无效')
        text = events(ctx).describe(event_id, mode)
        from .notifications import Notifications
        event = events(ctx).get(event_id)
        destinations = data.get('channel_ids',event.get('channel_ids',[]))
        if set(destinations)-set(event.get('channel_ids',[])):
            raise ValueError('请先在赛事组织中绑定所选群通道')
        result = Notifications(platform,clock).enqueue(text,destinations,actor=ctx.actor,venue_id=event['venue_id'],tournament_id=event_id)
        return {'queued':result['deliveries'],'notification_id':result['id'],'mode':'live' if live else 'simulation'}

    @app.get('/api/tournaments/{event_id}/media')
    def event_media(event_id: int, ctx=Depends(context)):
        events(ctx).get(event_id)
        return Media(ctx, clock).list(event_id)

    async def upload(file, ctx, tournament_id=None, caption=''):
        data = await file.read(MAX_BYTES + 1)
        await file.close()
        return Media(ctx, clock).save(file.filename, data, tournament_id, caption)

    @app.post('/api/tournaments/{event_id}/media')
    async def upload_event(event_id: int, file: UploadFile = File(...), caption: str = Form(''), ctx=Depends(context)):
        events(ctx).get(event_id)
        return await upload(file, ctx, event_id, caption)

    @app.post('/api/media')
    async def upload_media(file: UploadFile = File(...), ctx=Depends(context)):
        return await upload(file, ctx)

    @app.get('/api/media')
    def media(ctx=Depends(context)):
        return Media(ctx, clock).list()

    def file_response(manager, row):
        return FileResponse(manager.download_path(row), media_type=row['mime'], filename=row['name'],
                            content_disposition_type='inline' if row['kind'] == 'image' else 'attachment')

    @app.get('/api/media/{media_id}/download')
    def download(media_id: str, ctx=Depends(context)):
        manager = Media(ctx, clock)
        return file_response(manager, manager.get(media_id))

    @app.post('/api/media/{media_id}/link')
    def media_link(media_id: str, ctx=Depends(context)):
        return Media(ctx, clock).link(media_id)

    @app.post('/api/media/{media_id}/revoke')
    def revoke_media(media_id: str, ctx=Depends(context)):
        return Media(ctx, clock).revoke(media_id)

    @app.get('/register')
    def register_page():
        return FileResponse(ROOT / 'app' / 'static' / 'register.html', headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    def public_context(reference):
        if not re.fullmatch(r'(?:workspace|[a-f0-9]{32})\.[A-Za-z0-9_-]{40,60}', reference):
            raise PermissionError('链接无效')
        bot_id, token = reference.split('.', 1)
        if bot_id == 'workspace':
            return platform.workspace_context(actor='public',is_admin=False,source='public'),token
        bot = platform.bot(bot_id,active=True)
        ctx = platform.context(Principal('public','platform_admin',()),bot_id)
        return replace(ctx,actor='public',is_admin=False,source='public'),token

    @app.get('/public/registration/{reference}')
    def registration_status(reference: str):
        from .registration import form_status
        ctx, token = public_context(reference)
        if ctx.id == 'workspace':raise PermissionError('注册链接无效')
        return JSONResponse(form_status(ctx, token, clock), headers={'Cache-Control': 'no-store'})

    @app.post('/public/registration/{reference}')
    def registration_submit(reference: str, data: dict):
        from .registration import submit_form
        ctx, token = public_context(reference)
        if ctx.id == 'workspace':raise PermissionError('注册链接无效')
        return JSONResponse(submit_form(ctx, token, data, clock), headers={'Cache-Control': 'no-store'})

    @app.post('/public/registration/{reference}/lookup')
    def registration_lookup(reference: str, data: dict):
        from .registration import lookup_form
        ctx, token = public_context(reference)
        if ctx.id == 'workspace':raise PermissionError('注册链接无效')
        return JSONResponse(lookup_form(ctx,token,data,clock),headers={'Cache-Control':'no-store'})

    @app.get('/public/tables/{reference}')
    def table_image(reference: str):
        from .table_images import public_table
        ctx, token = public_context(reference)
        return FileResponse(public_table(ctx, token, clock), media_type='image/png', headers={'Cache-Control':'private, no-store', 'Referrer-Policy':'no-referrer'})

    @app.get('/public/media/{public_token}')
    def public_media(public_token: str):
        if '.' not in public_token:
            raise PermissionError('下载链接无效')
        bot_id, token = public_token.split('.', 1)
        if bot_id != 'workspace':platform.bot(bot_id,active=True)
        ctx = platform.workspace_context(actor='public-download',is_admin=False,source='public')
        manager = Media(ctx, clock)
        return file_response(manager, manager.public_download(token))

    @app.get('/api/messages')
    def messages(channel_id:str=None,ctx=Depends(context)):
        channels=platform.list_channels(include_archived=True)
        if channel_id:channels=[c for c in channels if c['id']==channel_id]
        rows=[]
        for channel in channels:
            channel_ctx=replace(ctx,id=channel['bot_id'],chat_id=channel['chat_id'],channel_id=channel['id'],transport_path=platform.root_for(channel['bot_id'])/'transport.sqlite3')
            for row in Outbox(channel_ctx).list():
                rows.append(row|{'local_id':row['id'],'id':channel['id']+':'+str(row['id']),'bot_id':channel['bot_id'],'bot_name':channel.get('bot_name',''),'channel_name':channel['name']})
        return sorted(rows,key=lambda r:r['created_at'],reverse=True)[:500]

    @app.post('/api/messages/preview')
    def preview(data:dict,ctx=Depends(context)):
        return {'markdown':Media(ctx,clock).compose(data.get('title',''),data.get('text',''),data.get('media_ids',[]))}

    @app.post('/api/messages/send')
    def send(data:dict,ctx=Depends(context)):
        from .notifications import Notifications
        notices=Notifications(platform,clock)
        notices.destinations(notices.select_channels(data.get('channel_ids')))
        markdown=Media(ctx,clock).compose(data.get('title',''),data.get('text',''),data.get('media_ids',[]),publish=True)
        result=notices.enqueue(markdown,data['channel_ids'],actor=ctx.actor)
        return {'queued':result['deliveries'],'notification_id':result['id'],'mode':'live' if live else 'simulation'}

    @app.delete('/api/messages/{message_id}')
    def cancel_message(message_id:str,ctx=Depends(context)):
        if ':' not in message_id:raise ValueError('请指定消息所属群通道')
        channel_id,local_id=message_id.split(':',1)
        channel=platform.channel(channel_id)
        channel_ctx=replace(ctx,id=channel['bot_id'],chat_id=channel['chat_id'],channel_id=channel_id,transport_path=platform.root_for(channel['bot_id'])/'transport.sqlite3')
        return Outbox(channel_ctx).cancel(int(local_id))

    @app.post('/api/simulator')
    async def simulator(data:dict,ctx=Depends(context)):
        user_id=identifier(data['user_id'])
        channel_id=data.get('channel_id')
        if not channel_id:raise ValueError('请先选择演练的机器人群通道')
        member=platform.context_for_channel(channel_id,user_id,False,'simulator')
        profile=Members(replace(member,is_admin=True),clock).get(user_id)
        member=replace(member,is_admin=bool(profile and profile['status']=='approved' and 'club_admin' in profile['roles']))
        message=message_input(data['content'],data.get('text_format','legacy'),data.get('mentions'))
        reply=await Commands(member,clock).run(message,data.get('name'),int(data.get('venue_id',member.default_venue_id)))
        return {'reply':reply,'mode':'simulator','notice':'已作用于统一后台的业务数据；没有发送群消息'}

    @app.get('/api/ai/capabilities')
    def ai_capabilities(ctx=Depends(context)):
        from .ai_catalog import features
        from .ai_routing import feature_descriptor
        return {'features': [feature_descriptor(ctx, item['feature_id'], clock) for item in features()]}

    @app.post('/api/ai/navigate')
    async def ai_navigate(data: dict, ctx=Depends(context)):
        from .ai_catalog import local_route
        from .ai_routing import feature_descriptor
        from .ai import unsupported_reason
        from .ai_gateway import interpret_limited
        content = data.get('content')
        if not isinstance(content, str) or not 0 < len(content.strip()) <= 1000:
            raise ValueError('请输入1至1000字业务需求')
        content = re.sub(r'^/?ai[\s:：]+', '', content.strip(), flags=re.I)
        feature_id = local_route(content)
        source = 'local'
        if not feature_id:
            reason = unsupported_reason(content)
            if reason:
                return {'feature': None, 'message': reason, 'source': source}
            navigation_ctx = replace(ctx, chat_id='admin-navigation')
            scope = {'bot_id': navigation_ctx.id, 'chat_id': navigation_ctx.chat_id, 'user_id': navigation_ctx.actor}
            navigation_context = {**scope, 'today': clock().date().isoformat(), 'venue_id': ctx.default_venue_id,
                                  'is_admin': True, 'approved': True}
            proposal = await interpret_limited(navigation_ctx, content, navigation_context, svc(ctx).config(True)['ai'], clock, navigation=True)
            if proposal.get('intent') != 'FeatureRoute':
                return {'feature': None, 'message': proposal.get('explanation', '请补充要办理的业务'), 'source': 'model'}
            if proposal.get('scope') != scope:
                raise PermissionError('功能导航身份无效')
            feature_id = (proposal.get('arguments') or {}).get('feature_id')
            source = 'model'
        feature = feature_descriptor(ctx, feature_id, clock)
        return {'feature': feature, 'message': feature['message'], 'source': source}

    @app.post('/api/ai/test')
    async def ai_test(data: dict, ctx=Depends(context)):
        from .ai_gateway import interpret_limited
        venue_id = int(data.get('venue_id', 1))
        commands = Commands(ctx, clock)
        return await interpret_limited(ctx, data['text'], commands.ai_context(venue_id), svc(ctx).config(True)['ai'], clock)

    @app.get('/api/audit')
    def audits(ctx=Depends(context)):
        return svc(ctx).audit()

    async def receive(bot_id, token, request, generic=False):
        body = await request.json()
        if generic:
            from .integrations import incoming_json
            body = incoming_json(platform, bot_id, token, body)
        if not isinstance(body, dict):
            raise ValueError('回调正文须为JSON对象')
        content = body.get('content')
        ctime = str(body.get('ctime', ''))
        if not isinstance(content, str) or not 0 < len(content) <= 4000 or not re.fullmatch(r'\d{1,20}', ctime):
            raise ValueError('回调字段无效')
        ctx = platform.callback_context(bot_id, token, body)
        channel = platform.channel(ctx.channel_id)
        receive_only = not channel['webhook_url'] and not channel['robot_key']
        from .bot_connection import record_callback
        record_callback(platform,bot_id,token,ctx.channel_id,body)
        # Identical to the old service hash, enabling imported dedupe continuity.
        event_id = hashlib.sha256(json.dumps([ctx.chat_id, ctx.actor, ctime, content], ensure_ascii=False).encode()).hexdigest()
        if generic:
            event_id = hashlib.sha256(('json:' + ctx.id + ':' + body['_event_id']).encode()).hexdigest()
        with connect(ctx.transport_db_path, True) as db:
            if not db.execute("INSERT OR IGNORE INTO inbox(event_id,status,created_at,channel_id) VALUES(?,'processing',?,?)", (event_id, stamp(clock()),ctx.channel_id)).rowcount:
                if receive_only:
                    prior = db.execute('SELECT status,reply FROM inbox WHERE event_id=?',(event_id,)).fetchone()
                    if prior and prior['status']=='completed':
                        return {'result':'ok','reply':prior['reply']}
                return {'result': 'ok'}
        try:
            message = body['_message'] if generic else message_input(content, 'legacy')
            reply = await Commands(ctx, clock).run(message, body.get('creator_name') or body.get('display_name'), ctx.default_venue_id)
        except (ValueError, PermissionError) as exc:
            reply = str(exc)
        except BaseException:
            with connect(ctx.transport_db_path, True) as db:
                db.execute("UPDATE inbox SET status='unknown',error='业务执行异常，禁止自动重放' WHERE event_id=?", (event_id,))
            raise
        with connect(ctx.transport_db_path, True) as db:
            if not receive_only:
                Outbox(ctx).enqueue(reply, 'callback:' + event_id, db)
            db.execute("UPDATE inbox SET status='completed',reply=? WHERE event_id=?", (reply, event_id))
        return {'result':'ok','reply':reply} if receive_only else {'result':'ok'}

    @app.get('/webhooks/{bot_id}/{token}')
    @app.get('/kingsoft/callback/{bot_id}/{token}')
    def callback_check(bot_id: str, token: str):
        platform.callback_context(bot_id, token)
        from .bot_connection import record_callback
        record_callback(platform,bot_id,token)
        return {'result': 'ok'}

    @app.post('/kingsoft/callback/{bot_id}/{token}')
    async def callback(bot_id: str, token: str, request: Request):
        return await receive(bot_id, token, request)

    @app.post('/webhooks/{bot_id}/{token}')
    async def json_callback(bot_id: str, token: str, request: Request):
        return await receive(bot_id, token, request, generic=True)

    @app.get('/kingsoft/callback/{token}')
    def legacy_check(token: str):
        platform.callback_context(platform.legacy_bot(token), token)
        return {'result': 'ok'}

    @app.post('/kingsoft/callback/{token}')
    async def legacy_callback(token: str, request: Request):
        return await receive(platform.legacy_bot(token), token, request)

    from .management_api import management_router
    app.include_router(management_router(platform,principal,context,clock))

    if (ROOT / 'app' / 'static').exists():
        app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')
    return app


# `uvicorn app.main:app --workers 1`; never print tokens or credential-bearing URLs.
app = create_app()
