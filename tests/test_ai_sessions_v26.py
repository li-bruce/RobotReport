"""User-reported date/ordinal regressions, with no external model calls."""
import asyncio
from datetime import date
import re
from unittest.mock import patch

import pytest

from app import ai
from app.commands import parse
from app.db import connect
from tests.test_ai import CONFIG, FakeRunnable, context
from tests.test_core import CoreFixture


@pytest.mark.parametrize('text', [
    '@机器人 2026-10-14 第二场 报名', '2026-10-14第2场报名',
    '2026年10月14日 第二 场 报名', '2026/10/14 二场 报名',
    '报名 2026-10-14 第二场', '第二场报名2026-10-14',
    '2026-10-14报名第二场', '场地1第二场2026-10-14报名',
    '<at user_id="bot">机器人</at>2026-10-14第２场报名',
])
def test_fixed_registration_accepts_literal_order_and_chinese_ordinals(text):
    assert parse(text, today=date(2026, 10, 13), bot_name='机器人') == (
        '报名', {'day': '2026-10-14', 'venue_id': 1, 'at': None, 'number': 2})


@pytest.mark.parametrize('text,opcode,number', [
    ('报名明天第一场', '报名', 1), ('明天第二場报名', '第二場报名', None),
    ('取消报名明天第2场', '取消报名', 2), ('名单 明天 第二场', '名单', 2),
    ('明天第一场报名+2 接龙', '报名+2 接龙', 1),
    ('报名明天第一场，张三，李四', '报名，张三，李四', 1),
])
def test_relative_selectors_use_business_date_and_preserve_guests(text, opcode, number):
    parsed, values = parse(text, today='2026-12-31')
    assert parsed == opcode
    assert values == {'day': '2027-01-01', 'venue_id': 1, 'at': None, 'number': number}


@pytest.mark.parametrize('text', [
    '2026-10-14第一场第二场报名', '明天后天报名',
    '场地1场地2明天报名', '明天0场报名', '明天第二场18:00报名',
    '2026-02-30第2场报名', '明天24:01报名',
])
def test_fixed_conflicting_or_invalid_selectors_never_choose_a_default(text):
    with pytest.raises(ValueError):
        parse(text, today='2026-10-13')


@pytest.mark.parametrize('text,score', [('第1场21:19', '21:19'), ('第二场 21：19，18：21，21：16', '21：19，18：21，21：16')])
def test_score_points_after_ordinal_are_not_mistaken_for_clock(text, score):
    opcode, values = parse(text, today='2026-10-13')
    assert opcode == score
    assert values['number'] in (1, 2)
    assert values['at'] is None


def test_action_first_clock_is_supported_without_a_date():
    assert parse('报名20:30', today='2026-10-13') == (
        '报名', {'day': None, 'venue_id': 1, 'at': '20:30', 'number': None})
    assert parse('21:19', today='2026-10-13')[0] == '21:19'


@pytest.mark.parametrize('text,name,number,opcode', [
    ('报名明天第一场', 'Register', 1, '报名'),
    ('明天第二场帮我报名', 'Register', 2, '报名'),
    ('帮我报名明天第 2 场的活动', 'Register', 2, '报名'),
    ('报名 明天 2场 接龙', 'Register', 2, '报名 接龙'),
    ('取消明天第二场的报名', 'CancelRegistration', 2, '取消报名'),
    ('明天第一场名单', 'Roster', 1, '名单'),
    ('明天第二场组织比赛', 'StartDailyPlan', 2, '组织比赛 双打'),
])
def test_ai_ordinal_grounding_preserves_all_dated_intents(text, name, number, opcode):
    with patch.object(ai, '_make_runnable', return_value=FakeRunnable(name)):
        result = asyncio.run(ai.interpret(text, context(), CONFIG))
    assert result.get('command') == f'场地1 2026-10-13 第{number}场 {opcode}', result
    assert result['arguments']['number'] == number
    assert result['arguments']['hour'] is None
    assert result['requires_confirmation'] == (name != 'Roster')


@pytest.mark.parametrize('args,text', [
    ({'number': 2}, '报名明天第一场'),
    ({'number': 1}, '报名明天'),
    ({'number': '1'}, '报名明天第一场'),
    ({'number': True}, '报名明天第一场'),
    ({'number': 0}, '报名明天第一场'),
    ({'session_id': 1}, '报名明天第一场'),
    ({'number': 1, 'hour': 18}, '报名明天第一场'),
    ({}, '明天第一场或第二场报名'),
    ({}, '明天不要第一场报名'),
    ({}, '明天第一场18:00报名'),
    ({}, '明天最后一场报名'),
    ({}, '明天所有场报名'),
    ({}, '明天1-2场报名'), ({}, '明天一或二场报名'), ({}, '明天1000场报名'),
])
def test_ai_rejects_guessed_mismatched_multiple_or_malformed_ordinals(args, text):
    with patch.object(ai, '_make_runnable', return_value=FakeRunnable('Register', args)):
        result = asyncio.run(ai.interpret(text, context(), CONFIG))
    assert result['command'] is None, result
    assert result['error'] in ('clarification_required', 'intent_invalid')


