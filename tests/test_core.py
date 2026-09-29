import copy
from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sports_szu.api import Ambiguous, ApiError, SchoolAPI
from sports_szu.engine import Engine, owner_key
from sports_szu.models import Plan, SHANGHAI, court_codes, court_rank, money
from sports_szu.security import Vault
from sports_szu.store import Store


class FakeAPI:
    def __init__(self):
        self.rows = []
        self.pay_calls = self.cancel_calls = self.book_calls = 0
        self.fail_pay = self.fail_cancel = self.fail_book = False
        self.balance_value = 3000
        self.price = {'success': True, 'gxje': 1500, 'tuipiao': 1500, 'zuizong': 0, 'tksyje': 3000}
        self.available = [{'WID': 'room-test', 'CGBM': '001', 'CGBM_DISPLAY': '测试场馆', 'CDMC': '羽毛球场B4'}]

    def orders(self):
        return copy.deepcopy(self.rows)

    def rooms(self, *args):
        return self.available

    def book(self, plan, date, slot, room, account):
        self.book_calls += 1
        if self.fail_book:
            raise Ambiguous('测试网络超时')
        row = order_row(date, slot, f'order-{self.book_calls}')
        self.rows.append(row)
        return {'code': '0', 'data': {'DHID': row['DHID']}}

    def balance(self):
        return self.balance_value

    def quote(self, wid):
        return self.price

    def token(self):
        return 'synthetic-test-token'

    def pay(self, wid, quote, token):
        self.pay_calls += 1
        if self.fail_pay:
            raise Ambiguous('测试付款超时')
        next(r for r in self.rows if r['WID'] == wid)['SFZF'] = '1'
        self.balance_value -= quote['gxje']
        return {'success': True}

    def cancel(self, wid):
        self.cancel_calls += 1
        if self.fail_cancel:
            raise Ambiguous('测试取消超时')
        row = next(r for r in self.rows if r['WID'] == wid)
        row['YYZT'] = 'CG_QX'
        if row['SFZF'] == '1':
            self.balance_value += 1500
        return {'code': '0'}


def order_row(date='2026-10-06', slot='19:00-20:00', identity='order-test'):
    start, end = slot.split('-')
    return {'WID': 'wid-' + identity, 'DHID': identity, 'YYRGH': 'test-user', 'YYSF': '预约人',
            'CDWID': 'room-test', 'XMDM': '001', 'XQWID': '1', 'SFZF': '0', 'YYZT': 'CG_YY',
            'YYSJD': f'{date} {start}~{date} {end}', 'TRANAMT': '15.00'}


