"""Member web access: short-lived bot links and callback-confirmed passwords."""
from datetime import timedelta
import hmac
import json
import re
import secrets

from .db import connect, dumps, now, stamp
from .password_auth import PasswordAuth
from .members import Members
from .service import Service, day_value
from .bot_capabilities import require_capability
from .time_rules import bind_clock


class EnrollmentPortal:
    def __init__(self, platform, clock=now):
        self.platform, self.clock, self.path = platform, clock, platform.path
        with connect(self.path) as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS member_web_accounts(
                scope_id TEXT NOT NULL,user_id TEXT NOT NULL,login_name TEXT NOT NULL UNIQUE,
                salt TEXT NOT NULL,password_hash TEXT NOT NULL,iterations INTEGER NOT NULL,
                updated_at TEXT NOT NULL,PRIMARY KEY(scope_id,user_id));
            CREATE TABLE IF NOT EXISTS member_web_tokens(
                token_hash TEXT PRIMARY KEY,kind TEXT NOT NULL,user_id TEXT NOT NULL,
                bot_id TEXT NOT NULL,channel_id TEXT NOT NULL,chat_id TEXT NOT NULL,
                csrf_secret TEXT NOT NULL,expires_at TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS member_web_bindings(
                code_hash TEXT PRIMARY KEY,session_hash TEXT NOT NULL,user_id TEXT NOT NULL,
                bot_id TEXT NOT NULL,channel_id TEXT NOT NULL,expires_at TEXT NOT NULL,
                confirmed_at TEXT NOT NULL DEFAULT '',consumed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS member_web_failures(
                id INTEGER PRIMARY KEY,peer_hash TEXT NOT NULL,account_hash TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS member_web_expiry ON member_web_tokens(expires_at);
            CREATE INDEX IF NOT EXISTS member_web_attempt_time ON member_web_failures(created_at);
            ''')

    def _context(self, row):
        ctx=bind_clock(self.platform.context_for_channel(row['channel_id'],row['user_id'],False,'callback'),self.clock)
        if (ctx.id,ctx.chat_id)!=(row['bot_id'],row['chat_id']) or not ctx.receive_commands:
            raise PermissionError('机器人连接已变更，请回群重新发送 手动报名')
        require_capability(ctx,'enrollment')
        Members(ctx,self.clock).require_approved(allow_admin=False)
        return ctx

    def _issue(self, db, ctx, kind, minutes):
        token=secrets.token_urlsafe(36)
        secret=secrets.token_hex(32)
        expires=stamp(self.clock()+timedelta(minutes=minutes))
        db.execute('INSERT INTO member_web_tokens VALUES(?,?,?,?,?,?,?,?,?)',
                   (PasswordAuth._digest(token),kind,ctx.actor,ctx.id,ctx.channel_id,ctx.chat_id,secret,expires,stamp(self.clock())))
        return {'token':token,'csrf_token':PasswordAuth._csrf(token,secret),'expires_at':expires}

    def link(self, ctx):
        if ctx.source not in ('callback','simulator') or not ctx.channel_id:
            raise PermissionError('请从群内机器人获取报名链接')
        ctx=self._context({'channel_id':ctx.channel_id,'user_id':ctx.actor,'bot_id':ctx.id,'chat_id':ctx.chat_id})
        base=Service(ctx,self.clock).config().get('public_base_url','').rstrip('/')
        if not base:
            raise ValueError('管理员尚未配置报名网页地址')
        with connect(self.path,True) as db:
            db.execute('DELETE FROM member_web_tokens WHERE expires_at<=?',(stamp(self.clock()),))
            db.execute("DELETE FROM member_web_tokens WHERE kind='link' AND user_id=? AND bot_id=?",(ctx.actor,ctx.id))
            result=self._issue(db,ctx,'link',20)
        return base+'/enroll#'+result['token']

    def exchange(self, token):
        PasswordAuth._token(token)
        with connect(self.path,True) as db:
            row=db.execute("SELECT * FROM member_web_tokens WHERE token_hash=? AND kind='link' AND expires_at>?",
                           (PasswordAuth._digest(token),stamp(self.clock()))).fetchone()
            if not row:
                raise PermissionError('报名链接已使用或过期，请回群重新发送 手动报名')
            ctx=self._context(row)
            db.execute('DELETE FROM member_web_tokens WHERE token_hash=?',(row['token_hash'],))
            return self._issue(db,ctx,'session',60)

    def session(self, token, csrf=None):
        PasswordAuth._token(token)
        with connect(self.path) as db:
            row=db.execute("SELECT * FROM member_web_tokens WHERE token_hash=? AND kind IN ('session','password_session') AND expires_at>?",
                           (PasswordAuth._digest(token),stamp(self.clock()))).fetchone()
        if not row:
            raise PermissionError('请登录后继续，或回群发送 手动报名')
        actual=PasswordAuth._csrf(token,row['csrf_secret'])
        if csrf is not None:
            PasswordAuth._require_csrf(csrf,actual)
        return self._context(row), dict(row)|{'csrf_token':actual}

    def _destinations(self, actor):
        result=[]
        for channel in self.platform.list_channels(active_only=True):
            try:
                ctx=self._context({'channel_id':channel['id'],'user_id':actor,'bot_id':channel['bot_id'],'chat_id':channel['chat_id']})
            except (ValueError,PermissionError):
                continue
            result.append(ctx)
        return result

    def login(self, login_name, password, peer):
        name=str(login_name or '').strip().casefold()[:100]
        peer_hash=PasswordAuth._digest(str(peer)[:256]); account_hash=PasswordAuth._digest(name)
        cutoff=stamp(self.clock()-timedelta(minutes=10)); failed=False
        with connect(self.path,True) as db:
            db.execute('DELETE FROM member_web_failures WHERE created_at<=?',(cutoff,))
            count=db.execute('SELECT COUNT(*) FROM member_web_failures WHERE peer_hash=? OR account_hash=?',(peer_hash,account_hash)).fetchone()[0]
            total=db.execute('SELECT COUNT(*) FROM member_web_failures').fetchone()[0]
            if count>=6 or total>=150:
                raise PermissionError('登录失败次数较多，请稍后再试')
            row=db.execute('SELECT * FROM member_web_accounts WHERE login_name=?',(name,)).fetchone()
            if re.fullmatch(r'\+?\d{7,15}',name):
                with connect(self.platform.workspace_context().db_path) as business:
                    users=[r['user_id'] for r in business.execute('SELECT user_id FROM member_records WHERE chat_id=? AND phone=?',('club',name))]
                row=db.execute('SELECT * FROM member_web_accounts WHERE scope_id=? AND user_id=?',('club',users[0])).fetchone() if len(users)==1 else None
            if not PasswordAuth._verify(password,row):
                db.execute('INSERT INTO member_web_failures(peer_hash,account_hash,created_at) VALUES(?,?,?)',(peer_hash,account_hash,stamp(self.clock())))
                failed=True
            else:
                contexts=self._destinations(row['user_id'])
                if not contexts:
                    raise PermissionError('目前没有可用的报名机器人，请联系管理员')
                result=self._issue(db,contexts[0],'password_session',24*60)
        if failed:
            raise PermissionError('登录名、手机号或密码不正确')
        return result

    def logout(self, token, csrf):
        self.session(token,csrf)
        with connect(self.path,True) as db:
            db.execute('DELETE FROM member_web_tokens WHERE token_hash=?',(PasswordAuth._digest(token),))

    def switch_bot(self, token, csrf, bot_id):
        ctx,row=self.session(token,csrf)
        if row['kind']!='password_session':
            raise PermissionError('此链接仅用于当前机器人；切换机器人请先设置密码并登录')
        target=next((c for c in self._destinations(ctx.actor) if c.id==bot_id),None)
        if not target:
            raise PermissionError('该机器人暂不支持您的报名')
        with connect(self.path,True) as db:
            db.execute('UPDATE member_web_tokens SET bot_id=?,channel_id=?,chat_id=? WHERE token_hash=?',
                       (target.id,target.channel_id,target.chat_id,row['token_hash']))

    def info(self, token):
        ctx,row=self.session(token)
        profile=Members(ctx,self.clock).require_approved(allow_admin=False)
        with connect(self.path) as db:
            account=db.execute('SELECT login_name FROM member_web_accounts WHERE scope_id=? AND user_id=?',(ctx.scope_id,ctx.actor)).fetchone()
        contexts=self._destinations(ctx.actor) if row['kind']=='password_session' else [ctx]
        return {'name':profile['name'],'bot_id':ctx.id,'bot_name':ctx.bot_name,'csrf_token':row['csrf_token'],
                'login_name':account['login_name'] if account else '', 'today':self.clock().date().isoformat(),
                'bots':[{'id':c.id,'name':c.bot_name} for c in contexts],
                'expires_at':row['expires_at']}

    def choices(self, token, day):
        ctx,_=self.session(token)
        target=day_value(day)
        if not self.clock().date() <= target <= self.clock().date()+timedelta(days=366):
            raise ValueError('请选择今天起366天内的日期')
        service=Service(ctx,self.clock)
        result=[]
        from .membership_terms import MembershipTerms
        for session in service.registration_sessions(day):
            data=service.registrations(day,session['venue_id'],session['id'],registration=True)
            own=[{'name':r['name'],'guest_name':r['guest_name'],'guest_index':r['guest_index'],
                  'category':r['category'],'can_cancel':session['can_manage']} for r in data['registrations']
                 if r['user_id']==ctx.actor and r['status']=='active']
            reason=session.get('booking_notice') if session.get('occupancies') else '场次已结束' if session['status']=='ended' else ''
            try:
                Members(ctx,self.clock).assert_eligible(ctx.actor,session)
                MembershipTerms(ctx,self.clock).require_enrollment(ctx.actor,day)
            except (ValueError,PermissionError) as exc:
                reason=str(exc)
            result.append({k:session[k] for k in ('id','venue_id','venue_name','number','start','end','count','capacity','substitute_capacity','external_substitute','can_manage')} |
                          {'can_enroll':session['can_enroll'] and not reason,'reason':reason or '', 'own':own})
        return {'day':day,'sessions':result}

    def act(self, token, csrf, data):
        ctx,_=self.session(token,csrf)
        if set(data)-{'action','request_id','day','venue_id','session_id','guests','guest_count'}:
            raise ValueError('报名字段无效')
        action=data.get('action')
        if action not in ('enroll','cancel','cancel_guests'):
            raise ValueError('请选择报名或取消')
        if type(data.get('venue_id')) is not int or type(data.get('session_id')) is not int:
            raise ValueError('请重新选择场地和场次')
        service=Service(ctx,self.clock)
        target=day_value(data.get('day'))
        if not self.clock().date() <= target <= self.clock().date()+timedelta(days=366):
            raise ValueError('请选择今天起366天内的日期')
        selection={key:data.get(key) for key in ('day','venue_id','session_id','request_id')}
        if not selection['request_id']:
            raise ValueError('缺少操作标识，请刷新后再试')
        if not isinstance(selection['day'],str):
            raise ValueError('请选择日期')
        guests=data.get('guests',[])
        if not isinstance(guests,list) or len(guests)>20 or any(not isinstance(v,str) for v in guests):
            raise ValueError('同行名单格式无效')
        from .enrollment_receipts import ReplayedEnrollment
        try:
            if action=='enroll':
                count=data.get('guest_count',len(guests))
                service.enroll(ctx.actor,'',guests=count,guest_names=guests or None,**selection)
            elif action=='cancel':
                service.cancel(ctx.actor,**selection)
            else:
                service.cancel_guests(ctx.actor,guests,**selection)
        except ReplayedEnrollment:
            pass
        return {'message':'报名成功' if action=='enroll' else '已取消报名', **self.choices(token,selection['day'])}

    def request_binding(self, token, csrf):
        ctx,row=self.session(token,csrf)
        code=secrets.token_hex(5).upper()
        with connect(self.path,True) as db:
            db.execute('DELETE FROM member_web_bindings WHERE session_hash=? OR expires_at<=?',(row['token_hash'],stamp(self.clock())))
            db.execute('INSERT INTO member_web_bindings(code_hash,session_hash,user_id,bot_id,channel_id,expires_at) VALUES(?,?,?,?,?,?)',
                       (PasswordAuth._digest(code),row['token_hash'],ctx.actor,ctx.id,ctx.channel_id,stamp(self.clock()+timedelta(minutes=10))))
        name=next(iter(ctx.bot_aliases),ctx.bot_name)
        return {'command':f'@{name} 确认网页登录 {code}'}

    def confirm_binding(self, ctx, code):
        # Only authenticated callback identity can approve persistent credentials.
        if ctx.source!='callback':
            raise PermissionError('请在群内由本人确认网页登录')
        ctx=self._context({'channel_id':ctx.channel_id,'user_id':ctx.actor,'bot_id':ctx.id,'chat_id':ctx.chat_id})
        with connect(self.path,True) as db:
            row=db.execute('SELECT * FROM member_web_bindings WHERE code_hash=? AND expires_at>? AND consumed=0',
                           (PasswordAuth._digest(code.upper()),stamp(self.clock()))).fetchone()
            if not row or (row['user_id'],row['bot_id'],row['channel_id'])!=(ctx.actor,ctx.id,ctx.channel_id):
                raise PermissionError('确认信息与本人不匹配，或已过期，请在报名页面重新发起')
            db.execute('UPDATE member_web_bindings SET confirmed_at=? WHERE code_hash=?',(stamp(self.clock()),row['code_hash']))
        return Members(ctx,self.clock).get(ctx.actor)['name']

    def binding_status(self, token):
        _,session=self.session(token)
        with connect(self.path) as db:
            row=db.execute('SELECT confirmed_at FROM member_web_bindings WHERE session_hash=? AND expires_at>? AND consumed=0',
                           (session['token_hash'],stamp(self.clock()))).fetchone()
        return {'confirmed':bool(row and row['confirmed_at'])}

    def save_account(self, token, csrf, login_name, password):
        ctx,session=self.session(token,csrf)
        name=str(login_name or '').strip().casefold()
        if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,39}',name):
            raise ValueError('登录名需3至40位，以字母开头，可包含数字、点、下划线或横线')
        salt,hashed,iterations=PasswordAuth._hash(password)
        with connect(self.path,True) as db:
            row=db.execute("SELECT * FROM member_web_bindings WHERE session_hash=? AND expires_at>? AND confirmed_at<>'' AND consumed=0",
                           (session['token_hash'],stamp(self.clock()))).fetchone()
            if not row or row['user_id']!=ctx.actor:
                raise PermissionError('请先在群里由本人确认网页登录')
            duplicate=db.execute('SELECT user_id FROM member_web_accounts WHERE login_name=? AND user_id<>?',(name,ctx.actor)).fetchone()
            if duplicate:
                raise ValueError('此登录名已被使用，请换一个')
            db.execute('''INSERT INTO member_web_accounts VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(scope_id,user_id) DO UPDATE SET login_name=excluded.login_name,salt=excluded.salt,
                password_hash=excluded.password_hash,iterations=excluded.iterations,updated_at=excluded.updated_at''',
                (ctx.scope_id,ctx.actor,name,salt,hashed,iterations,stamp(self.clock())))
            db.execute('UPDATE member_web_bindings SET consumed=1 WHERE session_hash=?',(session['token_hash'],))
            db.execute('DELETE FROM member_web_tokens WHERE user_id=? AND token_hash<>?',(ctx.actor,session['token_hash']))
            db.execute("UPDATE member_web_tokens SET kind='password_session' WHERE token_hash=?",(session['token_hash'],))
        return {'message':'登录信息已保存'}


def mount_portal(app, platform, clock, root):
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse
    portal=EnrollmentPortal(platform,clock)
    cookie='gather_member'

    def origin(request):
        value=request.headers.get('origin')
        if value and value!=str(request.base_url).rstrip('/'):
            raise PermissionError('请求来源无效')

    def credentials(request):
        origin(request)
        return request.cookies.get(cookie,''),request.headers.get('x-csrf-token','')

    def signed(result, request):
        response=JSONResponse({k:v for k,v in result.items() if k!='token'})
        response.set_cookie(cookie,result['token'],httponly=True,secure=request.url.scheme=='https',samesite='strict',path='/member',max_age=86400)
        return response

    @app.get('/enroll')
    def page():
        return FileResponse(root/'app'/'static'/'enroll.html')

    @app.post('/member/exchange')
    def exchange(data:dict,request:Request):
        origin(request)
        return signed(portal.exchange(data.get('token','')),request)

    @app.post('/member/login')
    def login(data:dict,request:Request):
        origin(request)
        return signed(portal.login(data.get('login'),data.get('password'),request.client.host if request.client else 'unknown'),request)

    @app.get('/member/session')
    def session(request:Request):
        return portal.info(request.cookies.get(cookie,''))

    @app.post('/member/logout')
    def logout(request:Request):
        portal.logout(*credentials(request))
        response=JSONResponse({'ok':True}); response.delete_cookie(cookie,path='/member')
        return response

    @app.post('/member/bot')
    def switch(data:dict,request:Request):
        portal.switch_bot(*credentials(request),data.get('bot_id'))
        return portal.info(request.cookies.get(cookie,''))

    @app.get('/member/sessions')
    def choices(day:str,request:Request):
        return portal.choices(request.cookies.get(cookie,''),day)

    @app.post('/member/enrollments')
    def act(data:dict,request:Request):
        return portal.act(*credentials(request),data)

    @app.post('/member/account/request')
    def binding(request:Request):
        return portal.request_binding(*credentials(request))

    @app.get('/member/account/status')
    def binding_status(request:Request):
        return portal.binding_status(request.cookies.get(cookie,''))

    @app.post('/member/account')
    def account(data:dict,request:Request):
        return portal.save_account(*credentials(request),data.get('login'),data.get('password'))

    app.state.enrollment_portal=portal
