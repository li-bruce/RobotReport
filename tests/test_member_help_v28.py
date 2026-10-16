from app.help import command_help


def test_member_management_help_is_permission_aware():
    member={'status':'approved'}
    assert '待审核' not in command_help(member)
    assert '审核通过 申请12' in command_help(member, permissions={'member.manage'})
    assert '审核通过 申请12' in command_help(member, is_admin=True)
    assert '待审核' not in command_help(member, permissions={'event.manage'})


def test_review_and_notification_guidance():
    assert '@本人通知' in command_help({'status':'pending'})
    guide=command_help({'status':'approved'}, category='管理', permissions=['member.manage'])
    assert '后台管理员默认拥有全部管理权限' in guide
    assert '同名时使用待审核名单中的申请编号' in guide
    assert '审核拒绝 申请12' in guide
    assert '手机号' in guide
