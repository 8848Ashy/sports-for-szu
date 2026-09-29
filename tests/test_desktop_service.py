import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
import requests
from sports_szu.engine import Engine
from sports_szu.models import Plan, SHANGHAI
from sports_szu.scheduling import Schedule
from sports_szu.service import DesktopService
from sports_szu.store import Store
from sports_szu.webserver import UIServer
from test_core import FakeAPI


class MemoryVault:
    def __init__(self):
        self.account = {'username': 'test-user', 'real_name': '测试用户', 'password': 'SYNTHETIC-PASSWORD',
                        'cookies': [{'name': 'session', 'value': 'SYNTHETIC-COOKIE', 'domain': 'ehall.szu.edu.cn'}]}

    def read(self):
        return copy.deepcopy(self.account)

    def update(self, **values):
        self.account.update(values)


class ServiceFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite')
        self.vault = MemoryVault()
        self.moment = datetime(2026, 10, 5, 12, 30, tzinfo=SHANGHAI)
        self.service = DesktopService(self.store, self.vault, lambda: self.moment)
        self.api = FakeAPI()
        self.service.engine = Engine(self.store, self.api, self.vault.read(), self.service.notify, lambda: self.moment)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()


class ServiceTests(ServiceFixture, unittest.TestCase):

    def test_blank_then_released_room_books_automatically(self):
        plan = Plan('p', auto_pay=False, slots=['19:00-20:00'])
        self.api.available = []
        self.service.create_task(plan.to_dict(), '2026-10-06')
        self.service.poll_task()
        self.assertEqual(self.api.book_calls, 0)
        self.assertEqual(self.store.all('tasks')[0]['status'], 'running')
        self.api.available = FakeAPI().available
        self.moment += timedelta(seconds=2)
        self.service.poll_task()
        self.assertEqual(self.api.book_calls, 1)
        self.assertEqual(self.store.all('tasks')[0]['status'], 'done')

    def test_three_preferred_slots_cap_at_two(self):
        plan = Plan('p', slots=['19:00-20:00', '20:00-21:00', '21:00-22:00'])
        self.service.create_task(plan.to_dict(), '2026-10-06')
        self.service.poll_task()
        self.assertEqual(self.api.book_calls, 2)
        self.assertEqual(self.store.all('tasks')[0]['completed'], plan.slots[:2])

    def test_interval_and_retry_limit(self):
        plan = Plan('p', retry_interval=5, max_retries=2)
        self.api.available = []
        self.service.create_task(plan.to_dict(), '2026-10-06')
        self.service.poll_task()
        self.service.poll_task()
        self.assertEqual(self.store.all('tasks')[0]['attempts'], 1)
        self.moment += timedelta(seconds=5)
        self.service.poll_task()
        self.moment += timedelta(seconds=5)
        self.service.poll_task()
        self.assertEqual(self.store.all('tasks')[0]['attempts'], 2)
        self.assertEqual(self.store.all('tasks')[0]['status'], 'done')

    def test_stop_prevents_request_without_cancelling_orders(self):
        self.service.create_task(Plan('p').to_dict(), '2026-10-06')
        self.service.action('booking_stop', {})
        self.service.poll_task()
        self.assertEqual(self.api.book_calls, 0)
        self.assertEqual(self.api.cancel_calls, 0)
        self.service.command('booking_stop', {})
        self.assertEqual(self.store.all('tasks')[0]['status'], 'stopped')

    def test_stopped_task_does_not_restart_in_same_schedule_window(self):
        schedule = Schedule('s', '每周', Plan('p').to_dict(), start_date='2026-10-05', weekdays=[0])
        self.service.action('schedule_save', schedule.to_dict())
        self.service.check_schedules()
        self.service.command('booking_stop', {})
        self.service.check_schedules()
        self.assertEqual(len(self.store.all('tasks')), 1)
        self.assertEqual(self.store.all('tasks')[0]['status'], 'stopped')

    def test_weekly_uses_each_execution_day(self):
        schedule = Schedule('s', '每周', Plan('p').to_dict(), start_date='2026-10-05', weekdays=[0], day_offset=1)
        self.service.action('schedule_save', schedule.to_dict())
        self.service.check_schedules()
        self.moment += timedelta(days=7)
        self.service.check_schedules()
        self.assertEqual([t['target_date'] for t in self.store.all('tasks')], ['2026-10-06', '2026-10-13'])

    def test_snapshot_never_returns_credentials(self):
        data = json.dumps(self.service.snapshot())
        self.assertNotIn('SYNTHETIC-PASSWORD', data)
        self.assertNotIn('SYNTHETIC-COOKIE', data)
        self.assertNotIn('"cookies"', data)

    def test_manual_restart_paused_and_fixed_date_preserved(self):
        self.api.available = []
        self.service.create_task(Plan('p').to_dict(), '2026-10-06')
        self.service.poll_task()
        self.moment += timedelta(days=1)
        DesktopService(self.store, self.vault, lambda: self.moment)
        task = self.store.all('tasks')[0]
        self.assertEqual(task['status'], 'paused_restart')
        self.assertEqual(task['target_date'], '2026-10-06')

    def test_unknown_booking_never_replayed(self):
        self.api.fail_book = True
        self.service.create_task(Plan('p').to_dict(), '2026-10-06')
        self.service.poll_task()
        self.moment += timedelta(seconds=5)
        self.service.poll_task()
        self.assertEqual(self.api.book_calls, 1)
        self.assertEqual(self.store.all('tasks')[0]['status'], 'stopped')

    def test_care_changes_do_not_mutate_existing_orders(self):
        self.service.create_task(Plan('p', slots=['19:00-20:00']).to_dict(), '2026-10-06')
        self.service.poll_task()
        before = self.store.all('orders')[0]
        self.service.action('care_save', {**self.store.get('settings', 'care_v2'), 'cancel_minutes': 180, 'daily_limit': 9000})
        self.assertEqual(before, self.store.all('orders')[0])


