from app.help import command_help,scoped_help
from app.bot_capabilities import ALL_CAPABILITIES
from tests.test_core import CoreFixture
from app.service import Service
from app.db import connect


def test_individual_businesses_have_fields_and_examples_without_other_actions():
    expected={'registration':'注册与资料','enrollment':'报名与预约','daily_events':'每日趣味赛',
              'large_events':'大型赛事报名','match_records':'零散对局','statistics':'报名统计','ai':'自然语言'}
    for key,title in expected.items():
        reply=command_help({'status':'approved'},capabilities=[key])
        assert '**'+title+'**' in reply
        assert '字段：' in reply and '示例：' in reply
        for other,label in expected.items():
            if other!=key:assert '**'+label+'**' not in reply
    ai=command_help({'status':'approved'},capabilities=['ai'])
    assert 'AI 查看帮助' in ai and '今天的对战表' not in ai


def test_management_examples_follow_permissions_and_business():
    reply=command_help({'status':'approved'},capabilities=['management'],permissions=['member.manage'])
    assert '审核通过 申请12' in reply and '场地占用' not in reply and '开启AI' not in reply
    reply=command_help({'status':'approved'},category='管理',capabilities=['management'],permissions=['event.manage'])
    assert '暂停比赛' not in reply
    reply=command_help({'status':'approved'},capabilities=['messages'])
    assert '后台“消息发布”' not in reply
    reply=command_help({'status':'approved'},is_admin=True,capabilities=['messages','management'])
    assert '后台“消息发布”' in reply


class ScopedHelpTests(CoreFixture):
    def test_live_scope_and_capability_changes_do_not_advertise_other_slots(self):
        slot=self.s.slots()[1]
        self.platform.update(self.admin,self.ctx.id,{'capabilities':['enrollment'],'venue_ids':[1],'slot_keys':[slot['slot_key']]})
        text=scoped_help(self.member,{'status':'approved'},clock=self.clock)
        self.assertIn('20:30–22:00',text);self.assertNotIn('17:30–20:30',text)
        self.assertIn('字段：',text);self.assertIn('报名，张三，李四',text)
        self.assertNotIn('组织比赛',text);self.assertNotIn('AI ',text)
        self.platform.update(self.admin,self.ctx.id,{'capabilities':['statistics']})
        changed=scoped_help(self.member,{'status':'approved'},clock=self.clock)
        self.assertIn('我的统计',changed);self.assertNotIn('报名与预约',changed)

    def test_help_is_read_only_and_escapes_venue_names(self):
        with connect(self.member.db_path) as db:before=db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        self.s.save_venue({'name':'馆<测试>'},1)
        text=scoped_help(self.member,{'status':'approved'},clock=self.clock)
        self.assertNotIn('馆<测试>',text)
        with connect(self.member.db_path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],before)
