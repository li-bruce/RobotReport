"""Private environment configuration, restart persistence and state isolation."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import env_settings as env
from app.main import create_app
from app.security import Platform, validate_webhook
from app.service import Service
from app.bot_groups import BotGroups
from scripts.bootstrap import bootstrap


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root/'state'
        self.file = self.root/'.env.local'
        self.token = 'synthetic-environment-administrator-token'
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.write(ROBORT_STATE_DIR=str(self.state), ROBORT_ADMIN_TOKEN=self.token, ROBORT_LIVE_SEND='0')

    def write(self, **values):
        current = env.parse(self.file.read_text(encoding='utf-8')) if self.file.exists() else {}
        self.file.write_text(env.render(current | values), encoding='utf-8')

    def app(self):
        return create_app(start_scheduler=False, env_file=str(self.file))

    def fixture(self):
        app = self.app()
        platform = app.state.platform
        principal = platform.authenticate(self.token)
        return app, platform, principal

    def bot(self, platform, principal, key='sample-key', chat='sample-group'):
        return platform.create(principal, {'name':'测试机器人','chat_id':chat,
            'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key='+key})

    def test_literal_roundtrip_and_safe_errors(self):
        secret = 'sample!$()#=密钥\\path"'
        self.assertEqual(env.parse(env.render({'ROBORT_AI_API_KEY':secret}))['ROBORT_AI_API_KEY'], secret)
        self.assertEqual(env.parse("ROBORT_AI_API_KEY='sample#${TOKEN}'")['ROBORT_AI_API_KEY'],'sample#${TOKEN}')
        for text in ('BAD=do-not-print','ROBORT_AI_API_KEY="do-not-print',
                     'ROBORT_AI_API_KEY=a\nROBORT_AI_API_KEY=do-not-print',
                     'ROBORT_AI_API_KEY="do-not-print\\n"'):
            with self.subTest(text=text), self.assertRaises(ValueError) as error:
                env.parse(text)
            self.assertNotIn('do-not-print', str(error.exception))

    def test_explicit_state_isolated_from_checkout_and_process(self):
        os.environ.update(ROBORT_ENV_FILE=str(self.file), ROBORT_AI_API_KEY='private-process-key', ROBORT_LIVE_SEND='1')
        app = create_app(self.root/'isolated', self.token, False)
        self.assertFalse(app.state.live)
        self.assertEqual(Service(app.state.platform.workspace_context()).config(True)['ai']['api_key'], '')
        self.assertIsNone(env.settings_for(app.state.platform.root).path)

    def test_startup_and_ui_writeback_survive_restart_and_redact(self):
        self.write(ROBORT_AI_API_KEY='sample-startup-key', ROBORT_AI_ENABLED='1',
                   ROBORT_PUBLIC_BASE_URL='https://club.example.org')
        app, platform, principal = self.fixture()
        service = Service(platform.context(principal))
        self.assertEqual(service.config(True)['ai']['api_key'], 'sample-startup-key')
        self.assertTrue(service.config()['ai']['configured'])
        self.assertNotIn('sample-startup-key', json.dumps(service.config()))
        with TestClient(app) as client:
            response = client.patch('/api/config', headers={'Authorization':'Bearer '+self.token},
                json={'ai':{'api_key':'sample-updated-key','enabled':False}, 'public_base_url':'https://next.example.org'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotIn('sample-updated-key', response.text)
        saved = env.parse(self.file.read_text(encoding='utf-8'))
        self.assertEqual(saved['ROBORT_AI_API_KEY'],'sample-updated-key')
        self.assertEqual(saved['ROBORT_AI_ENABLED'],'0')
        restarted = self.app()
        current = Service(restarted.state.platform.workspace_context()).config(True)
        self.assertEqual(current['ai']['api_key'],'sample-updated-key')
        self.assertFalse(current['ai']['enabled'])
        self.assertEqual(current['public_base_url'],'https://next.example.org')

    def test_process_override_cannot_silently_undo_ui_changes(self):
        os.environ['ROBORT_AI_API_KEY']='sample-managed-key'
        _, platform, principal = self.fixture()
        service = Service(platform.context(principal))
        before = self.file.read_bytes()
        with self.assertRaisesRegex(ValueError,'进程环境'):
            service.update_config({'ai':{'api_key':'sample-rejected-key'}})
        self.assertEqual(before,self.file.read_bytes())
        self.assertEqual(service.config(True)['ai']['api_key'],'sample-managed-key')
        service.update_config({'capacity':12})
        self.assertEqual(service.config()['capacity'],12)

    def test_bot_connection_rotation_removal_and_group_deletion(self):
        _, platform, principal = self.fixture()
        first = self.bot(platform, principal)
        second = self.bot(platform, principal, 'second-key')
        callback = 'synthetic-rotated-callback-token-long-enough'
        platform.update(principal,first['id'],{'callback_token':callback})
        saved = env.bot_settings(env.parse(self.file.read_text(encoding='utf-8'))['ROBORT_BOTS_JSON'])
        self.assertEqual(saved[first['id']]['callback_token'],callback)
        restarted = self.app().state.platform
        self.assertEqual(restarted.bot(first['id'])['callback_hash'],platform.bot(first['id'])['callback_hash'])
        channel = platform.list_channels(bot_id=first['id'])[0]
        platform.archive_channel(principal,first['id'],channel['id'])
        self.assertEqual(self.app().state.platform.list_channels(bot_id=first['id']),[])
        group = platform.list_channels(bot_id=second['id'])[0]['group_id']
        BotGroups(platform).remove(principal,group)
        self.assertEqual(self.app().state.platform.list_channels(bot_id=second['id']),[])
        platform.remove(principal,first['id'])
        self.assertNotIn(first['id'],env.bot_settings(env.parse(self.file.read_text(encoding='utf-8'))['ROBORT_BOTS_JSON']))
        self.app()

    def test_failed_database_mutation_restores_file(self):
        _, platform, principal = self.fixture()
        self.bot(platform,principal)
        second = self.bot(platform,principal,'second-key')
        before = self.file.read_bytes()
        with self.assertRaises(ValueError):
            platform.update(principal,second['id'],{'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=sample-key'})
        self.assertEqual(self.file.read_bytes(),before)
        self.assertEqual(platform.bot(second['id'])['robot_key'],'second-key')

    def test_private_endpoint_is_opt_in_and_scoped(self):
        self.write(ROBORT_WEBHOOK_ENDPOINTS='https://collab.example.org/woa/api/v1/webhook/send')
        _, platform, principal = self.fixture()
        url='https://collab.example.org/woa/api/v1/webhook/send?key=sample-private'
        platform.create(principal,{'name':'私有部署','chat_id':'private-group','webhook_url':url})
        with self.assertRaises(ValueError):validate_webhook(url,self.root/'other-state')
        for endpoint in ('http://collab.example.org/send','https://collab.example.org/send?key=sample', 'https://[broken'):
            self.write(ROBORT_WEBHOOK_ENDPOINTS=endpoint)
            with self.assertRaisesRegex(ValueError,'HTTPS'):self.app()

    def test_initial_password_never_resets_an_existing_password(self):
        self.write(ROBORT_INITIAL_ADMIN_PASSWORD='sample-initial-password')
        app = self.app()
        auth = app.state.passwords
        session = auth.login('sample-initial-password','local')
        auth.change(session['session_token'],'sample-initial-password','sample-updated-password',session['csrf_token'])
        restarted = self.app()
        restarted.state.passwords.login('sample-updated-password','local')
        with self.assertRaises(PermissionError):restarted.state.passwords.login('sample-initial-password','local')

    def test_export_preserves_existing_keys_and_excludes_plain_password(self):
        credentials = bootstrap(self.state)
        platform = Platform(self.state)
        token = json.loads(credentials.read_text())['admin_token']
        bot = self.bot(platform,platform.authenticate(token))
        values = env.exported(platform)
        self.assertEqual(values['ROBORT_ADMIN_TOKEN'],token)
        self.assertEqual(values['ROBORT_INITIAL_ADMIN_PASSWORD'],'')
        item=env.bot_settings(values['ROBORT_BOTS_JSON'])[bot['id']]
        self.assertEqual(item['callback_token'],bot['callback_path'].rsplit('/',1)[1])
        self.assertEqual(item['robot_key'],'sample-key')
        self.assertEqual(values['ROBORT_LIVE_SEND'],'0')

    def test_concurrent_file_changes_preserve_bots_and_rollback(self):
        local = env.LocalEnv(self.file,process={})
        def update(index):
            with local.change(bot_id=format(index,'032x'),bot_values={'robot_key':'sample-'+str(index)}):pass
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(update,range(12)))
        self.assertEqual(len(env.bot_settings(local.get('ROBORT_BOTS_JSON'))),12)
        before=self.file.read_bytes()
        with self.assertRaises(RuntimeError):
            with local.change({'ROBORT_AI_API_KEY':'sample-rollback'}):raise RuntimeError('abort')
        self.assertEqual(before,self.file.read_bytes())

    def test_template_empty_and_missing_explicit_file_rejected(self):
        values=env.parse((Path(__file__).resolve().parents[1]/'.env.example').read_text(encoding='utf-8'))
        self.assertEqual(set(values),set(env.KEYS))
        self.assertTrue(all(v=='' for v in values.values()))
        with self.assertRaises(ValueError):env.load(self.root/'missing.env')
        self.write(ROBORT_LIVE_SEND='maybe')
        with self.assertRaisesRegex(ValueError,'ROBORT_LIVE_SEND'):self.app()


if __name__ == '__main__':unittest.main()
