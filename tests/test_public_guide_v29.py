"""The user manual is public instruction, never an authenticated data endpoint."""
import asyncio
from fastapi.testclient import TestClient
from app.main import create_app
from tests.test_core import CoreFixture


class PublicGuideTests(CoreFixture):
    def test_manual_is_public_and_does_not_expose_member_data(self):
        app=create_app(self.platform.root,start_scheduler=False,live=False,clock=self.clock)
        with TestClient(app) as client:
            response=client.get('/guide')
            self.assertEqual(response.status_code,200)
            self.assertIn('text/html',response.headers['content-type'])
            self.assertEqual(response.headers['referrer-policy'],'no-referrer')
            for anchor in ('start','members','competitions','management'):
                self.assertIn(f'id="{anchor}"',response.text)
            self.assertIn('普通用户版',response.text)
            self.assertEqual(client.get('/guide/member').content,response.content)
            admin=client.get('/guide/admin')
            self.assertEqual(admin.status_code,200)
            self.assertIn('no-store',admin.headers['cache-control'])
            self.assertIn('管理员版',admin.text)
            self.assertIn('id="robots"',admin.text)
            self.assertIn('id="large-events"',admin.text)
            self.assertNotIn('球友1001',admin.text)
            self.assertNotIn('robot-test',admin.text)
            self.assertNotIn('创建赛事草稿',response.text)
            self.assertNotIn('球友1001',response.text)
            self.assertNotIn('robot-test',response.text)
            self.assertEqual(client.get('/api/members').status_code,401)
            for asset in ('guide.css','guide.js'):
                self.assertEqual(client.get('/static/'+asset).status_code,200)

    def test_bot_shares_manual_without_identity_tokens(self):
        self.s.update_config({'public_base_url':'https://club.example'})
        for command in ('帮助','使用说明','操作说明','使用手册'):
            response=asyncio.run(self.commands().run(command))
            self.assertIn('https://club.example/guide',response)
            self.assertNotIn('callback-secret',response)
        self.s.update_config({'public_base_url':''})
        response=asyncio.run(self.commands().run('使用说明'))
        self.assertNotIn('](/guide)',response)
