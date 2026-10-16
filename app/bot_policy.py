"""Business module routing shared by AI discovery and execution.

Robot capabilities limit entry points. They never grant a person's role, bypass
membership, or constrain the separately authenticated workspace administration.
"""
from types import SimpleNamespace

from .bot_capabilities import supports, require_capability


OPERATION_CAPABILITIES = {
    'StartDailyPlan': ('daily_events',), 'DailyPlan': ('daily_events',),
    'Register': ('enrollment',), 'CancelRegistration': ('enrollment',),
    'Roster': ('enrollment',), 'PersonalStats': ('statistics',),
    'AllStats': ('statistics', 'management'), 'SelfLevel': ('registration',),
    'EventRegistration': ('large_events',), 'ToggleAI': ('ai', 'management'),
    'PendingMembers': ('management',),
    'ReviewMember': ('management',),
    'AddMembershipPeriod': ('management',),
    'UpdateReminders': ('enrollment', 'management'),
    'UpdateSession': ('enrollment', 'management'),
    'AddActivitySlot': ('enrollment', 'management'),
    'QueryOccupancy': ('enrollment',),
    'ReserveVenue': ('management',), 'ReleaseVenue': ('management',), 'ToggleHoliday': ('management',),
}


def feature_capabilities(feature):
    key = feature['feature_id']
    required = []
    if key == 'membership.identity' or key in ('help.commands', 'help.manual'):
        pass
    elif key.startswith('membership.'):
        if feature['permission'] != 'admin':
            required.append('enrollment' if key == 'membership.guests' else 'registration')
    elif key.startswith(('registration.', 'venue.')):
        required.append('enrollment')
    elif key.startswith('large.') or key in ('tournament.enroll', 'tournament.organization'):
        required.append('large_events')
    elif key.startswith('daily.'):
        required.append('daily_events')
    elif key.startswith('casual.'):
        required.append('match_records')
    elif key.startswith('messages.'):
        required.append('messages')
    elif key.startswith('stats.'):
        required.append('statistics')
    elif key in ('help.ai', 'help.ai_status', 'system.ai'):
        required.append('ai')
    if feature['permission'] == 'admin':
        required.append('management')
    return tuple(dict.fromkeys(required))


def feature_enabled(ctx, feature):
    return all(supports(ctx, key) for key in feature_capabilities(feature))


def model_operation_enabled(context, operation):
    ctx = SimpleNamespace(capabilities=context.get('bot_capabilities'))
    return all(supports(ctx, key) for key in OPERATION_CAPABILITIES.get(operation, ()))


def require_operation(ctx, operation):
    require_capability(ctx, 'ai')
    for key in OPERATION_CAPABILITIES.get(operation, ()):
        require_capability(ctx, key)


def require_event(ctx, event):
    # Persisted kind is authoritative; names, participant roles and AI text are
    # not enough to distinguish the two event workflows.
    require_capability(ctx, 'daily_events' if event.get('kind') == 'daily' else 'large_events')
