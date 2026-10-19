"""Realistic bot display names must not hide registration or bypass permissions."""
import asyncio
from dataclasses import replace
import re

from fastapi.testclient import TestClient
import pytest

from app.commands import Commands, parse, strip_mention
from app.db import connect
from app.main import create_app
from app.members import Members
from tests.test_core import CoreFixture, TOKEN


@pytest.mark.parametrize('message,name', [
    ('@喂 - 一组专用 注册', '喂-一组专用'),
    ('@喂 - 一组专用 注册', '喂 - 一组专用'),
    ('@喂-一组专用 注册', '喂 - 一组专用'),
    ('  @喂\u00a0-\u2005一组专用\u3000注册  ', '喂-一组专用'),
    ('@喂&nbsp;-&nbsp;一组专用&nbsp;注册', '喂-一组专用'),
    ('＠喂 - 一组专用 注册', '喂-一组专用'),
    ('<at id="bot">喂 - 一组专用</at> 注册', '喂-一组专用'),
    ('&lt;at id="bot"&gt;喂 - 一组专用&lt;/at&gt; 注册', '喂-一组专用'),
    ('@羽球 [A](一组)+ 注册', '羽球 [A](一组)+'),
    ('@注册 助手 注册', '注册 助手'),
    ('@喂 注册', '喂-一组专用'),
    ('注册', '喂-一组专用'),
])
def test_bot_name_and_whitespace_variants(message, name):
    assert strip_mention(message, name) == '注册'
    assert parse(message, bot_name=name)[0] == '注册'


def test_only_leading_complete_name_is_removed_and_command_arguments_are_preserved():
    name = '喂-一组专用'
    assert strip_mention('@喂 - 一组专用 注册 张三 男 3级', name) == '注册 张三 男 3级'
    assert strip_mention('@喂 - 一组专用 AI 帮我查明天的名单', name) == 'AI 帮我查明天的名单'
    assert strip_mention('报名，@张三，李四', name) == '报名，@张三，李四'
    assert strip_mention('@喂 - 一组专用其他 注册', name) != '注册'
    assert strip_mention('@喂\n- 一组专用 注册', name) != '注册'
    assert strip_mention('@喂 - 二组专用 注册', name) != '注册'


class MentionRegistrationTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.platform.update(self.admin, self.ctx.id, {'name': '喂-一组专用'})
        self.s.update_config({'public_base_url': 'http://127.0.0.1:9874'})
        self.new_member = replace(self.member, actor='new-member')

    def test_callback_registration_link_submits_for_original_sender_and_deduplicates(self):
        app = create_app(self.temp.name, TOKEN, False, False, self.clock)
        body = {'robot_key': 'robot-test', 'chatid': '266382', 'creator': 'new-member',
                'ctime': 123456, 'content': '@喂 - 一组专用 注册'}
        path = self.bots[0]['callback_path']
        with TestClient(app) as client:
            assert client.post(path, json=body).json() == {'result': 'ok'}
            assert client.post(path, json=body).json() == {'result': 'ok'}
            with connect(self.ctx.transport_db_path) as db:
                reply = db.execute('SELECT reply FROM inbox').fetchone()[0]
                assert db.execute('SELECT count(*) FROM registration_forms').fetchone()[0] == 1
                assert db.execute('SELECT user_id FROM registration_forms').fetchone()[0] == 'new-member'
            reference = re.search(r'/register#([^)\s]+)', reply)[1]
            result = client.post('/public/registration/' + reference, json={'name': '张三', 'gender': 'male', 'level': 3})
            assert result.status_code == 200
            assert result.json()['member_status'] == 'pending'
        assert Members(self.new_member, self.clock).get()['name'] == '张三'
        assert Members(self.new_member, self.clock).get()['roles'] == []

    def test_name_refresh_connection_help_and_existing_membership_gates(self):
        router = Commands(self.new_member, self.clock)
        self.platform.update(self.admin, self.ctx.id, {'name': '喂 - 注册服务'})
        assert '/register#' in asyncio.run(router.run('@喂 - 注册服务 注册'))
        assert '已注册' in asyncio.run(Commands(self.member, self.clock).run('@喂 - 注册服务 注册'))
        with pytest.raises(PermissionError, match='打开本人注册表单'):
            asyncio.run(router.run('@喂 - 注册服务 报名'))
        app = create_app(self.temp.name, TOKEN, False, False, self.clock)
        with TestClient(app) as client:
            response = client.post(self.bots[0]['callback_path'], json={
                'robot_key': 'robot-test', 'chatid': '266382', 'creator': 'new-member',
                'ctime': 123457, 'content': '@喂 - 注册服务 连接测试'})
            assert response.status_code == 200
            with connect(self.ctx.transport_db_path) as db:
                assert '机器人连接成功' in db.execute('SELECT reply FROM inbox').fetchone()[0]

    def test_disabled_registration_and_manager_permissions_still_apply(self):
        self.platform.update(self.admin, self.ctx.id, {'capabilities': ['enrollment']})
        with pytest.raises(PermissionError, match='会员注册'):
            asyncio.run(Commands(self.new_member, self.clock).run('@喂 - 一组专用 注册'))
        self.platform.update(self.admin, self.ctx.id, {'capabilities': ['registration', 'management']})
        with pytest.raises(PermissionError):
            asyncio.run(Commands(self.member, self.clock).run('@喂 - 一组专用 审核通过 申请1'))