class ModelTests(unittest.TestCase):
    def test_money_no_float_rounding(self):
        self.assertEqual(money('15.01'), 1501)
        for bad in ('1.001', '-1', 'NaN', 'Infinity', None):
            with self.assertRaises(ValueError):
                money(bad)

    def test_courts(self):
        self.assertEqual(court_codes('b4-6,c4'), ['B4', 'B5', 'B6', 'C4'])
        self.assertLess(court_rank('羽毛球场B5', 'B4-6,C4-6', 'D7-8,A7-8'), court_rank('C1', 'B4-6,C4-6', 'D7-8,A7-8'))
        self.assertGreater(court_rank('A8', 'B4-6', 'A7-8'), court_rank('C1', 'B4-6', 'A7-8'))

    def test_weekdays_and_window(self):
        plan = Plan('test', enabled=True, weekdays=[0])
        monday = datetime(2026, 10, 5, 12, 30, tzinfo=SHANGHAI)
        self.assertTrue(plan.due(monday))
        self.assertFalse(plan.due(monday - timedelta(seconds=1)))
        self.assertFalse(plan.due(monday + timedelta(minutes=3)))
        self.assertFalse(plan.due(monday + timedelta(days=1)))
        self.assertEqual(plan.next_fire(monday + timedelta(minutes=5)), monday + timedelta(days=7))

    def test_date_bounds(self):
        plan = Plan('test', enabled=True, weekdays=[0], valid_from='2026-10-06', valid_until='2026-10-11')
        self.assertIsNone(plan.next_fire(datetime(2026, 10, 5, 12, tzinfo=SHANGHAI)))

    def test_validation(self):
        for change in ({'cancel_minutes': 30}, {'slots': ['19:00-20:00', '19:30-20:30']},
                       {'preferred': 'B8-4'}, {'day_offset': -1}, {'venue': ''}, {'weekdays': []}):
            with self.assertRaises(ValueError):
                Plan('test', **change).validate()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.sqlite'
        self.store = Store(self.path)
        self.api = FakeAPI()
        self.moment = datetime(2026, 10, 5, 12, 30, tzinfo=SHANGHAI)
        self.account = {'username': 'test-user', 'real_name': '测试姓名'}
        self.notices = []
        self.engine = Engine(self.store, self.api, self.account, self.notices.append, lambda: self.moment)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def job(self, **changes):
        start = datetime(2026, 10, 6, 19, tzinfo=SHANGHAI)
        job = {'id': 'order-test', 'owner': owner_key('test-user'), 'date': '2026-10-06', 'slot': '19:00-20:00',
               'start': start.timestamp(), 'created': self.moment.timestamp(), 'room': 'room-test',
               'sport': '001', 'campus': '1', 'venue': '测试场馆', 'cancel_minutes': 60,
               'max_cents': 3000, 'auto_pay': True, 'confirmed': False, 'message': ''}
        job.update(changes)
        self.store.put('orders', job['id'], job)
        self.api.rows = [order_row(identity=job['id'])]
        return job

    def process(self):
        self.engine.process(self.store.get('orders', 'order-test'))

    def test_payment_once_then_verify(self):
        self.job()
        self.process()
        self.process()
        self.assertEqual(self.api.pay_calls, 1)
        self.assertTrue(self.store.get('orders', 'order-test')['paid'])

    def test_payment_timeout_survives_restart(self):
        self.job()
        self.api.fail_pay = True
        with self.assertRaises(Ambiguous):
            self.process()
        self.store.close()
        self.store = Store(self.path)
        self.engine = Engine(self.store, self.api, self.account, clock=lambda: self.moment)
        self.process()
        self.assertEqual(self.api.pay_calls, 1)

    def test_insufficient_balance(self):
        self.job()
        self.api.price['zuizong'] = 100
        self.process()
        self.assertEqual(self.api.pay_calls, 0)

    def test_single_limit(self):
        self.job(max_cents=1000)
        self.process()
        self.assertEqual(self.api.pay_calls, 0)

    def test_daily_limit(self):
        self.job()
        self.store.put('settings', 'daily_limit', 1000)
        self.process()
        self.assertEqual(self.api.pay_calls, 0)

    def test_amount_mismatch(self):
        self.job()
        self.api.rows[0]['TRANAMT'] = '14.00'
        self.process()
        self.assertEqual(self.api.pay_calls, 0)

    def test_cannot_pay_another_owner(self):
        self.job()
        self.api.rows[0]['YYRGH'] = 'someone-else'
        with self.assertRaises(ApiError):
            self.process()
        self.assertEqual(self.api.pay_calls, 0)

    def test_cannot_pay_wrong_room(self):
        self.job()
        self.api.rows[0]['CDWID'] = 'other-room'
        with self.assertRaises(ApiError):
            self.process()

    def test_auto_cancel_at_60_minutes(self):
        self.job()
        self.api.rows[0]['SFZF'] = '1'
        self.moment = datetime(2026, 10, 6, 18, tzinfo=SHANGHAI)
        self.process()
        self.process()
        self.assertEqual(self.api.cancel_calls, 1)
        self.assertTrue(self.store.get('orders', 'order-test')['terminal'])

    def test_confirm_prevents_auto_cancel(self):
        self.job()
        self.engine.confirm('order-test')
        self.moment = datetime(2026, 10, 6, 18, tzinfo=SHANGHAI)
        self.process()
        self.assertEqual(self.api.cancel_calls, 0)

    def test_manual_cancel_confirmed_order(self):
        self.job(confirmed=True)
        self.engine.request_cancel('order-test')
        self.process()
        self.assertEqual(self.api.cancel_calls, 1)

    def test_cannot_confirm_inflight_cancel(self):
        self.job(cancel_attempt=True)
        with self.assertRaises(ValueError):
            self.engine.confirm('order-test')

    def test_cannot_confirm_pending_cancel(self):
        self.job()
        self.engine.request_cancel('order-test')
        with self.assertRaises(ValueError):
            self.engine.confirm('order-test')

    def test_no_cancel_after_safe_deadline(self):
        self.job()
        self.moment = datetime(2026, 10, 6, 18, 31, tzinfo=SHANGHAI)
        self.process()
        self.assertEqual(self.api.cancel_calls, 0)
        self.assertTrue(self.notices)

    def test_ambiguous_cancel_not_replayed(self):
        self.job(cancel_requested=True)
        self.api.fail_cancel = True
        with self.assertRaises(Ambiguous):
            self.process()
        self.process()
        self.assertEqual(self.api.cancel_calls, 1)

    def test_no_pay_at_cancel_cutoff(self):
        self.job()
        self.moment = datetime(2026, 10, 6, 18, tzinfo=SHANGHAI)
        self.process()
        self.assertEqual(self.api.pay_calls, 0)

    def test_reminder_before_cancellation(self):
        self.job(auto_pay=False)
        self.moment = datetime(2026, 10, 6, 17, tzinfo=SHANGHAI)
        self.process()
        self.process()
        self.assertEqual(len(self.notices), 1)

    def test_booking_dedupes_across_plans(self):
        plan = Plan('p1', auto_pay=False)
        self.engine.book(plan, '19:00-20:00')
        self.engine.book(Plan('p2'), '19:00-20:00')
        self.assertEqual(self.api.book_calls, 1)

    def test_booking_timeout_no_retry(self):
        self.api.fail_book = True
        self.engine.book(Plan('p1'), '19:00-20:00')
        self.moment += timedelta(seconds=10)
        self.engine.book(Plan('p1'), '19:00-20:00')
        self.assertEqual(self.api.book_calls, 1)

    def test_existing_order_not_adopted(self):
        self.api.rows = [order_row()]
        self.engine.book(Plan('p1'), '19:00-20:00')
        self.assertEqual(self.api.book_calls, 0)
        self.assertEqual(self.store.all('orders'), [])

    def test_only_configured_weekday(self):
        plan = Plan('p1', enabled=True, weekdays=[1])
        self.store.put('plans', plan.id, plan.to_dict())
        self.store.put('settings', 'scheduler_enabled', True)
        self.engine.tick()
        self.assertEqual(self.api.book_calls, 0)

    def test_order_care_survives_scheduler_pause(self):
        self.job()
        self.store.put('settings', 'scheduler_enabled', False)
        self.moment = datetime(2026, 10, 6, 18, tzinfo=SHANGHAI)
        self.engine.tick()
        self.assertEqual(self.api.cancel_calls, 1)

    def test_never_pay_after_40_minutes(self):
        self.job()
        self.moment += timedelta(minutes=40)
        self.process()
        self.assertEqual(self.api.pay_calls, 0)

    def test_interrupted_booking_alerts_on_restart(self):
        self.store.put('runs', 'test-run', {'id': 'test-run', 'status': 'submitted'})
        Engine(self.store, self.api, self.account, self.notices.append, lambda: self.moment)
        self.assertEqual(self.store.get('runs', 'test-run')['status'], 'unknown')
        self.assertTrue(self.notices)

    def test_payment_cutoff_rechecked_after_network(self):
        self.job()
        self.moment = datetime(2026, 10, 6, 17, 59, 59, tzinfo=SHANGHAI)
        self.store.patch_order('order-test', created=self.moment.timestamp())
        def register():
            self.moment += timedelta(seconds=2)
            return 'synthetic'
        self.api.token = register
        self.process()
        self.assertEqual(self.api.pay_calls, 0)


