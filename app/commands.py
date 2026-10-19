"""Deterministic command router. AI proposals always return through this router."""
from datetime import timedelta
from dataclasses import replace
import hashlib
import json
import re
import secrets
from .time_rules import effective_capabilities
from .db import connect, dumps, stamp, now
from .service import Service, day_value, time_value, clean_name
from .messages import escape, roster, tournament_text, table, mention, registration_table
from .members import Members
from .planning import DailyPlanning, STRATEGIES
from .score_parser import parse_score
from .tournaments import Tournaments
from .bot_capabilities import require_capability, supports
from .message_parser import MessageInput, normalize_message, parse, strip_mention
from .selection_parser import parse_selection

from .help import command_help, scoped_help
HELP = command_help({'status':'approved'})


def cancellation_guests(text):
    """Read a literal list of the caller's guests, never a different account."""
    if text == '取消报名' or not text.startswith('取消'):
        return None
    value = text[len('取消'):].lstrip()
    # An optional command label needs a delimiter: an actual guest name may
    # itself begin with 代报 / 报名 (including an anonymous sponsor label).
    value = re.sub(r'^(?:代报名|代报|报名)(?:\s+|[:：,，]\s*)', '', value)
    value = re.sub(r'^[:：,，]\s*', '', value).strip()
    if not value or '\n' in value or '\r' in value:
        raise ValueError('请填写要取消的代报名姓名，例如：取消张三，李四；取消本人请发送 取消报名')
    names = [part.strip() for part in re.split(r'[,，、]', value)]
    if any(not name for name in names) or len(names) > 20:
        raise ValueError('请用逗号分隔1至20位本人代报名的姓名')
    if any(len(name) > 100 or re.search(r'[\x00-\x1f\x7f]', name) for name in names):
        raise ValueError('请填写有效的代报名姓名（不超过100字）')
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('取消名单中的姓名不能重复，请每人只填写一次')
    return names