class ScheduleTests(unittest.TestCase):
    def test_once_and_weekly_window(self):
        schedule = Schedule('s', '单次', Plan('p').to_dict(), kind='once', start_date='2026-10-05').validate()
        fire = datetime(2026, 10, 5, 12, 30, tzinfo=SHANGHAI)
        self.assertTrue(schedule.due(fire))
        self.assertIsNone(schedule.due(fire + timedelta(minutes=3)))
        self.assertIsNone(schedule.due(fire + timedelta(days=7)))

    def test_fixed_date_and_end_date(self):
        schedule = Schedule('s', '每周', Plan('p').to_dict(), start_date='2026-10-05',
                            end_date='2026-10-06', weekdays=[0], date_mode='fixed', target_date='2026-10-08').validate()
        moment = datetime(2026, 10, 5, 12, tzinfo=SHANGHAI)
        self.assertEqual(schedule.booking_date(schedule.next_fire(moment)), '2026-10-08')
        self.assertIsNone(schedule.next_fire(moment + timedelta(days=7)))


class BridgeTests(ServiceFixture, unittest.TestCase):
    def test_local_bridge_requires_session_host_and_origin(self):
        server = UIServer(self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = requests.Session()
        client.trust_env = False
        try:
            self.assertEqual(client.get(server.origin + '/api/state').status_code, 401)
            link = server.launch_url()
            response = client.get(link)
            self.assertEqual(response.status_code, 200)
            self.assertIn('HttpOnly', response.history[0].headers['Set-Cookie'])
            self.assertEqual(client.get(link).status_code, 403)
            snapshot = client.get(server.origin + '/api/state')
            self.assertNotIn('SYNTHETIC-PASSWORD', snapshot.text)
            self.assertNotIn('SYNTHETIC-COOKIE', snapshot.text)
            action = {'action': 'booking_stop', 'payload': {}}
            self.assertEqual(client.post(server.origin + '/api/action', json=action, headers={'Origin': 'https://evil.example'}).status_code, 403)
            self.assertEqual(client.post(server.origin + '/api/action', json=action, headers={'Origin': server.origin}).status_code, 200)
            self.assertEqual(client.get(server.origin + '/api/state', headers={'Host': 'evil.example'}).status_code, 403)
        finally:
            client.close()
            server.shutdown()
            server.server_close()
