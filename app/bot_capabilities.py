"""Robot business capabilities, independent of a member's management role.

Robot capabilities limit which services a transport can expose. They never grant
membership or management authority; those checks remain in the business modules.
"""

_CATALOG = (
    ('registration', '会员注册', '注册申请、补充资料和自评级别'),
    ('enrollment', '活动报名', '活动报名、代报名、取消和报名名单'),
    ('daily_events', '每日赛事', '组织趣味比赛、查看赛程和记录比分'),
    ('large_events', '大型赛事', '大型赛事报名、组织、赛程和比赛记录'),
    ('match_records', '零散对局', '记录个人对局和查询对战历史'),
    ('statistics', '报名统计', '查看个人报名汇总和管理报名统计'),
    ('messages', '消息发布', '后台向群发送文本、图片、文件和公告'),
    ('ai', 'AI 助手', '用自然语言调用本机器人已开启的业务'),
    ('management', '群内管理', '办理管理操作，发布审核、角色、等级和组别变更通知'),
)
ALL_CAPABILITIES = tuple(item[0] for item in _CATALOG)
REQUIRED_CAPABILITIES = ()
DEFAULT_CAPABILITIES = tuple(key for key in ALL_CAPABILITIES if key not in ('ai','large_events','management'))


def capability_catalog():
    return [{'id': key, 'name': name, 'description': description,
             'required': key in REQUIRED_CAPABILITIES, 'default_enabled': key in DEFAULT_CAPABILITIES}
            for key, name, description in _CATALOG]


def normalize_capabilities(value):
    """None preserves legacy full access; an empty list disables all business."""
    if value is None:
        return ALL_CAPABILITIES
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
        raise ValueError('机器人业务能力必须为数组')
    if len(value) != len(set(value)):
        raise ValueError('机器人业务能力不能重复')
    unknown = set(value) - set(ALL_CAPABILITIES)
    if unknown:
        raise ValueError('包含不支持的机器人业务能力')
    selected = set(value) | set(REQUIRED_CAPABILITIES)
    return tuple(key for key in ALL_CAPABILITIES if key in selected)


def supports(context, capability):
    if capability not in ALL_CAPABILITIES:
        return False
    return capability in normalize_capabilities(getattr(context, 'capabilities', None))


def require_capability(context, capability):
    if not supports(context, capability):
        name = next((name for key, name, _ in _CATALOG if key == capability), capability)
        raise PermissionError(f'当前机器人未开启{name}，请使用支持此业务的机器人')
