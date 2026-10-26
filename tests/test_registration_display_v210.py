"""Roster contract: readable people, independent guest identity and explicit quota."""
from pathlib import Path
from unittest.mock import Mock

from app.competitions import TournamentService
from app.help import command_help
from app.messages import registration_gender, registration_name, registration_table, roster


def test_empty_roster_identifies_each_session_and_invites_without_including_cancelled_or_foreign_people():
    sessions=[dict(id=n, number=n, name=f'工作日 第{n}场', date='2026-10-20',start='17:30',end='20:30',
                   capacity=8,substitute_capacity=4) for n in (1,2)]
    rows=[dict(session_id=1,status='cancelled'),dict(session_id=99,status='active')]
    text=roster(dict(sessions=sessions,registrations=rows))
    assert text.count('暂时无人报名，欢迎报名参与！')==2
    assert '第1场' in text and '第2场' in text and '工作日' not in text
    assert '|' not in text and '正式' not in text
    assert roster(dict(sessions=[],registrations=[]))=='暂无场次安排。'
    sessions[1].update(occupancies=[1],booking_notice='校队训练')
    notice=roster(dict(sessions=sessions,registrations=rows))
    assert '校队训练' in notice and '已占用' in notice
    assert '第1场' in notice and notice.count('欢迎报名参与')==1


def test_empty_history_and_closed_sessions_do_not_invite_new_registrations():
    session=dict(id=41, number=2, name='工作日 第一场', date='2026-10-20', venue_name='东馆',
                 start='17:30', end='20:30', capacity=8, substitute_capacity=4)
    for change, expected in (({'status':'ended'}, '本场已结束'), ({'can_enroll':False}, '本场暂不可报名')):
        text=roster(dict(sessions=[session|change],registrations=[]))
        assert '第2场' in text and '第41场' not in text and '第一场' not in text
        assert '东馆' in text and expected in text and '欢迎' not in text


def test_roster_heading_uses_snapshot_context_and_separate_bold_time():
    session=dict(id=1234, number=12, name='工作日 第一场', date='2026-10-20', venue_name='老馆',
                 start='17:30', end='20:30', capacity=8, substitute_capacity=4)
    row=dict(session_id=1234,status='active',name='张三',gender='male',category='formal')
    text=roster(dict(sessions=[session],registrations=[row]))
    blocks=text.split('\n\n')
    assert '老馆' in blocks[0] and '第12场' in blocks[0]
    assert blocks[1]=='**17:30–20:30**'
    assert text.count('17:30')==1 and '工作日' not in text and '第一场' not in text
    assert '| 张三 | 男 | 正式 |' in text


def test_roster_has_three_columns_no_rank_or_internal_identifier():
    rows = [
        dict(name='张三', user_id='internal-1', guest_index=0, level=3,
             gender='male', category='正式', rank=11, status='active', session_id=1),
        dict(name='小王', user_id='internal-1', guest_index=1, sponsor_name='张三',
             gender='unknown', category='substitute', rank=12, status='active', session_id=1),
        dict(name='李四', user_id='internal-2', guest_index=0, level=None,
             gender='female', category='waitlist', rank=13, status='active', session_id=1),
        dict(name='已取消的人', user_id='internal-3', guest_index=0,
             status='cancelled', session_id=1),
    ]
    result = roster(dict(sessions=[dict(id=1, name='第一场', date='2026-10-14',
                                       start='17:30', end='20:30', capacity=1,
                                       substitute_capacity=1)], registrations=rows))
    assert '| 姓名 | 性别 | 名额 |' in result
    assert '| 张三（3级） | 男 | 正式 |' in result
    assert '| 小王（张三代报名） | 未填写 | 替补 |' in result
    assert '| 李四 | 女 | 候补 |' in result
    for hidden in ('internal-', '序号', '状态', '已取消的人', '| 11 |', '| 12 |', '| 13 |'):
        assert hidden not in result


def test_registration_name_never_repeats_id_and_proxy_has_no_inherited_level():
    assert registration_name(dict(name='private-id', user_id='private-id')) == '待完善姓名'
    guest = dict(name='private-id+2', user_id='private-id', guest_index=2, sponsor_name='private-id', level=9)
    assert registration_name(guest) == '同行2（待补姓名代报名）'
    assert registration_name(dict(name='王五', is_proxy=True, sponsor_name='赵六', level=9)) == '王五（赵六代报名）'
    assert registration_name(dict(name='张三', level=3), include_level=False) == '张三'
    assert registration_name(dict(name='张三', level=10)) == '张三'
    assert registration_gender(dict(is_proxy=True, gender='male')) == '未填写'


def test_table_safely_escapes_names_and_keeps_unknown_gender():
    text = registration_table([dict(name='<张|三>', gender=None, category=None)])
    assert '&lt;张\\|三&gt;' in text
    assert '<张' not in text
    assert '| 未填写 | — |' in text
    assert text.count('\n') == 2


def test_large_roster_preserves_cancelled_sponsor_name_but_only_lists_active_guests():
    event = dict(title='春季联赛', venue_id=1, date='2026-10-14', format='round_robin',
                 options=dict(discipline='doubles', best_of=1, points=21, win_by=2, cap=30),
                 organization=dict(meeting_instructions=''), registration_open='2026-10-01',
                 registration_close='2026-10-15', keyword='春季联赛', participant_count=1,
                 participants=[
                     dict(id='private-id:0', name='张三', user_id='private-id', guest_index=0,
                          status='cancelled', level=9, gender='male'),
                     dict(id='private-id:1', name='小王', user_id='private-id', guest_index=1,
                          status='active', level=9, gender='male', source_category='替补'),
                 ])
    service = TournamentService.__new__(TournamentService)
    service.get = Mock(return_value=event)
    text = service.render(1, 'registration')
    assert '| 姓名 | 性别 | 名额 |' in text
    assert '| 小王（张三代报名） | 未填写 | 替补 |' in text
    assert '9级' not in text
    assert 'private-id' not in text
    assert '| 张三 |' not in text
    assert event['participants'][1]['gender'] == 'male', 'rendering must not mutate stored snapshots'


def test_help_and_public_guide_explain_append_and_selective_cancellation():
    help_text = command_help({'status': 'approved'}, category='报名')
    guide = (Path(__file__).resolve().parents[1] / 'app/static/guide.html').read_text(encoding='utf-8')
    for text in (help_text, guide):
        assert '取消张三，李四' in text
        assert '仅取消本人' in text
        assert '每次追加' in text
        assert '取消整组' not in text
        assert '更新本人整组' not in text
    assert '春季联赛，报名，张三，李四' in guide
    assert '春季联赛，取消张三，李四' in guide
    assert '/static/guide.css?v=' in guide
    assert '/static/guide.js?v=' in guide
