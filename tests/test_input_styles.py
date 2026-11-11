"""Rendered text, explicit selectors and protected command boundaries."""
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from app.db import connect
from app.commands import Commands
from app.main import create_app
from app.message_parser import LiteralCommand, message_input, normalize_message, parse
from app.score_parser import parse_score
from app.selection_parser import parse_selection
from tests.test_core import CoreFixture, TOKEN


@pytest.mark.parametrize('body', ['**报名**', '__报名__', '*报名*', '_报名_', '~~报名~~',
    '<font color="red"><b>报名</b></font>', '<span style="font-size:16px"><strong>报</strong>名</span>',
    '&lt;b&gt;报名&lt;/b&gt;', '`报名`', '```text\n报名\n```', '# 报名', '> **报名**',
    '- **报名**', '1. 报名', '[报名](https://example.invalid/a_(b))', '\ufeff报\u200b名',
    '<p><u>报名</u></p>', '<a href="https://example.invalid/ignore">报名</a>'])
@pytest.mark.parametrize('mode', ['legacy', 'command', 'mentions'])
def test_style_matrix_uses_one_body_pipeline(body, mode):
    if mode == 'legacy':
        # Whole-message wrappers are handled as well as a styled body.
        raw = '<div><b>@群内名称</b><br>'+body+'</div>'
        message = message_input(raw, mode)
    elif mode == 'mentions':
        raw = '<font color="red">**@实际群名**</font>\n'+body
        start = raw.index('@')
        message = message_input(raw, mode, [{'start':start,'end':start+len('@实际群名'),'kind':'bot'}])
    else:
        message = message_input(body, mode)
    assert normalize_message(message, '后台名称不同').text == '报名'


def test_names_mentions_literals_links_and_multiple_actions_are_not_rewritten():
    literal = 'AI 找 team_A_B、A__B__C、A＋２杯、王一二，地址 https://example.invalid/a_b_c?key=x_y_z'
    assert normalize_message(message_input(literal)).text == literal
    person = '<at user_id="user_2">**原昵称**</at>'
    assert normalize_message(message_input('AI 查询 '+person)).text == 'AI 查询 '+person
    assert normalize_message(LiteralCommand('报名，**姓名**，A&amp;B')).text == '报名，**姓名**，A&amp;B'
    assert normalize_message(message_input('AI A&amp;lt;B')).text == 'AI A&lt;B'
    assert normalize_message(message_input('[说明](https://example.invalid/报名)')).text == '说明'
    assert normalize_message(message_input('<p>报名</p><p>取消报名</p>')).text == '报名\n\n取消报名'
    assert normalize_message(message_input(r'\*报名\*')).text == '*报名*'
    assert normalize_message(message_input('**不要** 报名')).text == '不要 报名'
    assert normalize_message(message_input('组队 👩\u200d💻')).text.endswith('👩\u200d💻')


@pytest.mark.parametrize('bad', ['<script>报名</script>', '<style>报名</style>报名',
    '<img alt="报名" src="x">', '![报名](https://example.invalid/x)', '<!--取消-->报名',
    '<span hidden>取消</span>报名', '<span style="display:none">取消</span>报名', '<?test?>报名'])
def test_nontext_or_hidden_content_does_not_become_a_command(bad):
    with pytest.raises(ValueError, match='文字指令'):
        normalize_message(message_input(bad))


def test_formatted_multiple_addressees_are_rejected_before_dispatch():
    raw = '<b>@甲</b> <i>@乙</i> **报名**'
    spans = [{'start':raw.index(name),'end':raw.index(name)+len(name),'kind':kind}
             for name,kind in [('@甲','bot'),('@乙','user')]]
    with pytest.raises(ValueError, match='一次只'):
        normalize_message(message_input(raw,'mentions',spans))
    with pytest.raises(ValueError, match='一次只'):
        normalize_message(raw)


@pytest.mark.parametrize('date', ['2026-11-12', '2026/11/12', '2026.11.12', '2026－11－12',
    '２０２６／１１／１２', '2026 — 11 — 12', '20261112', '２０２６１１１２',
    '2026 年 11 月 12 日', '二〇二六年十一月十二日', '贰零贰陆年壹拾壹月壹拾贰号'])