class AdapterTests(unittest.TestCase):
    def test_http_redirect_rejected(self):
        from unittest.mock import Mock
        from sports_szu.api import LoginRequired
        api = SchoolAPI([])
        api.session.post = Mock(return_value=Mock(status_code=302))
        with self.assertRaises(LoginRequired):
            api.orders()
        self.assertFalse(api.session.post.call_args.kwargs['allow_redirects'])

    def test_no_cookie_replay_to_arbitrary_domains(self):
        api = SchoolAPI([{'name': 'test', 'value': 'synthetic', 'domain': 'evil.example'},
                         {'name': 'CAS', 'value': 'synthetic', 'domain': 'authserver.szu.edu.cn'},
                         {'name': 'site', 'value': 'synthetic', 'domain': '.szu.edu.cn'}])
        self.assertEqual([(c.name, c.domain) for c in api.session.cookies], [('site', 'ehall.szu.edu.cn')])
        self.assertFalse(api.session.trust_env)

    def test_write_network_timeout_is_ambiguous(self):
        import requests
        from unittest.mock import Mock
        api = SchoolAPI([])
        api.session.post = Mock(side_effect=requests.Timeout())
        with self.assertRaises(Ambiguous):
            api.cancel('synthetic')

    def test_gym_zero_occupancy_available(self):
        api = SchoolAPI.__new__(SchoolAPI)
        row = {'WID': 'room', 'CGBM': '004', 'CGBM_DISPLAY': '测试健身房', 'CDMC': '一楼健身房',
               'disabled': False, 'text': '0/120'}
        api.request = lambda *a, **k: {'code': '0', 'datas': {'getOpeningRoom': {'rows': [row]}}}
        self.assertEqual(len(api.rooms(Plan('p', sport='gym', venue='测试健身房'), '2026-10-06', '19:00-20:00')), 1)

    def test_malformed_quote_missing_field(self):
        api = SchoolAPI.__new__(SchoolAPI)
        api.request = lambda *a, **k: {'success': True}
        with self.assertRaises(ApiError):
            api.quote('test')

    def test_gym_payload_no_court_preference_required(self):
        api = SchoolAPI.__new__(SchoolAPI)
        calls = []
        api.request = lambda *args, **kwargs: calls.append((args, kwargs)) or {'code': '0'}
        api.book(Plan('gym', sport='gym'), '2026-10-06', '19:00-20:00',
                 {'WID': 'dynamic-resource', 'CGBM': '004'}, {'username': 'test', 'real_name': '测试'})
        data = calls[0][0][1]
        self.assertEqual((data['XMDM'], data['YYLX'], data['CGDM'], data['CYRS']), ('007', '2.0', '004', '1'))
        self.assertEqual(data['CDWID'], 'dynamic-resource')

    def test_room_priority(self):
        api = SchoolAPI.__new__(SchoolAPI)
        rows = [{'WID': x, 'CGBM': '001', 'CGBM_DISPLAY': '测试场馆', 'CDMC': x, 'disabled': False, 'text': '可预约'} for x in ['A8', 'C1', 'B4']]
        api.request = lambda *a, **k: {'code': '0', 'datas': {'getOpeningRoom': {'rows': rows}}}
        rooms = api.rooms(Plan('p', venue='测试场馆'), '2026-10-06', '19:00-20:00')
        self.assertEqual([r['CDMC'] for r in rooms], ['B4', 'C1', 'A8'])

    def test_quote_rejects_float_and_bool(self):
        api = SchoolAPI.__new__(SchoolAPI)
        for wrong in (1.0, True, '100'):
            api.request = lambda *a, **k: {'success': True, 'gxje': wrong, 'tuipiao': 0, 'zuizong': 0, 'tksyje': 0}
            with self.assertRaises(ApiError):
                api.quote('test')


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI only')
class WindowsSecurityTests(unittest.TestCase):
    def test_vault_encrypted_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Vault(directory)
            vault.update(password='synthetic-secret-test', username='test')
            self.assertNotIn(b'synthetic-secret-test', vault.path.read_bytes())
            self.assertEqual(vault.read()['password'], 'synthetic-secret-test')
            vault.update(password='')
            self.assertEqual(vault.read()['password'], '')


if __name__ == '__main__':
    unittest.main()
