import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
import requests
from sports_szu.engine import Engine
from sports_szu.engine import owner_key
from unittest.mock import Mock
from sports_szu.api import LoginRequired, ApiError
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

    def test_preflight_checks_early_and_final_but_does_not_book_early(self):
        self.service.action('schedule_save', Schedule('s', '预检', Plan('p').to_dict(), start_date='2026-10-05', weekdays=[0]).to_dict())
        original = self.api.orders
        self.api.orders = Mock(side_effect=original)
        self.moment = self.moment.replace(hour=12, minute=25)
        self.service.check_schedules()
        self.assertEqual(self.api.orders.call_count, 1)
        self.assertEqual(self.store.all('tasks'), [])
        self.moment = self.moment.replace(minute=29, second=40)
        self.service.check_schedules()
        self.assertEqual(self.api.orders.call_count, 2)
        self.moment = self.moment.replace(minute=30, second=0)
        self.service.check_schedules()
        self.assertEqual(len(self.store.all('tasks')), 1)
        self.assertEqual(self.api.book_calls, 0)

    def test_invalid_preflight_blocks_task_and_rechecks_after_login(self):
        self.service.action('schedule_save', Schedule('s', '预检', Plan('p').to_dict(), start_date='2026-10-05', weekdays=[0]).to_dict())
        original = self.api.orders
        self.api.orders = Mock(side_effect=LoginRequired())
        self.service.launch_login = Mock()
        self.service.check_schedules()
        self.assertEqual(self.store.all('tasks'), [])
        self.service.launch_login.assert_called_once_with(False)
        self.api.orders = original
        self.moment += timedelta(seconds=31)
        self.service.check_schedules()
        self.assertFalse(self.service.session_invalid)
        self.assertEqual(len(self.store.all('tasks')), 1)

    def test_no_daily_login_limit_but_attempts_have_cooldown(self):
        self.store.put('settings', 'login_attempts', {'date': self.moment.date().isoformat(), 'count': 100})
        self.service.launch_login = Mock()
        self.service._login_expired()
        self.service._login_expired()
        self.service.launch_login.assert_called_once_with(False)
        self.moment += timedelta(seconds=61)
        self.service._login_expired()
        self.assertEqual(self.service.launch_login.call_count, 2)

    def test_preflight_network_failure_does_not_create_task_or_force_login(self):
        self.service.action('schedule_save', Schedule('s', '预检', Plan('p').to_dict(), start_date='2026-10-05', weekdays=[0]).to_dict())
        self.api.orders = Mock(side_effect=ApiError())
        self.service.launch_login = Mock()
        self.service.check_schedules()
        self.service.check_schedules()
        self.assertEqual(self.api.orders.call_count, 1)
        self.assertEqual(self.store.all('tasks'), [])
        self.service.launch_login.assert_not_called()
        self.moment += timedelta(minutes=4)
        self.service.check_schedules()
        self.assertTrue(any('已超过补执行窗口' in item['message'] for item in self.service.logs))

    def test_log_file_rotation_is_bounded(self):
        from sports_szu.safe_log import make_handler, write
        directory = Path(self.temp.name) / 'rotation'
        handler = make_handler(directory)
        handler.maxBytes = 150
        handler.backupCount = 2
        for number in range(30):
            write(handler, self.moment, 'info', f'查询记录{number}：无可预约场地')
        handler.close()
        self.assertLessEqual(len(list(directory.iterdir())), 3)
        self.assertIn('查询记录29', (directory / 'runtime.log').read_text(encoding='utf-8'))

    def test_file_logs_survive_service_restart_without_credentials(self):
        directory = Path(self.temp.name) / 'logs'
        service = DesktopService(self.store, self.vault, lambda: self.moment, log_directory=directory)
        service.log('登录预检通过', 'success')
        service.file_log.close()
        content = (directory / 'runtime.log').read_text(encoding='utf-8')
        self.assertIn('2026-10-05T12:30:00+08:00', content)
        self.assertIn('登录预检通过', content)
        self.assertNotIn('SYNTHETIC-PASSWORD', content)
        self.assertNotIn('SYNTHETIC-COOKIE', content)
        second = DesktopService(self.store, self.vault, lambda: self.moment, log_directory=directory)
        second.file_log.close()
        self.assertIn('登录预检通过', (directory / 'runtime.log').read_text(encoding='utf-8'))

    def test_switch_account_stops_tasks_and_clears_session(self):
        self.service.create_task(Plan('p').to_dict(), '2026-10-06')
        self.service.action('schedule_save', Schedule('s', '旧定时', Plan('p').to_dict(), start_date='2026-10-05').to_dict())
        self.store.put('orders', 'old-order', {'id': 'old-order', 'owner': 'old-owner', 'terminal': False})
        generation = self.service.account_generation
        self.service.login_busy = True
        self.service.balance_cents = 1000
        self.service.action('account_save', {'username': 'new-user', 'real_name': '新用户', 'remember': True, 'password': ''})
        self.assertEqual(self.vault.read()['cookies'], [])
        self.assertEqual(self.vault.read()['password'], '')
        self.assertIsNone(self.service.engine)
        self.assertIsNone(self.service.balance_cents)
        self.assertFalse(self.service.login_busy)
        self.assertEqual(self.store.all('tasks')[0]['status'], 'stopped')
        self.assertFalse(self.store.get('settings', 'schedules_v2')[0]['enabled'])
        self.assertTrue(self.service.snapshot()['orders'][0]['previous_account'])
        self.service.login_results.put((generation, 'ok', [{'name': 'old-session'}]))
        self.service.step()
        self.assertEqual(self.vault.read()['cookies'], [])

    def test_same_account_preserves_saved_password_and_cookie(self):
        before = self.vault.read()
        self.service.action('account_save', {'username': before['username'], 'real_name': '新姓名', 'remember': True, 'password': ''})
        self.assertEqual(self.vault.read()['cookies'], before['cookies'])
        self.assertEqual(self.vault.read()['password'], before['password'])

    def test_switch_back_does_not_resume_old_order_management(self):
        self.store.put('orders', 'old-order', {'id': 'old-order', 'owner': owner_key('test-user'), 'terminal': False})
        for username in ('new-user', 'test-user'):
            self.service.action('account_save', {'username': username, 'real_name': '测试用户', 'remember': False})
        engine = Engine(self.store, self.api, self.vault.read(), clock=lambda: self.moment)
        engine.process = Mock()
        engine.tick(run_schedules=False)
        engine.process.assert_not_called()
        self.assertTrue(self.service.snapshot()['orders'][0]['previous_account'])
        with self.assertRaises(ValueError):
            engine.request_cancel('old-order')

    def test_stale_login_does_not_clear_new_login_busy(self):
        self.service.login_busy = True
        self.service.login_results.put(('outdated-generation', 'ok', [{'name': 'old-session'}]))
        self.service.engine = None
        self.service.step()
        self.assertTrue(self.service.login_busy)
        self.assertEqual(self.vault.read()['cookies'][0]['value'], 'SYNTHETIC-COOKIE')

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
            credentials = {'action': 'account_load', 'payload': {}}
            self.assertEqual(client.post(server.origin + '/api/action', json=credentials, headers={'Origin': 'https://evil.example'}).status_code, 403)
            loaded = client.post(server.origin + '/api/action', json=credentials, headers={'Origin': server.origin}).json()
            self.assertEqual(loaded['account']['password'], 'SYNTHETIC-PASSWORD')
            self.assertNotIn('cookies', loaded['account'])
            action = {'action': 'booking_stop', 'payload': {}}
            self.assertEqual(client.post(server.origin + '/api/action', json=action, headers={'Origin': 'https://evil.example'}).status_code, 403)
            self.assertEqual(client.post(server.origin + '/api/action', json=action, headers={'Origin': server.origin}).status_code, 200)
            self.assertEqual(client.get(server.origin + '/api/state', headers={'Host': 'evil.example'}).status_code, 403)
        finally:
            client.close()
            server.shutdown()
            server.server_close()
