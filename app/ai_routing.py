"""Feature discovery and navigation; authorization remains in domain services."""
from urllib.parse import urlsplit

from .ai_catalog import get_feature
from .db import now
from .members import Members
from .messages import escape
from .service import Service


def feature_descriptor(ctx, feature_id, clock=now):
    from .scope import refresh_context
    from .bot_policy import feature_enabled, feature_capabilities
    from .bot_capabilities import require_capability
    ctx = refresh_context(ctx)
    feature = get_feature(feature_id)
    if not feature:
        raise ValueError('未找到该业务功能，请重新描述需求')
    if not feature_enabled(ctx, feature):
        for capability in feature_capabilities(feature):
            require_capability(ctx, capability)
    member = Members(ctx, clock).get() or {}
    approved = ctx.is_admin or member.get('status') == 'approved'
    permission = feature['permission']
    from .ai_permissions import feature_permission
    capability=feature_permission(feature)
    allowed = permission == 'public' or approved and (permission != 'admin' or ctx.can(capability))
    result = {**feature, 'allowed': bool(allowed), 'url': None}
    if not allowed:
        result['command'] = None
        result['admin_route'] = None
        result['steps'] = []
        result['message'] = f"已匹配：**{escape(feature['name'])}**。" + ('此功能需要管理员权限，请联系管理员办理。' if permission == 'admin' else '请先发送 注册 并等待会员审核通过。')
        return result
    route = feature.get('admin_route')
    if route:
        base = Service(ctx, clock).config().get('public_base_url', '').rstrip('/')
        parts = urlsplit(base)
        if parts.scheme in ('http', 'https') and parts.hostname and not (parts.username or parts.password or parts.query or parts.fragment):
            result['url'] = base + route
    lines = [f"**功能引导 · {escape(feature['name'])}**", escape(feature['description'])]
    lines.extend(f"{index}. {escape(step)}" for index, step in enumerate(feature['steps'], 1))
    if result['url']:
        lines.append(f"[打开{escape(feature['name'])}]({result['url']})")
    if route:
        lines.append('管理页面需要单独登录后台管理员账号。')
    if feature['mode'] == 'guide':
        lines.append('已匹配功能，尚未执行操作。')
    result['message'] = '\n\n'.join(lines)
    return result
