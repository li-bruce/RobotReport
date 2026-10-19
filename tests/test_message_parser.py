"""Transport-independent addressing, preservation, routing and authority cases."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.commands import Commands
from app.db import connect
from app.main import create_app
from app.message_parser import MessageInput, message_input, normalize_message
from app import env_settings
from tests.test_core import CoreFixture, TOKEN


@pytest.mark.parametrize('name', ['喂 - 一组专用', '注册 助手', '🏸 Club A', '球场 [A]+(1)', 'AI & 比赛'])
@pytest.mark.parametrize('command', ['注册', '名单', '报名，张三，李四', '取消张三', 'AI 明天第二场报名',
                                     '确认', '审核通过 申请1', '周二赛，第六场 21：19', '连接测试'])
def test_every_command_uses_the_same_address_boundary(name, command):
    assert normalize_message('@' + name + '：' + command, name).text == command
    assert normalize_message('<at user_id="recipient">任意显示名称</at>' + command, name).text == command
    raw = '@旧名称 🏸 ' + command
    message = message_input(raw, 'mentions', [{'start': 0, 'end': len('@旧名称 🏸'), 'kind': 'bot'}])
    assert normalize_message(message, name).text == command
    assert normalize_message(MessageInput(command), name).text == command


@pytest.mark.parametrize('body', ['报名，<at user_id="person">张三</at>，李四',
                                 'AI 查询 @张 三 的资料\n不要修改', '申请分组 A&amp;B',
                                 '记录 女双：张三，李四 VS 王五，赵六 21-19', '@literal remains'])
def test_command_and_structured_modes_preserve_business_text_exactly(body):
    assert normalize_message(MessageInput(body)).text == body
    prefix = '@一个名称 '
    message = message_input(prefix + body, 'mentions', [{'start': 0, 'end': len(prefix)-1, 'kind': 'bot'}])
    assert normalize_message(message, '完全不同的后台名称').text == body


def test_only_the_leading_legacy_tag_is_removed_not_people_in_arguments():
    body = '报名，<at user_id="person">张三</at>，李四'
    assert normalize_message('<at user_id="bot">接收者</at> ' + body).text == body
    assert normalize_message('AI 查询 <at user_id="person">张三</at>').text.endswith('</at>')
    assert normalize_message('&lt;at user_id="bot"&gt;接收者&lt;/at&gt; 注册').text == '注册'
    with pytest.raises(ValueError, match='完整的机器人提及'):
        normalize_message('<at user_id="bot">未闭合 注册')


@pytest.mark.parametrize('body', ['@其他机器人 注册', '@注册 助手其他 注册', '@注册\n助手 注册', '@注册 注册', '@未知 注册'])
def test_no_keyword_scan_or_partial_name_guess(body):
    with pytest.raises(ValueError, match='完整的机器人提及'):
        normalize_message(body, '注册 助手')


def test_user_mentions_do_not_become_the_address_or_sender():
    text = '@张三 注册'
    message = message_input(text, 'mentions', [{'start': 0, 'end': 3, 'kind': 'user'}])
    assert normalize_message(message).text == text
    text = 'AI 找 @机器人 的帮助'
    message = message_input(text, 'mentions', [{'start': 5, 'end': 9, 'kind': 'bot'}])
    assert normalize_message(message).text == text
    for raw in ('@机器人 @其他 注册', '<at>机器人</at><at>其他</at> 注册'):
        with pytest.raises(ValueError, match='一次只'):
            normalize_message(raw, '机器人')
    with pytest.raises(ValueError, match='一次只'):
        normalize_message(message_input('@甲 @乙 注册', 'mentions', [
            {'start': 0, 'end': 2, 'kind': 'bot'}, {'start': 3, 'end': 5, 'kind': 'user'}]))


@pytest.mark.parametrize('spans', [None, {}, [None], [{'start': 0, 'end': 2}],
    [{'start': -1, 'end': 2, 'kind': 'bot'}], [{'start': True, 'end': 2, 'kind': 'bot'}],
    [{'start': 0, 'end': 999, 'kind': 'bot'}], [{'start': 0, 'end': 0, 'kind': 'bot'}],
    [{'start': 0, 'end': 2, 'kind': 'admin'}], [{'start': 0, 'end': 2, 'kind': 'bot', 'user_id': 'admin'}],
    [{'start': 0, 'end': 3, 'kind': 'bot'}, {'start': 2, 'end': 4, 'kind': 'user'}],
    [{'start': 3, 'end': 4, 'kind': 'bot'}, {'start': 0, 'end': 2, 'kind': 'user'}]])
def test_invalid_span_metadata_rejected(spans):
    if spans is None:
        assert message_input('注册', 'mentions', spans).mentions == ()
    else:
        with pytest.raises(ValueError):
            message_input('@甲 注册', 'mentions', spans)


def test_format_validation_and_code_point_offsets():
    for value in ('', ' \n', None, 42, 'a' * 4001):
        with pytest.raises(ValueError):
            message_input(value)
    with pytest.raises(ValueError):
        message_input('注册', 'guess')
    with pytest.raises(ValueError):
        message_input('@甲 注册', 'command', [{'start': 0, 'end': 2, 'kind': 'bot'}])
    assert normalize_message(message_input('@🏸 注册', 'mentions', [{'start': 0, 'end': 2, 'kind': 'bot'}])).text == '注册'


class UnifiedEntryTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.platform.update(self.admin, self.ctx.id, {'name': '俱乐部 - 注册 助手'})
        self.s.update_config({'public_base_url': 'http://127.0.0.1:9874'})

    def test_bare_address_opens_help_without_creating_enrollment(self):
        router = Commands(self.member, self.clock)
        expected = asyncio.run(router.run(MessageInput('帮助')))
        for message in ('@俱乐部 - 注册 助手', '<at user_id="bot">任意名称</at>',
                        message_input('@Club A', 'mentions', [{'start': 0, 'end': 7, 'kind': 'bot'}])):
            assert asyncio.run(router.run(message)) == expected
        assert self.s.registrations()['registrations'] == []

    def test_current_legacy_and_simulator_share_registration_and_connection_routing(self):
        app = create_app(self.temp.name, TOKEN, False, False, self.clock)
        prefix = '@俱乐部 - 注册 助手 '
        with TestClient(app) as client:
            for command in ('注册', '帮助', '连接测试', 'AI 帮助'):
                expected = asyncio.run(Commands(self.member, self.clock).run(MessageInput(command)))
                simulator = client.post('/api/simulator', headers={'Authorization': 'Bearer ' + TOKEN},
                    json={'content': prefix + command, 'user_id': '1001', 'channel_id': self.ctx.channel_id})
                assert simulator.status_code == 200, simulator.text
                assert simulator.json()['reply'] == expected
                for path in (self.bots[0]['callback_path'], '/kingsoft/callback/callback-secret-01234567890123456789'):
                    body = {'robot_key': 'robot-test', 'chatid': '266382', 'creator': '1001',
                            'ctime': str(len(command)), 'content': prefix + command}
                    assert client.post(path, json=body).status_code == 200
                with connect(self.ctx.transport_db_path) as db:
                    assert db.execute('SELECT reply FROM inbox ORDER BY rowid DESC LIMIT 1').fetchone()[0] == expected
            with connect(self.ctx.transport_db_path) as db:
                assert db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 4

    def test_unrecognized_name_does_not_create_member_and_wrong_token_cannot_parse(self):
        app = create_app(self.temp.name, TOKEN, False, False, self.clock)
        with TestClient(app) as client:
            body = {'robot_key': 'robot-test', 'chatid': '266382', 'creator': 'never-registered',
                    'ctime': '123', 'content': '@俱乐部 - 未知 注册'}
            assert client.post(self.bots[0]['callback_path'] + 'bad', json=body).status_code == 403
            assert client.post(self.bots[0]['callback_path'], json=body).status_code == 200
        with connect(self.ctx.transport_db_path) as db:
            assert '完整的机器人提及' in db.execute('SELECT reply FROM inbox').fetchone()[0]
            assert db.execute('SELECT count(*) FROM registration_forms').fetchone()[0] == 0
        with connect(self.ctx.db_path) as db:
            assert db.execute("SELECT count(*) FROM member_records WHERE user_id='never-registered'").fetchone()[0] == 0

    def test_normalized_body_is_not_reinterpreted_and_ai_uses_the_same_identity(self):
        router = Commands(self.member, self.clock)
        body = '明天帮我安排，搭档 <at user_id="other">另一人</at>'
        with patch('app.ai_catalog.local_route', return_value=None), patch.object(router, 'propose', new_callable=AsyncMock, return_value='proposal') as propose:
            assert asyncio.run(router.run('@俱乐部 - 注册 助手 AI ' + body)) == 'proposal'
            assert propose.call_args.args[0] == body
            assert router.ctx.actor == '1001' and not router.ctx.is_admin
        # A literal command body must not accidentally decode an entity twice.
        with patch('app.commands.parse_selection', wraps=__import__('app.selection_parser', fromlist=['parse_selection']).parse_selection) as selection:
            asyncio.run(router.run(MessageInput('帮助 A&amp;B')))
            assert selection.call_args.args[0] == '帮助 A&amp;B'

    def test_json_spans_ignore_display_name_changes_and_keep_permissions_and_dedupe(self):
        env_file = self.platform.root / '.env.local'
        env_file.write_text(env_settings.render({'ROBORT_WEBHOOK_JSON_ENDPOINTS': 'https://adapter.example.org/messages'}), encoding='utf-8')
        env_settings.bind(self.platform.root, env_settings.load(env_file))
        self.platform.save_channel(self.admin, self.ctx.id, {'webhook_url': 'https://adapter.example.org/messages'}, self.ctx.channel_id)
        path = f'/webhooks/{self.ctx.id}/callback-secret-01234567890123456789'
        app = create_app(self.temp.name, TOKEN, False, False, self.clock, env_file=env_file)
        body = {'event_id': 'structured-new', 'group_id': '266382', 'user_id': 'new-spans',
                'text': '@外部名称 🏸 注册', 'text_format': 'mentions',
                'mentions': [{'start': 0, 'end': len('@外部名称 🏸'), 'kind': 'bot'}]}
        with TestClient(app) as client:
            assert client.post(path, json=body | {'group_id': 'wrong'}).status_code == 403
            assert client.post(path, json=body | {'is_admin': True}).status_code == 400
            assert client.post(path, json=body | {'mentions': [{'start': 0, 'end': 999, 'kind': 'bot'}]}).status_code == 400
            for _ in range(2):
                assert client.post(path, json=body).status_code == 200
            self.platform.update(self.admin, self.ctx.id, {'name': '后来改名的机器人'})
            assert client.post(path, json=body | {'event_id': 'structured-review', 'user_id': '1001',
                'text': '@外部名称 🏸 审核通过 申请1'}).status_code == 200
        with connect(self.ctx.transport_db_path) as db:
            rows = db.execute('SELECT reply FROM inbox ORDER BY rowid').fetchall()
            assert len(rows) == 2 and '/register#' in rows[0][0]
            assert '权限' in rows[1][0]
            assert db.execute('SELECT user_id FROM registration_forms').fetchone()[0] == 'new-spans'
        self.platform.update(self.admin, self.ctx.id, {'capabilities': ['enrollment']})
        with pytest.raises(PermissionError, match='会员注册'):
            asyncio.run(Commands(self.member, self.clock).run(message_input(body['text'], 'mentions', body['mentions'])))
