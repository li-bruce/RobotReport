"""Editable robot copy. Business facts, identities and commands remain server-owned."""
from dataclasses import dataclass
from html import escape as html_escape
from html.parser import HTMLParser
import hashlib
import json
import re
from string import Formatter

from .db import connect, dumps, audit
from .messages import escape


class Markup(str):
    """Markup produced by our own command/table/link renderers, never user input."""


class MembershipPrompt(PermissionError):
    """A denied operation with an already-rendered registration next step."""


@dataclass(frozen=True)
class Template:
    title: str
    category: str
    text: str
    limit: int = 400
    image: bool = False


TEMPLATES = {}
VARIABLE_LABELS = {'greeting':'称呼','name':'姓名','person':'通知对象','people':'通知名单','system_name':'系统名称',
                  'bot_name':'机器人名称','commands':'可用指令','content':'业务结果','table':'业务表格','steps':'执行步骤',
                  'heading':'场次与时间','title':'标题','detail':'详细结果','reason':'原因','status':'当前状态',
                  'event':'赛事名称','result':'处理结果','count':'人数','number':'编号','venue':'场地',
                  'date':'日期','start':'开始时间','end':'结束时间','level':'级别','gender':'性别','groups':'组别',
                  'names':'同行姓名','fields':'变更项目','field':'项目','decision':'审核结果','remaining':'保留的同行',
                  'ai_hint':'AI入口','choices':'可选赛事','form_link':'本人表单链接'}


def variable_label(key):
    return VARIABLE_LABELS.get(key,'指令示例' if key.endswith(('_command','_example')) else key)


def define(key, title, category, text, limit=400, image=False):
    TEMPLATES[key] = Template(title, category, text, limit, image)


