"""Shared guidance belongs after the whole list, including combined AI results."""
import asyncio
from dataclasses import replace
from datetime import datetime
import json

import pytest

from app.bot_capabilities import ALL_CAPABILITIES
from app.commands import Commands
from app.db import TZ, connect
from app.dialogue import settings_api
from app.members import Members
from app.messages import ListReply, Outbox, join_replies, roster
from app.scheduler import Scheduler
from app.security import Platform
from app.service import Service


@pytest.fixture
def bot(tmp_path):
    platform = Platform(tmp_path, 'list-guidance-test-admin-token-32-characters')
    admin = platform.authenticate('list-guidance-test-admin-token-32-characters')
    created = platform.create(admin, {'name': '喂-大型赛事', 'chat_id': 'test-group', 'capabilities': list(ALL_CAPABILITIES)})
    ctx = platform.context(admin, created['id'])
    at = [datetime(2026, 11, 9, 16, 59, tzinfo=TZ)]
    clock = lambda: at[0]
    manager = Members(ctx, clock)
    manager.touch('小林', '1001', source='admin')
    manager.review('1001', 'approved')
    return platform, admin, ctx, at, clock


def sessions():
    return [dict(id=n, number=n, venue_id=2, date='2026-11-09', venue_name='B2球场',
                 start='17:30' if n == 1 else '20:30', end='20:30' if n == 1 else '22:00',
                 status='open', can_enroll=True, capacity=8, substitute_capacity=4) for n in (1, 2)]


def test_two_empty_sessions_have_one_example_after_both_statuses(bot):
    _, _, ctx, _, _ = bot
    text = roster({'sessions': sessions(), 'registrations': []}, 2, ctx=ctx)
    assert text.count('暂时无人报名。') == 2
    assert text.count('欢迎报名参与！') == text.count('@喂-大型赛事') == 1
    assert text.index('@喂-大型赛事') > text.index('**20:30–22:00**')
    assert '2026-11-09 场地2 第1场 报名' in text
    assert text.endswith('一起活动吧。')


def test_single_session_still_has_its_exact_example(bot):
    _, _, ctx, _, _ = bot
    text = roster({'sessions': sessions()[1:], 'registrations': []}, 2, ctx=ctx)
    assert '2026-11-09 场地2 第2场 报名' in text and text.count('@喂-大型赛事') == 1


@pytest.mark.parametrize('closed', [{'status': 'ended'}, {'can_enroll': False}, {'occupancies': [1], 'booking_notice': '训练'}])
def test_unavailable_sessions_do_not_supply_an_enrollment_example(bot, closed):
    _, _, ctx, _, _ = bot
    items = sessions()
    items[0].update(closed)
    text = roster({'sessions': items, 'registrations': []}, 2, ctx=ctx)
    assert '2026-11-09 场地2 第2场 报名' in text
    items[1].update(closed)
    text = roster({'sessions': items, 'registrations': []}, 2, ctx=ctx)
    assert '@' not in text and '一起活动' not in text


def test_mixed_roster_keeps_table_and_puts_guidance_after_last_section(bot):
    _, _, ctx, _, _ = bot
    row = dict(session_id=2, status='active', name='小林', gender='female', category='formal')
    text = roster({'sessions': sessions(), 'registrations': [row]}, 2, ctx=ctx)
    assert '| 小林 | 女 | 正式 |' in text
    assert text.index('@喂-大型赛事') > text.index('| 小林 |')


def test_existing_custom_template_is_preserved_and_used_once(bot):
    platform, admin, ctx, _, _ = bot
    saved = settings_api(platform, admin, ctx.id)
    custom = '暂时无人报名，欢迎报名参与！发送 {enroll_command}，下场见。'
    settings_api(platform, admin, ctx.id, {'revision': saved['revision'], 'system_name': '聚场',
                 'overrides': {'roster.empty': custom, 'roster.empty_status': '还没有人报名。'}})
    text = roster({'sessions': sessions(), 'registrations': []}, 2, ctx=ctx)
    assert text.count('还没有人报名。') == 2 and text.count('下场见。') == 1
    assert text.index('下场见。') > text.index('**20:30–22:00**')
    assert settings_api(platform, admin, ctx.id)['overrides']['roster.empty'] == custom


