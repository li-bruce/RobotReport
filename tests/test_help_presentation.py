"""Help is scoped, concise and displays colored syntax without changing commands."""
import html,re
import pytest
from app.help import command_help,command,parameter,COMMAND_COLOR,PARAMETER_COLOR


def plain(text):return html.unescape(re.sub('<[^>]*>','',text))


@pytest.mark.parametrize('cap,category', [('registration','注册'),('enrollment','报名'),('daily_events','比赛'),('large_events','大型赛事'),('match_records','记录'),('statistics','统计'),('ai','AI')])
def test_help_progressively_discloses_one_business(cap,category):
    brief=command_help({'status':'approved'},capabilities=[cap])
    detail=command_help({'status':'approved'},category=category,capabilities=[cap])
    assert len(plain(brief))<220
    assert '帮助 '+category in plain(brief)
    assert '<font color="'+COMMAND_COLOR+'">' in brief
    assert len(plain(detail))>len(plain(brief))
    assert '| ---' not in brief+detail


def test_format_parameters_have_distinct_color_and_visible_brackets():
    reply=command_help({'status':'approved'},category='报名',capabilities=['enrollment'])
    assert COMMAND_COLOR!=PARAMETER_COLOR
    assert '<font color="'+PARAMETER_COLOR+'">〈' in reply
    assert '格式：' in reply
    assert '取消张三，李四' in plain(reply)


def test_formatting_does_not_accept_html_or_generate_links():
    for render in (command,parameter):
        value=render('<script>alert(1)</script> & 姓名')
        assert '<script>' not in value and '&lt;script&gt;' in value and '&amp;' in value


def test_brief_help_follows_roles_and_registration_status():
    ordinary=plain(command_help({'status':'approved'}))
    assert '审核通过' not in ordinary and '关闭AI' not in ordinary
    admin=plain(command_help({'status':'approved'},is_admin=True,capabilities=['management','registration']))
    assert '审核通过' in admin and '关闭AI' not in admin and '暂停比赛' not in admin
    pending=plain(command_help({'status':'pending'}))
    assert '@本人通知' in pending and '注册资料' in pending and '报名' not in pending
    assert len(ordinary)<800