class Commands:
    def __init__(self, ctx, clock=now):
        self.ctx, self.clock = ctx, clock
        self.service = Service(ctx, clock)

    def refresh_context(self, *, enforce_access=False):
        """Re-read transport scope and club roles after model waits/confirmations."""
        from .security import Platform, permissions_for_roles
        ctx = self.ctx
        if ctx.platform_root and ctx.channel_id:
            platform = Platform(ctx.platform_root)
            platform.bot(ctx.id, active=True)
            fresh = platform.context_for_channel(ctx.channel_id, ctx.actor, ctx.is_admin, ctx.source)
            if (fresh.id, fresh.chat_id) != (ctx.id, ctx.chat_id):
                raise PermissionError('机器人或群通道已变化，请重新发送指令')
            if ctx.source in ('callback', 'simulator') and not fresh.receive_commands:
                raise PermissionError('该群通道已关闭机器人指令')
            ctx = fresh
        if ctx.source in ('callback', 'simulator'):
            with connect(ctx.db_path) as db:
                approved = db.execute('SELECT 1 FROM member_records WHERE chat_id=? AND user_id=? AND status=\'approved\'', (ctx.scope_id, ctx.actor)).fetchone()
                roles = [row[0] for row in db.execute('SELECT role FROM user_roles WHERE user_id=? AND scope_type=\'club\' AND scope_id=?', (ctx.actor, ctx.scope_id))] if approved else []
            ctx = replace(ctx, is_admin='club_admin' in roles, permissions=permissions_for_roles(roles))
        from .time_rules import bind_clock
        self.ctx = bind_clock(ctx,self.clock)
        self.service = Service(self.ctx, self.clock)
        if enforce_access:
            from .bot_access import require_access
            require_access(ctx,self.clock)
        return ctx

    def tournaments(self):
        return Tournaments(self.ctx, self.clock)

    def event_allowed(self, event):
        return supports(self.ctx, 'daily_events' if event.get('kind') == 'daily' else 'large_events')

    def require_event(self, event):
        from .bot_policy import require_event
        require_event(self.ctx,event)
        return event

    def require_command_capabilities(self, text):
        """Reject disabled command families before profile or business writes.

        Event commands are checked after resolving their actual kind.
        """
        if text in ('修改资料','申请修改资料','修改个人信息','申请调整资料','可选分组','分组列表','我的资料申请','资料申请进度','取消资料申请') or re.match(r'^(?:申请|修改|调整)(?:等级|分组)', text):
            require_capability(self.ctx, 'registration')
        if re.match(r'^资料(?:待审核|审核列表|查看|通过|拒绝)', text):
            require_capability(self.ctx, 'management')
        if not supports(self.ctx, 'daily_events'):
            from .formats import metadata
            daily_names = {value for item in metadata() for value in (item['id'], item['name'], item['name'] + '比赛')}
            if (text.startswith('组织比赛') or text in ('单打', '双打', '男双', '女双', '混双')
                    or re.fullmatch(r'分配\s*(随机|种子分布|平均分布|高带低)', text) or text in daily_names):
                require_capability(self.ctx, 'daily_events')
        if text != '占用查询' and re.match(r'^(?:占用|释放占用|开启节假日|关闭节假日|节假日设置)',text):
            require_capability(self.ctx, 'management')
        if text in ('场地占用','占用查询','场地安排'):
            require_capability(self.ctx, 'enrollment')
        if (text in ('补全资料','注册','注册申请','注册资料','会员状态','我的资料','我的会籍','续费记录','会员有效期','性别','补充性别')
                or re.match(r'^(?:注册|设置姓名|确认注册)\s+', text)
                or re.fullmatch(r'(?:补充)?性别\s*[:：]?\s*(?:男|女)', text)
                or re.fullmatch(r'自评\s*(?:\d+|[一二三四五六七八九])\s*级', text)):
            require_capability(self.ctx, 'registration')
        if (text in ('名单', '') or re.fullmatch(r'报名(?:\+\d+)?(?:\s+接龙)?', text)
                or re.match(r'^报名(?:\s+接龙)?[,，]', text)
                or text.startswith('取消') and not re.fullmatch(r'取消\s+[a-f0-9]{12}', text, re.I) and text not in ('取消记录','取消计划','取消资料申请')):
            require_capability(self.ctx, 'enrollment')
        if (re.fullmatch(r'(?:我的)?(?:对战历史|对局历史|对战记录|对战统计|对局统计)(?:\s+.+)?', text)
                or re.match(r'^(?:修改记录|作废记录|撤销记录|记录)', text)
                or re.fullmatch(r'(?:选择|选)\s*\d+', text)
                or text in ('重复记录','取消记录')):
            require_capability(self.ctx, 'match_records')
        if text in ('我的统计', '统计', '全部统计', '统计 全部'):
            require_capability(self.ctx, 'statistics')
            if text != '我的统计':
                require_capability(self.ctx, 'management')
        if (text in ('待审核','待审批','审核列表','注册审核列表')
                or re.match(r'^(?:审核通过|通过审核|审核批准|审核同意|审核拒绝|拒绝审核|审核退回)', text)):
            require_capability(self.ctx, 'management')
        if (re.match(r'^/?AI(?:\s|:|：|$|帮助|状态|确认|取消|进度|计划)', text, re.I)
                or text in ('确认','取消计划','查看计划','计划进度','进度','计划')
                or re.fullmatch(r'(?:确认|取消|进度|计划)\s+[a-f0-9]{12}', text, re.I)):
            require_capability(self.ctx, 'ai')
        if re.fullmatch(r'(?:开启|关闭)AI', text, re.I):
            require_capability(self.ctx, 'ai')
            require_capability(self.ctx, 'management')

    def event_by_name(self, title, venue_id, day=None, statuses=None):
        def normalized(value):
            return re.sub(r'\s+', '', value).casefold()
        # Resolve exact names to explain a disabled business without exposing
        # its schedule. All public lists and actual reads remain filtered.
        items = [item for item in self.tournaments().list(venue_id, _include_disabled=True)
                 if (not day or item['date'] == day) and (not statuses or item['status'] in statuses)]
        names = {normalized(title)}
        if day:
            names.add(normalized(f'{day} {title}'))
        found = [item for item in items if normalized(item['title']) in names]
        if not found:
            raise ValueError('未找到该赛事名称。请按对战表顶部的赛事名称输入，例如：周二双打，第1场21:19')
        visible = [item for item in found if self.event_allowed(item)]
        if not visible:
            self.require_event(found[0])
        found = visible
        if len(found) > 1:
            raise ValueError('存在同名赛事，本次未操作。请在指令前添加日期，或在后台为赛事设置不同名称。')
        return self.require_event(self.tournaments().get(found[0]['id'], venue_id))

    async def run(self, content, name=None, venue_id=None, allow_ai=True, expected=None):
        self.refresh_context()
        message = normalize_message(content, self.ctx.bot_name)
        if message.text in ('连接', '连接测试'):
            return '**机器人连接成功**\n发送“帮助”查看本机器人的可用功能。'
        text, selection = parse_selection(message.text or '帮助', venue_id or self.ctx.default_venue_id, today=self.clock().date())
        venue_id = selection['venue_id']
        self.service.venue(venue_id)
        self.require_command_capabilities(text)
        members = Members(self.ctx, self.clock)
        # A platform display name can prefill a record; it never approves membership.
        member = members.touch(name=name if name and name != self.ctx.actor else None)
        if text=='补全资料':
            text='修改资料' if member['status']=='approved' else '注册'
        if text in ('注册','注册申请'):
            if member['status']=='approved':
                if member.get('gender') not in ('male','female') or not member.get('phone') or not member.get('level'):
                    from .registration import create_form
                    link=create_form(self.ctx,self.clock,purpose='profile')
                    if link:return f"**{escape(member['name'])} · 已注册**\n\n[补全个人资料]({link})"
                return f"**{escape(member['name'])} · 已注册**"
            if member['status']=='suspended':
                return '会员已停用，请联系管理员处理，无需重复注册。'
            from .registration import create_form
            url = create_form(self.ctx, self.clock)
            intro = '姓名、性别必填；级别、手机号选填。'
            if url:
                return mention(self.ctx.actor) + f'\n\n[填写注册资料]({url})'
            return intro + '\n\n发送：注册 张三 男 2级；也可发送 注册 张三 男。'
        register = re.fullmatch(r'(?:注册|设置姓名)\s+(.+)', text)
        if register:
            if member['status']=='approved':
                return f"**{escape(member['name'])} · 已注册**"
            tokens = register[1].strip().split()
            phone, level, gender = None, None, None
            if tokens and re.fullmatch(r'(?:1[3-9]\d{9}|\+[1-9]\d{7,14})', tokens[-1]):
                phone = tokens.pop()
            if tokens and re.fullmatch(r'(?:自评)?(?:\d+|[一二三四五六七八九])级', tokens[-1]):
                raw_level = re.sub(r'自评|级', '', tokens.pop())
                level = int(raw_level) if raw_level.isdigit() else '一二三四五六七八九'.index(raw_level) + 1
                members._level(level)
            if tokens and tokens[-1] in ('男', '女', 'male', 'female'):
                gender = tokens.pop()
            if not tokens or not gender:
                raise ValueError('姓名和性别必填，例如：注册 张三 男 2级；也可发送 注册 打开表单')
            member = members.apply(' '.join(tokens), level, phone, gender=gender)
            if member.get('registration_result')=='linked':
                return f"**{escape(member['name'])} · 资料已关联，注册成功**"
            if member['status'] == 'approved':
                return f"**{escape(member['name'])} · 已注册**"
            return f"**{escape(member['name'])} · 注册申请已提交，待审核**"
        confirmation = re.fullmatch(r'确认注册\s+([a-f0-9]{10})', text)
        if confirmation:
            from .registration import confirm_form
            member = confirm_form(self.ctx, confirmation[1], self.clock)
            if member.get('registration_result')=='linked':
                return f"**{escape(member['name'])} · 资料已关联，注册成功**"
            if member['status'] == 'approved':
                return f"**{escape(member['name'])} · 已注册**"
            return f"{escape(member['name'])}，注册申请已提交，等待管理员审核。"
        if text in ('修改资料','申请修改资料','修改个人信息','申请调整资料','可选分组','分组列表','我的资料申请','资料申请进度','取消资料申请') or re.match(r'^(?:(?:申请|修改|调整)(?:等级|分组)|资料(?:待审核|审核列表|查看|通过|拒绝))', text):
            from .profile_changes import ProfileChanges
            profile_reply = ProfileChanges(self.ctx, self.clock).handle(text)
            if profile_reply is not None:
                return profile_reply
        if text in ('注册资料','会员状态','我的资料','我的会籍','续费记录','会员有效期'):
            labels = {'unregistered':'未注册','pending':'待审批','approved':'已通过','rejected':'已退回','suspended':'已停用'}
            result = table(['姓名', '性别', '会员状态', '等级', '自评申请'], [[member['name'] or '待填写', {'male':'男','female':'女'}.get(member.get('gender'),'待补齐'), labels.get(member['status'], member['status']),
                f"{member['level']}级" if member.get('level') else '无级别', f"{member['requested_level']}级 · 待审核" if member.get('level_status') == 'pending' else '无待审申请']])
            from .membership_terms import MembershipTerms
            terms = MembershipTerms(self.ctx, self.clock)
            summary = terms.summary()
            current, upcoming = summary.get('current_term'), summary.get('next_term')
            result += '\n\n**会籍有效期：' + escape(summary['status_label']) + '**'
            if current:
                result += f"\n当前有效期：{current['start_date']} 至 {current['end_date']}（含首尾日）。"
            if upcoming:
                result += f"\n下一段有效期：{upcoming['start_date']} 至 {upcoming['end_date']}。"
            if member['status'] == 'approved' and summary.get('configured') and not summary['can_enroll']:
                result += '\n当前不能新增报名；仍可查询历史、取消已有报名。续费请联系管理员。'
            if text in ('我的会籍','续费记录','会员有效期'):
                periods = terms.list()
                result += '\n\n' + (table(['起始日期','截止日期','记录状态'], [[row['start_date'], row['end_date'], '有效记录' if row['status']=='valid' else '已作废'] for row in periods]) if periods else '尚未登记有效期，请联系管理员。')
            if member['status'] == 'approved' and text in ('注册资料','会员状态','我的资料'):
                result += '\n\n分组：' + ( '、'.join(escape(g['name']) for g in member['groups']) or '未分组')
                result += '\n发送 修改资料 申请调整；我的资料申请 查看进度。'
            return result
        if text == '身份':
            return mention(self.ctx.actor, member.get('name') or '') + f"\n\n用户ID：{escape(self.ctx.actor)}；场地{venue_id}；" + ('后台管理员 · 正式会员' if self.ctx.is_admin else {'unregistered':'未注册账号','pending':'注册申请待审批','approved':'正式会员','rejected':'注册申请已退回','suspended':'会员已停用'}.get(member['status'],'未注册账号'))
        if text in ('我的权限','权限'):
            from .security import PERMISSION_DEFINITIONS
            permitted = [item for item in PERMISSION_DEFINITIONS if self.ctx.can(item['id'])]
            return '**我的管理权限**\n\n' + (table(['权限'], [[p['name']] for p in permitted]) if permitted else '当前没有管理权限。已审核会员可以使用本机器人已开启的会员功能。')
        if text in ('使用说明','使用手册','操作说明'):
            base = self.service.config().get('public_base_url', '').rstrip('/')
            return f'[打开使用说明]({base}/guide)\n\n会员报名、比赛操作和管理员使用方法。' if base else '发送 帮助 查看常用指令；使用说明网页请向管理员获取。'
        if text in ('帮助','help','?','？') or text.startswith('帮助 '):
            category = text[3:].strip() if text.startswith('帮助 ') else None
            response = scoped_help(self.ctx,member,'AI' if category and category.casefold() == 'ai' else category,self.clock)
            base = self.service.config().get('public_base_url', '').rstrip('/')
            return response + (f'\n\n[完整使用说明]({base}/guide)' if base else '')
        gender_input = re.fullmatch(r'(?:补充)?性别\s*[:：]?\s*(男|女)', text)
        if gender_input:
            members.set_gender(gender_input[1])
            return '性别已保存为' + gender_input[1] + '，可继续' + ('报名或' if supports(self.ctx, 'enrollment') else '') + '组织比赛。'
        if text in ('性别', '补充性别'):
            return '发送：性别 男 / 性别 女。'
        self_rating = re.fullmatch(r'自评\s*(\d+|[一二三四五六七八九])\s*级', text)
        if self_rating:
            level = int(self_rating[1]) if self_rating[1].isdigit() else '一二三四五六七八九'.index(self_rating[1]) + 1
            members.request_level(level)
            return f'已提交自评{level}级，等待管理员审核。'
        from .bot_access import require_access
        require_access(self.ctx,self.clock)
        ai_entry = re.fullmatch(r'(?:/?ai)(?:[\s:：]+(.*))?', text, re.I | re.S)
        ai_body = (ai_entry[1] or '').strip() if ai_entry else None
        if re.fullmatch(r'AI\s*帮助', text, re.I) or ai_entry and ai_body in ('', '帮助', 'help', '?', '？'):
            return command_help(member, self.ctx.is_admin, 'AI',permissions=self.ctx.permissions,capabilities=effective_capabilities(self.ctx))
        if re.fullmatch(r'AI\s*状态', text, re.I) or ai_entry and ai_body == '状态':
            enabled = self.service.config()['ai']['enabled']
            return '**AI业务助手：' + ('已开启' if enabled else '已关闭') + '**\n\n发送 AI 加一句业务需求；普通指令始终直接处理。发送 帮助 AI 查看用法。'
        if ai_entry and allow_ai:
            from .ai_catalog import local_route
            feature_id = local_route(ai_body)
            if feature_id:
                return await self.route_feature(feature_id)
        members.require_approved()
        from .availability_dialog import handle_availability
        availability_reply = handle_availability(self.ctx,text,selection,self.clock)
        if availability_reply is not None:
            return availability_reply
        from .member_review import MemberReview
        review_reply = MemberReview(self.ctx,self.clock).handle(text)
        if review_reply is not None:
            return review_reply
        name = member.get('name') or name or '管理员'
        control = ai_body if ai_entry else re.sub(r'^AI\s*', '', text, flags=re.I)
        if control in ('确认','取消计划','查看计划','计划进度','进度','计划') or ai_entry and control == '取消':
            from .ai_conversation import AiConversation
            conversation = AiConversation(self.ctx,self.clock)
            pending = control in ('确认','取消计划','取消')
            record = conversation.current(pending=pending)
            if control == '确认':
                return await self.confirm(record['token'],name)
            if control in ('取消计划','取消'):
                return self.cancel_proposal(record['token'])
            from .ai_plan_flow import Plans, is_plan
            if is_plan(self,record['token']):
                result = Plans(self).status(record['token'])
                if record['status'] == 'interrupted':
                    result += '\n\n执行曾中断，请先核对业务记录，再提出剩余需求。'
                return result
            labels = {'completed':'已完成','pending':'待确认','failed':'未完成','running':'处理中','expired':'已过期','cancelled':'已取消','drafting':'正在整理'}
            return '**AI 计划 · '+labels.get(record['status'],record['status'])+'**'
        confirm = re.fullmatch(r'(?:AI[\s:：]*)?确认\s+([a-f0-9]{12})', ai_body if ai_entry else text, re.I)
        if confirm:
            return await self.confirm(confirm[1], name)
        cancel = re.fullmatch(r'(?:AI[\s:：]*)?取消\s+([a-f0-9]{12})', ai_body if ai_entry else text, re.I)
        if cancel:
            return self.cancel_proposal(cancel[1])
        progress = re.fullmatch(r'(?:AI[\s:：]*)?(?:进度|计划)\s+([a-f0-9]{12})', ai_body if ai_entry else text, re.I)
        if progress:
            from .ai_plan_flow import Plans
            return Plans(self).status(progress[1])
        if re.fullmatch(r'(?:开启|关闭)AI', text, re.I):
            enabled = text.startswith('开启')
            self.service.update_config({'ai': {'enabled': enabled}})
            return '**AI 助手已' + ('开启**' if enabled else '关闭**')
        if ai_entry:
            if not allow_ai:
                raise ValueError('AI提案不能再次调用AI')
            body = (ai_entry[1] or '').strip()
            prefix = ' '.join(value for value in (selection['day'], selection['at'], f"第{selection['number']}场" if selection['number'] else None) if value)
            return await self.propose((prefix + ' ' + body).strip(), venue_id)
        from .casual_dialog import handle_casual
        casual_reply = handle_casual(self.ctx, text, venue_id, self.clock, explicit_venue=bool(re.match(r'^场地\s*\d+', message.text)))
        if casual_reply is not None:
            return casual_reply
        if text in ('我的统计', '统计', '全部统计', '统计 全部'):
            data = self.service.stats(venue_id, self.ctx.actor if text == '我的统计' else None)
            summary = data['summary']
            title = '# ' + ('我的统计' if text == '我的统计' else '场地统计')
            result = title + '\n\n' + table(['指标', '数量'], [[label, summary[key]] for key, label in [('registrations','报名记录'),('active','有效报名'),('cancelled','取消记录'),('guests','代报记录'),('participations','出赛人次')]])
            if text != '我的统计' and data.get('members'):
                result += '\n\n' + table(['姓名','报名','有效','取消','出赛'], [[m.get('name') or '待完善姓名',m['registrations'],m['active'],m['cancelled'],m['participations']] for m in data['members']])
            if text == '我的统计' and supports(self.ctx, 'match_records'):
                from .casual import CasualMatches
                games = CasualMatches(self.ctx, self.clock).statistics(user_id=self.ctx.actor, venue_id=venue_id)
                result += '\n\n## 对战汇总\n\n' + table(['完赛', '获胜', '失利', '胜率'], [[games['played'], games['wins'], games['losses'], str(games['win_rate']) + '%']])
                pass  # Detailed history is available on explicit request.
            return result
        guest_names = None
        if re.match(r'^报名(?:\s+接龙)?[,，]', text):
            parts = re.split(r'[,，、]', text)
            guest_names = [v.strip() for v in parts[1:]]
            if not guest_names or any(not v for v in guest_names):
                raise ValueError('请填写每位同行姓名，例如：报名，AAA，BBB')
            text = '报名+' + str(len(guest_names)) + (' 接龙' if '接龙' in parts[0] else '')
        match = re.fullmatch(r'报名(?:\+(\d+))?(?:\s+(接龙))?', text)
        if match:
            guests = int(match[1] or 0)
            data = self.service.enroll(self.ctx.actor, name, guests, **selection, guest_names=guest_names)
            session = data['sessions'][0]
            if match[2]:
                return roster(data, venue_id)
            owned = [r for r in data['registrations'] if r['user_id'] == self.ctx.actor and r['status'] == 'active']
            total_guests = sum(r['guest_index'] > 0 for r in owned)
            return f"## 报名成功 · {session['date']}\n\n场地{venue_id} · {escape(session['name'])} {session['start']}–{session['end']}\n\n" + registration_table(owned)
        if text == '取消报名':
            result = self.service.cancel(self.ctx.actor, **selection)
            remaining = sum(r['user_id'] == self.ctx.actor and r['guest_index'] > 0 and r['status'] == 'active' for r in result['registrations'])
            reply = '**已取消本人报名**' if result['cancelled'] else '本人尚未报名。'
            return reply + (f'\n\n保留 {remaining} 位代报名同行。' if remaining else '')
        if text in ('名单', ''):
            session_id = None
            if selection['at'] or selection['number']:
                session_id = self.service.resolve_session(**selection, for_write=False)['id']
            return roster(self.service.registrations(selection['day'], venue_id, session_id), venue_id)
        tournaments = self.tournaments()
        formats = tournaments.formats()
        named_enrollment = re.fullmatch(r'(.+?)[,，]\s*(报名(?:\+(\d+))?|报名[,，].+|取消报名|取消.+)', text)
        if named_enrollment:
            event = self.event_by_name(named_enrollment[1].strip(), venue_id, selection['day'])
            if named_enrollment[2] == '取消报名':
                tournaments.cancel(event['id'], self.ctx.actor, venue_id)
                return f"已处理 {escape(event['title'])} 的本人取消报名，代报名人员仍保留。"
            event_guests = cancellation_guests(named_enrollment[2])
            if event_guests is not None:
                tournaments.cancel_guests(event['id'], self.ctx.actor, event_guests, venue_id)
                return f"已取消 {escape(event['title'])} 中本人代报名的：{escape('、'.join(event_guests))}。其他报名保持不变。"
            named = re.fullmatch(r'报名[,，](.+)', named_enrollment[2])
            event_names = [v.strip() for v in re.split(r'[,，、]', named[1])] if named else None
            tournaments.enroll(event['id'], self.ctx.actor, name, len(event_names) if event_names is not None else int(named_enrollment[3] or 0), venue_id, guest_names=event_names)
            return tournaments.describe(event['id'], 'registration')
        cancel_names = cancellation_guests(text)
        if cancel_names is not None:
            result = self.service.cancel_guests(self.ctx.actor, cancel_names, **selection)
            return f"已取消本人代报名的：{escape('、'.join(cancel_names))}（{result['cancelled']}人）。其他报名保持不变，后续报名已依次递补。"
        organizing = re.fullmatch(r'组织比赛(?:\s*(单打|双打|男双|女双|混双))?(?:[\s，,]+(.+))?', text)
        if organizing:
            return DailyPlanning(self.ctx, self.clock).begin(selection, 'singles' if organizing[1] == '单打' else 'doubles', title=organizing[2], doubles_category={'男双':'mens','女双':'womens','混双':'mixed'}.get(organizing[1],'open'))
        if text in ('单打', '双打', '男双', '女双', '混双'):
            return DailyPlanning(self.ctx, self.clock).change(venue_id, discipline='singles' if text == '单打' else 'doubles', doubles_category={'男双':'mens','女双':'womens','混双':'mixed'}.get(text,'open'))
        distribution = re.fullmatch(r'分配\s*(随机|种子分布|平均分布|高带低)', text)
        if distribution:
            return DailyPlanning(self.ctx, self.clock).change(venue_id, assignment_strategy=STRATEGIES[distribution[1]])
        selected = next((f for f in formats if text in (f['name'], f['id'], f['name'] + '比赛')), None)
        if selected:
            return DailyPlanning(self.ctx, self.clock).create(venue_id, selected['id'])
        organization_edit = re.fullmatch(r'([^,，]+)[,，]\s*(集合说明|公开说明|协调安排)(?:\s*[:：]\s*|\s+)?(.*)', text, re.S)
        if organization_edit:
            from .event_management import EventManagement
            label, value = organization_edit[2], organization_edit[3].strip()
            if not value:
                raise ValueError(f'请在{label}后填写内容，例如：赛事名，{label} 18点到场准备。')
            event = self.event_by_name(organization_edit[1].strip(), venue_id, selection['day'])
            field = {'集合说明': 'meeting_instructions', '公开说明': 'description', '协调安排': 'coordination'}[label]
            EventManagement(self.ctx, self.clock).save_organization(event['id'], {field: value, 'expected_version': event['version']})
            # Never publish internal coordination text into the group reply.
            result = f"{escape(event['title'])} 的{label}已保存。"
            if field != 'coordination':
                result += f"\n\n发送 {escape(event['title'])}，说明 查看公开内容。"
            return result
        score_input = ('第' + str(selection['number']) + '场' + text) if selection['number'] is not None else text
        parsed_score = parse_score(score_input)
        if parsed_score:
            if parsed_score['title']:
                event = self.event_by_name(parsed_score['title'], venue_id, selection['day'], ('running', 'paused', 'completed'))
            elif parsed_score['event_id'] is not None:
                event = self.require_event(tournaments.get(parsed_score['event_id'], venue_id))
            else:
                candidates = [e for e in tournaments.list(venue_id) if self.event_allowed(e) and e['status'] == 'running' and (not selection['day'] or e['date'] == selection['day'])]
                if not candidates:
                    return '当前场地没有进行中的赛事，请先发送 组织比赛。'
                if len(candidates) > 1:
                    return '存在多场进行中赛事，本次没有记分。请指定赛事名称：\n\n' + table(['赛事名称', '记分示例'], [[e['title'], f"{e['title']}，第{parsed_score['number']}场21:19"] for e in candidates])
                event = tournaments.get(candidates[0]['id'], venue_id)
            match = next((m for m in event['matches'] if m['number'] == parsed_score['number']), None)
            if not match:
                raise ValueError('该赛事没有此场次，请查看对战表')
            result = tournaments.score(event['id'], parsed_score['number'], parsed_score['scores'],
                (expected or {}).get('match_version', match['version']), venue_id, by_number=True)
            return tournaments.describe(result['id'])
        event_action = re.fullmatch(r'(.+?)[,，]\s*(对战表|比分|积分表|我的赛程|规则|说明|暂停比赛|暂停|继续比赛|恢复比赛|晋级|关闭比赛|关闭)', text)
        if event_action:
            event = self.event_by_name(event_action[1].strip(), venue_id, selection['day'])
            action = event_action[2]
            if action in ('对战表','比分','积分表','我的赛程','规则','说明'):
                return tournaments.describe(event['id'], 'rules' if action in ('规则','说明') else 'mine' if action == '我的赛程' else 'results' if action == '积分表' else 'schedule')
            if action in ('继续比赛','恢复比赛') and event['status'] == 'running':
                return tournaments.describe(event['id'])
            tournaments.transition(event['id'], {'暂停比赛':'pause','暂停':'pause','继续比赛':'resume','恢复比赛':'resume','晋级':'advance','关闭比赛':'close','关闭':'close'}[action], venue_id)
            return f"{escape(event['title'])} 已" + ('暂停。' if action in ('暂停比赛','暂停') else '恢复。' if action in ('继续比赛','恢复比赛') else '推进到下一阶段。' if action == '晋级' else '关闭，历史对战与比分已保留。')
        if text in ('对战表', '比分', '我的赛程'):
            entries = [e for e in tournaments.list(venue_id) if self.event_allowed(e) and e['date'] == (selection['day'] or self.clock().date().isoformat())]
            if text == '我的赛程':
                entries = [e for e in entries if any(str(p['user_id']) == self.ctx.actor for p in tournaments.get(e['id'])['participants'])]
            if not entries:
                return '当日没有符合条件的赛事。'
            return '\n\n'.join(tournaments.describe(e['id'], 'mine' if text == '我的赛程' else 'schedule') for e in entries)
        resume = re.fullmatch(r'继续比赛(?:\s+(\d+))?', text)
        if resume:
            if resume[1]:
                event = self.require_event(tournaments.get(int(resume[1]), venue_id))
                if event['status'] == 'paused':
                    event = tournaments.transition(event['id'], 'resume', venue_id)
                elif event['status'] not in ('running', 'closed'):
                    raise ValueError('该赛事不在可继续状态')
                return tournaments.describe(event['id'])
            candidates = [event for event in tournaments.resume_candidates(venue_id, selection['day']) if self.event_allowed(event)]
            if not candidates:
                return '未找到本场地、本群且人员匹配的未结束赛事。可先报名后发起赛制。'
            if len(candidates) == 1:
                return await self.run(MessageInput(f'场地{venue_id} 继续比赛 {candidates[0]["id"]}'), name, allow_ai=False)
            return '存在多场未结束赛事，请选择：\n\n' + '\n\n'.join(f"继续比赛 {e['id']} · {escape(e['title'])}" for e in candidates)
        event_command = re.fullmatch(r'赛事\s*(\d+)\s+(报名(?:\+(\d+))?|取消报名|名单)', text)
        if event_command:
            event_id = int(event_command[1])
            self.require_event(tournaments.get(event_id, venue_id))
            if event_command[2] == '名单':
                event = tournaments.get(event_id, venue_id)
                return tournaments.describe(event_id, 'registration')
            if event_command[2] == '取消报名':
                tournaments.cancel(event_id, self.ctx.actor, venue_id)
                return '已处理该赛事的本人取消报名，代报名人员仍保留。'
            tournaments.enroll(event_id, self.ctx.actor, name, int(event_command[3] or 0), venue_id)
            return '赛事报名已更新。'
        for summary in tournaments.list(venue_id, _include_disabled=True):
            keyword = summary.get('keyword')
            phrase = (keyword if keyword.endswith('报名') else keyword + '报名') if keyword else ''
            if phrase and re.fullmatch(re.escape(phrase) + r'(?:\+(\d+))?', text):
                self.require_event(summary)
                matched = re.fullmatch(re.escape(phrase) + r'(?:\+(\d+))?', text)
                tournaments.enroll(summary['id'], self.ctx.actor, name, int(matched[1] or 0), venue_id)
                return f"已更新 {escape(summary['title'])} 的报名。"
        return '未识别指令，本次未执行。发送 帮助 查看本机器人可用指令。' + ('自然语言需求请以 AI 开头。' if supports(self.ctx, 'ai') else '')

    async def route_feature(self, feature_id):
        from .ai_routing import feature_descriptor
        self.refresh_context()
        require_capability(self.ctx, 'ai')
        feature = feature_descriptor(self.ctx, feature_id, self.clock)
        if feature['allowed'] and feature['mode'] == 'command' and feature.get('command'):
            return await self.run(MessageInput(feature['command']), allow_ai=False)
        return feature['message']

    def event_ai_actions(self, event):
        permissions, status = event['permissions'], event['status']
        actions = ['query']
        if permissions['score'] and (status == 'running' or status == 'completed' and permissions['correct_score']):
            actions.append('score')
        if status == 'paused' and permissions['manage'] or status == 'running':
            actions.append('resume')
        if permissions['manage']:
            if status in ('running', 'paused'):
                actions.append('pause')
            if status not in ('completed', 'cancelled', 'archived'):
                actions.append('close')
            matches = event.get('matches', [])
            if status == 'running' and event['format'] == 'group_knockout' and matches and all(m['status'] == 'completed' and m['stage'] != 'knockout' for m in matches):
                actions.append('advance')
        if status == 'registration':
            try:
                self.tournaments().engine._registration_allowed(event, self.clock())
            except ValueError:
                pass
            else:
                actions.extend(['enroll', 'cancel'])
        return actions

    def ai_context(self, venue_id):
        self.refresh_context()
        require_capability(self.ctx, 'ai')
        Members(self.ctx, self.clock).require_approved()
        self.service.venue(venue_id, True)
        tournaments = self.tournaments()
        visible = [event for event in tournaments.list() if self.event_allowed(event)][:100]
        entries = [tournaments.get(event['id']) for event in visible]
        events = []
        for event in entries:
            permissions = event['permissions']
            actions = self.event_ai_actions(event)
            events.append({'id': event['id'], 'title': event['title'], 'venue_id': event['venue_id'],
                           'date': event['date'], 'actions': actions,
                           'match_numbers': [m['number'] for m in event['matches'] if m['status'] == 'ready' or m['status'] == 'completed' and permissions['correct_score']]})
        planner = DailyPlanning(self.ctx, self.clock) if supports(self.ctx, 'daily_events') else None
        record = planner.get(venue_id) if planner else None
        planning = None
        formats = tournaments.formats() if planner else []
        if record and record['status'] == 'ready':
            try:
                _, _, available = planner.info(record)
            except (ValueError, PermissionError):
                # An expired/closed planning session must not disable unrelated AI queries.
                pass
            else:
                planning = {**record['payload'], 'venue_id': venue_id, 'status': 'ready', 'token':record['token'],
                            'available_format_ids': [f['id'] for f in available if f['available']]}
                # Keep the complete catalogue for a request that starts a new
                # planning session. Availability above belongs only to the
                # existing session and still constrains direct DailyPlan use.
        return {'bot_id': self.ctx.id, 'chat_id': self.ctx.chat_id, 'user_id': self.ctx.actor,
                'today': self.clock().date().isoformat(), 'venue_id': venue_id, 'is_admin': self.ctx.is_admin, 'approved': True,
                'permissions':list(self.ctx.permissions),
                'bot_capabilities':list(self.ctx.capabilities) if self.ctx.capabilities is not None else None,
                'formats': formats, 'planning': planning, 'events': events,
                'allowed_venue_ids': [v['id'] for v in self.service.venues() if v['active']],
                'scoreable_tournament_ids': [e['id'] for e in events if 'score' in e['actions']],
                'resumable_tournament_ids': [e['id'] for e in events if 'resume' in e['actions']]}

    def snapshot(self, command):
        text, selection = parse_selection(command)
        self.require_command_capabilities(text)
        result = {'bot_capabilities':list(self.ctx.capabilities) if self.ctx.capabilities is not None else None}
        if re.fullmatch(r'自评[1-9]级', text):
            member = Members(self.ctx, self.clock).require_approved()
            result['member'] = {key: member.get(key) for key in ('status', 'level', 'requested_level', 'level_status', 'gender')}
        event_mutation = re.fullmatch(r'(.+?)[,，]\s*(暂停比赛|关闭比赛|晋级|报名(?:\+\d+)?|报名[,，].+|取消报名|取消.+)', text)
        if event_mutation:
            event = self.event_by_name(event_mutation[1], selection['venue_id'], selection['day'])
            if event_mutation[2] in ('暂停比赛', '关闭比赛', '晋级'):
                action = {'暂停比赛':'pause', '关闭比赛':'close', '晋级':'advance'}[event_mutation[2]]
                if action not in self.event_ai_actions(event):
                    raise PermissionError('当前没有操作权限，或赛事状态不允许该操作')
            else:
                self.tournaments().engine._registration_allowed(event, self.clock())
            result['event_digest'] = hashlib.sha256(dumps(event).encode()).hexdigest()
        if not event_mutation and (re.fullmatch(r'报名(?:\+\d+)?(?:\s+接龙)?|取消报名', text) or re.match(r'^报名(?:\s+接龙)?[,，]', text) or cancellation_guests(text) is not None or text.startswith('组织比赛')):
            session = self.service.resolve_session(**selection)
            command = f"场地{selection['venue_id']} {session['date']} {session['start']} {text}"
            result['session_id'] = session['id']
            result['session'] = session
            if text.startswith(('报名', '取消')):
                data = self.service.registrations(session['date'], selection['venue_id'], session['id'])
                result['owned_registrations'] = [{key: row.get(key) for key in ('id','guest_index','guest_name','name','status')}
                                                 for row in data['registrations'] if row['user_id'] == self.ctx.actor]
        elif text in {f['name'] for f in self.tournaments().formats()}:
            planner = DailyPlanning(self.ctx, self.clock)
            record = planner.get(selection['venue_id'])
            if not record or record['status'] != 'ready':
                raise ValueError('请先发送 组织比赛，确定场次和单打/双打后再选择赛制')
            payload = record['payload']
            candidates = self.tournaments().planning_candidates(payload['date'], selection['venue_id'], payload['session_id'])
            session = candidates['session']
            command = f"场地{selection['venue_id']} {session['date']} {session['start']} {text}"
            result.update(planning=record, formal=candidates['formal'], substitutes=candidates['substitutes'], session=session)
        score = parse_score(text)
        resume = re.fullmatch(r'继续比赛(?:\s+(\d+))?', text)
        if score:
            event = self.event_by_name(score['title'], selection['venue_id'], selection['day']) if score['title'] else self.require_event(self.tournaments().get(score['event_id'], selection['venue_id']))
            if not self.tournaments().can_score(event):
                raise PermissionError('无本赛事记分权限或临时授权已过期')
            match = next((m for m in event['matches'] if m.get('number') == score['number']), None)
            if not match:
                raise ValueError('记分场次不存在')
            if event['status'] not in ('running','completed') or match['status'] not in ('ready','completed'):
                raise ValueError('赛事或比赛场次尚未开放记分，请先查询对战表')
            if match['status'] == 'completed' and not event['permissions']['correct_score']:
                raise PermissionError('已有比分仅赛事组织者或管理员可以修正')
            result['match_version'] = match['version']
            result['event_digest'] = hashlib.sha256(dumps(event).encode()).hexdigest()
        named_resume = re.fullmatch(r'(.+?)[,，]\s*(?:继续比赛|恢复比赛)', text)
        if named_resume:
            event = self.event_by_name(named_resume[1], selection['venue_id'], selection['day'])
            if event['status'] != 'running' and not (event['status'] == 'paused' and event['permissions']['manage']):
                raise PermissionError('当前没有恢复此赛事的权限，或赛事状态不允许恢复')
            result['event_digest'] = hashlib.sha256(dumps(event).encode()).hexdigest()
        if resume:
            if not resume[1]:
                candidates = self.tournaments().resume_candidates(selection['venue_id'], selection['day'])
                candidates = [e for e in candidates if self.event_allowed(e) and (e['status'] == 'running' or e['status'] == 'paused' and self.tournaments().get(e['id'])['permissions']['manage'])]
                if len(candidates) != 1:
                    raise ValueError('未能唯一确定可继续的赛事，请使用赛事名称重新提出操作')
                if len(candidates) == 1:
                    command = f"场地{selection['venue_id']} 继续比赛 {candidates[0]['id']}"
                    event = self.tournaments().get(candidates[0]['id'])
                    result['event_digest'] = hashlib.sha256(dumps(event).encode()).hexdigest()
            else:
                event = self.require_event(self.tournaments().get(int(resume[1]), selection['venue_id']))
                result['event_digest'] = hashlib.sha256(dumps(event).encode()).hexdigest()
        return command, result

    async def propose(self, text, venue_id):
        from .ai_conversation import AiConversation
        self.refresh_context()
        require_capability(self.ctx,'ai')
        Members(self.ctx,self.clock).require_approved()
        if not self.service.config()['ai']['enabled']:
            return 'AI业务助手未开启，请联系管理员开启。'
        conversation = AiConversation(self.ctx,self.clock)
        generation, prior = conversation.begin(self.service.config()['ai']['confirmation_minutes'])
        try:
            return await self._propose(text,venue_id,generation,prior)
        finally:
            conversation.abandon(generation)

    async def _propose(self, text, venue_id, generation, prior):
        from .ai_gateway import interpret_limited
        from .ai import unsupported_reason
        from .ai_catalog import local_route
        self.refresh_context()
        require_capability(self.ctx, 'ai')
        feature_id = local_route(text)
        if feature_id:
            return await self.route_feature(feature_id)
        Members(self.ctx, self.clock).require_approved()
        if not self.service.config()['ai']['enabled']:
            return 'AI业务助手未开启，本次未调用模型。请使用固定指令，或联系管理员开启AI。'
        unsupported = unsupported_reason(text)
        if unsupported:
            return unsupported
        policy_epoch = self.ai_policy_epoch()
        context = self.ai_context(venue_id)
        if prior:
            context['pending_plan'] = prior
        proposal = await interpret_limited(self.ctx, text, context, self.service.config(True)['ai'], self.clock)
        from .ai_conversation import AiConversation
        AiConversation(self.ctx,self.clock).ensure_generation(generation)
        self.refresh_context()
        require_capability(self.ctx, 'ai')
        Members(self.ctx, self.clock).require_approved()
        self.service.venue(venue_id, True)
        if not self.service.config()['ai']['enabled']:
            return 'AI已关闭，本次未执行操作。'
        if policy_epoch != self.ai_policy_epoch():
            return '业务配置已变化，本次未执行，请重新提出操作。'
        if proposal.get('intent') == 'BusinessPlan':
            from .ai_plan_flow import Plans
            return await Plans(self).propose(proposal,generation)
        if proposal.get('intent') == 'FeatureRoute':
            scope = {'bot_id': self.ctx.id, 'chat_id': self.ctx.chat_id, 'user_id': self.ctx.actor}
            if proposal.get('scope') != scope:
                raise PermissionError('AI提案身份绑定无效')
            return await self.route_feature((proposal.get('arguments') or {}).get('feature_id'))
        command = proposal.get('command')
        if not command:
            return proposal.get('explanation', 'AI暂不可用，请使用固定指令。')
        scope = {'bot_id': self.ctx.id, 'chat_id': self.ctx.chat_id, 'user_id': self.ctx.actor}
        if proposal.get('scope') != scope:
            raise PermissionError('AI提案身份绑定无效')
        intent = proposal.get('intent')
        writes = {'Register','CancelRegistration','StartDailyPlan','DailyPlan','ResumeTournament','RecordScore','ToggleAI','SelfLevel','EventAction','EventRegistration'}
        query_text, _ = parse_selection(command)
        valid = {
            'SelfLevel': bool(re.fullmatch(r'自评[1-9]级', query_text)),
            'EventAction': bool(re.fullmatch(r'.+?[,，]\s*(暂停比赛|关闭比赛|晋级)', query_text)),
            'EventRegistration': bool(re.fullmatch(r'.+?[,，]\s*(报名(?:\+\d+)?|报名[,，].+|取消报名|取消.+)', query_text)),
            'Register': bool(re.fullmatch(r'报名(?:\+\d+)?(?:\s+接龙)?', query_text) or re.fullmatch(r'报名(?:\s+接龙)?[,，][^\n]+', query_text)),
            'CancelRegistration': query_text == '取消报名' or cancellation_guests(query_text) is not None,
            'StartDailyPlan': bool(re.fullmatch(r'组织比赛(?:\s*(单打|双打|男双|女双|混双))?(?:[\s，,]+(.+))?', query_text)),
            'DailyPlan': query_text in {f['name'] for f in self.tournaments().formats()},
            'ResumeTournament': bool(re.fullmatch(r'继续比赛(?:\s+\d+)?|.+?[,，]\s*(?:继续比赛|恢复比赛)', query_text)),
            'RecordScore': parse_score(query_text) is not None,
            'ToggleAI': query_text in ('开启AI','关闭AI'),
            'Roster': query_text == '名单', 'PersonalStats': query_text == '我的统计',
            'AllStats': query_text == '统计', 'Help': query_text == '帮助',
            'TournamentQuery': bool(re.fullmatch(r'.+?[,，]\s*(?:对战表|比分|积分表|我的赛程|规则)', query_text)),
        }
        if not valid.get(intent):
            raise ValueError('AI提案不属于已支持的业务流程，本次未执行，请使用固定指令')
        if intent in ('AllStats','ToggleAI'):
            from .ai_permissions import OPERATION_PERMISSIONS
            self.ctx.require(OPERATION_PERMISSIONS[intent])
        if intent == 'DailyPlan' and self.ai_context(venue_id)['planning'] != context['planning']:
            raise ValueError('策划或报名人数已变化，请重新选择赛制')
        if intent in writes:
            command, snapshot = self.snapshot(command)
            token = secrets.token_hex(6)
            arguments = proposal.get('arguments') or {}
            if intent == 'StartDailyPlan':
                from .ai import StartDailyPlan
                arguments = StartDailyPlan.model_validate(arguments).model_dump()
            expires = stamp(self.clock() + timedelta(minutes=self.service.config()['ai']['confirmation_minutes']))
            from contextlib import nullcontext
            from .ai_conversation import AiConversation
            conversation = AiConversation(self.ctx,self.clock)
            steps = [{'operation':intent,'arguments':arguments}]
            payload = dumps({'version':6,'intent':intent,'arguments':arguments,'command':command,'snapshot':snapshot,
                             'policy_epoch':self.ai_policy_epoch(),'request_day':self.clock().date().isoformat()})
            with conversation.publication(generation) as shared:
                connection = nullcontext(shared) if self.ctx.db_path == self.ctx.transport_db_path else connect(self.ctx.transport_db_path,True)
                with connection as db:
                    db.execute("UPDATE ai_pending SET status='superseded' WHERE chat_id=? AND user_id=? AND status='pending'", (self.ctx.chat_id, self.ctx.actor))
                    db.execute('INSERT INTO ai_pending(token,bot_id,chat_id,user_id,command,expires_at) VALUES(?,?,?,?,?,?)',
                               (token,self.ctx.id,self.ctx.chat_id,self.ctx.actor,payload,expires))
                conversation.publish(generation,token,steps,expires,shared)
            detail = ''
            if intent in ('Register', 'CancelRegistration', 'EventRegistration'):
                from .ai_operations import describe_step
                detail = '\n\n' + escape(describe_step({'operation':intent,'arguments':arguments})[1])
            if intent == 'StartDailyPlan':
                label = next((k for k,v in STRATEGIES.items() if v == arguments['assignment_strategy']), '随机')
                detail = f'\n\n分配方式：{label}；确认后显示人数与可选赛制，再选择比赛形式。'
            return '**AI 计划待确认 · 尚未执行**\n\n1. '+escape(command)+detail+'\n\n回复 **确认** 执行；修改请发 **AI + 修改意见**；放弃请发 **取消计划**。'
        return '**AI 查询 · 后台结果**\n\n' + await self.run(MessageInput(command), allow_ai=False)

    def ai_policy_epoch(self):
        with connect(self.ctx.db_path) as db:
            return db.execute("SELECT COALESCE(MAX(id),0) FROM audit WHERE action='config.update'").fetchone()[0]

    def cancel_proposal(self, token):
        self.refresh_context()
        require_capability(self.ctx, 'ai')
        from .ai_plan_flow import Plans, is_plan
        from .ai_conversation import AiConversation
        conversation = AiConversation(self.ctx,self.clock)
        if is_plan(self, token):
            return Plans(self).cancel(token)
        conversation.claim(token)
        with connect(self.ctx.transport_db_path, True) as db:
            changed = db.execute("UPDATE ai_pending SET status='cancelled' WHERE token=? AND bot_id=? AND chat_id=? AND user_id=? AND status='pending' AND expires_at>?", (token,self.ctx.id,self.ctx.chat_id,self.ctx.actor,stamp(self.clock()))).rowcount
        if not changed:
            conversation.finish(token,'failed')
            raise ValueError('提案已失效或不属于当前身份，未执行任何操作')
        conversation.finish(token,'cancelled')
        return 'AI提案已取消，未执行业务操作。'

    async def confirm(self, token, name):
        from .ai_conversation import AiConversation
        self.refresh_context()
        require_capability(self.ctx,'ai')
        Members(self.ctx,self.clock).require_approved()
        from .ai_plan_flow import Plans, is_plan
        if is_plan(self,token):
            return await Plans(self).confirm(token)
        conversation = AiConversation(self.ctx,self.clock)
        conversation.claim(token)
        try:
            result = await self._confirm(token,name)
            from .ai_plan_flow import Plans, is_plan
            status = Plans(self).store.get(token)['status'] if is_plan(self,token) else 'completed'
        except BaseException:
            conversation.finish(token,'failed')
            raise
        conversation.finish(token,status)
        return result

    async def _confirm(self, token, name):
        self.refresh_context()
        require_capability(self.ctx, 'ai')
        Members(self.ctx, self.clock).require_approved()
        from .ai_plan_flow import Plans, is_plan
        if is_plan(self, token):
            return await Plans(self).confirm(token)
        with connect(self.ctx.transport_db_path, True) as db:
            row = db.execute("SELECT * FROM ai_pending WHERE token=? AND bot_id=? AND chat_id=? AND user_id=? AND status='pending' AND expires_at>?", (token, self.ctx.id, self.ctx.chat_id, self.ctx.actor, stamp(self.clock()))).fetchone()
            if not row:
                raise ValueError('确认已过期、已使用，或不属于当前身份')
            db.execute("UPDATE ai_pending SET status='consuming' WHERE token=?", (token,))
        payload = json.loads(row['command'])
        try:
            if not self.service.config()['ai']['enabled']:
                raise ValueError('AI已关闭，提案不能继续确认；请使用固定指令')
            if payload.get('version') != 6 or 'plan_id' in payload or payload.get('policy_epoch') != self.ai_policy_epoch() or payload.get('request_day') != self.clock().date().isoformat():
                raise ValueError('业务配置、日期或AI流程版本已变化，请重新提出操作')
            _, current = self.snapshot(payload['command'])
            if current != payload['snapshot']:
                raise ValueError('场次、人员或赛事版本已变化，请重新提出操作')
            if payload['intent'] == 'StartDailyPlan':
                from .ai import StartDailyPlan
                arguments = StartDailyPlan.model_validate(payload['arguments'])
                _, selection = parse_selection(payload['command'])
                result = DailyPlanning(self.ctx,self.clock).begin(selection,arguments.discipline,arguments.assignment_strategy,arguments.title,doubles_category=arguments.doubles_category)
            else:
                result = await self.run(MessageInput(payload['command']), name, allow_ai=False, expected=payload['snapshot'])
        except BaseException:
            with connect(self.ctx.transport_db_path, True) as db:
                db.execute("UPDATE ai_pending SET status='failed' WHERE token=?", (token,))
            raise
        with connect(self.ctx.transport_db_path, True) as db:
            db.execute("UPDATE ai_pending SET status='consumed' WHERE token=?", (token,))
        return '**AI 已确认 · 执行结果**\n\n' + result