define('entry.unregistered', '未注册用户', '招呼与帮助', '{greeting}您尚未注册到 **{system_name}**，我还无法确认您的会员身份。\n\n请发送 {register_command}，打开本人链接填写资料，再按页面提示确认或提交审核。')
define('entry.pending', '注册待审核', '招呼与帮助', '{greeting}您的注册申请已提交，正在等待审核；有结果会 @您通知。\n\n发送 {profile_command} 可查看进度。')
define('entry.rejected', '注册被退回', '招呼与帮助', '{greeting}您的注册申请已退回，请核对资料后重新提交：{register_command}。')
define('entry.suspended', '会员已停用', '招呼与帮助', '{greeting}您的会员资格已停用，请联系会员管理员处理。\n\n{profile_command} 可查看当前资料。')
define('entry.registration_elsewhere', '当前机器人不办理注册', '招呼与帮助', '{greeting}您还未通过 **{system_name}** 的会员审核，我暂时无法办理会员业务。请联系负责注册的机器人或群管理员。')
define('entry.member', '会员直接 @ 机器人', '招呼与帮助', '{greeting}我是 **{bot_name}**，可以这样找我：\n\n{commands}\n\n发送 {help_command} 查看更多用法。')
define('entry.no_features', '暂无开放功能', '招呼与帮助', '{greeting}我目前没有开放的业务功能，请联系群管理员。')
define('entry.unknown', '指令未识别', '招呼与帮助', '{greeting}我还没理解这条消息，本次未执行。发送 {help_command} 看看可以怎样说。{ai_hint}')
define('entry.error', '业务操作未完成', '招呼与帮助', '{greeting}这次没能完成：{reason}\n\n需要查看用法时，请发送 {help_command}。')
define('entry.connected', '连接成功', '招呼与帮助', '**机器人连接成功**，您好！发送 {help_command}，看看我能帮您做什么。')
define('help.intro', '详细帮助开场', '招呼与帮助', '{greeting}下面是我可以帮您办理的事项：\n\n{content}', 300)
define('registration.form', '打开注册表单', '注册与资料', '{greeting}请点击 {form_link}，核对并提交您的资料。\n\n姓名、性别必填；手机号和1–9级自评选填。')
define('registration.fallback', '文字注册示例', '注册与资料', '{greeting}姓名、性别必填，可以这样发送：{register_example}；手机号和1–9级自评选填。')
define('registration.approved', '已注册', '注册与资料', '{greeting}您已注册，无需重复申请。发送 {profile_command} 可查看资料。')
define('registration.incomplete', '已注册但资料待补齐', '注册与资料', '{greeting}您已注册，还有资料需要补齐：{form_link}。')
define('registration.linked', '导入资料关联成功', '注册与资料', '{greeting}资料已关联，注册成功！发送 {help_command} 看看可以参加哪些活动。')
define('registration.submitted', '注册已提交', '注册与资料', '{greeting}注册申请已提交，正在等待审核；有结果会 @您通知。')
define('profile.form', '资料调整表单', '注册与资料', '{greeting}需要调整个人资料吗？请打开 {form_link}，提交后由会员管理员审核。')
define('profile.fallback', '资料调整示例', '注册与资料', '您可以这样申请：{group_example} 或 {level_example}。')
define('profile.empty', '暂无资料申请', '注册与资料', '您尚未提交资料调整申请。需要修改时，请发送 {edit_command}。')
define('profile.submitted', '资料申请已提交', '注册与资料', '{content}\n\n申请已提交，审核结果会 @您通知。', image=True)
define('profile.cancelled', '撤回资料申请', '注册与资料', '已为您撤回资料申请，当前资料保持不变。')
define('profile.no_pending', '没有待审资料申请', '注册与资料', '您目前没有待审核的资料申请。')
define('profile.gender', '性别已保存', '注册与资料', '已为您保存性别：**{gender}**。')
define('profile.gender_guide', '补充性别示例', '注册与资料', '请告诉我您的性别：{male_command} 或 {female_command}。')
define('profile.level', '自评级别已提交', '注册与资料', '已收到您的 **自评{level}级** 申请，请等待管理员审核。')
define('profile.next', '资料查询后的引导', '注册与资料', '需要调整资料时，请发送 {edit_command}；{requests_command} 可查看进度。')
define('roster.empty_status', '场次无人报名', '活动报名', '暂时无人报名。')
define('roster.empty', '名单末尾报名引导', '活动报名', '欢迎报名参与！例如发送 {enroll_command}，一起活动吧。')
define('roster.ended', '结束场次无人报名', '活动报名', '暂时无人报名 · 本场已结束。')
define('roster.unavailable', '暂不可报名', '活动报名', '暂时无人报名 · 本场暂不可报名。')
define('roster.no_sessions', '没有场次', '活动报名', '当前没有可显示的场次安排，请换个日期查询。')
define('enrollment.success', '报名成功', '活动报名', '{heading}\n\n已为您登记，期待见到您！\n\n{table}', image=True)
define('enrollment.cancelled', '取消本人报名', '活动报名', '**已取消本人报名**，下次有空再来！{remaining}')
define('enrollment.not_enrolled', '本人尚未报名', '活动报名', '您本人尚未报名，本次无需取消。{remaining}')
define('enrollment.guests_cancelled', '取消指定同行', '活动报名', '已取消您代报名的 **{names}**（{count}人），其他报名保持不变。')
define('broadcast.open', '每日活动邀请', '定时与主动通知', '{heading}\n\n今天也来活动一下吧！发送 {enroll_command} 即可报名。')
define('broadcast.closing', '报名即将结束', '定时与主动通知', '{heading}\n\n报名即将结束，想参加的朋友请抓紧：{enroll_command}。')
define('notice.approved', '注册审核通过', '定时与主动通知', '# 注册审核已通过\n\n{person}，欢迎加入 **{system_name}**！\n\n发送 {help_command}，看看我能帮您做什么。')
define('notice.restored', '会员资格恢复', '定时与主动通知', '# 会员资格已恢复\n\n{person}，欢迎回来！发送 {help_command} 查看可用功能。')
define('notice.rejected', '注册审核退回', '定时与主动通知', '# 注册审核未通过\n\n{person}，请联系会员管理员核对资料，再发送 {register_command} 重新申请。')
define('notice.suspended', '会员资格停用', '定时与主动通知', '# 会员资格已停用\n\n{person}，您的会员业务权限已暂停。如需恢复，请联系会员管理员。')
define('notice.pending', '会员资料待审', '定时与主动通知', '# 会员资料等待审核\n\n{person}，我们已收到您的资料，审核后会再通知您。')
define('notice.changed', '其他身份变更', '定时与主动通知', '# {title}\n\n{person}，您的资料有更新：\n\n{detail}')
define('notice.level_approved', '级别审核通过', '定时与主动通知', '# 级别审核已通过\n\n{person}，您的级别已更新为 **{level}级**。')
define('notice.level_rejected', '级别审核未通过', '定时与主动通知', '# 级别审核未通过\n\n{person}，本次申请未通过，当前显示为 **无级别**。如有疑问，请联系会员管理员。')
define('notice.groups', '组别调整通知', '定时与主动通知', '# 会员组别已更新\n\n{person}，您当前的组别是 **{groups}**。')
define('notice.profile_approved', '资料申请通过', '定时与主动通知', '# 资料调整已通过\n\n{person}，您的 **{fields}** 已更新。发送 {profile_command} 可以查看。')
define('notice.profile_rejected', '资料申请退回', '定时与主动通知', '# 资料调整已退回\n\n{person}，本次 **{fields}** 申请未通过，原资料保持不变。请联系会员管理员核对后重新申请。')
define('notice.roles', '管理角色变更', '定时与主动通知', '# 管理角色已更新\n\n{person}，您的管理职责有调整：\n\n{detail}')
define('notice.occupancy', '场地占用变动', '定时与主动通知', '{content}\n\n请留意场次变化，合理安排您的活动时间。')
define('notice.event_closed', '大型赛事报名结束', '定时与主动通知', '**{event} · 报名结束**\n\n场地{venue} · 已报名 **{count} 人**\n\n感谢大家报名！请留意接下来的分组与赛程通知。')
define('notice.import_invite', '邀请导入成员确认资料', '定时与主动通知', '**请确认会员资料**\n\n{people}，您好！已为您预登记会员资料。\n\n请发送 {confirm_registration_command}，打开本人链接核对并确认。')
define('planning.begin_first', '提示先组织比赛', '每日与大型赛事', '想一起组场比赛吗？先发送 {organize_command}，我会列出当前人数和可用赛制。')
define('planning.expired', '每日策划过期', '每日与大型赛事', '这次策划已过期，请发送 {organize_command} 重新开始。')
define('planning.closed', '每日策划已结束', '每日与大型赛事', '这次策划已结束。想再来一场，请发送 {organize_command}。')
define('planning.busy', '正在生成赛事', '每日与大型赛事', '正在为您安排比赛，请稍后发送 {resume_command} 查看。')
define('planning.ready', '已生成每日赛事', '每日与大型赛事', '已生成赛事，比赛安排好了！请发送 {resume_command} 查看。')
define('planning.exists', '本场次已有每日比赛', '每日与大型赛事', '本场次已有 **{event}**。发送 {schedule_command} 查看。')
define('planning.deleted', '原每日比赛已删除', '每日与大型赛事', '原比赛已删除，发送 {organize_command} 重新组织。')
define('planning.options', '每日策划选择赛制', '每日与大型赛事', '{content}\n\n以上标为可用的赛制都可以选，回复对应名称就能开始。', image=True)
define('event.enrolled', '赛事报名已更新', '每日与大型赛事', '**{event}** 的报名已更新，期待您的参与！')
define('event.cancelled', '取消赛事本人报名', '每日与大型赛事', '已处理 **{event}** 的本人取消报名，代报名人员仍保留。')
define('event.guests_cancelled', '取消赛事代报名', '每日与大型赛事', '已取消 **{event}** 中您代报名的 **{names}**，其他报名保持不变。')
define('event.none_running', '当前没有进行中赛事', '每日与大型赛事', '当前场地还没有进行中的赛事。想安排一场？请发送 {organize_command}。')
define('event.none_today', '当天没有匹配赛事', '每日与大型赛事', '当天暂时没有符合条件的赛事，您可以换个日期或场地查询。')
define('event.no_resume', '没有可继续的赛事', '每日与大型赛事', '暂未找到本场地、本群且人员匹配的未结束赛事。可以先报名，再发送 {organize_command} 安排新比赛。')
define('event.choose_score', '记分前选择赛事', '每日与大型赛事', '有几场比赛正在进行，本次尚未记分。请带上赛事名称再告诉我：\n\n{table}', image=True)
define('event.choose_resume', '选择继续哪场比赛', '每日与大型赛事', '找到几场还没结束的比赛，您想继续哪一场？\n\n{choices}')
define('event.transition', '赛事状态调整', '每日与大型赛事', '**{event}** 已{result}')
define('event.deleted', '每日比赛已删除', '每日与大型赛事', '**{event}** 已删除，本场次可以重新组织比赛。')
define('event.organization_saved', '赛事安排已保存', '每日与大型赛事', '**{event}** 的{field}已保存。{detail}')
define('record.guide', '零散对局示例', '对局记录', '打完一局了吗？可以这样记下来：\n\n{record_example}\n{single_record_example}\n\n请带上双方姓名和比分；本人需要参赛。')
define('record.saved', '对局已记录', '对局记录', '{content}\n\n已记录并计入您的对战统计！需要修正可发送 {edit_command}。', image=True)
define('record.updated', '比分已修正', '对局记录', '{content}\n\n比分已修正，统计也已同步更新。', image=True)
define('record.cancelled', '撤销对局草稿', '对局记录', '已取消当前记录草稿，您此前提交的对局仍然保留。')
define('record.voided', '对局已作废', '对局记录', '对局 **{number}** 已作废，不再计入统计。')
define('record.duplicate', '对局疑似重复', '对局记录', '{content}\n\n这局似乎已经记录过，本次未重复记入。如果确实又打了一局，请回复 {repeat_command}。', image=True)
define('record.choose', '选择同名会员', '对局记录', '找到多位 **{name}**，请选择对应的会员：\n\n{table}\n\n回复 {choose_command}（换成对应序号）。', image=True)
define('record.choose_first', '等待选择同名会员', '对局记录', '请先完成同名会员选择，我再为您保存这局记录。')
define('record.no_duplicate', '没有重复对局待确认', '对局记录', '当前没有待确认的重复对局，请重新发送记录内容。')
define('review.empty', '没有待审核注册', '会员管理', '**注册审核**\n\n目前没有待审核的注册申请。')
define('review.guide', '注册审批示例', '会员管理', '请发送 {approve_command} 或 {pending_command} 查看待审核申请。')
define('review.list', '待审核名单引导', '会员管理', '{content}\n\n请发送 {approve_command} 或 {reject_command}，也可以填写唯一姓名。', image=True)
define('review.done', '注册审批完成', '会员管理', '**审核{decision}**\n\n申请{number} · {name}。结果通知已安排，可在后台查看发送状态。')
define('review.already', '注册已经审核', '会员管理', '申请{number} · {name} 已审核{decision}，本次没有重复处理。')
define('profile.review_empty', '没有待审资料调整', '会员管理', '目前没有待审核的资料调整申请。')
define('profile.review_list', '资料审批名单引导', '会员管理', '{content}\n\n可发送 {view_command}、{approve_command} 或 {reject_command}。', image=True)
define('profile.review_done', '资料审批完成', '会员管理', '{content}\n\n审核结果将通知本人。', image=True)
define('profile.review_already', '资料申请已处理', '会员管理', '{content}\n\n该申请已处理，无需重复操作。', image=True)
define('availability.released', '释放场地占用', '场地管理', '占用已释放。发送 {roster_command} 查看最新场次安排。')
define('availability.saved', '登记场地占用', '场地管理', '**占用已登记 · 占用{number}**\n\n场地{venue} · {date} {start}–{end}\n\n相关场次暂停新增报名，已有{count}条报名保留。')
define('availability.guide', '场地占用指令示例', '场地管理', '需要安排场地吗？可以这样操作：\n\n{reserve_example}\n{query_example}\n{release_example}')
define('availability.holidays', '节假日安排开关', '场地管理', '已为您{status}场地{venue}的节假日专用场次。')
define('ai.confirm', 'AI方案待确认', 'AI助手', '**安排待确认 · 尚未执行**\n\n{steps}\n\n回复 {confirm_command} 后我再执行；要调整请发 {edit_command}，放弃请发 {cancel_command}。', 500)
define('ai.status', 'AI开关状态', 'AI助手', '**AI助手：{status}**\n\n把需求放在 {ai_command} 后告诉我，例如 {ai_example}；普通指令也可以照常使用。')
define('ai.query', 'AI查询结果', 'AI助手', '**AI 查询**\n\n为您找到这些结果：\n\n{content}', image=True)
define('ai.rejected', 'AI方案未通过检查', 'AI助手', '**计划未通过检查 · 尚未执行**\n\n第{number}步：{reason}\n\n请带上 {ai_command} 告诉我怎么调整。')
define('ai.changed', 'AI检查期间数据变化', 'AI助手', '业务数据刚刚有变化，本次未执行。请带上 {ai_command} 重新告诉我您的需求。')
define('ai.interrupted', 'AI执行中止', 'AI助手', '**AI计划执行中止**\n\n{content}\n\n已完成的步骤保留，后续步骤没有执行。请先核对结果，再告诉我剩余需求。')
define('ai.completed', 'AI计划已完成', 'AI助手', '**AI计划已完成**\n\n{content}', image=True)
define('ai.cancelled', 'AI计划已取消', 'AI助手', '好的，AI计划已取消，未执行业务操作。')
define('ai.progress', 'AI计划进度', 'AI助手', '**AI计划 · {status}**\n\n{content}')
define('ai.disabled', 'AI尚未开启', 'AI助手', 'AI业务助手暂未开启，本次未调用模型。您可以发送 {help_command} 使用普通指令，或联系管理员开启。')
define('ai.disabled_during', '处理中关闭了AI', 'AI助手', 'AI已关闭，本次未执行操作。请发送 {help_command} 使用普通指令。')
define('ai.policy_changed', 'AI处理中配置变更', 'AI助手', '业务配置刚刚有变化，本次未执行。请重新告诉我您的需求。')
define('ai.toggle', 'AI开关已修改', 'AI助手', '好的，**AI助手已{status}**。')

