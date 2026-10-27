"""Message headings keep one theme across sessions and delivery paths."""
from datetime import datetime
import asyncio
import json
import re

import httpx
import pytest

from app.db import TZ, connect, dumps
from app.messages import Outbox, roster, style_markdown
from app.scheduler import Scheduler
from app.security import Platform


@pytest.fixture
def configured_bot(tmp_path):
    platform = Platform(tmp_path, 'test-theme-admin-token-with-32-characters')
    admin = platform.authenticate('test-theme-admin-token-with-32-characters')
    bot = platform.create(admin, {'name': '样式测试', 'chat_id': '123456', 'robot_key': 'theme-test'})
    ctx = platform.context(admin, bot['id'])
    with connect(ctx.db_path, True) as db:
        db.execute("UPDATE config SET value=? WHERE key='theme'", (dumps({'enabled': True, 'accent': '#62b5e5', 'banner_url': ''}),))
    return platform, ctx


def assert_session_headings(text):
    headings = re.findall(r"<font color='(#[0-9a-f]{6})'><b>(.*?)</b></font>", text)
    assert len(headings) == 2, 'Every session must use the same heading wrapper, including later sessions'
    assert all(color == '#62b5e5' for color, _ in headings)
    assert '第1场' in headings[0][1] and '第2场' in headings[1][1]
    assert not re.search(r'^#{1,6}\s', text, re.M), 'No unthemed oversized headings may remain'
    assert '**17:30–20:30**' in text and '**20:30–22:00**' in text


@pytest.mark.parametrize('populated', [False, True])
def test_all_roster_session_headings_share_the_first_heading_style(configured_bot, populated):
    _, ctx = configured_bot
    sessions = [dict(id=1, number=1, date='2026-10-27', venue_name='B2球场', start='17:30', end='20:30', capacity=8, substitute_capacity=4),
                dict(id=2, number=2, date='2026-10-27', venue_name='B2球场', start='20:30', end='22:00', capacity=8, substitute_capacity=4)]
    rows = [dict(session_id=1, status='active', name='示例会员', gender='female', category='formal')] if populated else []
    with connect(ctx.db_path, True) as db:
        db.execute("UPDATE config SET value=? WHERE key='public_base_url'", (json.dumps('https://example.test'),))
    outbox = Outbox(ctx)
    ids = outbox.enqueue(roster(dict(sessions=sessions, registrations=rows)), key='theme-roster')
    sent = outbox.list()
    assert len(ids) == len(sent) == 1
    text = sent[0]['payload']['markdown']['text']
    assert_session_headings(text)
    assert text.count('暂时无人报名，欢迎报名参与！') == (1 if populated else 2)
    assert text.count('/public/tables/') == int(populated)
    assert outbox.enqueue('重复触发不改写原消息', key='theme-roster') == ids
    assert outbox.list()[0]['text'] == text


def test_scheduled_empty_rosters_use_the_same_shared_theme(configured_bot):
    platform, ctx = configured_bot
    at = datetime(2026, 10, 27, 16, 0, tzinfo=TZ)
    clock = lambda: at
    scheduler = Scheduler(platform, clock=clock)
    at = at.replace(hour=17, minute=30)
    scheduler.tick()
    rows = Outbox(ctx).list()
    assert len(rows) == 1
    assert_session_headings(rows[0]['text'])
    scheduler.tick()
    assert len(Outbox(ctx).list()) == 1


def test_shared_style_handles_intro_nested_headings_and_one_banner():
    theme = {'accent': '#62b5e5', 'banner_url': 'https://example.test/banner.png'}
    source = '\n活动安排\n\n# 今日报名\n\n## 第1场\n\n**17:30–20:30**\n\n### 第2场 ###\n\n欢迎参与！'
    text = style_markdown(source, theme)
    assert text.count('<font color=') == 3
    assert '<b>第2场</b>' in text
    assert text.count(theme['banner_url']) == 1
    assert '**17:30–20:30**' in text and text.endswith('欢迎参与！')
    assert style_markdown(text, theme) == text


@pytest.mark.parametrize('fence', ['```', '~~~~'])
def test_code_examples_and_non_heading_hashes_are_not_restyled(fence):
    example = f'{fence}text\n## 示例内容\n{fence[:2]}\n# 仍为代码\n{fence}\n'
    body = '\\# 转义文字\n#标签\n    ## 缩进代码\n> ## 引用\n[链接](https://example.test/#part)\n'
    result = style_markdown('## 场次\n' + example + body + '### 下一场\n', {})
    assert example in result and body in result
    assert result.count('<font color=') == 2
    assert result.endswith('<b>下一场</b></font>\n')


def test_disabled_theme_and_existing_command_colors_are_preserved():
    source = '## 报名用法\n<font color="#0066b2">报名</font> <font color="#916100">〈姓名〉</font>\n'
    assert style_markdown(source, {'enabled': False, 'banner_url': 'https://example.test/banner.png'}) == source
    text = style_markdown(source, {})
    assert text.splitlines()[1] == source.splitlines()[1]
    assert style_markdown('暂时无人报名', {}) == '暂时无人报名'


def test_styled_titles_keep_escaped_user_data_and_reject_invalid_accent():
    source = roster(dict(sessions=[dict(id=1,number=1,date='2026-10-27',venue_name='<img src=x>',
                                      start='17:30',end='20:30')], registrations=[]))
    text = style_markdown(source, {'accent': "red' onclick='bad"})
    assert 'onclick' not in text and '<img' not in text and '&lt;img src=x&gt;' in text
    assert "<font color='#197661'>" in text


def test_long_multi_session_messages_keep_the_same_headings_after_chunking(configured_bot):
    _, ctx = configured_bot
    source = '\n\n'.join(f'## 第{number}场\n\n**17:30–20:30**\n\n' + '报名记录' * 140 for number in range(1, 15))
    outbox = Outbox(ctx)
    ids = outbox.enqueue(source)
    rows = sorted(outbox.list(), key=lambda row: row['id'])
    assert len(ids) > 1
    text = ''.join(row['text'] for row in rows)
    assert text.count("<font color='#62b5e5'><b>") == 14
    assert all(len(row['text']) <= 4500 for row in rows)
    assert not re.search(r'^#{1,6}\s', text, re.M)


def test_final_webhook_payload_keeps_all_themed_sections(configured_bot):
    platform, ctx = configured_bot
    admin = platform.authenticate('test-theme-admin-token-with-32-characters')
    platform.update(admin, ctx.id, {'webhook_url': 'https://xz.wps.cn/api/v1/webhook/send?key=theme-test'})
    outbox = Outbox(ctx)
    outbox.enqueue('## 第1场\n\n**17:30–20:30**\n\n欢迎参与！\n\n## 第2场\n\n**20:30–22:00**\n\n欢迎参与！')
    captured = []

    async def exercise():
        async def receive(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={'result': 'ok'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
            assert await outbox.deliver_one(platform, live=True, client=client) == 'sent'

    asyncio.run(exercise())
    assert len(captured) == 1 and captured[0]['msgtype'] == 'markdown'
    assert_session_headings(captured[0]['markdown']['text'])