@pytest.mark.parametrize('number', ['2', '２', '二', '两', '贰', '②'])
def test_date_and_ordinal_matrix(date, number):
    command, selection = parse('**'+date+'** <b>第 '+number+' 场</b> 报 名 ＋ 两 接 龙')
    assert command == '报名+2 接龙'
    assert selection == {'day':'2026-11-12','venue_id':1,'at':None,'number':2}


@pytest.mark.parametrize('separator', [':','：','-','‐','‑','‒','–','—','−','－','﹣','/','／','比'])
@pytest.mark.parametrize('points', [('21','19'),('２１','１９'),('二十一','十九'),('贰拾壹','拾玖')])
def test_score_formats_preserve_the_named_event(separator, points):
    result = parse_score(f'A_２—俱乐部，第贰场 {points[0]} {separator} {points[1]}')
    assert result == {'title':'A_２—俱乐部','event_id':None,'number':2,'scores':[[21,19]]}


def test_selection_does_not_normalize_names_or_guess_ambiguous_input():
    assert parse_selection('报名，A＋２杯')[0] == '报名，A＋２杯'
    assert parse_selection('场地② 报名')[1]['venue_id'] == 2
    assert parse_selection('自评 叁 级')[0] == '自评3级'
    assert parse_selection('2026.11.12 ２０：３０ 取消 报名')[1]['at'] == '20:30'
    assert parse_selection('2026年11月12日 二十点半 报名')[1]['at'] == '20:30'
    assert parse_selection('明天 第二场 报名',today='2026-11-11')[1]['day'] == '2026-11-12'
    for bad in ('2026-02-30 报名','2026-13-01 名单','2026-11-12 第零场 报名','自评 十 级'):
        with pytest.raises(ValueError):parse_selection(bad)
    with pytest.raises(ValueError, match='场次与比分'):
        parse_score('双打，１２２１－１９')
    assert parse_selection('11/12 报名')[0] == '11/12 报名'
    assert parse_score('21-19挑战赛，第2场21:19')['title'] == '21-19挑战赛'
    text = normalize_message('<b>@外部</b> **周三赛**，<font color="blue">第贰场</font> `２１−１９`').text
    assert parse_score(text) == {'title':'周三赛','event_id':None,'number':2,'scores':[[21,19]]}


@pytest.mark.parametrize('value', ('2026年10月13日20时报名', '２０２６年１０月１３日２０时报名', '二〇二六年十月十三日二十时报名'))
def test_adjacent_calendar_date_and_clock(value):
    assert parse_selection(value) == ('报名', {'day':'2026-10-13','venue_id':1,'at':'20:00','number':None})


class StyledCommandTests(CoreFixture):
    def test_callback_styles_preserve_permissions_sender_and_retry_deduplication(self):
        app = create_app(self.temp.name,TOKEN,False,False,self.clock)
        path=self.bots[0]['callback_path']
        body={'robot_key':'robot-test','chatid':'266382','creator':'1001',
              'ctime':'123456789','content':'<font color="red"><b>@群内名字</b></font> **报名＋２**'}
        # Use the callback's existing adapter fixture contract.
        body['robot_key']=self.platform.bot(self.ctx.id)['robot_key']
        with TestClient(app) as client:
            assert client.post(path+'wrong',json=body).status_code==403
            for _ in range(2):assert client.post(path,json=body).status_code==200
        rows=self.s.registrations()['registrations']
        assert len(rows)==3 and {r['user_id'] for r in rows}=={'1001'}
        with pytest.raises(PermissionError):
            asyncio.run(Commands(self.member,self.clock).run('**审核通过 申请1**'))
        count=len(rows)
        asyncio.run(Commands(self.member,self.clock).run('<p>报名</p><p>取消报名</p>'))
        assert len(self.s.registrations()['registrations'])==count

    def test_ai_receives_visible_text_with_the_authenticated_identity(self):
        router=Commands(self.member,self.clock)
        with patch('app.ai_catalog.local_route',return_value=None),patch.object(router,'propose',new_callable=AsyncMock,return_value='proposal') as propose:
            assert asyncio.run(router.run('<b>@外部名字</b> **AI** <font color="blue">帮我安排明天的比赛</font>'))=='proposal'
            assert propose.call_args.args[0]=='帮我安排明天的比赛'
            assert router.ctx.actor=='1001' and not router.ctx.is_admin