from .dialogue_help import COPY
for _label, (_key_name, _copy) in COPY.items():
    define('help.'+_key_name, _label+'帮助说明', '分类使用帮助', _copy, 600)


def fields(text):
    result = set()
    for _, key, spec, conversion in Formatter().parse(text):
        if key is not None:
            if not re.fullmatch(r'[a-z_]+', key) or spec or conversion:
                raise ValueError('变量只支持 {变量名}，不支持表达式')
            result.add(key)
    return result


class _SafeHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack=[]

    def handle_starttag(self, tag, attrs):
        if tag not in ('font', 'b', 'strong', 'i', 'em', 'br'):
            raise ValueError('仅支持加粗、斜体和 font 颜色标签')
        if tag == 'font':
            if len(attrs) != 1 or attrs[0][0] != 'color' or not re.fullmatch(r'#[0-9a-fA-F]{6}', attrs[0][1] or ''):
                raise ValueError('颜色请使用六位色值，如 #0066b2')
        elif attrs:
            raise ValueError('样式标签不能添加其他属性')
        if tag!='br':self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag not in ('font', 'b', 'strong', 'i', 'em', 'br'):
            raise ValueError('不支持此样式标签')
        if not self.stack or self.stack.pop()!=tag:
            raise ValueError('请成对关闭样式标签')

    def handle_comment(self, data):
        raise ValueError('文案中不能包含 HTML 注释')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        if tag!='br':self.handle_endtag(tag)


