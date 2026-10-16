"""Live robot capabilities filter direct commands, help and saved form links."""
import asyncio
from dataclasses import replace
from unittest.mock import patch

from app.commands import Commands
from app.help import command_help
from app.members import Members
from app.registration import create_form, form_status, submit_form
from app.tournaments import Tournaments
from tests.test_core import CoreFixture


class BotCommandsV211Tests(CoreFixture):
    def capabilities(self, enabled):
        self.platform.update(self.admin, self.ctx.id, {'capabilities': list(enabled)})

    def send(self, text, ctx=None):
        return asyncio.run(Commands(ctx or self.member, self.clock).run(text))

    def test_disabled_families_reject_before_member_or_business_mutations(self):
        self.capabilities([])
        stranger = replace(self.member, actor='never-seen')
        commands = ['注册', '注册 张三 男 3级', '确认注册 abcdef0123', '注册资料',
                    '我的会籍', '性别 男', '自评3级', '报名', '报名，张三',
                    '取消报名', '取消张三', '明天第二场报名', '名单', '2026-10-13',
                    '记录 单打 我 VS 张三 21:19', '选择1', '重复记录', '取消记录',
                    '我的对战历史', '修改记录 1 21:19', '作废记录1',
                    '我的统计', '统计', '待审核', '审核通过 张三',
                    'AI', 'AI状态', 'AI帮助', '/ai:报名', 'AI确认 abcdef012345',
                    'AI取消 abcdef012345', 'AI进度 abcdef012345', '开启AI']
        with patch('app.ai.interpret') as model:
            for text in commands:
                with self.subTest(text=text), self.assertRaisesRegex(PermissionError, '当前机器人未开启'):
                    self.send(text, stranger)
        model.assert_not_called()
        self.assertIsNone(Members(stranger, self.clock).get())
        self.assertEqual(self.s.registrations()['registrations'], [])

    def test_management_switch_never_grants_management_role(self):
        self.capabilities(['management','statistics','ai'])
        for text in ('待审核', '统计', '开启AI'):
            with self.subTest(text=text), self.assertRaises(PermissionError):
                self.send(text)
        self.capabilities(['statistics','ai'])
        for text in ('待审核', '统计', '开启AI'):
            with self.subTest(text=text), self.assertRaisesRegex(PermissionError, '群内管理'):
                self.send(text, self.ctx)

    def test_enrollment_remains_append_only_when_enabled(self):
        self.capabilities(['enrollment'])
        self.send('报名，张三，李四')
        self.send('报名+5')
        self.assertEqual(len(self.s.registrations()['registrations']), 8)
        self.send('取消报名')
        self.send('取消张三')
        active = [r for r in self.s.registrations()['registrations'] if r['status']=='active']
        self.assertEqual(len(active), 6)
        self.assertIn('李四', [r['name'] for r in active])

    def make_event(self, kind, title, running=True):
        events = Tournaments(self.ctx, self.clock)
        if kind == 'daily':
            for user in ('1001','1002','1003','1004'):
                self.s.enroll(user, '球友'+user)
        with patch('app.competitions._now', self.clock):
            event = events.create({'kind':kind, 'title':title, 'keyword':title if kind == 'large' else '',
                'date':self.now.date().isoformat(), 'format':'round_robin',
                'organizer_user_id':'1001', 'options':{'discipline':'singles'},
                'participants':[{'id':u,'user_id':u} for u in ('1001','1002','1003','1004')],
                'registration_open':'2026-10-12T00:00:00+08:00',
                'registration_close':'2026-10-12T17:00:00+08:00'})
            if running and kind != 'daily':
                events.transition(event['id'], 'open_registration')
                events.transition(event['id'], 'close_registration')
                event = events.transition(event['id'], 'start')
        return event

    def test_daily_only_competitions_large_events_do_not_leak(self):
        daily = self.make_event('daily', '每日双打')
        large = self.make_event('large', '大型联赛')
        self.capabilities(['daily_events'])
        for text in ('大型联赛，对战表', '大型联赛，暂停比赛', '大型联赛，第一场21:19',
                     '大型联赛，报名', f"赛事{large['id']} 名单", f"继续比赛 {large['id']}"):
            with self.subTest(text=text), self.assertRaisesRegex(PermissionError, '大型赛事'):
                self.send(text)
        reply = self.send('对战表')
        self.assertIn('每日双打', reply)
        self.assertNotIn('大型联赛', reply)
        self.assertIn('每日双打', self.send('每日双打，第一场21:19'))
        self.assertIn('暂停', self.send('每日双打，暂停比赛'))
        self.assertIn('恢复', self.send('每日双打，恢复比赛'))
        self.assertEqual(Tournaments(self.platform.workspace_context(),self.clock).get(large['id'])['status'], 'running')
        self.assertEqual(Tournaments(self.ctx,self.clock).get(daily['id'])['matches'][0]['status'], 'completed')

    def test_large_event_keyword_and_numeric_enrollment_are_gated_by_kind(self):
        event = self.make_event('large', '周末友谊杯', running=False)
        with patch('app.competitions._now', self.clock):
            Tournaments(self.ctx,self.clock).transition(event['id'], 'open_registration')
        self.capabilities(['enrollment'])
        for text in ('周末友谊杯报名', f"赛事{event['id']} 报名", '周末友谊杯，报名，张三'):
            with self.subTest(text=text), self.assertRaisesRegex(PermissionError, '大型赛事'):
                self.send(text)
        self.capabilities(['large_events'])
        self.assertIn('已更新', self.send('周末友谊杯报名'))
        with self.assertRaisesRegex(PermissionError, '活动报名'):
            self.send('报名')

    def test_hidden_large_event_with_same_title_does_not_block_daily_lookup(self):
        daily = self.make_event('daily','同名赛事')
        self.make_event('large','同名赛事')
        self.capabilities(['daily_events'])
        router = Commands(self.member,self.clock)
        router.refresh_context()
        self.assertEqual(router.event_by_name('同名赛事',1)['id'],daily['id'])

    def test_daily_planning_uses_shared_roster_even_without_enrollment_capability(self):
        for user in ('1001','1002','1003','1004'):
            self.s.enroll(user, '球友'+user)
        self.capabilities(['daily_events'])
        self.assertIn('积分赛', self.send('组织比赛'))
        self.assertIn('对战', self.send('积分赛'))

    def test_context_and_single_ai_snapshots_refresh_after_revocation(self):
        self.make_event('daily', '每日双打')
        self.make_event('large', '大型联赛')
        self.capabilities(['ai','enrollment','daily_events'])
        router = Commands(self.member,self.clock)
        context = router.ai_context(1)
        self.assertNotIn('large_events', context['bot_capabilities'])
        self.assertEqual([e['title'] for e in context['events']], ['每日双打'])
        _, before = router.snapshot('报名')
        self.capabilities(['ai','enrollment','registration','daily_events'])
        router.refresh_context()
        _, after = router.snapshot('报名')
        self.assertNotEqual(before['bot_capabilities'], after['bot_capabilities'])
        self.capabilities([])
        for action in (lambda: asyncio.run(router.propose('帮我报名',1)),
                       lambda: asyncio.run(router.confirm('abcdef012345','小李')),
                       lambda: router.cancel_proposal('abcdef012345')):
            with self.assertRaisesRegex(PermissionError, 'AI'):
                action()

    def test_previously_issued_registration_links_stop_after_revocation(self):
        self.s.update_config({'public_base_url':'https://club.example'})
        stranger = replace(self.member,actor='new-registration')
        url = create_form(stranger,self.clock)
        token = url.split('#',1)[1].split('.',1)[1]
        self.capabilities(['enrollment'])
        for action in (lambda: create_form(stranger,self.clock),
                       lambda: form_status(stranger,token,self.clock),
                       lambda: submit_form(stranger,token,{'name':'新会员','gender':'male'},self.clock)):
            with self.assertRaisesRegex(PermissionError, '会员注册'):
                action()
        self.assertIsNone(Members(stranger,self.clock).get())

    def test_registration_only_robot_does_not_advertise_enrollment_or_ai(self):
        self.capabilities(['registration'])
        registered = self.send('注册')
        self.assertIn('帮助', registered)
        self.assertNotIn('发送 名单', registered)
        self.assertNotIn('AI', self.send('无法识别的业务'))
        self.s.update_config({'public_base_url':'https://club.example'})
        stranger = replace(self.member, actor='register-only')
        token = create_form(stranger,self.clock).split('#',1)[1].split('.',1)[1]
        result = submit_form(stranger,token,{'name':'新会员','gender':'female'},self.clock)
        self.assertIn('帮助',result['message'])
        self.assertNotIn('即可报名',result['message'])

    def test_stats_does_not_run_disabled_casual_business(self):
        self.capabilities(['statistics'])
        with patch('app.casual.CasualMatches.statistics') as casual_stats:
            text = self.send('我的统计')
        casual_stats.assert_not_called()
        self.assertIn('报名记录',text)
        self.assertNotIn('对战汇总',text)


def test_help_shows_only_enabled_businesses_and_actual_roles():
    member = {'status':'approved'}
    daily = command_help(member, capabilities=['daily_events'])
    for absent in ('帮助 注册','帮助 报名','帮助 AI','帮助 管理','帮助 大型赛事','我的统计','我的对战历史'):
        assert absent not in daily
    assert '组织比赛' in daily
    assert '本机器人未开启' in command_help(member,category='注册',capabilities=[])
    assert '发送 **注册**' not in command_help(None,capabilities=[])
    reviewer = command_help(member,category='管理',permissions=['member.manage'],capabilities=['management'])
    assert '审核通过' in reviewer
    assert '开启AI' not in reviewer
    assert '暂停比赛' not in reviewer
    plain = command_help(member,category='管理')
    assert '审核通过' not in plain
    ai = command_help(member,category='AI',capabilities=['ai'])
    for absent in ('帮我占个位','取消自己代报','我要注册','管理员配置','会员审核','对战历史','开启AI'):
        assert absent not in ai

