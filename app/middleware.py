"""Reject unauthorized requests before multipart parsing; bound streamed bodies."""
from starlette.responses import JSONResponse
from http.cookies import SimpleCookie
import hmac
from starlette.datastructures import Headers


class BodyTooLarge(Exception):
    pass


class BoundaryMiddleware:
    def __init__(self, app, platform, passwords=None, upload_limit=10 * 1024 * 1024 + 65536):
        self.app, self.platform, self.upload_limit = app, platform, upload_limit
        self.passwords = passwords

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        path = scope['path']
        if path.startswith('/api/'):
            auth = headers.get('authorization', '')
            try:
                if auth.startswith('Bearer ') and auth[7:]:
                    principal = self.platform.authenticate(auth[7:])
                else:
                    cookies = SimpleCookie()
                    cookies.load(headers.get('cookie', ''))
                    token = cookies.get('robort_session')
                    if not token or self.passwords is None:
                        return await JSONResponse({'detail':'请先登录管理后台'},401)(scope,receive,send)
                    current = self.passwords.session(token.value)
                    principal = current['principal']
                    if scope['method'] not in ('GET','HEAD','OPTIONS'):
                        origin = headers.get('origin')
                        expected = scope.get('scheme','http') + '://' + headers.get('host','')
                        if origin and origin != expected:
                            raise PermissionError('请求来源无效')
                        self.passwords._require_csrf(headers.get('x-csrf-token',''), current['csrf_token'])
                scope.setdefault('state', {})['principal'] = principal
                if principal.role != 'platform_admin' and not path.startswith(('/api/bots','/api/channels')):
                    raise PermissionError('机器人运维凭据不能访问统一会员和赛事；请登录管理后台')
                if path.startswith(('/api/bots', '/api/grants', '/api/channels')):
                    if scope['method'] != 'GET':
                        self.platform.require_platform(principal)
                else:
                    self.platform.context(principal, headers.get('x-bot-id'))
            except PermissionError as exc:
                return await JSONResponse({'detail': str(exc)}, 403)(scope, receive, send)
            except ValueError as exc:
                return await JSONResponse({'detail': str(exc)}, 400)(scope, receive, send)
        limit = self.upload_limit if 'multipart/form-data' in headers.get('content-type', '') else 65536
        length = headers.get('content-length', '')
        if length and (not length.isdigit() or int(length) > limit):
            return await JSONResponse({'detail': '请求正文超出大小限制'}, 413)(scope, receive, send)
        consumed, started = 0, False

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message['type'] == 'http.request':
                consumed += len(message.get('body', b''))
                if consumed > limit:
                    raise BodyTooLarge()
            return message

        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                message.setdefault('headers', []).extend([
                    (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'),
                    (b'x-frame-options', b'DENY'),
                    (b'cache-control', b'no-store'),
                ])
                if path.startswith('/public/media/') or path.endswith('/download'):
                    message['headers'].append((b'content-security-policy', b"default-src 'none'; sandbox"))
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracked_send)
        except BodyTooLarge:
            if not started:
                await JSONResponse({'detail': '请求正文超出大小限制'}, 413)(scope, receive, tracked_send)