def validate(key, text):
    spec = TEMPLATES.get(key)
    if spec is None:
        raise ValueError('未知回复模板')
    if not isinstance(text, str) or not text.strip() or len(text) > spec.limit:
        raise ValueError(f'此文案须为1–{spec.limit}字')
    expected = fields(spec.text)
    if fields(text) != expected:
        raise ValueError('请保留这些变量：' + '、'.join('{' + f + '}' for f in sorted(expected)))
    if any(ord(c) < 32 and c not in '\n\t' for c in text):
        raise ValueError('文案包含不可见控制字符')
    parser=_SafeHTML()
    for line in text.splitlines():
        parser.feed(line)
        if parser.stack:
            raise ValueError('样式标签请在同一行内成对使用')
    parser.close()
    if parser.stack:
        raise ValueError('请成对关闭样式标签')
    # Links are supplied by the service; editable copy cannot inject tracking links,
    # images, credentials or active URL schemes into registration instructions.
    if re.search(r'\]\s*\(|^\s*\[[^\]]+\]:|<\s*(?:https?|javascript|data):', text, re.M | re.I):
        raise ValueError('请使用现有链接变量，不要在文案中插入链接或图片')
    return text.strip()


def _key(ctx):
    return 'dialogue:' + ctx.id


def read(ctx):
    if ctx is None or not ctx.db_path.exists():
        return {'system_name': '聚场', 'overrides': {}}
    with connect(ctx.db_path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='config'").fetchone():
            return {'system_name': '聚场', 'overrides': {}}
        row = db.execute('SELECT value FROM config WHERE key=?', (_key(ctx),)).fetchone()
    return json.loads(row[0]) if row else {'system_name': '聚场', 'overrides': {}}


def command(ctx, text):
    name = (getattr(ctx, 'bot_name', '') or '机器人').strip()
    return Markup('<font color="#0066b2"><b>' + html_escape('@' + name + ' ' + text) + '</b></font>')


def link(label, url):
    from urllib.parse import urlsplit
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('表单地址无效')
    # Escape characters meaningful to Markdown, without changing the destination.
    return Markup('[' + escape(label) + '](' + url.replace('(', '%28').replace(')', '%29') + ')')


def render(ctx, key, *, settings=None, **values):
    spec = TEMPLATES[key]
    settings = settings if settings is not None else read(ctx)
    name = str(values.get('name') or '').strip()
    if name == getattr(ctx, 'actor', None) or name in ('待完善姓名', '待填写', '未命名'):
        name = ''
    common = {'system_name': settings.get('system_name') or '聚场',
              'bot_name': getattr(ctx, 'bot_name', '') or '机器人',
              'greeting': Markup((escape(name) + '，' if name else '') + '您好！')}
    for key_name, cmd in {'register':'注册', 'profile':'注册资料', 'help':'帮助', 'organize':'组织比赛',
                          'resume':'继续比赛', 'enroll':'报名', 'roster':'名单', 'confirm':'确认',
                          'cancel':'取消计划'}.items():
        common[key_name + '_command'] = command(ctx, cmd)
    common.update(values)
    template = settings.get('overrides', {}).get(key, spec.text)
    # Invalid/stale local overrides cannot break inbound processing.
    try:
        validate(key, template)
    except ValueError:
        template = spec.text
    return template.format_map({k: str(v) if isinstance(v, Markup) else escape(v) for k, v in common.items()})


def entry(ctx, member, *, name=None):
    from .time_rules import effective_capabilities
    enabled = set(effective_capabilities(ctx))
    status = (member or {}).get('status', 'unregistered')
    name = (member or {}).get('name') or name
    if status != 'approved' and not ctx.is_admin:
        if status in ('pending', 'rejected', 'suspended'):
            key = 'entry.' + status
        else:
            key = 'entry.unregistered' if 'registration' in enabled else 'entry.registration_elsewhere'
        # Do not advertise a command this robot cannot process.
        if 'registration' not in enabled:
            key = 'entry.registration_elsewhere'
        return render(ctx, key, name=name)
    from .bot_access import require_access
    try:
        require_access(ctx,getattr(ctx,'policy_clock',None))
    except PermissionError as exc:
        return render(ctx,'entry.error',name=name,reason=str(exc))
    choices = []
    for cap, cmd, description in (
        ('registration', '注册', '查看注册状态'),
        ('enrollment', '报名', '参加活动'), ('enrollment', '名单', '看看谁会来'),
        ('daily_events', '组织比赛', '一起安排比赛'), ('large_events', '帮助 大型赛事', '了解赛事报名'),
        ('match_records', '记录', '记下一局比赛'), ('statistics', '我的统计', '回顾参与情况'),
        ('ai', 'AI 帮助', '用一句话告诉我需求')):
        if cap in enabled:
            choices.append(str(command(ctx, cmd)) + ' · ' + description)
    if 'management' in enabled and ctx.can('member.manage'):
        choices = [str(command(ctx, '待审核')) + ' · 处理注册申请'] + choices
    return render(ctx, 'entry.member' if choices else 'entry.no_features', name=name,
                  commands=Markup('\n'.join(choices[:5])))


def revision(settings):
    return hashlib.sha256(dumps(settings).encode()).hexdigest()


def settings_api(platform, user, bot_id, data=None):
    platform.require_platform(user)
    bot = platform.bot(bot_id)
    from dataclasses import replace
    ctx = replace(platform.workspace_context(actor=user.id, source='admin'), id=bot_id, bot_name=bot['name'])
    with connect(ctx.db_path, data is not None) as db:
        row = db.execute('SELECT value FROM config WHERE key=?', (_key(ctx),)).fetchone()
        current = json.loads(row[0]) if row else {'system_name': '聚场', 'overrides': {}}
        if data is not None:
            if set(data) != {'revision', 'system_name', 'overrides'}:
                raise ValueError('请提供文案、系统名称与版本')
            if data['revision'] != revision(current):
                from fastapi import HTTPException
                raise HTTPException(409, '文案已被其他管理员修改，请重新载入后编辑')
            name = data['system_name']
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or re.search(r'[\r\n<>]', name):
                raise ValueError('系统名称须为1–40字')
            overrides = data['overrides']
            if not isinstance(overrides, dict) or len(overrides) > len(TEMPLATES):
                raise ValueError('文案配置无效')
            saved = {k: validate(k, v) for k, v in overrides.items()}
            current = {'system_name': name.strip(), 'overrides': {k: v for k, v in saved.items() if v != TEMPLATES[k].text}}
            db.execute('INSERT OR REPLACE INTO config(key,value) VALUES(?,?)', (_key(ctx), dumps(current)))
            audit(db, user.id, 'bot.dialogue.update', {'bot_id': bot_id, 'overrides': sorted(current['overrides'])})
    return current | {'revision': revision(current), 'templates': [
        {'id': k, 'title': s.title, 'category': s.category, 'default': s.text, 'limit': s.limit,
         'image': s.image, 'variables': sorted(fields(s.text)),
         'variable_labels':{f:variable_label(f) for f in fields(s.text)}} for k, s in TEMPLATES.items()]}


def preview(platform, user, bot_id, data):
    platform.require_platform(user)
    bot = platform.bot(bot_id)
    key, text = data.get('id'), data.get('text')
    validate(key, text)
    from dataclasses import replace
    ctx = replace(platform.workspace_context(), id=bot_id, bot_name=bot['name'])
    values = {f: f'〈{variable_label(f)}〉' for f in fields(TEMPLATES[key].text)}
    values.update(name='小林', person=Markup('**小林**'), reason='这场活动暂时不能报名',
                  event='周末友谊赛', count=8, number=12, level=5, groups='兴趣组', gender='女',
                  fields='姓名、组别', decision='通过', status='已开启', names='小张、小王',
                  venue=1, date='2026-11-18', start='17:30', end='20:30', remaining='', ai_hint='',
                  commands=Markup(str(command(ctx,'注册'))+' · 查看注册状态\n'+str(command(ctx,'报名'))+' · 参加活动'),
                  steps='1. 为您报名明天第一场\n2. 查看该场名单',
                  heading=Markup('## 2026-11-18 · 主场地 · 第1场\n\n**17:30–20:30**'),
                  detail='当前资料已更新', content=Markup('〈业务结果〉'), table=Markup('〈名单表格〉'),
                  form_link=Markup('[确认本人资料](https://example.invalid/register)'),
                  register_example=command(ctx,'注册 小林 女 5级'), edit_command=command(ctx,'修改资料'),
                  approve_command=command(ctx,'审核通过 申请12'), reject_command=command(ctx,'审核拒绝 申请12'),
                  pending_command=command(ctx,'待审核'), choose_command=command(ctx,'选择1'),
                  record_example=command(ctx,'记录 双打 我、小张 VS 小王、小李 21:19'),
                  repeat_command=command(ctx,'重复记录'), view_command=command(ctx,'资料查看 申请12'),
                  group_example=command(ctx,'申请分组 兴趣组'), level_example=command(ctx,'申请等级 5级'),
                  requests_command=command(ctx,'我的资料申请'), ai_command=command(ctx,'AI'),
                  ai_example=command(ctx,'AI 帮我查今天的名单'),people='小林、小张',choices=command(ctx,'继续比赛 12'),
                  result='暂停。',field='集合说明',confirm_registration_command=command(ctx,'注册确认'),
                  single_record_example=command(ctx,'记录 单打 我 VS 小张 21:19'),
                  guests_command=command(ctx,'报名，小张，小王'),cancel_self_command=command(ctx,'取消报名'),
                  cancel_guests_command=command(ctx,'取消小张，小王'),schedule_example=command(ctx,'春季联赛，我的赛程'),
                  edit_example=command(ctx,'修改记录 12 21:18'),void_example=command(ctx,'作废记录 12'),
                  stats_command=command(ctx,'我的统计'),progress_command=command(ctx,'AI进度'),
                  coordination_example=command(ctx,'春季联赛，协调安排 18点到场准备'),
                  delete_example=command(ctx,'周末友谊赛，删除比赛'),schedule_command=command(ctx,'周末友谊赛，对战表'))
    values.update(male_command=command(ctx,'性别 男'),female_command=command(ctx,'性别 女'),
                  reserve_example=command(ctx,'场地1 明天 占用 17:30-20:30 校队训练'),
                  query_example=command(ctx,'场地1 明天 场地占用'),release_example=command(ctx,'场地1 释放占用12'))
    # Auto-generated variables use the same renderer as real messages.
    for f in ('greeting','system_name','bot_name','register_command','profile_command','help_command',
              'organize_command','resume_command','enroll_command','roster_command','confirm_command','cancel_command'):
        values.pop(f, None)
    body = render(ctx, key, settings={'system_name': str(data.get('system_name') or '聚场')[:40], 'overrides': {key: text}}, **values)
    from .messages import style_markdown
    return {'reply': style_markdown(body, {'accent':'#0066b2'}), 'image': TEMPLATES[key].image}
