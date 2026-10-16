"""Connection proof, safe group removal and retry boundaries; synthetic traffic only."""
import asyncio
from datetime import timedelta
import json
import uuid
import unittest
from unittest.mock import patch

import httpx

from app.bot_connection import send_test, summary, record_callback
from app.bot_groups import BotGroups
from app.db import connect, now
from app.security import Platform, Principal
from tests import test_bot_onboarding_v215 as onboarding


class ConnectionFlowTests(unittest.TestCase):
    setUp=onboarding.OnboardingTests.setUp
    pending=onboarding.OnboardingTests.pending
    body=onboarding.OnboardingTests.body
    callback=onboarding.OnboardingTests.callback
    def send(self,bot,response=None,request_id=None,handler=None,**kwargs):
        self.requests=[]
        def respond(request):
            self.requests.append(request)
            return handler(request) if handler else (response or httpx.Response(200,json={'result':'ok'}))
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                return await send_test(self.p,self.admin,bot['id'],bot['channels'][0]['id'],request_id or uuid.uuid4().hex,
                                       client=client,live=True,**kwargs)
        return asyncio.run(run())

    def test_flexible_addresses_and_connection_proof_never_trusts_response_chatid(self):
        bot=self.pending();cid=bot['channels'][0]['id'];path=f"/api/bots/{bot['id']}/connection?channel_id={cid}"
        initial=self.client.get(path,headers=self.headers).json()
        self.assertTrue(initial['callback_url'].endswith(bot['callback_path']));self.assertEqual(initial['connection_status'],'needs_test')
        self.assertEqual(self.send(bot,httpx.Response(200,json={'result':'ok','chatid':'untrusted'}))['status'],'sent')
        payload=json.loads(self.requests[0].content)
        self.assertEqual(payload['msgtype'],'text');self.assertIn('消息发送连接成功',payload['text']['content'])
        ready=self.client.get(path,headers=self.headers).json()
        self.assertTrue(ready['callback_url'].endswith(bot['callback_path']))
        self.assertEqual(ready['connection_status'],'waiting_callback')
        self.assertEqual(self.p.channel(cid)['chat_id'],'')
        self.assertEqual(self.client.get(bot['callback_path']).json(),{'result':'ok'})
        self.assertEqual(self.client.get(path,headers=self.headers).json()['connection_status'],'waiting_message')
        self.assertEqual(self.callback(bot,self.body(content='连接')).status_code,200)
        connected=self.client.get(path,headers=self.headers).json();self.assertEqual(connected['connection_status'],'connected')
        self.assertEqual(self.p.channel(cid)['chat_id'],'266382')
        ctx=self.p.context_for_channel(cid)
        with connect(ctx.transport_db_path) as db:
            self.assertIn('机器人连接成功',db.execute('SELECT reply FROM inbox').fetchone()[0])

    def test_failed_unknown_and_simulated_do_not_claim_connection(self):
        for response in (httpx.Response(200,json={'result':'error','message':'private secret'}),httpx.Response(403,json={}),
                         httpx.Response(503,json={}),httpx.Response(200,text='bad response')):
            with self.subTest(code=response.status_code):
                bot=self.pending(uuid.uuid4().hex)
                result=self.send(bot,response)
                self.assertIn(result['status'],('unknown','failed'));self.assertNotIn('private secret',str(result))
                self.assertFalse(summary(self.p,self.p.channel(bot['channels'][0]['id']))['send_verified'])
        bot=self.pending('simulation');result=asyncio.run(send_test(self.p,self.admin,bot['id'],bot['channels'][0]['id'],uuid.uuid4().hex))
        self.assertEqual(result['status'],'simulated')
        self.assertFalse(summary(self.p,self.p.channel(bot['channels'][0]['id']))['send_verified'])

    def test_idempotency_cooldown_and_admin_scope(self):
        bot=self.pending();rid=uuid.uuid4().hex
        self.assertEqual(self.send(bot,request_id=rid)['status'],'sent')
        self.assertEqual(self.send(bot,request_id=rid)['status'],'sent');self.assertEqual(len(self.requests),0)
        with self.assertRaisesRegex(ValueError,'20秒'):self.send(bot)
        other=self.pending('second')
        with self.assertRaises(ValueError):self.send(other,request_id=rid)
        with self.assertRaises(PermissionError):asyncio.run(send_test(self.p,Principal('limited','bot_admin',(bot['id'],)),bot['id'],bot['channels'][0]['id'],uuid.uuid4().hex))
        route=f"/api/bots/{other['id']}/connection/test"
        data={'channel_id':bot['channels'][0]['id'],'request_id':uuid.uuid4().hex}
        self.assertEqual(self.client.post(route,json=data).status_code,401)
        self.assertEqual(self.client.post(route,headers=self.headers,json=data).status_code,403)

    def test_each_robot_needs_own_send_and_callback_verification(self):
        one=self.pending();two=self.pending('second')
        self.send(one);self.callback(one);self.send(two)
        self.assertEqual(summary(self.p,self.p.channel(one['channels'][0]['id']))['connection_status'],'connected')
        self.assertFalse(summary(self.p,self.p.channel(two['channels'][0]['id']))['receive_verified'])
        self.assertEqual(self.callback(two,self.body('second',chat='wrong')).status_code,403)
        self.assertFalse(summary(self.p,self.p.channel(two['channels'][0]['id']))['receive_verified'])
        self.callback(two,self.body('second'))
        self.assertEqual(summary(self.p,self.p.channel(two['channels'][0]['id']))['connection_status'],'connected')

    def test_normal_reply_completes_connection_without_test_button(self):
        from app.messages import Outbox
        bot=self.pending();cid=bot['channels'][0]['id']
        self.callback(bot,self.body(content='<at user_id="robot">机器人</at> 连接'))
        self.assertFalse(summary(self.p,self.p.channel(cid))['send_verified'])
        async def deliver():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'result':'ok'}))) as client:
                return await Outbox(self.p.context_for_channel(cid)).deliver_one(self.p,live=True,client=client)
        self.assertEqual(asyncio.run(deliver()),'sent')
        self.assertEqual(summary(self.p,self.p.channel(cid))['connection_status'],'connected')

    def test_changed_credentials_or_callback_revoke_previous_verification(self):
        bot=self.pending();cid=bot['channels'][0]['id'];self.send(bot);self.callback(bot)
        self.p.save_channel(self.admin,bot['id'],{'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=changed'},cid)
        state=summary(self.p,self.p.channel(cid));self.assertFalse(state['send_verified']);self.assertFalse(state['receive_verified'])
        record_callback(self.p,bot['id'],bot['callback_path'].split('/')[-1],cid,self.body())
        self.assertFalse(summary(self.p,self.p.channel(cid))['receive_verified'])
        self.send(bot,clock=lambda:now()+timedelta(minutes=1));self.callback(bot,self.body('changed'))
        self.assertEqual(summary(self.p,self.p.channel(cid))['connection_status'],'connected')
        self.p.update(self.admin,bot['id'],{'callback_token':'new-callback-credential-for-test'})
        self.assertEqual(summary(self.p,self.p.channel(cid))['connection_status'],'needs_test')

    def test_change_during_send_does_not_verify_the_new_destination(self):
        bot=self.pending();cid=bot['channels'][0]['id']
        def change(request):
            self.p.save_channel(self.admin,bot['id'],{'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=changed'},cid)
            return httpx.Response(200,json={'result':'ok'})
        self.assertEqual(self.send(bot,handler=change)['status'],'changed')
        self.assertFalse(summary(self.p,self.p.channel(cid))['send_verified'])

    def test_delete_group_detaches_all_robots_without_erasing_history_and_can_readd(self):
        one=self.pending();two=self.pending('second');self.callback(one);self.callback(two,self.body('second'))
        untouched=self.pending('other',group=self.groups.save(self.admin,{'name':'其他群'}))
        before=self.p.channel(untouched['channels'][0]['id'])
        result=self.client.delete('/api/groups/'+self.group['id'],headers=self.headers)
        self.assertEqual(result.status_code,200);self.assertEqual(result.json()['disconnected'],2)
        for bot in (one,two):
            self.assertEqual(self.p.list_channels(self.admin,bot['id']),[])
            old=self.p.channel(bot['channels'][0]['id']);self.assertTrue(old['archived']);self.assertEqual(old['chat_id'],'266382')
            with connect(self.p.root_for(bot['id'])/'transport.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],1)
            self.assertEqual(self.callback(bot,self.body(bot['name'])).status_code,403)
        self.assertEqual(self.p.channel(untouched['channels'][0]['id']),before)
        self.assertEqual(self.client.delete('/api/groups/'+self.group['id'],headers=self.headers).json()['disconnected'],0)
        resumed=Platform(self.p.root,self.token)
        self.assertEqual(len(BotGroups(resumed).list(self.admin)),1)
        newgroup=self.groups.save(self.admin,{'name':'重新接入'})
        self.p.save_channel(self.admin,one['id'],{'group_id':newgroup['id'],'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=onboarding-one'})
        self.assertEqual(self.callback(one).status_code,200)
        self.assertEqual(next(g for g in self.groups.list(self.admin) if g['id']==newgroup['id'])['chat_id'],'266382')

    def test_delete_empty_historical_group_and_reject_stale_merged_target(self):
        empty=self.groups.save(self.admin,{'name':'空群'})
        self.assertEqual(self.groups.remove(self.admin,empty['id'])['disconnected'],0)
        bot=self.pending();self.callback(bot);self.p.archive_channel(self.admin,bot['id'],bot['channels'][0]['id'])
        self.assertEqual(self.groups.remove(self.admin,self.group['id'])['disconnected'],0)
        fresh=self.groups.save(self.admin,{'name':'等待识别'})
        current=self.p.create(self.admin,{'name':'原群','chat_id':'266382','robot_key':'existing'})
        other=self.pending('merge',group=fresh);self.callback(other,self.body('merge'))
        with self.assertRaisesRegex(ValueError,'合并'):self.groups.remove(self.admin,fresh['id'])
        self.assertEqual(len(self.p.list_channels(self.admin,current['id'])),1)
        with self.assertRaises(PermissionError):self.groups.remove(Principal('x','bot_admin',()),current['channels'][0]['group_id'])
