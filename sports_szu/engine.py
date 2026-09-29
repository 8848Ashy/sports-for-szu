"""Serial state machine. Persist intent before every financial write."""
from datetime import datetime, timedelta
import hashlib
import threading
from .api import ApiError, Ambiguous, LoginRequired
from .models import SHANGHAI, money, now


def owner_key(username):
    return hashlib.sha256(username.encode()).hexdigest()


class Engine:
    def __init__(self, store, api, account, notify=lambda message: None, clock=now):
        self.store, self.api, self.account = store, api, account
        self.notify, self.clock = notify, clock
        self.last_review = 0
        # UI confirmation and cancellation dispatch share this lock.
        self.lock = threading.RLock()
        for run in self.store.all('runs'):
            if run.get('status') == 'submitted':
                run['status'] = 'unknown'
                self.store.put('runs', run['id'], run)
                self.notify('发现中断时尚未核实的预约请求；已停止该时段重试，请到官网核实')

    def confirm(self, key):
        with self.lock:
            job = self.store.get('orders', key)
            if not job or job.get('cancel_attempt') or job.get('cancel_requested') or job.get('terminal'):
                raise ValueError('订单已在取消或已结束，不能确认使用')
            if job['owner'] != owner_key(self.account['username']):
                raise ValueError('请切回创建订单的账号')
            self.store.patch_order(key, confirmed=True, message='已确认使用，不再自动取消')
            self.last_review = 0

    def request_cancel(self, key):
        with self.lock:
            job = self.store.get('orders', key)
            if not job or job.get('terminal') or job['owner'] != owner_key(self.account['username']):
                raise ValueError('订单不可操作或账号不匹配')
            self.store.patch_order(key, cancel_requested=True, message='等待核实并取消')
            self.last_review = 0

    def tick(self):
        with self.lock:
            # Outstanding orders always take precedence over making new ones.
            review = self.clock().timestamp() - self.last_review >= 10
            if review:
                self.last_review = self.clock().timestamp()
            for job in self.store.all('orders') if review else []:
                if job['owner'] != owner_key(self.account['username']) or job.get('terminal'):
                    continue
                try:
                    self.process(job)
                except LoginRequired:
                    raise
                except (ApiError, ValueError, KeyError, TypeError):
                    self.store.patch_order(job['id'], message='状态核实失败，请打开官网检查；不会重复付款')
            if self.store.get('settings', 'scheduler_enabled', False):
                for plan in self.store.plans():
                    if plan.due(self.clock()):
                        for slot in plan.slots:
                            self.book(plan, slot)

    def book(self, plan, slot):
        moment = self.clock()
        date = (moment.date() + timedelta(days=plan.day_offset)).isoformat()
        account_key = owner_key(self.account['username'])
        # Dedupe across overlapping plans as well as application restarts.
        key = f'{account_key}:{date}:{plan.sport}:{plan.campus}:{slot}'
        run = self.store.get('runs', key)
        if run and run.get('status') in ('submitted', 'unknown', 'done', 'stopped'):
            return
        if run and moment.timestamp() - run.get('last_try', 0) < 5:
            return
        start = datetime.fromisoformat(f'{date}T{slot.split("-")[0]}').replace(tzinfo=SHANGHAI)
        if moment >= start - timedelta(minutes=plan.cancel_minutes + 5):
            self.store.put('runs', key, {'id': key, 'status': 'stopped', 'reason': '距取消截止时间过近'})
            return
        # Never create a duplicate of an existing reservation in that time period.
        existing = self.api.orders()
        for row in existing:
            if str(row.get('YYRGH')) == self.account['username'] and row.get('YYSF') == '预约人' and row.get('YYZT') == 'CG_YY':
                if self.same_period(row.get('YYSJD', ''), date, slot):
                    self.store.put('runs', key, {'id': key, 'status': 'stopped', 'reason': '该时段已有预约'})
                    return
        rooms = self.api.rooms(plan, date, slot)
        record = {'id': key, 'status': 'waiting', 'last_try': moment.timestamp()}
        self.store.put('runs', key, record)
        # Strict priority and serial requests; only explicit business rejection
        # permits trying the next court. An ambiguous write stops this time slot.
        for room in rooms:
            record['status'] = 'submitted'
            self.store.put('runs', key, record)
            try:
                result = self.api.book(plan, date, slot, room, self.account)
            except (ApiError, LoginRequired):
                record['status'] = 'unknown'
                self.store.put('runs', key, record)
                self.notify('预约请求结果不明，请到官网核实；该时段已停止重试，未自动付款')
                return
            if str(result.get('code')) == '0':
                order_id = result.get('data', {}).get('DHID')
                if not order_id:
                    record['status'] = 'unknown'
                    self.store.put('runs', key, record)
                    self.notify('预约返回缺少订单号，请到官网核实；未自动付款')
                    return
                order_id = str(order_id)
                job = {'id': order_id, 'owner': account_key, 'date': date, 'slot': slot,
                       'start': start.timestamp(), 'created': moment.timestamp(), 'room': str(room['WID']),
                       'sport': '007' if plan.sport == 'gym' else '001', 'campus': plan.campus,
                       'venue': room['CGBM_DISPLAY'] + ' · ' + room.get('CDMC', ''),
                       'cancel_minutes': plan.cancel_minutes, 'max_cents': plan.max_cents,
                       'auto_pay': plan.auto_pay, 'confirmed': False, 'message': '预约成功，待核实订单'}
                self.store.put('orders', order_id, job)
                record.update(status='done', order_id=order_id)
                self.store.put('runs', key, record)
                self.notify('新预约已生成，请在“我的场地”确认使用；未确认将按设置取消')
                self.process(job)
                return
            # Only a structured non-success business response permits another attempt.
            if not isinstance(result.get('code'), str) or not result.get('msg'):
                record['status'] = 'unknown'
                self.store.put('runs', key, record)
                self.notify('预约返回异常，请人工核实；已停止该时段重试')
                return
        record['status'] = 'waiting'
        self.store.put('runs', key, record)

    @staticmethod
    def same_period(period, date, slot):
        try:
            first, last = period.split('~')
            start = datetime.fromisoformat(first.strip())
            end = datetime.fromisoformat(last.strip())
            return start.date().isoformat() == date and f'{start:%H:%M}-{end:%H:%M}' == slot
        except (ValueError, TypeError):
            return False

    def row(self, job):
        matches = [r for r in self.api.orders() if str(r.get('DHID')) == job['id']
                   and str(r.get('YYRGH')) == self.account['username'] and r.get('YYSF') == '预约人']
        if len(matches) != 1:
            raise ApiError('指定订单不存在或账号不匹配')
        row = matches[0]
        if (not row.get('WID') or str(row.get('CDWID')) != job['room']
                or str(row.get('XMDM')) != job['sport'] or str(row.get('XQWID')) != job['campus']
                or not self.same_period(row.get('YYSJD', ''), job['date'], job['slot'])):
            raise ApiError('订单信息与本地记录不符，暂停操作')
        return row

    def process(self, job):
        key = job['id']
        row = self.row(job)
        moment = self.clock()
        timestamp = moment.timestamp()
        if row.get('YYZT') == 'CG_QX':
            if job.get('cancel_attempt') and job.get('paid'):
                balance = self.api.balance()
                delta = balance - job.get('balance_before_cancel', balance)
                message = f'已取消；余额变化 {delta / 100:.2f}元（非独立退款凭证，请核对）'
            else:
                message = '已取消（网站已确认）'
            self.store.patch_order(key, terminal=True, message=message)
            return
        if row.get('YYZT') == 'CG_WC':
            self.store.patch_order(key, terminal=True, message='已完成')
            return
        if row.get('YYZT') != 'CG_YY':
            raise ApiError('未知预约状态')
        if str(row.get('SFZF')) not in ('0', '1'):
            raise ApiError('未知支付状态')
        paid = str(row['SFZF']) == '1'
        if paid:
            self.store.patch_order(key, paid=True)
        cutoff = job['start'] - job['cancel_minutes'] * 60
        if job.get('cancel_requested') or (not job['confirmed'] and timestamp >= cutoff):
            if timestamp >= job['start'] - 30 * 60:
                self.store.patch_order(key, message='已错过安全取消窗口，请立即到官网处理')
                if not job.get('missed_alert'):
                    self.notify('有订单错过取消窗口，请立即到官网检查；不能保证退款')
                    self.store.patch_order(key, missed_alert=True)
                return
            if job.get('cancel_attempt'):
                self.store.patch_order(key, message='取消结果未确认，请官网核实；不会盲目重发')
                return
            balance = self.api.balance()
            self.store.patch_order(key, cancel_attempt=True, balance_before_cancel=balance, paid=paid,
                                   message='取消请求已登记，等待网站确认')
            try:
                self.api.cancel(row['WID'])
            finally:
                self.notify('已尝试取消订单，请查看“我的场地”的网站核实结果')
            return  # Next tick queries authoritative state even after a successful HTTP response.
        if job.get('cancel_attempt'):
            return
        if not job['confirmed'] and timestamp >= cutoff - 60 * 60 and not job.get('reminded'):
            self.notify('预约将在约一小时内自动取消，请在“我的场地”选择使用或取消')
            self.store.patch_order(key, reminded=True)
        if paid:
            self.store.patch_order(key, message='已支付 · ' + ('已确认使用' if job['confirmed'] else '等待确认使用'))
            return
        if not job['auto_pay']:
            self.store.patch_order(key, message='等待手动支付；自动取消规则仍生效')
            return
        if job.get('pay_attempt'):
            self.store.patch_order(key, message='付款结果尚未确认，请官网核实；不会重复扣款')
            return
        if timestamp - job['created'] >= 40 * 60:
            self.store.patch_order(key, auto_pay=False, message='已超过40分钟，停止自动付款')
            return
        quote = self.api.quote(row['WID'])
        amount = quote['gxje']
        if (quote['zuizong'] != 0 or quote['tuipiao'] != amount or quote['tksyje'] < amount
                or amount > job['max_cents'] or money(row.get('TRANAMT')) != amount):
            self.store.patch_order(key, auto_pay=False, message='余额不足、超上限或金额不一致；未付款')
            self.notify('自动付款已暂停：余额不足、超上限或报价不一致，请检查订单')
            return
        token = self.api.token()
        fresh = self.row(job)
        if fresh.get('YYZT') != 'CG_YY' or str(fresh.get('SFZF')) != '0':
            return
        # Re-evaluate deadline after network roundtrips; do not pay into cancellation.
        if not job['confirmed'] and self.clock().timestamp() >= cutoff:
            return
        limit = self.store.get('settings', 'daily_limit', 6000)
        if not self.store.reserve_payment(key, amount, moment.date().isoformat(), limit):
            self.store.patch_order(key, message='每日付款额度不足，未付款')
            return
        try:
            self.api.pay(row['WID'], quote, token)
        finally:
            self.notify('余额付款已尝试，正在核实；请以订单实际支付状态为准')