@pytest.mark.parametrize('text,expected', [
    ('报名明天第一场', '场地1 2026-10-13 第1场 报名'),
    ('明天第二场取消报名', '场地1 2026-10-13 第2场 取消报名'),
    ('明天第2场名单', '场地1 2026-10-13 第2场 名单'),
])
def test_no_tool_call_only_falls_back_to_complete_literal_session_request(text, expected):
    fake = FakeRunnable(calls=[], content='模型解释文本，声称已经执行；禁止回显')
    with patch.object(ai, '_make_runnable', return_value=fake):
        result = asyncio.run(ai.interpret(text, context(), CONFIG))
    assert result['command'] == expected, result
    assert '模型解释' not in str(result)
    assert result['requires_confirmation'] == ('名单' not in expected)


@pytest.mark.parametrize('text,fake', [
    ('明天第一场报名，然后关闭AI', FakeRunnable(calls=[])),
    ('明天第一场报名，请替别人报名', FakeRunnable(calls=[])),
    ('明天第一场报名', FakeRunnable(calls=[], invalid=[{'error': 'bad json'}])),
    ('明天第一场报名', FakeRunnable(calls=[{'name': 'Register', 'args': {'number': 2}}])),
    ('明天第一场报名', FakeRunnable(calls=[{'name': 'Register', 'args': {}}, {'name': 'Help', 'args': {}}])),
])
def test_fallback_does_not_repair_unsafe_intent_or_conflicting_model(text, fake):
    with patch.object(ai, '_make_runnable', return_value=fake):
        result = asyncio.run(ai.interpret(text, context(), CONFIG))
    assert result['command'] is None, result


def test_fallback_keeps_membership_and_venue_permissions():
    for trusted in (context(approved=False), context()):
        text = '明天第一场报名' if not trusted.get('approved', True) else '场地2明天第一场报名'
        with patch.object(ai, '_make_runnable', return_value=FakeRunnable(calls=[])):
            result = asyncio.run(ai.interpret(text, trusted, CONFIG))
        assert result['command'] is None
        assert result['error'] == 'forbidden'


class OrdinalBusinessBridgeTests(CoreFixture):
    def send(self, text):
        return asyncio.run(self.commands().run(text))

    def test_fixed_screenshot_enroll_and_cancel_target_second_session(self):
        reply = self.send('@一号 2026-10-14 第二场 报名')
        assert '20:30' in reply
        rows = self.s.registrations('2026-10-14')['registrations']
        assert len(rows) == 1
        second = self.s.sessions('2026-10-14')[1]
        assert rows[0]['session_id'] == second['id']
        assert '已取消' in self.send('取消报名 2026-10-14第二场')
        assert self.s.registrations('2026-10-14')['registrations'][0]['status'] == 'cancelled'

    def test_ai_screenshot_proposes_first_session_then_confirms_once(self):
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'local-ai-session-test'}})
        with patch.object(ai, '_make_runnable', return_value=FakeRunnable('Register', {'number': 1})):
            reply = self.send('AI 报名明天第一场')
        assert '待确认' in reply, reply
        assert '2026-10-13' in reply.replace('\\', '') and '17:30' in reply
        assert self.s.registrations('2026-10-13')['registrations'] == []
        token = self.pending_match(reply)[1]
        result = self.send('AI确认 ' + token)
        assert '报名已确认' in result
        rows = self.s.registrations('2026-10-13')['registrations']
        assert len(rows) == 1
        assert rows[0]['session_id'] == self.s.sessions('2026-10-13')[0]['id']
        with self.assertRaises(ValueError):
            self.send('AI确认 ' + token)

    def test_out_of_range_ordinal_cannot_create_pending_registration(self):
        self.s.update_config({'ai': {'enabled': True, 'api_key': 'local-ai-session-test'}})
        with patch.object(ai, '_make_runnable', return_value=FakeRunnable('Register')):
            with self.assertRaisesRegex(ValueError, '未找到'):
                self.send('AI 报名明天第九场')
        with connect(self.ctx.transport_db_path) as db:
            assert db.execute("SELECT COUNT(*) FROM ai_pending WHERE status='pending'").fetchone()[0] == 0
        assert self.s.registrations('2026-10-13')['registrations'] == []
