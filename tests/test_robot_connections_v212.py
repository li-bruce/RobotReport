import asyncio
from datetime import timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app
from app.messages import Outbox
from app.security import Principal
from app.tournaments import Tournaments
from tests.test_core import CoreFixture, TOKEN


class RobotConnectionTests(CoreFixture):
    def test_paste_one_webhook_derives_key_and_callback_works_in_same_group(self):
        bot=self.bots[1]['id'];channel=self.platform.list_channels(self.admin,bot)[0]['id']
        self.platform.save_channel(self.admin,bot,{'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=second-bot-key'},channel)
        self.assertEqual(self.platform.channel(channel)['robot_key'],'second-bot-key')
        with TestClient(create_app(self.temp.name,TOKEN,False,False,self.clock)) as client:
            path=self.bots[1]['callback_path']
            body={'chatid':266382,'creator':1001,'content':'帮助','ctime':123456,'robot_key':'second-bot-key'}
            self.assertEqual(client.get(path).json(),{'result':'ok'})
            self.assertEqual(client.post(path,json=body).status_code,200)
            self.assertEqual(client.post(self.bots[0]['callback_path'],json=body).status_code,403)
            other=self.platform.context_for_channel(channel)
            self.assertEqual(len(Outbox(other).list()),1)
            self.assertEqual(len(Outbox(self.ctx).list()),0)

    def test_callback_url_cannot_be_saved_as_robot_key(self):
        before=self.platform.channel(self.ctx.channel_id)
        with self.assertRaisesRegex(ValueError,'不能填写网址'):
            self.platform.save_channel(self.admin,self.ctx.id,{'robot_key':'http://example.test/kingsoft/callback/secret'},self.ctx.channel_id)
        self.assertEqual(self.platform.channel(self.ctx.channel_id),before)

    def test_rebind_only_requires_webhook_and_preserves_old_connection(self):
        before=self.platform.channel(self.ctx.channel_id)
        changed=self.platform.save_channel(self.admin,self.ctx.id,{'chat_id':'other-group','webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=other-group-key'},self.ctx.channel_id)
        self.assertEqual(self.platform.channel(changed['id'])['robot_key'],'other-group-key')
        old=self.platform.channel(self.ctx.channel_id)
        self.assertTrue(old['archived']);self.assertEqual(old['robot_key'],before['robot_key'])

    def test_large_event_assignment_and_notification_ignore_daily_slots(self):
        self.platform.update(self.admin,self.ctx.id,{'venue_ids':[1],'slot_keys':['slot:1'],'capabilities':['large_events']})
        self.platform.save_channel(self.admin,self.ctx.id,{'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=events-only'},self.ctx.channel_id)
        self.platform.save_channel(self.admin,self.ctx.id,{'slot_keys':[]},self.ctx.channel_id)
        with patch('app.competitions._now',self.clock):
            event=Tournaments(self.platform.workspace_context(),self.clock).create({'kind':'large','title':'周末公开赛','keyword':'周末公开赛','format':'round_robin','venue_id':1,
                'date':'2026-10-17','registration_open':self.now.isoformat(),'registration_close':(self.now+timedelta(days=2)).isoformat()})
            with TestClient(create_app(self.temp.name,TOKEN,False,False,self.clock)) as client:
                headers={'Authorization':'Bearer '+TOKEN}
                channels=client.get('/api/channels',headers=headers).json()
                channel=next(c for c in channels if c['id']==self.ctx.channel_id)
                self.assertIn('large_events',channel['capabilities'])
                self.assertEqual(channel['bot_venue_ids'],[1])
                org=client.get(f"/api/tournaments/{event['id']}/organization",headers=headers).json()
                response=client.put(f"/api/tournaments/{event['id']}/organization",headers=headers,json={'expected_version':org['version'],'channel_ids':[self.ctx.channel_id]})
                self.assertEqual(response.status_code,200,response.text)
                response=client.post(f"/api/tournaments/{event['id']}/notify",headers=headers,json={'type':'registration','channel_ids':[self.ctx.channel_id]})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(asyncio.run(Outbox(self.ctx).deliver_one(self.platform,at=self.now)),'simulated')
            Tournaments(self.member,self.clock).get(event['id'])

    def test_large_event_still_enforces_venue_scope(self):
        self.platform.update(self.admin,self.ctx.id,{'venue_ids':[],'capabilities':['large_events']})
        with self.assertRaises(PermissionError):self.s.assert_event_scope({'venue_id':1,'kind':'large'})
