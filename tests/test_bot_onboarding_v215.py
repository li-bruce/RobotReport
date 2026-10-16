"""Name-only groups, per-robot callback discovery and optional daily events."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.bot_capabilities import DEFAULT_CAPABILITIES, ALL_CAPABILITIES
from app.bot_groups import BotGroups
from app.bot_policy import model_operation_enabled, require_operation
from app.commands import Commands
from app.db import connect
from app.main import create_app
from app.members import Members
from app.messages import Outbox
from app.notifications import Notifications
from app.security import Platform, Principal
from app.tournaments import Tournaments
from tests.test_core import CoreFixture


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token = 'local-onboarding-test-token-32-characters'
        self.app = create_app(Path(self.tmp.name), self.token, live=False, start_scheduler=False)
        self.p = self.app.state.platform
        self.admin = self.p.authenticate(self.token)
        self.groups = BotGroups(self.p)
        self.group = self.groups.save(self.admin, {'name': '周三羽球群'})
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer ' + self.token}

    def pending(self, key='onboarding-one', group=None, **extra):
        return self.p.create(self.admin, {'name': key, 'group_id': (group or self.group)['id'],
            'webhook_url': 'https://xz.wps.cn/api/v1/webhook/send?key=' + key, **extra})

    def body(self, key='onboarding-one', chat='266382', **changes):
        return {'chatid': chat, 'creator': 'local-member', 'robot_key': key,
                'content': '帮助', 'ctime': '1792020000000', **changes}

    def callback(self, bot, body=None):
        return self.client.post(bot['callback_path'], json=body or self.body())

    def test_group_name_only_creation_rename_and_admin_boundary(self):
        self.assertEqual(self.group['chat_id'], '')
        denied = Principal('member', 'bot_admin', ())
        with self.assertRaises(PermissionError): self.groups.list(denied)
        for values in ({'name':'群','chat_id':'266382'}, {'name':''}, {'name':'bad\nname'}):
            with self.subTest(values=values), self.assertRaises(ValueError): self.groups.save(self.admin, values)
        response=self.client.post('/api/groups',headers=self.headers,json={'name':'学生群'})
        self.assertEqual(response.status_code,200,response.text)
        group=response.json()
        self.assertEqual(group['chat_id'],'')
        self.assertEqual(self.client.patch('/api/groups/'+group['id'],headers=self.headers,json={'name':'学生晚场'}).json()['name'],'学生晚场')
        self.assertEqual(self.client.get('/api/groups').status_code,401)

    def test_new_defaults_and_explicit_empty_survive_restart(self):
        new=self.pending()
        self.assertEqual(tuple(new['capabilities']),DEFAULT_CAPABILITIES)
        self.p.update(self.admin,new['id'],{'capabilities':[]})
        legacy=self.pending('legacy',capabilities=list(ALL_CAPABILITIES))
        resumed=Platform(Path(self.tmp.name),self.token)
        visible={r['id']:r for r in resumed.list(self.admin)}
        self.assertEqual(visible[new['id']]['capabilities'],[])
        self.assertEqual(tuple(visible[legacy['id']]['capabilities']),ALL_CAPABILITIES)

    def test_pending_excluded_from_scheduling_and_get_validation_does_not_bind(self):
        bot=self.pending(); channel=bot['channels'][0]
        self.assertTrue(channel['awaiting_callback'])
        self.assertFalse(channel['configured'])
        self.assertEqual(self.p.list_channels(active_only=True),[])
        self.assertEqual(self.p.system_contexts(),[])
        with self.assertRaises(PermissionError): self.p.context_for_channel(channel['id'])
        self.assertEqual(self.client.get(bot['callback_path']).json(),{'result':'ok'})
        self.assertEqual(self.groups.list(self.admin)[0]['chat_id'],'')

    def test_bad_or_malformed_callback_never_binds(self):
        bot=self.pending()
        invalid=[self.body(robot_key='wrong'),self.body(creator=''),self.body(content=''),
                 self.body(ctime='not-time'),self.body(chatid=''),self.body(content=None)]
        for body in invalid:
            with self.subTest(fields=body):
                self.assertIn(self.callback(bot,body).status_code,(400,403))
                self.assertEqual(self.p.channel(bot['channels'][0]['id'])['chat_id'],'')
                self.assertEqual(self.groups.list(self.admin)[0]['chat_id'],'')
        self.assertEqual(self.client.post(bot['callback_path']+'wrong',json=self.body()).status_code,403)

    def test_valid_first_post_binds_and_routes_reply_once(self):
        bot=self.pending(); cid=bot['channels'][0]['id']
        self.assertEqual(self.p.channel(cid)['robot_key'],'onboarding-one')
        for _ in range(2): self.assertEqual(self.callback(bot).json(),{'result':'ok'})
        channel=self.p.channel(cid)
        self.assertEqual(channel['chat_id'],'266382')
        self.assertEqual(self.groups.list(self.admin)[0]['chat_id'],'266382')
        ctx=self.p.context_for_channel(cid)
        with connect(ctx.transport_db_path) as db:
            rows=list(db.execute('SELECT channel_id FROM outbox'))
            self.assertEqual([r['channel_id'] for r in rows],[cid])
        self.assertEqual(len(self.p.system_contexts()),1)

    def test_shared_group_requires_each_robots_own_authenticated_callback(self):
        one=self.pending(); two=self.pending('onboarding-two')
        self.assertEqual(self.callback(one).status_code,200)
        self.assertEqual(self.p.channel(two['channels'][0]['id'])['chat_id'],'')
        self.assertEqual(self.callback(two).status_code,403)
        self.assertEqual(self.callback(two,self.body('onboarding-two',chat='other-group')).status_code,403)
        self.assertEqual(self.callback(two,self.body('onboarding-two')).status_code,200)
        self.assertEqual({c['chat_id'] for c in self.p.list_channels(active_only=True)},{'266382'})
        self.assertEqual(len(self.groups.list(self.admin)),1)
        for bot in (one,two):
            self.assertEqual(len(Outbox(self.p.context_for_channel(bot['channels'][0]['id'])).list()),1)

    def test_concurrent_same_group_discovery_serializes(self):
        bots=[self.pending('parallel-'+str(i)) for i in range(2)]
        def identify(bot):
            return self.p.callback_context(bot['id'],bot['callback_path'].split('/')[-1],self.body(bot['name'])).chat_id
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(identify,bots))
        self.assertEqual(results,['266382','266382'])
        self.assertEqual(len(self.groups.list(self.admin)),1)

    def test_concurrent_conflicting_groups_only_one_wins(self):
        bots=[self.pending('race-'+str(i)) for i in range(2)]
        def identify(pair):
            i,bot=pair
            try: return self.p.callback_context(bot['id'],bot['callback_path'].split('/')[-1],self.body(bot['name'],chat='group-'+str(i))).chat_id
            except PermissionError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(identify,enumerate(bots)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(len(self.p.list_channels(active_only=True)),1)

    def test_placeholder_merges_into_known_group_without_activating_other_robot(self):
        existing=self.p.create(self.admin,{'name':'已连接','chat_id':'266382','robot_key':'existing-key'})
        before=self.p.channel(existing['channels'][0]['id'])
        one=self.pending(); two=self.pending('pending-peer')
        self.assertEqual(self.callback(one).status_code,200)
        self.assertEqual(len(self.groups.list(self.admin)),1)
        self.assertEqual(self.p.channel(existing['channels'][0]['id']),before)
        self.assertEqual(self.p.channel(two['channels'][0]['id'])['chat_id'],'')
        self.assertEqual(self.callback(two,self.body('pending-peer')).status_code,200)
        self.assertEqual({r['group_id'] for r in self.p.list_channels()},{before['group_id']})

    def test_rebind_keeps_old_queue_and_requires_new_verification(self):
        bot=self.pending(); self.assertEqual(self.callback(bot).status_code,200)
        cid=bot['channels'][0]['id']; original=self.p.channel(cid)
        second=self.groups.save(self.admin,{'name':'新群'})
        moved=self.p.save_channel(self.admin,bot['id'],{'group_id':second['id'],
            'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=next-key'},cid)
        self.assertNotEqual(moved['id'],cid)
        self.assertTrue(self.p.channel(cid)['archived'])
        self.assertEqual(self.p.channel(cid)['chat_id'],original['chat_id'])
        self.assertTrue(moved['awaiting_callback'])
        self.assertEqual(self.callback(bot).status_code,403)
        self.assertEqual(self.callback(bot,self.body('next-key',chat='new-group')).status_code,200)
        ctx=self.p.context_for_channel(moved['id'])
        with connect(ctx.transport_db_path) as db:
            self.assertEqual({r[0] for r in db.execute('SELECT channel_id FROM outbox')},{cid,moved['id']})

    def test_pending_connection_can_move_before_first_callback(self):
        bot=self.pending(); second=self.groups.save(self.admin,{'name':'另一个群'})
        moved=self.p.save_channel(self.admin,bot['id'],{'group_id':second['id'],
            'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=next-key'},bot['channels'][0]['id'])
        self.assertTrue(moved['awaiting_callback'])
        self.assertEqual(moved['group_id'],second['id'])

    def test_connection_editor_is_private_and_never_leaks_to_lists(self):
        bot=self.pending(); other=self.pending('other')
        path=f"/api/bots/{bot['id']}/connection?channel_id={bot['channels'][0]['id']}"
        self.assertEqual(self.client.get(path).status_code,401)
        response=self.client.get(path,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertTrue(response.json()['callback_url'].endswith(bot['callback_path']))
        self.assertTrue(response.json()['webhook_url'].endswith('key=onboarding-one'))
        self.assertEqual(self.client.get(f"/api/bots/{bot['id']}/connection?channel_id={other['channels'][0]['id']}",headers=self.headers).status_code,403)
        for path in ('/api/bots','/api/channels','/api/groups'):
            response=self.client.get(path,headers=self.headers)
            self.assertNotIn('key=onboarding-one',response.text)
            self.assertNotIn(bot['callback_path'],response.text)


class OptionalDailyTests(CoreFixture):
    def disable_daily(self):
        self.platform.update(self.admin,self.ctx.id,{'capabilities':[key for key in ALL_CAPABILITIES if key!='daily_events']})

    def test_disabled_daily_commands_do_not_register_strangers(self):
        self.disable_daily(); stranger=replace(self.member,actor='never-registered')
        for text in ('组织比赛','组织比赛 混双','积分赛','单打','五人转','分配 高带低','round_robin'):
            with self.subTest(text=text),self.assertRaisesRegex(PermissionError,'每日赛事'):
                asyncio.run(Commands(stranger,self.clock).run(text))
        self.assertIsNone(Members(stranger,self.clock).get())

    def test_disabled_daily_blocks_existing_event_query_score_and_notifications(self):
        for user in ('1001','1002','1003','1004'):self.s.enroll(user,'球友'+user)
        with patch('app.competitions._now',self.clock):
            event=Tournaments(self.member,self.clock).create({'kind':'daily','format':'round_robin','title':'今日双打'})
        self.disable_daily()
        for text in ('今日双打，对战表','今日双打，第一场21:19','今日双打，暂停比赛'):
            with self.subTest(text=text),self.assertRaises((PermissionError,ValueError)):
                asyncio.run(Commands(self.member,self.clock).run(text))
        self.assertEqual(Tournaments(self.member,self.clock).list(),[])
        with self.assertRaisesRegex(PermissionError,'每日赛事'):Tournaments(self.member,self.clock).get(event['id'])
        result=Notifications(self.platform,self.clock).enqueue('每日对战表',[self.ctx.channel_id],tournament_id=event['id'],venue_id=1)
        self.assertEqual(result['deliveries'][0]['status'],'failed')
        self.assertIn('今日双打',Tournaments(self.platform.workspace_context(),self.clock).get(event['id'])['title'])
        self.platform.update(self.admin,self.ctx.id,{'capabilities':['daily_events']})
        self.assertEqual(len(Tournaments(self.member,self.clock).list()),1)

    def test_daily_hidden_from_ai_catalog_help_and_execution(self):
        self.disable_daily()
        from app.scope import refresh_context
        ctx=refresh_context(self.member)
        context=Commands(ctx,self.clock).ai_context(1)
        self.assertEqual(context['planning'],None)
        self.assertEqual(context['formats'],[])
        for operation in ('StartDailyPlan','DailyPlan'):
            self.assertFalse(model_operation_enabled({'bot_capabilities':list(ctx.capabilities)},operation))
            with self.assertRaisesRegex(PermissionError,'每日赛事'):require_operation(ctx,operation)
        reply=asyncio.run(Commands(ctx,self.clock).run('帮助'))
        self.assertNotIn('组织比赛',reply)
        reply=asyncio.run(Commands(ctx,self.clock).run('帮助 AI'))
        self.assertNotIn('组织今天第一场',reply)
