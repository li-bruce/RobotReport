"""Suggest an eligible robot in the current group without forwarding a booking."""
import re

from .bot_capabilities import require_capability
from .dialogue import Markup, command, render
from .input_formats import NUMBER
from .members import Members
from .membership_terms import MembershipTerms
from .message_parser import LiteralCommand, normalize_message
from .selection_parser import parse_selection
from .service import Service
from .time_rules import bind_clock


def recommendation(ctx, content, clock, venue_id=None):
    if isinstance(content, LiteralCommand) or ctx.source not in ('callback', 'simulator') or not ctx.platform_root or not ctx.chat_id:
        return None
    message = normalize_message(content, ctx.bot_name)
    text, selection = parse_selection(message.text, venue_id or ctx.default_venue_id, today=clock().date())
    match = re.fullmatch(r'报名(?:\+(\d+))?(?:\s+接龙)?', text)
    named = re.match(r'^报名(?:\s+接龙)?[,，]', text)
    if not (match or named or text == '手动报名'):
        return None
    # Pending/suspended members still need their normal registration guidance.
    try:
        Members(ctx, clock).require_approved(bot_access=False)
    except PermissionError:
        return None
    guests = int(match[1] or 0) if match else len(re.split(r'[,，、]', text)) - 1 if named else 0
    if guests > 20:
        return None
    explicit_venue = venue_id is not None or re.search(rf'场地\s*{NUMBER}', message.text)
    automatic = not explicit_venue and not selection['at'] and not selection['number']
    from .security import Platform
    platform = Platform(ctx.platform_root)
    # Shared membership and past callbacks are not a reason to advertise robots
    # in another group. Filter before evaluating any candidate's business access.
    channels = sorted((c for c in platform.list_channels(active_only=True) if c['chat_id'] == ctx.chat_id),
                      key=lambda c: (c['bot_name'], c['id']))
    choices, seen = [], set()
    for channel in channels:
        if channel['bot_id'] == ctx.id or channel['bot_id'] in seen:
            continue
        try:
            candidate = bind_clock(platform.context_for_channel(channel['id'], ctx.actor, False, 'callback'), clock)
            if not candidate.receive_commands:
                continue
            require_capability(candidate, 'enrollment')
            members = Members(candidate, clock)
            members.require_approved()
            target = dict(selection)
            target['venue_id'] = None if automatic else selection['venue_id'] if explicit_venue else candidate.default_venue_id
            service = Service(candidate, clock)
            session = service.resolve_session(**target, registration=True, available_only=True)
            if not session['can_enroll']:
                continue
            service.assert_enrollment_scope(session)
            members.assert_eligible(ctx.actor, session, guests)
            MembershipTerms(candidate, clock).require_enrollment(ctx.actor, session['date'])
        except (PermissionError, ValueError):
            continue
        choices.append(str(command(candidate, message.text)))
        seen.add(candidate.id)
        if len(choices) == 3:
            break
    if not choices:
        return None
    name = Members(ctx, clock).get(ctx.actor).get('name')
    return render(ctx, 'enrollment.choose_bot', name=name, commands=Markup('\n\n'.join(choices)))
