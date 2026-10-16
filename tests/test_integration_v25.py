"""Transport/UI contracts for scoped gender-aware records; isolated synthetic state."""
import asyncio
from dataclasses import replace
import re
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import ai
from app.db import connect
from app.main import create_app
from app.members import Members
from app.planning import DailyPlanning
from tests.test_core import CoreFixture, TOKEN
from tests.test_ai import FakeRunnable


class IntegrationV25Tests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.client=TestClient(create_app(self.temp.name,TOKEN,False,False,self.clock))
        self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer '+TOKEN,'X-Bot-ID':self.ctx.id}
        for user,gender in [('1001','male'),('1002','female'),('1003','male'),('1004','female')]:
            Members(self.ctx,self.clock).update_details(user,gender=gender)

    def test_gender_command_and_inline_registration(self):
        actor=replace(self.member,actor='new-gender')
        with self.assertRaises(ValueError):asyncio.run(self.commands(actor).run('注册 新会员'))
        reply=asyncio.run(self.commands(actor).run('注册 新会员 女 九级 13800000001'))
        self.assertIn('已提交',reply)
        profile=Members(actor,self.clock).get()
        self.assertEqual((profile['gender'],profile['requested_level'],profile['phone']),('female',9,'13800000001'))
        self.assertIn('女',asyncio.run(self.commands().run('性别 女')))
        self.assertEqual(Members(self.member,self.clock).get()['gender'],'female')

    def test_ai_mixed_plan_requires_confirmation_and_retains_category(self):
        for user in ('1001','1002','1003','1004'):self.s.enroll(user,'ignored')
        self.s.update_config({'ai':{'enabled':True,'api_key':'local-fake-key'}})
        with patch.object(ai,'_make_runnable',return_value=FakeRunnable('StartDailyPlan',{'discipline':'doubles','doubles_category':'mixed'})):
            reply=asyncio.run(self.commands().run('AI 我想组织今天的混双比赛'))
        self.assertIsNone(DailyPlanning(self.member,self.clock).get(1))
        token=self.pending_match(reply)
        self.assertIsNotNone(token,reply)
        confirmed=asyncio.run(self.commands().run('AI确认 '+token[1]))
        self.assertIn('混双',confirmed)
        self.assertEqual(DailyPlanning(self.member,self.clock).get(1)['payload']['doubles_category'],'mixed')
        context=ai._trusted_context(self.commands().ai_context(1))
        self.assertEqual(context['planning']['doubles_category'],'mixed')

    def test_admin_casual_http_correct_void_and_member_query(self):
        reply=asyncio.run(self.commands().run('记录 单打 我 VS 球友1002 21:19'))
        self.assertIn('21',reply)
        response=self.client.get('/api/casual/history',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        record=response.json()['matches'][0]
        self.assertEqual(record['score_b'],19)
        match_id=record['source_id']
        response=self.client.patch(f'/api/casual/matches/{match_id}',headers=self.headers,json={'score_a':18,'score_b':21,'expected_version':record['version']})
        self.assertEqual(response.status_code,200,response.text)
        stats=self.client.get('/api/casual/statistics?user_id=1001',headers=self.headers).json()
        self.assertEqual((stats['played'],stats['losses']),(1,1))
        self.assertIn('对战汇总',asyncio.run(self.commands().run('我的统计')))
        response=self.client.post(f'/api/casual/matches/{match_id}/void',headers=self.headers,json={'expected_version':record['version']+1})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/casual/statistics?user_id=1001',headers=self.headers).json()['played'],0)
        self.assertEqual(self.client.get('/api/casual/history?include_void=true',headers=self.headers).json()['matches'][0]['status'],'void')
        self.assertEqual(self.client.get('/api/casual/history').status_code,401)

    def test_formats_http_uses_real_gender_and_eligibility(self):
        for user in ('1001','1002','1003','1004'):self.s.enroll(user,'ignored')
        session=self.s.sessions()[0]
        base=f'/api/formats?date={session["date"]}&session_id={session["id"]}&doubles_category=mixed'
        result=self.client.get(base,headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        selected=next(row for row in result.json() if row['id']=='round_robin')
        self.assertTrue(selected['available'])
        result=self.client.get(base+'&min_level=9',headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(any(row['available'] for row in result.json()))
        response=self.client.get(base+'&allowed_group_ids=bad',headers=self.headers)
        self.assertEqual(response.status_code,400,response.text)
