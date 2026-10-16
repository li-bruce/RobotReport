"""Per-bot and per-member AI budgets; normalized audit only, no prompt histories."""
import asyncio
from datetime import timedelta
from .db import connect, stamp, now, audit

_semaphores = {}


async def interpret_limited(ctx, text, context, config, clock=now, *, navigation=False):
    from .scope import refresh_context
    from .bot_capabilities import require_capability
    ctx = refresh_context(ctx)
    from .bot_access import require_access
    require_access(ctx,clock)
    require_capability(ctx, 'ai')
    context = {**context, 'bot_capabilities': ctx.capabilities}
    from .ai import interpret
    if navigation:
        from .ai import classify_feature
        interpret = classify_feature
    if not config.get('enabled'):
        return await interpret(text, context, config)
    at = clock()
    with connect(ctx.transport_db_path, True) as db:
        cutoff = stamp(at - timedelta(minutes=1))
        count = db.execute('SELECT COUNT(*) FROM ai_requests WHERE created_at>?', (cutoff,)).fetchone()[0]
        member_count = db.execute('SELECT COUNT(*) FROM ai_requests WHERE created_at>? AND chat_id=? AND user_id=?', (cutoff, ctx.chat_id, ctx.actor)).fetchone()[0]
        if count >= 20 or member_count >= 5:
            return {'command': None, 'explanation': 'AI请求过于频繁，请稍后重试或使用固定指令。', 'error': 'rate_limit'}
        db.execute('INSERT INTO ai_requests(chat_id,user_id,created_at) VALUES(?,?,?)', (ctx.chat_id, ctx.actor, stamp(at)))
        db.execute('DELETE FROM ai_requests WHERE created_at<?', (stamp(at - timedelta(days=1)),))
    key = id(asyncio.get_running_loop())
    semaphore = _semaphores.setdefault(key, asyncio.Semaphore(2))
    if semaphore.locked():
        return {'command': None, 'explanation': 'AI正在处理其他请求，请稍后重试。', 'error': 'busy'}
    async with semaphore:
        result = await interpret(text, context, config)
    with connect(ctx.db_path, True) as db:
        audit(db, ctx.actor, 'ai.navigate' if navigation else 'ai.interpret', {'intent': result.get('intent'), 'error': result.get('error'), 'confirmation': result.get('requires_confirmation', False)})
    return result
