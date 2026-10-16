"""One physical group, independent robot transports, shared club business."""
import asyncio
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
import httpx

from app.db import TZ, connect
from app.main import create_app
from app.members import Members
from app.messages import Outbox
from app.notifications import Notifications
from app.service import Service
from app.bot_capabilities import ALL_CAPABILITIES


class MultipleRobotsPerGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 19, 16, 0, tzinfo=TZ)
        self.clock = lambda: self.now
        self.token = 'v27-transport-test-admin-token-000000'
        self.app = create_app(Path(self.temp.name), self.token, False, False, self.clock)
        self.platform = self.app.state.platform
        self.principal = self.platform.authenticate(self.token)
        self.admin = self.platform.workspace_context()
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer ' + self.token}
        self.bots = []
        for name in ('甲', '乙'):
            response = self.client.post('/api/bots', headers=self.headers, json={
                'name': name + '机器人', 'chat_id': 'same-room', 'capabilities': list(ALL_CAPABILITIES),
                'robot_key': name + '-test-key',
                'webhook_url': 'https://xz.wps.cn/api/v1/webhook/send?key=v27-' + ('a' if name == '甲' else 'b'),
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.bots.append(response.json())
        self.channels = [b['channels'][0]['id'] for b in self.bots]
        members = Members(self.admin, self.clock)
        members.touch('张三', 'member-one', source='admin')
        members.review('member-one', 'approved')

    def context(self, index):
        return self.platform.context_for_channel(self.channels[index], 'member-one', False, 'callback')

    def callback(self, index, **changes):
        body = {'chatid': 'same-room', 'creator': 'member-one', 'ctime': '123456',
                'content': '报名，李四', 'robot_key': ('甲' if index == 0 else '乙') + '-test-key'}
        return self.client.post(self.bots[index]['callback_path'], json=body | changes)

    def test_same_group_bots_route_only_to_authenticated_transport(self):
        self.assertEqual(self.callback(0).json(), {'result': 'ok'})
        self.assertEqual(len(Outbox(self.context(0)).list()), 1)
        self.assertEqual(Outbox(self.context(1)).list(), [])
        self.assertNotEqual(self.context(0).transport_db_path, self.context(1).transport_db_path)
        self.assertEqual(self.context(0).db_path, self.context(1).db_path)
        self.assertEqual(self.callback(1, robot_key='甲-test-key').status_code, 403)
        wrong_token = self.bots[1]['callback_path'].rsplit('/', 1)[0] + '/' + self.bots[0]['callback_path'].rsplit('/', 1)[1]
        self.assertEqual(self.client.post(wrong_token, json={
            'chatid': 'same-room', 'creator': 'member-one', 'robot_key': '乙-test-key',
            'ctime': '123456', 'content': '报名'}).status_code, 403)
        self.assertEqual(self.callback(1, chatid='unbound-room').status_code, 403)
        self.assertEqual(Outbox(self.context(1)).list(), [])

    def test_same_event_on_both_bots_has_separate_inboxes_and_one_registration(self):
        for index in (0, 1, 0, 1):
            self.assertEqual(self.callback(index).status_code, 200)
        for index in (0, 1):
            ctx = self.context(index)
            self.assertEqual(len(Outbox(ctx).list()), 1)
            with connect(ctx.transport_db_path) as db:
                inbox = list(db.execute('SELECT event_id,channel_id,status FROM inbox'))
            self.assertEqual(len(inbox), 1)
            self.assertEqual(inbox[0]['channel_id'], ctx.channel_id)
            self.assertEqual(inbox[0]['status'], 'completed')
            self.assertEqual(Members(ctx, self.clock).get()['name'], '张三')
        rows = Service(self.admin, self.clock).registrations()['registrations']
        self.assertEqual([(r['name'], r['guest_index']) for r in rows], [('张三', 0), ('李四', 1)])

    def test_each_robot_uses_its_own_webhook_in_same_group(self):
        destinations = []

        async def exercise():
            async def handler(request):
                destinations.append(request.url.params['key'])
                return httpx.Response(200, json={'result': 'ok'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                for index in (0, 1):
                    Outbox(self.context(index)).enqueue('各自回复', 'same-reply-key')
                    self.assertEqual(await Outbox(self.context(index)).deliver_one(
                        self.platform, live=True, client=client, at=self.now), 'sent')

        asyncio.run(exercise())
        self.assertEqual(destinations, ['v27-a', 'v27-b'])

    def test_broadcast_same_group_is_sent_once_and_recovery_keeps_sender(self):
        notices = Notifications(self.platform, self.clock)
        self.assertEqual([c.channel_id for c in notices.destinations(self.channels)], [self.channels[0]])
        first = notices.enqueue('同群只提醒一次', self.channels, key='same-group-notice')
        self.assertEqual(first['channel_ids'], [self.channels[0]])
        self.assertEqual(first['status'], 'queued')
        with connect(notices.path, True) as db:
            db.execute("UPDATE notification_jobs SET status='pending' WHERE id=?", (first['id'],))
            db.execute("UPDATE notification_deliveries SET status='pending',outbox_ids='[]' WHERE job_id=?", (first['id'],))
        notices.recover()
        repeated = notices.enqueue('不能另选机器人重复发送', list(reversed(self.channels)), key='same-group-notice')
        self.assertEqual(repeated['channel_ids'], first['channel_ids'])
        self.assertEqual(len(Outbox(self.context(0)).list()), 1)
        self.assertEqual(Outbox(self.context(1)).list(), [])

    def test_broadcast_chooses_available_robot_without_changing_configuration(self):
        self.platform.save_channel(self.principal, self.bots[0]['id'], {'active': False}, self.channels[0])
        notices = Notifications(self.platform, self.clock)
        job = notices.enqueue('改由可用机器人通知', self.channels, key='available-robot')
        self.assertEqual(job['channel_ids'], [self.channels[1]])
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(len(Outbox(self.context(1)).list()), 1)
        self.assertFalse(self.platform.channel(self.channels[0])['active'])

    def test_broadcast_does_not_select_robot_outside_activity_scope(self):
        second_venue = Service(self.admin, self.clock).save_venue({'name': '第二场地'})
        self.platform.update(self.principal, self.bots[0]['id'], {'venue_ids': [second_venue['id']]})
        notices = Notifications(self.platform, self.clock)
        job = notices.enqueue('场地一提醒', self.channels, venue_id=1, key='scope-aware-sender')
        self.assertEqual(job['channel_ids'], [self.channels[1]])
        self.assertEqual(Outbox(self.context(0)).list(), [])
        self.assertEqual(len(Outbox(self.context(1)).list()), 1)

    def test_broadcast_prefers_configured_sender_over_unconfigured_first_candidate(self):
        self.platform.save_channel(self.principal, self.bots[0]['id'], {'webhook_url': ''}, self.channels[0])
        notices = Notifications(self.platform, self.clock)
        job = notices.enqueue('有发送配置的机器人负责', self.channels, key='configured-first')
        self.assertEqual(job['channel_ids'], [self.channels[1]])
        self.assertEqual(Outbox(self.context(0)).list(), [])
        self.assertEqual(len(Outbox(self.context(1)).list()), 1)

    def test_broadcast_all_unconfigured_keeps_first_eligible_simulation_candidate(self):
        for index in (0, 1):
            self.platform.save_channel(self.principal, self.bots[index]['id'], {'webhook_url': ''}, self.channels[index])
        self.platform.update(self.principal, self.bots[0]['id'], {'venue_ids': []})
        notices = Notifications(self.platform, self.clock)
        job = notices.enqueue('模拟通知', self.channels, venue_id=1, key='simulation-candidate')
        self.assertEqual(job['channel_ids'], [self.channels[1]])
        self.assertEqual(job['status'], 'queued')

    def test_daily_reminder_prefers_configured_sender_in_same_group(self):
        from app.scheduler import Scheduler
        self.platform.save_channel(self.principal, self.bots[0]['id'], {'webhook_url': ''}, self.channels[0])
        self.now = self.now.replace(hour=9, minute=29)
        scheduler = Scheduler(self.platform, live=False, clock=self.clock)
        self.now = self.now.replace(minute=30)
        scheduler.tick()
        self.assertEqual(Outbox(self.context(0)).list(), [])
        self.assertEqual(len(Outbox(self.context(1)).list()), 1)

    def test_admin_message_api_allows_same_group_multiple_robot_candidates(self):
        response = self.client.post('/api/messages/send', headers=self.headers,
                                    json={'text': '统一通知', 'channel_ids': self.channels})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['queued']), 1)
        self.assertEqual(sum(len(Outbox(self.context(i)).list()) for i in (0, 1)), 1)

    def test_same_robot_cannot_duplicate_group_or_share_other_robot_webhook(self):
        response = self.client.post('/api/bots/' + self.bots[0]['id'] + '/channels',
                                    headers=self.headers, json={'name': '重复群', 'chat_id': 'same-room', 'robot_key': 'new-key'})
        self.assertEqual(response.status_code, 400)
        response = self.client.patch('/api/bots/' + self.bots[1]['id'] + '/channels/' + self.channels[1],
                                     headers=self.headers, json={'webhook_url': 'https://xz.wps.cn/api/v1/webhook/send?key=v27-a'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('同群多个机器人', response.text)

    def test_large_event_binds_two_robots_in_one_group_and_notifies_once(self):
        response = self.client.post('/api/tournaments', headers=self.headers, json={
            'title': '多机器人秋季赛', 'keyword': '秋季赛', 'kind': 'large', 'format': 'round_robin',
            'venue_id': 1, 'date': '2026-10-19', 'registration_open': '2026-10-19T09:00:00+08:00',
            'registration_close': '2026-10-19T18:00:00+08:00',
        })
        self.assertEqual(response.status_code, 200, response.text)
        event_id = response.json()['id']
        response = self.client.put(f'/api/tournaments/{event_id}/organization',
                                    headers=self.headers, json={'channel_ids': self.channels})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()['channel_ids']), set(self.channels))
        response = self.client.post(f'/api/tournaments/{event_id}/notify', headers=self.headers,
                                     json={'type': 'registration'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['queued']), 1)
        self.assertEqual(sum(len(Outbox(self.context(i)).list()) for i in (0, 1)), 1)
        # Empty explicit bot scope means no venue, not unrestricted access.
        self.platform.update(self.principal, self.bots[1]['id'], {'venue_ids': []})
        response = self.client.put(f'/api/tournaments/{event_id}/organization',
                                    headers=self.headers, json={'channel_ids': self.channels})
        self.assertEqual(response.status_code, 400)
        saved = self.client.get(f'/api/tournaments/{event_id}/organization', headers=self.headers).json()
        self.assertEqual(set(saved['channel_ids']), set(self.channels))


if __name__ == '__main__':
    unittest.main()
