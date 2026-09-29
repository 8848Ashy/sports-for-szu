"""Desktop controller. The web interface gets only explicitly selected public fields."""
import copy
from datetime import datetime, timedelta
import queue
import threading
import time
import uuid
from .api import ApiError, LoginRequired, SchoolAPI
from .catalog import SPORTS, VENUES
from .engine import Engine, owner_key
from .login import sign_in
from .models import Plan, SHANGHAI, now
from .scheduling import Schedule

ACTIVE = ('queued', 'running', 'waiting_login', 'paused_restart')


class DesktopService:
    def __init__(self, store, vault, clock=now):
        self.store, self.vault, self.clock = store, vault, clock
        self.lock = threading.RLock()
        self.commands = queue.Queue(maxsize=64)
        self.stop = threading.Event()
        self.booking_stop = threading.Event()
        self.engine = None
        self.login_busy = False
        self.login_results = queue.Queue()
        self.logs = []
        self.serial = 0
        self.connection = '尚未验证登录'
        self.balance_cents = None
        self.next_login_attempt = 0
        self.next_balance_check = 0
        self.next_care_check = 0
        self.notification = lambda text: None
        self.close_window = lambda: None
        self.quit_app = lambda: self.stop.set()
        self._migrate()
        for task in store.all('tasks'):
            if task['status'] in ('running', 'waiting_login'):
                task['status'] = 'queued' if task.get('resume_on_restart') else 'paused_restart'
                store.put('tasks', task['id'], task)
        self.log('预约助手已就绪（桌面端）', 'success')

    def _migrate(self):
        previous = self.store.plans()
        if self.store.get('settings', 'booking_v2') is None:
            plan = previous[-1] if previous else Plan('current', slots=['19:00-20:00', '20:00-21:00'])
            self.store.put('settings', 'booking_v2', {'config': plan.to_dict(),
                'target_date': (self.clock().date() + timedelta(days=plan.day_offset)).isoformat()})
        if self.store.get('settings', 'care_v2') is None:
            plan = Plan(**self.store.get('settings', 'booking_v2')['config'])
            self.store.put('settings', 'care_v2', {key: getattr(plan, key) for key in
                           ('auto_pay', 'max_cents', 'cancel_minutes', 'preferred', 'avoided')})
        if self.store.get('settings', 'schedules_v2') is None:
            migrated = []
            for plan in previous:
                migrated.append(Schedule(plan.id, plan.name, plan.to_dict(), fire_time=plan.fire_time,
                    start_date=plan.valid_from or self.clock().date().isoformat(), end_date=plan.valid_until,
                    weekdays=plan.weekdays, day_offset=plan.day_offset,
                    enabled=plan.enabled and bool(self.store.get('settings', 'scheduler_enabled', False))).validate().to_dict())
            self.store.put('settings', 'schedules_v2', migrated)

    def log(self, message, level='info', notify=False):
        with self.lock:
            self.serial += 1
            self.logs.append({'id': self.serial, 'time': self.clock().strftime('%H:%M:%S'),
                              'message': message, 'level': level})
            self.logs = self.logs[-150:]
        if notify:
            self.notification(message)

    def notify(self, message):
        self.log(message, 'info', True)

    def snapshot(self):
        moment = self.clock()
        account = self.vault.read()
        own = owner_key(account.get('username', ''))
        schedules = []
        for data in self.store.get('settings', 'schedules_v2', []):
            item = Schedule(**data)
            fire = item.next_fire(moment)
            schedules.append({**data, 'next_fire': fire.isoformat() if fire else None,
                              'next_target': item.booking_date(fire) if fire else None})
        # Serialize only application data: no account dict, cookies or password.
        with self.lock:
            return {'server_time': moment.isoformat(), 'account': {
                        'username': account.get('username', ''), 'real_name': account.get('real_name', ''),
                        'remembered': bool(account.get('password')), 'has_session': bool(account.get('cookies'))},
                    'connection': self.connection, 'login_busy': self.login_busy, 'balance_cents': self.balance_cents,
                    'booking': self.store.get('settings', 'booking_v2'), 'care': self.store.get('settings', 'care_v2'),
                    'daily_limit': self.store.get('settings', 'daily_limit', 6000), 'schedules': schedules,
                    'tasks': [t for t in self.store.all('tasks') if t.get('owner') == own][-40:],
                    'orders': [j for j in self.store.all('orders') if j.get('owner') == own],
                    'logs': copy.deepcopy(self.logs),
                    'sports': {key: value[0] for key, value in SPORTS.items()}, 'venues': VENUES}

    def action(self, name, payload):
        if not isinstance(payload, dict):
            raise ValueError('操作参数不合法')
        if name == 'booking_save':
            plan = Plan(**payload['config']).validate()
            target = self._date(payload['target_date'])
            self.store.put('settings', 'booking_v2', {'config': plan.to_dict(), 'target_date': target})
            self.log('预约设置已保存')
        elif name == 'care_save':
            config = self.store.get('settings', 'booking_v2')['config']
            allowed = ('auto_pay', 'max_cents', 'cancel_minutes', 'preferred', 'avoided')
            changes = {key: payload[key] for key in allowed}
            Plan(**{**config, **changes}).validate()
            daily = payload['daily_limit']
            if type(daily) is not int or not 0 < daily <= 100000:
                raise ValueError('每日上限需为0–1000元之间的有效金额')
            self.store.put('settings', 'care_v2', changes)
            self.store.put('settings', 'daily_limit', daily)
            self.log('托管设置已保存，对之后创建的订单生效', 'success')
        elif name == 'schedule_save':
            item = Schedule(**payload).validate()
            with self.lock:
                values = self.store.get('settings', 'schedules_v2', [])
                values = [x for x in values if x['id'] != item.id] + [item.to_dict()]
                self.store.put('settings', 'schedules_v2', values)
            self.log('定时预约已保存：' + item.name, 'success')
        elif name in ('schedule_toggle', 'schedule_delete'):
            with self.lock:
                values = self.store.get('settings', 'schedules_v2', [])
                if name == 'schedule_delete':
                    values = [x for x in values if x['id'] != payload['id']]
                else:
                    for x in values:
                        if x['id'] == payload['id']:
                            x['enabled'] = bool(payload['enabled'])
                self.store.put('settings', 'schedules_v2', values)
            self.log('定时设置已更新；已有订单继续托管')
        elif name == 'account_save':
            self._save_account(payload)
        elif name == 'booking_stop':
            # Interrupt polling promptly; never interrupt an in-flight financial request.
            self.booking_stop.set()
            self.commands.put_nowait((name, payload))
        elif name in ('booking_start', 'task_resume', 'login', 'order_confirm', 'order_cancel',
                      'order_time', 'order_stop_pay', 'order_retry_pay', 'balance_refresh'):
            if name == 'login' and self.login_busy:
                raise ValueError('登录窗口已打开，请先完成登录')
            self.commands.put_nowait((name, copy.deepcopy(payload)))
        elif name == 'logs_clear':
            with self.lock:
                self.logs.clear()
        elif name == 'window_hide':
            self.close_window()
        elif name == 'quit':
            self.quit_app()
        else:
            raise ValueError('不支持的操作')
        return {'ok': True}

    @staticmethod
    def _date(value):
        return datetime.strptime(value, '%Y-%m-%d').date().isoformat()

    def _save_account(self, values):
        username, real_name = str(values['username']).strip(), str(values['real_name']).strip()
        if not username or not real_name:
            raise ValueError('请填写学号/工号和姓名')
        old = self.vault.read()
        if old.get('username') != username:
            if any(not j.get('terminal') for j in self.store.all('orders')) or any(t['status'] in ACTIVE for t in self.store.all('tasks')):
                raise ValueError('仍有托管订单或预约任务，请先处理后再切换账号')
        password = ''
        if values.get('remember'):
            password = str(values.get('password') or (old.get('password', '') if old.get('username') == username else ''))
        self.vault.update(username=username, real_name=real_name, password=password,
                          cookies=old.get('cookies', []) if old.get('username') == username else [])
        self.commands.put_nowait(('reload', {}))
        self.log('本地设置已加密保存；没有上传账号信息', 'success')

    def reload(self):
        account = self.vault.read()
        self.engine = Engine(self.store, SchoolAPI(account['cookies']), account, self.notify, self.clock) if account.get('cookies') else None
        self.connection = '已保存登录，等待核验' if self.engine else '请登录'
        self.next_balance_check = 0

    def launch_login(self, interactive, transient_password=''):
        if self.login_busy:
            return
        self.login_busy = True
        account = self.vault.read()
        if transient_password:
            account['password'] = transient_password
        self.log('官方登录窗口已打开，请完成登录和验证码' if interactive else '正在使用本地凭据刷新登录')
        def work():
            try:
                self.login_results.put(('ok', sign_in(account, interactive=interactive)))
            except Exception as exc:
                # Never expose Playwright's raw exceptions: they can include filled values.
                kind = type(exc).__name__
                text = '登录未完成，请检查官方页面、网络或 Chromium 安装'
                if 'strict mode violation' in str(exc):
                    text = '登录表单出现多个匹配项，请手动填写后重试'
                elif 'Timeout' in kind or 'timeout' in str(exc).lower():
                    text = '登录页面等待超时，请检查学校网站连接后重试'
                self.login_results.put(('error', f'{text}（{kind}）'))
        threading.Thread(target=work, daemon=True, name='official-login').start()

    def _login_expired(self):
        self.connection = '登录失效，请重新登录'
        account = self.vault.read()
        day = self.clock().date().isoformat()
        attempts = self.store.get('settings', 'login_attempts', {})
        count = attempts.get('count', 0) if attempts.get('date') == day else 0
        if account.get('password') and count < 3 and self.clock().timestamp() >= self.next_login_attempt and not self.login_busy:
            self.next_login_attempt = self.clock().timestamp() + 600
            self.store.put('settings', 'login_attempts', {'date': day, 'count': count + 1})
            self.launch_login(False)

    def create_task(self, config, target, identity=None, source='manual', duration=0, resume=False):
        if identity and self.store.get('tasks', identity):
            return
        account = self.vault.read()
        if not account.get('username'):
            raise ValueError('请先设置账号并登录')
        plan = Plan(**{**config, **self.store.get('settings', 'care_v2')}).validate()
        self._date(target)
        if target < self.clock().date().isoformat():
            raise ValueError('预约日期已过去，请修改日期')
        task = {'id': identity or uuid.uuid4().hex, 'owner': owner_key(account['username']),
                'config': plan.to_dict(), 'target_date': target, 'status': 'queued', 'attempts': 0,
                'completed': [], 'next_poll': 0, 'source': source, 'created': self.clock().timestamp(),
                'deadline': self.clock().timestamp() + duration * 60 if duration else None,
                'resume_on_restart': resume, 'message': '等待开始'}
        if not self.store.get('tasks', task['id']):
            self.store.put('tasks', task['id'], task)
            self.log(f'开始预约：{target} · {SPORTS[plan.sport][0]} · {", ".join(plan.slots)}', 'success')
        self.booking_stop.clear()

    def check_schedules(self):
        for data in self.store.get('settings', 'schedules_v2', []):
            schedule = Schedule(**data)
            fire = schedule.due(self.clock())
            if fire:
                self.create_task(schedule.config, schedule.booking_date(fire),
                                 f'schedule:{schedule.id}:{fire.date()}', schedule.name,
                                 schedule.duration_minutes, schedule.resume_on_restart)

    def poll_task(self):
        if not self.engine or self.booking_stop.is_set():
            return
        tasks = [t for t in self.store.all('tasks') if t['status'] in ('queued', 'running', 'waiting_login')
                 and t['owner'] == owner_key(self.engine.account['username'])]
        if not tasks:
            return
        task = tasks[0]
        moment = self.clock().timestamp()
        if moment < task['next_poll']:
            return
        plan = Plan(**task['config'])
        max_bookings = min(2, len(plan.slots))
        if task['attempts'] >= plan.max_retries or (task['deadline'] and moment >= task['deadline']):
            task.update(status='done', message='已到查询次数或蹲退时长上限')
            self.store.put('tasks', task['id'], task)
            self.log(task['message'], 'warning')
            return
        task.update(status='running', attempts=task['attempts'] + 1, message='查询可预约场地…')
        self.store.put('tasks', task['id'], task)
        if task['attempts'] == 1 or task['attempts'] % 10 == 0:
            self.log(f'第 {task["attempts"]} 次查询（{len(task["completed"])}/{max_bookings}）')
        try:
            for slot in plan.slots:
                if self.booking_stop.is_set() or len(task['completed']) >= max_bookings:
                    break
                if slot in task['completed']:
                    continue
                self.engine.book(plan, slot, task['target_date'], self.booking_stop.is_set)
                run = self.store.get('runs', self.engine.run_key(plan, task['target_date'], slot), {})
                if run.get('status') == 'done' or run.get('reason') == '该时段已有预约':
                    task['completed'].append(slot)
                    self.log(f'{slot} 已预约（{len(task["completed"])}/{max_bookings}）', 'success')
                elif run.get('status') in ('unknown', 'submitted', 'stopped'):
                    task.update(status='stopped', message=run.get('reason') or '预约结果不明，请到官网核实')
                    break
            if len(task['completed']) >= max_bookings:
                task.update(status='done', message=f'已完成 {len(task["completed"])} 个时段')
                self.log(task['message'], 'success', True)
            elif task['status'] == 'running':
                task['message'] = '等待空场，继续查询…'
        except LoginRequired:
            task.update(status='waiting_login', message='等待重新登录')
            self._login_expired()
        except ApiError:
            task['message'] = '本轮查询失败，将按间隔重试'
            if task['attempts'] <= 3 or task['attempts'] % 20 == 0:
                self.log(task['message'], 'warning')
        finally:
            task['next_poll'] = self.clock().timestamp() + (max(5, plan.retry_interval) if task['status'] == 'waiting_login' else plan.retry_interval)
            if self.booking_stop.is_set():
                task.update(status='stopped', message='已停止预约')
            self.store.put('tasks', task['id'], task)

    def command(self, name, payload):
        if name == 'reload':
            self.reload()
        elif name == 'login':
            self.launch_login(True, str(payload.get('password', '')))
        elif name == 'booking_start':
            if not self.engine:
                raise ValueError('请先完成官方登录')
            if any(t['status'] in ('queued', 'running', 'waiting_login') for t in self.store.all('tasks')):
                raise ValueError('已有预约任务运行，请先停止当前任务')
            current = self.store.get('settings', 'booking_v2')
            self.create_task(current['config'], current['target_date'])
        elif name == 'booking_stop':
            for task in self.store.all('tasks'):
                if task['status'] in ACTIVE:
                    task.update(status='stopped', message='已停止预约')
                    self.store.put('tasks', task['id'], task)
            self.booking_stop.clear()
            self.log('预约已停止；已有订单继续托管')
        elif name == 'task_resume':
            task = self.store.get('tasks', payload['id'])
            if not task or task['status'] != 'paused_restart':
                raise ValueError('该任务无需恢复')
            if task['owner'] != owner_key(self.vault.read().get('username', '')):
                raise ValueError('请切回任务所属账号')
            task.update(status='queued', next_poll=0)
            self.store.put('tasks', task['id'], task)
        elif name.startswith('order_'):
            if not self.engine:
                raise ValueError('请先登录')
            with self.engine.lock:
                job = self.store.get('orders', payload['id'])
                if not job or job['owner'] != owner_key(self.engine.account['username']) or job.get('terminal'):
                    raise ValueError('订单已结束或不属于当前账号')
                if name == 'order_confirm':
                    self.engine.confirm(job['id'])
                elif name == 'order_cancel':
                    self.engine.request_cancel(job['id'])
                elif name == 'order_time':
                    timestamp = datetime.fromisoformat(payload['cancel_at']).replace(tzinfo=SHANGHAI).timestamp()
                    if not self.clock().timestamp() < timestamp <= job['start'] - 60 * 60 or job.get('cancel_attempt'):
                        raise ValueError('取消时间必须在未来且至少提前60分钟；已提交取消的订单不能修改')
                    self.store.patch_order(job['id'], cancel_at=timestamp, confirmed=False, message='自动取消时间已更新')
                elif name == 'order_stop_pay':
                    self.store.patch_order(job['id'], auto_pay=False, message='已停止此单自动付款')
                elif name == 'order_retry_pay':
                    if job.get('pay_attempt') or job.get('cancel_attempt') or job.get('cancel_requested'):
                        raise ValueError('已发出付款或取消请求，请先到官网核实，不能重复提交')
                    self.store.patch_order(job['id'], auto_pay=True, message='等待重新核验余额支付')
                self.engine.last_review = 0
        elif name == 'balance_refresh':
            self.next_balance_check = 0

    def step(self):
        while True:
            try:
                status, data = self.login_results.get_nowait()
            except queue.Empty:
                break
            self.login_busy = False
            if status == 'ok':
                self.vault.update(cookies=data)
                self.reload()
                self.log('登录已完成，Cookie 已加密保存', 'success')
            else:
                self.log(data, 'error', True)
        try:
            name, payload = self.commands.get_nowait()
            self.command(name, payload)
        except queue.Empty:
            pass
        except (ValueError, KeyError, TypeError) as exc:
            self.log(str(exc) if isinstance(exc, ValueError) else '设置数据不完整，请重新保存', 'error')
        if self.engine:
            try:
                self.engine.tick(run_schedules=False)
                if self.clock().timestamp() >= self.next_balance_check:
                    self.next_balance_check = self.clock().timestamp() + 60
                    self.balance_cents = self.engine.api.balance()
                    self.connection = '已登录'
            except LoginRequired:
                self._login_expired()
            except ApiError:
                self.connection = '网络异常，等待重试'
        self.check_schedules()
        self.poll_task()

    def run(self):
        self.reload()
        while not self.stop.is_set():
            try:
                self.step()
            except Exception:
                self.log('任务处理异常，请检查设置或在官网核实订单', 'error')
                self.stop.wait(3)
            self.stop.wait(0.2)
