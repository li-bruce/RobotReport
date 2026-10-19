"""Organization APIs kept separate from transport and tournament execution."""
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import Response
from .db import connect
from .members import Members
from .people import People


def management_router(platform, principal, context, clock):
    router = APIRouter()

    @router.get('/api/workspace')
    def workspace(ctx=Depends(context)):
        with connect(ctx.db_path) as db:
            counts = {'members':db.execute("SELECT COUNT(*) FROM member_records WHERE status='approved'").fetchone()[0],
                      'applications':db.execute("SELECT COUNT(*) FROM member_records WHERE status='pending'").fetchone()[0],
                      'unregistered':db.execute("SELECT COUNT(*) FROM member_records WHERE status='unregistered'").fetchone()[0],
                      'venues':db.execute('SELECT COUNT(*) FROM venues').fetchone()[0]}
        return {'name':'羽协管理后台','architecture_version':2,'counts':counts}

    @router.get('/api/roles')
    def roles(ctx=Depends(context)):
        return People(ctx,clock).role_directory()

    @router.put('/api/members/{user_id}/roles')
    def set_roles(user_id:str,data:dict,ctx=Depends(context)):
        return People(ctx,clock).set_roles(user_id,data.get('roles'))

    @router.get('/api/registration-applications')
    def applications(user_id:str=None,ctx=Depends(context)):
        return People(ctx,clock).applications(user_id)

    @router.get('/api/identity-notices')
    def identity_notices(limit:int=50,ctx=Depends(context)):
        ctx.require('member.manage')
        from .identity_notifications import IdentityNotifications
        return {'notices':IdentityNotifications(platform,clock).list(limit)}

    @router.post('/api/identity-notices/{event_id}/retry')
    def retry_identity_notice(event_id:str,ctx=Depends(context)):
        ctx.require('member.manage')
        from .identity_notifications import IdentityNotifications
        return IdentityNotifications(platform,clock).retry(event_id,ctx.actor)

    @router.get('/api/guests')
    def guests(ctx=Depends(context)):
        return People(ctx,clock).guests()

    @router.get('/api/member-imports/template')
    def import_template(ctx=Depends(context)):
        from .member_imports import MemberImports
        return Response(MemberImports(ctx,clock).template(),media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="members-template.csv"'})

    @router.post('/api/member-imports/preview')
    async def preview_import(file:UploadFile=File(...),ctx=Depends(context)):
        from .member_imports import MemberImports
        try:
            content=await file.read(2*1024*1024+1)
            if len(content)>2*1024*1024:
                raise ValueError('名册文件不能超过2MiB')
            return MemberImports(ctx,clock).preview(content,file.filename or 'members.csv')
        finally:
            await file.close()

    @router.post('/api/member-imports/commit')
    def commit_import(data:dict,ctx=Depends(context)):
        from .member_imports import MemberImports
        return MemberImports(ctx,clock).commit(data.get('token'))

    @router.get('/api/member-imports/records')
    def imported_records(status:str=None,ctx=Depends(context)):
        from .member_imports import MemberImports
        return MemberImports(ctx,clock).list(status)

    @router.patch('/api/member-imports/records/{record_id}')
    def update_imported_record(record_id:int,data:dict,ctx=Depends(context)):
        from .member_imports import MemberImports
        return MemberImports(ctx,clock).update(record_id,data)

    @router.get('/api/channels')
    def channels(user=Depends(principal)):
        return platform.list_channels(user)

    @router.get('/api/groups')
    def groups(user=Depends(principal)):
        from .bot_groups import BotGroups
        return {'groups':BotGroups(platform).list(user)}

    @router.post('/api/groups')
    def create_group(data:dict,user=Depends(principal)):
        from .bot_groups import BotGroups
        return BotGroups(platform).save(user,data)

    @router.patch('/api/groups/{group_id}')
    def update_group(group_id:str,data:dict,user=Depends(principal)):
        from .bot_groups import BotGroups
        return BotGroups(platform).save(user,data,group_id)

    @router.delete('/api/groups/{group_id}')
    def remove_group(group_id:str,user=Depends(principal)):
        from .bot_groups import BotGroups
        return BotGroups(platform).remove(user,group_id)

    @router.get('/api/bots/{bot_id}/channels')
    def bot_channels(bot_id:str,include_archived:bool=False,user=Depends(principal)):
        return platform.list_channels(user,bot_id,include_archived=include_archived)

    @router.post('/api/bots/{bot_id}/channels')
    def create_channel(bot_id:str,data:dict,user=Depends(principal)):
        return platform.save_channel(user,bot_id,data)

    @router.patch('/api/bots/{bot_id}/channels/{channel_id}')
    def update_channel(bot_id:str,channel_id:str,data:dict,user=Depends(principal)):
        return platform.save_channel(user,bot_id,data,channel_id)

    @router.delete('/api/bots/{bot_id}/channels/{channel_id}')
    def remove_channel(bot_id:str,channel_id:str,user=Depends(principal)):
        return platform.archive_channel(user,bot_id,channel_id)

    def validate_channels(ids,venue_id,kind='large'):
        if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(x,str) for x in ids):
            raise ValueError('请选择有效的群通道列表')
        for channel_id in set(ids):
            ctx = platform.context_for_channel(channel_id)
            from .bot_capabilities import require_capability
            if kind != 'daily':
                require_capability(ctx, 'large_events')
            if (ctx.venue_restricted or ctx.venue_ids) and venue_id not in ctx.venue_ids:
                raise ValueError('所选群通道未开通赛事所在场地')
        return list(dict.fromkeys(ids))

    @router.get('/api/tournaments/{event_id}/organization')
    def organization(event_id:int,ctx=Depends(context)):
        from .event_management import EventManagement
        return EventManagement(ctx,clock).get_organization(event_id)

    @router.put('/api/tournaments/{event_id}/organization')
    def save_organization(event_id:int,data:dict,ctx=Depends(context)):
        from .event_management import EventManagement
        from .tournaments import Tournaments
        event = Tournaments(ctx,clock).get(event_id)
        return EventManagement(ctx,clock).save_organization(event_id,data,
            channel_validator=lambda ids,venue_id: validate_channels(ids,venue_id,event['kind']))

    return router
