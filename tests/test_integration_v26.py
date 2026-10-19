"""Membership HTTP authorization and AI confirmation integration, synthetic data only."""
import asyncio
from datetime import datetime
import re
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import ai
from app.db import TZ, connect
from app.main import create_app
from app.membership_terms import MembershipTerms
from app.ai_catalog import local_route
from tests.test_ai import FakeRunnable
from tests.test_core import CoreFixture, TOKEN


class IntegrationV26Tests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.client = TestClient(create_app(self.temp.name, TOKEN, False, False, self.clock))
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer '+TOKEN, 'X-Bot-ID': self.ctx.id}
        self.base = '/api/members/1001/membership'

    def add(self, start, end):
        response = self.client.post(self.base+'/periods', headers=self.headers,
                                   json={'start_date':start, 'end_date':end, 'amount':'100.50','note':'年费'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_api_period_ledger_auth_overlap_gap_void_and_retained_history(self):
        self.assertEqual(self.client.get(self.base).status_code,401)
        self.assertEqual(self.client.post(self.base+'/periods',json={}).status_code,401)
        old = self.add('2025-10-12','2026-10-11')
        self.add('2026-11-01','2027-10-31')
        response=self.client.get(self.base,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(data['summary']['status'],'gap')
        self.assertFalse(data['summary']['can_enroll'])
        self.assertEqual(len(data['periods']),2)
        self.assertEqual(data['periods'][1]['amount_cents'],10050)
        self.assertIn('created_at',data['summary']['registration'])
        self.assertIn('applications',data)
        overlap=self.client.post(self.base+'/periods',headers=self.headers,json={'start_date':'2026-10-11','end_date':'2026-11-01'})
        self.assertEqual(overlap.status_code,400)
        current=self.add('2026-10-12','2026-10-31')
        self.assertEqual(self.client.get(self.base,headers=self.headers).json()['summary']['status'],'active')
        url=self.base+f"/periods/{current['id']}/void"
        self.assertEqual(self.client.post(url,headers=self.headers,json={'expected_version':999}).status_code,400)
        self.assertEqual(self.client.post(url,headers=self.headers,json={'expected_version':current['version'],'note':'重复登记'}).status_code,200)
        self.assertEqual(self.client.post(url,headers=self.headers,json={'expected_version':current['version']}).status_code,400)
        data=self.client.get(self.base,headers=self.headers).json()
        self.assertEqual(data['summary']['status'],'gap')
        self.assertEqual(len(data['periods']),3)
        self.assertTrue(any(p['id']==old['id'] and p['status']=='valid' for p in data['periods']))

    def test_self_commands_and_ai_membership_queries_work_after_expiry(self):
        self.s.enroll('1001','ignored')
        self.add('2025-10-12','2026-10-11')
        for text in ('我的会籍','续费记录','会员有效期','注册资料','AI 我的会员什么时候到期','AI 查询我的续费记录'):
            response=asyncio.run(self.commands().run(text))
            self.assertIn('已到期',response,text)
            self.assertNotIn('100.50',response)
        for text in ('我的统计','名单','我的对战历史'):
            self.assertTrue(asyncio.run(self.commands().run(text)))
        self.assertIn('取消',asyncio.run(self.commands().run('取消报名')))
        with self.assertRaises(PermissionError):asyncio.run(self.commands().run('报名'))
        self.assertEqual(local_route('为会员登记续费'),'membership.renewal')
        self.assertIn('管理员权限',asyncio.run(self.commands().run('AI 为会员登记续费')))
        with self.assertRaises(PermissionError):MembershipTerms(self.member,self.clock).list('1002')

    def test_expired_since_ai_proposal_cannot_confirm_new_registration(self):
        period=self.add('2026-10-12','2026-12-31')
        self.s.update_config({'ai':{'enabled':True,'api_key':'synthetic-only'}})
        with patch.object(ai,'_make_runnable',return_value=FakeRunnable('Register',{'number':1})):
            reply=asyncio.run(self.commands().run('AI 报名明天第一场'))
        code=self.pending_match(reply)
        self.assertIsNotNone(code,reply)
        MembershipTerms(self.ctx,self.clock).void('1001',period['id'],period['version'],'退款终止')
        with self.assertRaises(PermissionError):asyncio.run(self.commands().run('AI确认 '+code[1]))
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM registrations WHERE user_id='1001' AND status='active'").fetchone()[0],0)

    def test_screenshot_static_command_selects_second_session(self):
        self.now=datetime(2026,10,13,9,tzinfo=TZ)
        reply=asyncio.run(self.commands().run('@一号 2026-10-14 第二场 报名'))
        self.assertIn('20:30',reply)
        with connect(self.ctx.db_path) as db:
            row=db.execute("SELECT s.* FROM sessions s JOIN registrations r ON r.session_id=s.id WHERE r.user_id='1001' AND r.status='active'").fetchone()
            self.assertEqual(row['date'],'2026-10-14')
            self.assertEqual(row['start'],'20:30')
