import copy
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import create_app
from app.bot_broadcasts import settings
from app.time_rules import rule_fingerprint, broadcast_delivery_allowed
from tests.test_core import CoreFixture, TOKEN


class BotBroadcastSettingsTests(CoreFixture):
    def read(self, index=0):
        return settings(self.platform, self.admin, self.bots[index]['id'])

    def save(self, value, index=0):
        return settings(self.platform, self.admin, self.bots[index]['id'],
                        {'rules': value['rules'], 'revision': value['revision']})

    def test_shared_edit_preserves_other_robot_and_queued_fingerprint(self):
        rule={'id':'shared','name':'活动提醒','enabled':True,'kinds':['open'],
              'time':'09:30','when':{},'bot_ids':[b['id'] for b in self.bots]}
        self.s.update_config({'broadcast_rules':[rule]})
        original=self.s.config()['broadcast_rules'][0]
        other=self.read(1);own=self.read()
        own['rules'][0]['time']='10:30'
        self.save(own)
        assert self.read(1)==other
        current=self.s.config()['broadcast_rules']
        kept=next(r for r in current if r['id']=='shared')
        assert rule_fingerprint(kept)==rule_fingerprint(original)
        otherctx=self.platform.context_for_channel(self.bots[1]['channels'][0]['id']) if 'channels' in self.bots[1] else self.platform.context(self.admin,self.bots[1]['id'])
        from dataclasses import replace
        otherctx=replace(otherctx,daily_notifications=True)
        key=f"broadcast:shared:{rule_fingerprint(original)}:{self.now.date()}:0930:1:open:unused"
        assert broadcast_delivery_allowed(key,otherctx,self.now,self.s.config())
        assert self.read()['rules'][0]['time']=='10:30'

    def test_unchanged_shared_rule_keeps_identity(self):
        before=self.read();self.save(before)
        assert self.read()==before
        raw=self.s.config()['broadcast_rules']
        assert len(raw)==3 and all(len(r['bot_ids'])==2 for r in raw)

    def test_delete_only_removes_target_membership_and_noop_does_not_restore_it(self):
        before=self.read(1);own=self.read();own['rules']=[];self.save(own)
        assert self.read()['rules']==[] and self.read(1)==before
        self.save(before,1)
        assert self.read()['rules']==[]

    def test_revision_rejects_stale_own_draft_but_allows_other_robot_update(self):
        a=self.read();b=self.read(1);stale=copy.deepcopy(a)
        a['rules'][0]['time']='10:31';self.save(a)
        b['rules'][0]['time']='10:32';self.save(b,1)
        assert next(r for r in self.read()['rules'] if r['kinds']==['open'])['time']=='10:31'
        with self.assertRaises(HTTPException) as err:self.save(stale)
        assert err.exception.status_code==409

    def test_parallel_saves_to_different_bots_do_not_lose_updates(self):
        a=self.read();b=self.read(1)
        a['rules'][0]['time']='10:31';b['rules'][0]['time']='10:32'
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda x:self.save(x[0],x[1]),[(a,0),(b,1)]))
        assert next(r for r in self.read()['rules'] if r['kinds']==['open'])['time']=='10:31'
        assert next(r for r in self.read(1)['rules'] if r['kinds']==['open'])['time']=='10:32'

    def test_reject_cross_robot_invalid_time_and_duplicate_ids_without_partial_write(self):
        initial=self.s.config()
        for mutate in (lambda r:r.update(bot_ids=[self.bots[1]['id']]),lambda r:r.update(time='29:00')):
            own=self.read();mutate(own['rules'][0])
            with self.assertRaises(ValueError):self.save(own)
            assert self.s.config()==initial
        own=self.read();own['rules'].append(own['rules'][0])
        with self.assertRaises(ValueError):self.save(own)
        assert self.s.config()==initial

    def test_api_auth_scope_and_conflict(self):
        app=create_app(self.platform.root,TOKEN,start_scheduler=False,live=False,clock=self.clock)
        path=f"/api/bots/{self.bots[0]['id']}/broadcasts"
        with TestClient(app) as client:
            assert client.get(path).status_code in (401,403)
            client.headers['Authorization']='Bearer '+TOKEN
            value=client.get(path).json()
            assert client.put(path,json={'rules':value['rules'],'revision':value['revision']}).status_code==200
            assert client.put(path,json={'rules':[],'revision':'stale'}).status_code==409
            assert client.put(path,json={'rules':[],'revision':value['revision'],'daily_enabled':False}).status_code==400

    def test_global_pause_and_other_config_are_preserved(self):
        self.s.update_config({'daily_enabled':False})
        before=self.s.config();own=self.read();own['rules'][0]['time']='11:00';self.save(own)
        after=self.s.config()
        assert {k:v for k,v in before.items() if k!='broadcast_rules'}=={k:v for k,v in after.items() if k!='broadcast_rules'}