def test_combining_lists_never_deduplicates_business_facts():
    first = ListReply('一样的事实', [('signup', '一次报名引导')])
    second = ListReply('一样的事实', [('signup', '另一个报名示例')])
    reply = join_replies([join_replies(['第一部分', first]), second])
    assert reply.count('一样的事实') == 2 and reply.endswith('一次报名引导')
    assert '另一个报名示例' not in reply
    assert json.loads(json.dumps(reply)) == reply


def test_outbox_table_images_and_long_messages_keep_one_final_guidance(bot):
    _, _, ctx, _, clock = bot
    Service(ctx, clock).update_config({'public_base_url': 'https://example.test'})
    row = dict(session_id=2, status='active', name='小林', gender='female', category='formal')
    text = roster({'sessions': sessions(), 'registrations': [row]}, 2, ctx=ctx)
    reply = join_replies([text] * 25)
    outbox = Outbox(ctx)
    ids = outbox.enqueue(reply, 'list-guidance-long')
    chunks = [row['text'] for row in sorted(outbox.list(), key=lambda r: r['id'])]
    assert len(ids) > 1 and ''.join(chunks).count('@喂-大型赛事') == 1
    assert '@喂-大型赛事' in chunks[-1] and all('@喂-大型赛事' not in part for part in chunks[:-1])
    assert ''.join(chunks).count('/public/tables/') == 25
    assert outbox.enqueue('不同内容', 'list-guidance-long') == ids


@pytest.mark.parametrize('kind', ['open', 'closing', 'roster'])
@pytest.mark.parametrize('populated', [0, 1, 2])
def test_scheduled_lists_use_one_footer_and_preserve_delivery_deduplication(bot, kind, populated):
    platform, _, ctx, at, clock = bot
    service = Service(ctx, clock)
    items = service.sessions('2026-11-09')
    for item in items[:populated]:
        service.enroll('1001', '小林', session_id=item['id'])
    service.update_config({'broadcast_rules': [{'id': 'list-test', 'name': '列表', 'enabled': True,
        'kinds': [kind], 'bot_ids': [ctx.id], 'time': '17:00', 'when': {}}]})
    scheduler = Scheduler(platform, clock=clock)
    at[0] = at[0].replace(hour=17, minute=0)
    scheduler.tick()
    scheduler.tick()
    messages = Outbox(ctx).list()
    assert len(messages) == 1
    text = messages[0]['text']
    assert text.count('小林') == populated
    assert text.count('已报名 **1 人**') == populated
    assert text.count('暂时无人报名') == 2 - populated
    assert '第1场' in text and '第2场' in text and '**20:30–22:00**' in text
    if kind == 'roster' and populated == 2:
        assert '@喂-大型赛事' not in text
    else:
        assert text.count('@喂-大型赛事') == 1
        assert text.index('@喂-大型赛事') > text.index('**20:30–22:00**')


@pytest.mark.parametrize('write', [False, True])
def test_ai_multiple_rosters_share_one_footer_in_read_and_confirmed_results(bot, write):
    from app.ai_conversation import AiConversation
    from app.ai_plan_flow import Plans
    platform, _, ctx, _, clock = bot
    Service(ctx, clock).update_config({'ai': {'enabled': True, 'api_key': 'fake-list-test'}})
    user = replace(ctx, actor='1001', is_admin=False, permissions=(), source='callback')
    commands = Commands(user, clock)
    steps = [{'operation': 'Roster', 'arguments': {'venue_id': 1, 'date': '2026-11-09', 'number': n}} for n in (1, 2)]
    if write:
        steps.insert(0, {'operation': 'Register', 'arguments': {'venue_id': 1, 'date': '2026-11-10', 'number': 1, 'guests': 0, 'guest_names': [], 'chain': False}})
    proposal = {'intent': 'BusinessPlan', 'steps': steps, 'scope': {'bot_id': ctx.id, 'chat_id': ctx.chat_id, 'user_id': '1001'}}
    flow = Plans(commands)
    reply = asyncio.run(flow.propose(proposal))
    if write:
        token = AiConversation(user, clock).current()['token']
        reply = asyncio.run(flow.confirm(token))
    assert reply.count('@喂-大型赛事') == 1
    assert reply.index('@喂-大型赛事') > reply.index('**20:30–22:00**')
    assert reply.count('暂时无人报名。') == 2
    assert not Outbox(ctx).list()
