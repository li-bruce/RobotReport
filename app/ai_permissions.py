"""Capability names shared by model discovery and execution checks."""

OPERATION_PERMISSIONS = {
    'AllStats':'activity.manage', 'ToggleAI':'system.manage',
    'UpdateReminders':'activity.manage', 'UpdateSession':'activity.manage',
    'AddMembershipPeriod':'member.manage', 'AddActivitySlot':'activity.manage',
    'PendingMembers':'member.manage', 'ReviewMember':'member.manage',
    'ReserveVenue':'activity.manage', 'ReleaseVenue':'activity.manage', 'ToggleHoliday':'activity.manage',
}


def permits(context, operation):
    from .bot_policy import model_operation_enabled
    if not model_operation_enabled(context, operation):
        return False
    permission = OPERATION_PERMISSIONS.get(operation)
    return not permission or context.get('is_admin') is True or permission in context.get('permissions', ())


def feature_permission(feature):
    if feature['permission']!='admin':
        return None
    key=feature['feature_id']
    if key=='membership.roles': return 'roles.manage'
    if key.startswith('membership.'): return 'member.manage'
    if key.startswith(('registration.','venue.','stats.')): return 'activity.manage'
    if key.startswith(('large.','daily.','casual.')): return 'event.manage'
    if key.startswith('messages.'): return 'message.manage'
    if key.startswith('bots.'): return 'bot.manage'
    return 'system.manage'
