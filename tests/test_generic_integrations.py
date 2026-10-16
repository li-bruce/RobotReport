import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
from fastapi.testclient import TestClient
from app import env_settings
from app.ai import _make_runnable, Help
from app.bot_connection import send_test
from app.db import connect
from app.integrations import callback_path, accepted, outbound_payload
from app.main import create_app
from app.messages import Outbox
from app.security import validate_webhook
from app.service import Service
from tests.test_core import CoreFixture


class GenericIntegrationTests(CoreFixture):
    def setUp(self):
        super().setUp()
        self.url = 'https://adapter.example.org/messages?token=synthetic-test-token'
        self.file = Path(self.temp.name) / '.env.local'
        self.file.write_text(env_settings.render({
            'ROBORT_WEBHOOK_JSON_ENDPOINTS': 'https://adapter.example.org/messages',
            'ROBORT_AI_ENDPOINTS': 'https://models.example.org/v1',
        }), encoding='utf-8')
        env_settings.bind(self.platform.root, env_settings.load(self.file))
        self.platform.save_channel(self.admin, self.ctx.id, {'webhook_url': self.url}, self.ctx.channel_id)

    def test_generic_url_allows_configured_path_only_and_legacy_stays_compatible(self):
        self.assertEqual(validate_webhook(self.url, self.platform.root), self.url)
        self.assertEqual(validate_webhook('https://adapter.example.org/messages', self.platform.root), 'https://adapter.example.org/messages')
        for bad in ('http://adapter.example.org/messages', 'https://adapter.example.org/other',
                    'https://user@adapter.example.org/messages', 'https://adapter.example.org:8443/messages',
                    'https://adapter.example.org/messages#fragment', 'https://untrusted.example.org/messages'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_webhook(bad, self.platform.root)
        payload = {'msgtype': 'markdown', 'markdown': {'text': 'hello'}}
        self.assertEqual(outbound_payload(payload, 'https://xz.wps.cn/api/v1/webhook/send?key=test', self.platform.root), payload)
        self.assertTrue(accepted(httpx.Response(200, json={'result': 'ok'}), 'https://xz.wps.cn/api/v1/webhook/send?key=test', self.platform.root))
        self.assertFalse(accepted(httpx.Response(200, json={'result': 'error'}), 'https://xz.wps.cn/api/v1/webhook/send?key=test', self.platform.root))

    def test_json_callback_auth_scope_dedupe_and_display_name(self):
        app = create_app(self.platform.root, env_file=self.file, start_scheduler=False, clock=self.clock)
        path = callback_path(self.platform, self.ctx.id, 'callback-secret-01234567890123456789')
        self.assertTrue(path.startswith('/webhooks/'))
        body = {'event_id': 'message-123', 'group_id': '266382', 'user_id': '1001', 'text': '我的资料', 'display_name': '平台别名'}
        with TestClient(app) as client:
            self.assertEqual(client.post(path+'wrong', json=body).status_code, 403)
            self.assertEqual(client.post(path, json=body | {'group_id': 'other-group'}).status_code, 403)
            self.assertEqual(client.post(path, json=body | {'is_admin': True}).status_code, 400)
            self.assertEqual(client.post(path, json=body | {'event_id': ''}).status_code, 400)
            for text in ('我的资料', '报名'):
                response = client.post(path, json=body | {'text': text})
                self.assertEqual(response.status_code, 200, response.text)
        with connect(self.ctx.transport_db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0], 1)
            reply = db.execute('SELECT reply FROM inbox').fetchone()[0]
        self.assertIn('小李', reply)
        self.assertNotIn('平台别名', reply)
        self.assertEqual(len(self.s.registrations()['registrations']), 0)

    def test_legacy_bot_cannot_use_generic_callback_without_opt_in(self):
        app = create_app(self.platform.root, env_file=self.file, start_scheduler=False, clock=self.clock)
        with TestClient(app) as client:
            response = client.post(f"/webhooks/{self.bots[1]['id']}/callback-secret-01234567890123456789",
                                   json={'event_id': 'id', 'group_id': '266382', 'user_id': '1001', 'text': '帮助'})
        self.assertEqual(response.status_code, 403)

    def test_json_outbound_accepts_204_and_connection_test_uses_same_contract(self):
        captured = []
        def handler(request):
            import json
            captured.append(json.loads(request.content))
            return httpx.Response(204)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                box = Outbox(self.ctx)
                box.enqueue('测试消息', 'generic-test')
                self.assertEqual(await box.deliver_one(self.platform, live=True, client=client, at=self.now), 'sent')
                result = await send_test(self.platform, self.admin, self.ctx.id, self.ctx.channel_id, 'a'*32,
                                         live=True, client=client, clock=self.clock)
                self.assertEqual(result['status'], 'sent')
        asyncio.run(run())
        self.assertEqual(len(captured), 2)
        for payload in captured:
            self.assertEqual(set(payload), {'type', 'content', 'event_id', 'group_id'})
            self.assertEqual(payload['group_id'], '266382')
            self.assertNotIn('robot_key', payload)

    def test_model_endpoint_is_configurable_but_arbitrary_url_never_allowed(self):
        self.s.update_config({'ai': {'base_url': 'https://models.example.org/v1', 'model': 'custom-tool-model', 'api_key': 'synthetic-api-key'}})
        private = self.s.config(True)['ai']
        self.assertEqual(private['base_url'], 'https://models.example.org/v1')
        self.assertNotIn('_allowed_base_urls', self.s.config()['ai'])
        for url in ('http://127.0.0.1/v1', 'https://unapproved.example.org/v1'):
            with self.assertRaises(ValueError):
                self.s.update_config({'ai': {'base_url': url}})
        with self.assertRaises(ValueError):
            self.s.update_config({'ai': {'_allowed_base_urls': ['https://unapproved.example.org/v1']}})
        with patch('langchain_openai.ChatOpenAI') as model:
            _make_runnable(private, [Help])
            options = model.call_args.kwargs
            self.assertEqual(options['base_url'], private['base_url'])
            self.assertNotIn('enable_thinking', options['extra_body'])
            self.assertFalse(options['use_responses_api'])
            options['http_client'].close()
            asyncio.run(options['http_async_client'].aclose())
        exported = env_settings.exported(self.platform)
        self.assertEqual(exported['ROBORT_AI_ENDPOINTS'], 'https://models.example.org/v1')
        self.assertEqual(exported['ROBORT_WEBHOOK_JSON_ENDPOINTS'], 'https://adapter.example.org/messages')
