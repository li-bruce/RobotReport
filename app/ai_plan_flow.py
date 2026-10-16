"""Preview complete semantic plans on isolated SQLite snapshots, then confirm once.

Domain APIs own their transactions. A plan is deliberately not advertised as one
cross-database transaction: a runtime failure preserves each completed receipt,
stops dependent steps, and never automatically repeats an uncertain write.
"""
from contextlib import contextmanager, closing
from dataclasses import replace
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import sqlite3
import tempfile

from .ai_operations import execute_step, describe_step, bind_target
from .ai_plan_store import AiPlanStore, PLAN_VERSION
from .db import connect, dumps
from .messages import escape, table


_VOLATILE = {'audit','identity_observations','inbox','outbox','ai_pending','ai_requests','ai_plans',
             'ai_plan_steps','notification_marks','generated_tables','registration_forms','daily_planning',
             'membership_term_audit','tour_audit','casual_audit','workspace_migration_issues',
             'identity_notification_events','identity_notification_deliveries','ai_conversations','availability_notices'}


def _database_digest(path, only=None):
    """Hash domain state, excluding traffic, receipts and display-name touch times."""
    result = []
    with closing(sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro',uri=True)) as db:
        tables = sorted(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        for name in tables:
            if name in _VOLATILE or only is not None and name not in only: continue
            columns = [r[1] for r in db.execute('PRAGMA table_info("' + name.replace('"','""') + '")')]
            if name in ('member_records','profiles'):
                columns = [c for c in columns if c not in ('updated_at','last_seen_at')]
            fields = ','.join('"' + c.replace('"','""') + '"' for c in columns)
            rows = [list(r) for r in db.execute('SELECT ' + fields + ' FROM "' + name.replace('"','""') + '"')]
            rows.sort(key=lambda r: json.dumps(r,ensure_ascii=False,default=str))
            result.append([name, columns, rows])
    return hashlib.sha256(json.dumps(result,ensure_ascii=False,default=str,separators=(',',':')).encode()).hexdigest()


def snapshot(commands):
    ctx = commands.ctx
    result = {'business': _database_digest(ctx.db_path),
              'scope': [ctx.id,ctx.chat_id,ctx.actor,ctx.scope_id,ctx.is_admin,list(ctx.permissions),list(ctx.venue_ids),ctx.venue_restricted,list(ctx.slot_keys) if ctx.slot_keys is not None else None,ctx.default_venue_id,ctx.receive_commands,
                        list(ctx.capabilities) if ctx.capabilities is not None else None]}
    if ctx.platform_root:
        result['registry'] = _database_digest(Path(ctx.platform_root)/'registry.sqlite3',{'bots','bot_channels'})
    with connect(ctx.transport_db_path) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_planning'").fetchone()
        rows = [dict(r) for r in db.execute('SELECT * FROM daily_planning WHERE chat_id=? AND user_id=? ORDER BY venue_id',(ctx.chat_id,ctx.actor))] if exists else []
    result['planning'] = hashlib.sha256(dumps(rows).encode()).hexdigest()
    return result


def _backup(source, target):
    target.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(f'{Path(source).resolve().as_uri()}?mode=ro',uri=True)) as original:
        with closing(sqlite3.connect(target)) as copied:
            original.backup(copied)


@contextmanager
def preview_commands(commands):
    """Only SQLite is copied. No scheduler, webhook worker or network client starts."""
    from .commands import Commands
    ctx = commands.ctx
    with tempfile.TemporaryDirectory(prefix='robort-plan-') as directory:
        root = Path(directory)
        if ctx.platform_root:
            old = Path(ctx.platform_root).resolve()
            def mapped(path): return root / Path(path).resolve().relative_to(old)
            _backup(old/'registry.sqlite3',root/'registry.sqlite3')
            cloned = replace(ctx,root=mapped(ctx.root),transport_path=mapped(ctx.transport_db_path),
                             platform_root=root,bot_root=mapped(ctx.bot_root) if ctx.bot_root else None)
        else:
            cloned = replace(ctx,root=root/'workspace',transport_path=root/'transport.sqlite3',
                             bot_root=None,platform_root=None)
        _backup(ctx.db_path,cloned.db_path)
        if cloned.transport_db_path != cloned.db_path: _backup(ctx.transport_db_path,cloned.transport_db_path)
        yield Commands(cloned,commands.clock)


def _error(exc):
    # Domain validation messages are safe to show; infrastructure exceptions may
    # contain paths, query text or configuration values and must stay server-side.
    if isinstance(exc,(ValueError,PermissionError)):
        return str(exc)[:600]
    return '处理未能完成，请在后台核对记录后重试'


class Plans:
    def __init__(self, commands):
        self.commands = commands
        self.store = AiPlanStore(commands.ctx,commands.clock)

    async def propose(self, proposal, generation=None):
        commands = self.commands
        commands.refresh_context(enforce_access=True)
        expected = {'bot_id':commands.ctx.id,'chat_id':commands.ctx.chat_id,'user_id':commands.ctx.actor}
        if proposal.get('scope') != expected: raise PermissionError('AI计划身份绑定无效')
        steps = deepcopy(proposal.get('steps'))
        if not isinstance(steps,list) or not 1<=len(steps)<=8: raise ValueError('计划须包含1至8个业务步骤')
        from .ai_planner import READ_OPERATIONS
        writes = any(s['operation'] not in READ_OPERATIONS for s in steps)
        if not writes:
            results = []
            for step in steps:
                results.append(await execute_step(commands,step))
            return '**AI 查询 · 后台结果**\n\n' + '\n\n---\n\n'.join(results)
        before = snapshot(commands)
        index = 0
        try:
            with preview_commands(commands) as preview:
                for index, step in enumerate(steps):
                    bind_target(preview,step)
                    await execute_step(preview,step)
        except Exception as exc:
            return f'**计划未通过检查 · 尚未执行**\n\n第{index+1}步：{escape(_error(exc))}\n\n请调整整句话后重新发送。'
        commands.refresh_context(enforce_access=True)
        if snapshot(commands) != before:
            return '业务数据在检查期间发生变化，本次未执行，请重新发送需求。'
        from .ai_conversation import AiConversation, preview_text
        conversation = AiConversation(commands.ctx,commands.clock)
        minutes = commands.service.config()['ai']['confirmation_minutes']
        if generation is None:
            generation, _ = conversation.begin(minutes)
        epoch = commands.ai_policy_epoch()
        with conversation.publication(generation) as db:
            shared = db if commands.ctx.db_path == commands.ctx.transport_db_path else None
            record = self.store.create(steps,before,epoch,commands.clock().date().isoformat(),minutes,_connection=shared)
            conversation.publish(generation,record['token'],steps,record['expires_at'],db)
        return preview_text(steps)

    async def confirm(self, token):
        from .ai_conversation import AiConversation
        from .bot_capabilities import require_capability
        from .members import Members
        self.commands.refresh_context(enforce_access=True)
        require_capability(self.commands.ctx,'ai')
        Members(self.commands.ctx,self.commands.clock).require_approved()
        conversation = AiConversation(self.commands.ctx,self.commands.clock)
        conversation.claim(token)
        try:
            result = await self._execute(token)
            status = self.store.get(token)['status']
        except BaseException:
            conversation.finish(token,'failed')
            raise
        conversation.finish(token,status)
        return result

    async def _execute(self, token):
        commands = self.commands
        commands.refresh_context(enforce_access=True)
        if not commands.service.config()['ai']['enabled']: raise ValueError('AI已关闭，提案不能继续确认；请使用固定指令')
        record = self.store.claim(token,policy_epoch=commands.ai_policy_epoch(),
                                  request_day=commands.clock().date().isoformat(),snapshot=snapshot(commands))
        results = []
        expected_snapshot = record['snapshot']
        for item in record['steps']:
            index = item['index']
            try:
                commands.refresh_context(enforce_access=True)
                if snapshot(commands) != expected_snapshot:
                    raise ValueError('步骤之间的权限、范围或业务数据已变化；已完成步骤保留，请重新核对剩余操作')
            except Exception as exc:
                self.store.fail_step(token,index,_error(exc))
                return '**AI 计划执行中止**\n\n' + self.status(token) + '\n\n已完成的步骤保留；后续步骤没有执行。'
            self.store.start_step(token,index)
            try:
                output = await execute_step(commands,{'operation':item['operation'],'arguments':item['arguments']})
                self.store.finish_step(token,index,output)
                results.append(f"**第{index+1}步 · {escape(describe_step(item)[0])}**\n\n" + output)
            except Exception as exc:
                detail = _error(exc)
                self.store.fail_step(token,index,detail)
                return '**AI 计划执行中止**\n\n' + self.status(token) + '\n\n已完成的步骤保留；后续步骤没有执行。失败步骤如遇系统中断，请核对业务记录后重新提出剩余操作。'
            # The plan's own successful writes form the next baseline. A later
            # configuration step must not invalidate itself by changing an epoch.
            try:
                commands.refresh_context(enforce_access=True)
                expected_snapshot = snapshot(commands)
            except Exception as exc:
                self.store.abort(token,_error(exc))
                return '**AI 计划执行中止**\n\n' + self.status(token) + '\n\n已完成的步骤保留；后续步骤没有执行。'
        self.store.finish(token)
        return '**AI 计划已完成**\n\n' + '\n\n---\n\n'.join(results)

    def status(self, token):
        record = self.store.get(token)
        labels = {'pending':'待确认','running':'处理中或中断待核对','completed':'已完成','partial':'部分完成',
                  'failed':'已中止','cancelled':'已取消','expired':'已过期','done':'完成','skipped':'未执行'}
        rows = [f"{s['index']+1}. **{escape(describe_step(s)[0])} · {labels.get(s['status'],s['status'])}**：{escape(describe_step(s)[1])}" +
                ('\n'+escape(s['error']) if s['error'] else '') for s in record['steps']]
        return f"**AI 计划 · {labels.get(record['status'],record['status'])}**\n\n" + '\n\n'.join(rows)

    def cancel(self, token):
        from .ai_conversation import AiConversation
        conversation = AiConversation(self.commands.ctx,self.commands.clock)
        conversation.claim(token)
        try:
            self.store.cancel(token)
        except BaseException:
            conversation.finish(token,'failed')
            raise
        conversation.finish(token,'cancelled')
        return 'AI计划已取消，未执行业务操作。'


def is_plan(commands, token):
    with connect(commands.ctx.transport_db_path) as db:
        row = db.execute('SELECT command FROM ai_pending WHERE token=? AND bot_id=? AND chat_id=? AND user_id=?',
                         (token,commands.ctx.id,commands.ctx.chat_id,commands.ctx.actor)).fetchone()
    if not row: return False
    payload = json.loads(row['command'])
    # Keep old plans on the plan path so the store can invalidate their preview;
    # never reinterpret an older plan pointer as a single-step confirmation.
    return payload.get('version') in (4, PLAN_VERSION) and payload.get('plan_id') == token
