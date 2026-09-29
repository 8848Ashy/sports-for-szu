import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import uuid
import webbrowser

from .api import INDEX, LoginRequired, SchoolAPI
from .engine import Engine
from .login import sign_in
from .models import Plan, SHANGHAI, money, now
from .security import SingleInstance, Vault, data_dir
from .store import Store


class App:
    def __init__(self, root, directory):
        self.root, self.directory = root, directory
        self.vault, self.store = Vault(directory), Store(directory / 'state.sqlite')
        self.events, self.actions = queue.Queue(), queue.Queue()
        self.stop = threading.Event()
        self.engine = None
        self.tray = None
        self.last_auto_login = 0
        self.last_tick = 0
        self.last_error_notice = 0
        root.title('深大预约助手 · 本地版')
        root.geometry('1100x780')
        root.minsize(900, 650)
        style = ttk.Style()
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Treeview', rowheight=30)
        root.configure(bg='#f0f2f5')
        style.configure('TButton', padding=(12, 7), font=('Microsoft YaHei UI', 10))
        style.configure('Accent.TButton', background='#1890ff', foreground='white')
        style.configure('Success.TButton', background='#52c41a', foreground='white')
        style.configure('Warn.TButton', background='#fa8c16', foreground='white')
        style.configure('Danger.TButton', background='#ff4d4f', foreground='white')
        style.configure('Card.TLabelframe', background='white')
        style.configure('Card.TLabelframe.Label', background='white', foreground='#666')
        self.status = tk.StringVar(value='启动中；不会自动启用新预约计划')
        header = ttk.Frame(root)
        header.pack(fill='x', padx=20, pady=(16, 5))
        ttk.Label(header, text='◉  深大体育馆自动预约', font=('Microsoft YaHei UI', 18, 'bold')).pack(side='left')
        ttk.Label(header, text='本地安全版', foreground='#888').pack(side='right', pady=6)
        ttk.Label(root, textvariable=self.status).pack(anchor='w', padx=20, pady=(0, 10))
        notebook = ttk.Notebook(root)
        notebook.pack(fill='both', expand=True, padx=16)
        self.plans_tab = ttk.Frame(notebook, padding=12)
        self.orders_tab = ttk.Frame(notebook, padding=12)
        self.settings_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.plans_tab, text='预约计划')
        notebook.add(self.orders_tab, text='我的场地')
        notebook.add(self.settings_tab, text='账号与安全')
        self.build_plans()
        self.build_orders()
        self.build_settings()
        self.log = ScrolledText(root, height=6, state='disabled', font=('Microsoft YaHei UI', 9))
        self.log.pack(fill='x', padx=16, pady=10)
        root.protocol('WM_DELETE_WINDOW', self.hide)
        self.refresh()
        threading.Thread(target=self.worker, daemon=True, name='booking-worker').start()
        root.after(250, self.drain)
        self.setup_tray()

    def table(self, parent, columns, labels, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True, pady=8)
        table = ttk.Treeview(frame, columns=columns, show='headings', selectmode='browse')
        for col, label, width in zip(columns, labels, widths):
            table.heading(col, text=label)
            table.column(col, width=width, minwidth=70)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=table.xview)
        table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        table.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return table

    def build_plans(self):
        bar = ttk.Frame(self.plans_tab)
        bar.pack(fill='x')
        actions = [('新增计划', lambda: self.plan_editor(), 'Accent.TButton'),
                   ('编辑选中计划', self.edit_plan, 'TButton'),
                   ('删除选中计划', self.delete_plan, 'Danger.TButton'),
                   ('⚡ 立即抢场', self.immediate_booking, 'Success.TButton'),
                   ('⏱ 开启定时抢场', lambda: self.scheduler(True), 'Warn.TButton'),
                   ('暂停新预约', lambda: self.scheduler(False), 'TButton')]
        for label, action, style_name in actions:
            ttk.Button(bar, text=label, command=action, style=style_name).pack(side='left', padx=(0, 5))
        ttk.Label(self.plans_tab, text='按北京时间执行；每个时段最多订一场。暂停新预约不会停止已有订单的自动取消。', wraplength=900).pack(anchor='w', pady=8)
        self.plan_table = self.table(self.plans_tab, ('name', 'sport', 'days', 'slots', 'next', 'target'),
                                     ('计划', '项目', '抢场星期', '目标时段', '下次执行', '预约使用日'),
                                     (160, 85, 100, 150, 160, 120))

    def immediate_booking(self):
        key = self.selection(self.plan_table)
        if not key:
            return
        plan_data = self.store.get('plans', key)
        if not plan_data:
            return
        plan = Plan(**plan_data).validate()
        if not self.engine:
            messagebox.showwarning('尚未登录', '请先在“账号与安全”完成官方登录。')
            return
        if not messagebox.askyesno('立即抢场', f'立即执行“{plan.name}”的第一个时段 {plan.slots[0]}？\n\n这会真实创建预约订单；自动付款仍按计划设置执行。'):
            return
        self.actions.put(('immediate', plan.id))

    def build_orders(self):
        bar = ttk.Frame(self.orders_tab)
        bar.pack(fill='x')
        ttk.Button(bar, text='使用场地（不再自动取消）', command=self.confirm_order).pack(side='left')
        ttk.Button(bar, text='取消选中订单', command=self.cancel_order).pack(side='left', padx=8)
        ttk.Button(bar, text='打开学校官网核实', command=lambda: webbrowser.open(INDEX + '#/sportVenue')).pack(side='left')
        self.history = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='显示历史', variable=self.history, command=self.refresh).pack(side='right')
        ttk.Label(self.orders_tab, text='只托管本软件新建的订单。确认使用 ≠ 入场核验；到期记录自动从当前列表隐藏，异常订单保留。', wraplength=900).pack(anchor='w', pady=8)
        self.order_table = self.table(self.orders_tab, ('venue', 'period', 'cutoff', 'state'),
                                      ('场地', '使用时间', '自动取消截止时间', '状态 / 待处理事项'),
                                      (230, 175, 155, 360))

    def build_settings(self):
        account = self.vault.read()
        self.user = tk.StringVar(value=account.get('username', ''))
        self.real_name = tk.StringVar(value=account.get('real_name', ''))
        self.password = tk.StringVar()
        self.remember = tk.BooleanVar(value=bool(account.get('password')))
        self.daily = tk.StringVar(value=f'{self.store.get("settings", "daily_limit", 6000) / 100:.2f}')
        frame = ttk.Frame(self.settings_tab)
        frame.pack(anchor='nw', fill='x')
        for index, (label, var) in enumerate([('学号 / 工号', self.user), ('姓名（预约所需）', self.real_name),
                                             ('登录密码（留空保留已存密码）', self.password), ('每日自动付款上限（元）', self.daily)]):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky='w', pady=8)
            ttk.Entry(frame, textvariable=var, width=38, show='●' if var is self.password else '').grid(row=index, column=1, sticky='w', padx=15)
        ttk.Checkbutton(frame, text='记住密码：仅当前 Windows 用户可解密；取消勾选会删除已保存密码', variable=self.remember).grid(row=4, column=0, columnspan=2, sticky='w', pady=10)
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Button(buttons, text='保存本地设置', command=self.save_account).pack(side='left')
        ttk.Button(buttons, text='打开官方登录窗口', command=self.login).pack(side='left', padx=8)
        ttk.Button(buttons, text='退出助手', command=self.quit).pack(side='left')
        ttk.Label(frame, text=('安全边界：不保存支付密码，不充值，不上传 Cookie；密码与 Cookie 由 Windows DPAPI 加密。\n'
                              '使用记住密码可尝试重新登录，但验证码仍需本人处理。\n'
                              '请勿分享本地数据目录或 HAR。订单记录在本机保存，不随代码上传。\n'
                              '关机、睡眠、断网、登录失效可能使自动取消失败；软件不能保证零损失。\n'
                              '关闭窗口优先收起到托盘；真正退出会停止所有托管。'),
                  justify='left', wraplength=880).grid(row=6, column=0, columnspan=2, sticky='w', pady=18)

    def save_account(self):
        try:
            username, real_name = self.user.get().strip(), self.real_name.get().strip()
            if not username or not real_name:
                raise ValueError('请填写学号和姓名')
            limit = money(self.daily.get())
            if not 0 < limit <= 100000:
                raise ValueError('每日上限需大于0且不超过1000元')
            old = self.vault.read()
            if username != old.get('username') and any(not j.get('terminal') for j in self.store.all('orders')):
                raise ValueError('还有未结束的托管订单，不能切换账号')
            password = (self.password.get() or (old.get('password', '') if username == old.get('username') else '')) if self.remember.get() else ''
            self.vault.update(username=username, real_name=real_name, password=password,
                              cookies=old.get('cookies', []) if username == old.get('username') else [])
            self.store.put('settings', 'daily_limit', limit)
            self.password.set('')
            self.actions.put(('reload', None))
            self.events.put(('log', '本地设置已加密保存；没有上传账号信息'))
            return True
        except Exception as exc:
            messagebox.showerror('设置未保存', str(exc))
            return False

    def login(self):
        transient_password = self.password.get()
        if self.save_account():
            if messagebox.askokcancel('官方登录', '请在学校官方页面登录上面配置的本人账号。不要切换为他人账号；验证码请手动完成。'):
                self.actions.put(('login', transient_password))

    def scheduler(self, enabled):
        if enabled and not self.vault.read().get('cookies'):
            messagebox.showwarning('尚未登录', '请先保存账号并完成官方登录')
            return
        self.store.put('settings', 'scheduler_enabled', enabled)
        self.events.put(('log', '定时抢场已开启' if enabled else '新预约已暂停；已有订单继续托管'))
        self.refresh()

    def selection(self, table):
        selection = table.selection()
        if not selection:
            messagebox.showinfo('请选择', '请先选中一行')
            return None
        return selection[0]

    def confirm_order(self):
        key = self.selection(self.order_table)
        if key and messagebox.askyesno('确认使用', '确认使用这笔订单？之后不会因未确认而自动取消。未支付的订单仍需完成支付。'):
            self.actions.put(('confirm', key))

    def cancel_order(self):
        key = self.selection(self.order_table)
        if key and messagebox.askyesno('取消预约', '确定取消选中的这一笔订单？退款结果以学校系统为准。'):
            self.actions.put(('cancel', key))

    def edit_plan(self):
        key = self.selection(self.plan_table)
        if key:
            self.plan_editor(Plan(**self.store.get('plans', key)))

    def delete_plan(self):
        key = self.selection(self.plan_table)
        if key and messagebox.askyesno('删除计划', '删除此计划？已生成的订单仍会继续托管。'):
            self.store.delete_plan(key)
            self.refresh()

    def plan_editor(self, plan=None):
        plan = plan or Plan(id=uuid.uuid4().hex)
        win = tk.Toplevel(self.root)
        win.title('预约计划 · 时间均为北京时间')
        win.geometry('720x740')
        win.transient(self.root)
        win.grab_set()
        form = ttk.Frame(win, padding=18)
        form.pack(fill='both', expand=True)
        fields = [('name', '计划名称', plan.name), ('sport', '项目', '健身房' if plan.sport == 'gym' else '羽毛球'),
                  ('campus', '校区', '粤海' if plan.campus == '1' else '丽湖'), ('venue', '场馆准确名称', plan.venue),
                  ('fire_time', '抢场时间', plan.fire_time), ('day_offset', '预约几天后（0=当天，1=次日）', str(plan.day_offset)),
                  ('slots', '时段（逗号分隔，每段订一场）', ','.join(plan.slots)),
                  ('preferred', '羽毛球优先场地', plan.preferred), ('avoided', '羽毛球靠后场地（仍可预约）', plan.avoided),
                  ('cancel_minutes', '未确认时，开始前几分钟取消', str(plan.cancel_minutes)),
                  ('max_cents', '单笔付款上限（元）', f'{plan.max_cents / 100:.2f}'),
                  ('valid_from', '生效日期 YYYY-MM-DD（可空）', plan.valid_from),
                  ('valid_until', '结束日期 YYYY-MM-DD（可空）', plan.valid_until)]
        variables, widgets = {}, {}
        for row, (key, title, value) in enumerate(fields):
            ttk.Label(form, text=title).grid(row=row, column=0, sticky='w', pady=5)
            var = variables[key] = tk.StringVar(value=value)
            if key in ('sport', 'campus'):
                widget = ttk.Combobox(form, textvariable=var, state='readonly', values=['羽毛球', '健身房'] if key == 'sport' else ['粤海', '丽湖'])
            else:
                widget = ttk.Entry(form, textvariable=var)
            widget.grid(row=row, column=1, sticky='ew', padx=(16, 0), pady=5)
            widgets[key] = widget
        form.columnconfigure(1, weight=1)

        def sport_change(*_):
            gym = variables['sport'].get() == '健身房'
            for name in ('preferred', 'avoided'):
                widgets[name].configure(state='disabled' if gym else 'normal')
            if variables['campus'].get() == '粤海':
                variables['venue'].set('运动广场西馆一楼健身房' if gym else '运动广场东馆羽毛球场')
        widgets['sport'].bind('<<ComboboxSelected>>', sport_change)
        if plan.sport == 'gym':
            widgets['preferred'].configure(state='disabled')
            widgets['avoided'].configure(state='disabled')
        days_frame = ttk.Frame(form)
        days_frame.grid(row=13, column=0, columnspan=2, sticky='w', pady=10)
        ttk.Label(days_frame, text='抢场星期：').pack(side='left')
        days = []
        for index, label in enumerate('一二三四五六日'):
            variable = tk.BooleanVar(value=index in plan.weekdays)
            days.append(variable)
            ttk.Checkbutton(days_frame, text=label, variable=variable).pack(side='left', padx=4)
        enabled = tk.BooleanVar(value=plan.enabled)
        pay = tk.BooleanVar(value=plan.auto_pay)
        ttk.Checkbutton(form, text='启用此计划（还需主界面开启定时抢场）', variable=enabled).grid(row=14, column=0, columnspan=2, sticky='w')
        ttk.Checkbutton(form, text='抢到后自动用余额付款；未确认使用则按时间自动取消', variable=pay).grid(row=15, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Label(form, text='健身房不选择具体场地。规则变化或余额不足会暂停付款；不能保证抢到或及时退款。', wraplength=650).grid(row=16, column=0, columnspan=2, sticky='w', pady=8)

        def save():
            try:
                values = {key: var.get().strip() for key, var in variables.items()}
                values.update(id=plan.id, enabled=enabled.get(), auto_pay=pay.get(),
                              weekdays=[i for i, v in enumerate(days) if v.get()],
                              sport='gym' if values['sport'] == '健身房' else 'badminton',
                              campus='1' if values['campus'] == '粤海' else '2',
                              day_offset=int(values['day_offset']), cancel_minutes=int(values['cancel_minutes']),
                              max_cents=money(values['max_cents']),
                              slots=[s.strip() for s in values['slots'].replace('，', ',').split(',')])
                changed = Plan(**values).validate()
                self.store.put('plans', changed.id, changed.to_dict())
                win.destroy()
                self.refresh()
            except (ValueError, TypeError) as exc:
                messagebox.showerror('计划未保存', str(exc), parent=win)
        ttk.Button(form, text='保存计划', command=save).grid(row=17, column=1, sticky='e', pady=8)

    def refresh(self):
        def replace(table, rows):
            selected = table.selection()
            table.delete(*table.get_children())
            for key, values in rows:
                table.insert('', 'end', iid=key, values=values)
            if selected and table.exists(selected[0]):
                table.selection_set(selected[0])
        from datetime import datetime, timedelta
        moment = now()
        rows = []
        for plan in self.store.plans():
            fire = plan.next_fire(moment)
            rows.append((plan.id, (plan.name, '健身房' if plan.sport == 'gym' else '羽毛球',
                                  '、'.join('一二三四五六日'[i] for i in plan.weekdays), ','.join(plan.slots),
                                  fire.strftime('%m-%d %H:%M') if fire else '未启用 / 无后续日期',
                                  (fire.date() + timedelta(days=plan.day_offset)).isoformat() if fire else '—')))
        replace(self.plan_table, rows)
        rows = []
        for job in self.store.all('orders'):
            if not self.history.get() and job.get('terminal') and job['date'] < moment.date().isoformat():
                continue
            cutoff = datetime.fromtimestamp(job['start'] - job['cancel_minutes'] * 60, SHANGHAI)
            rows.append((job['id'], (job['venue'], job['date'] + ' ' + job['slot'],
                                     '已确认使用' if job['confirmed'] else cutoff.strftime('%m-%d %H:%M'), job['message'])))
        replace(self.order_table, rows)

    def emit(self, message):
        self.events.put(('notice', message))

    def reload_engine(self):
        account = self.vault.read()
        if account.get('cookies') and account.get('username'):
            self.engine = Engine(self.store, SchoolAPI(account['cookies']), account, self.emit)
        else:
            self.engine = None

    def worker(self):
        self.reload_engine()
        while not self.stop.is_set():
            try:
                try:
                    action, value = self.actions.get(timeout=0.3)
                except queue.Empty:
                    action, value = None, None
                if action == 'reload':
                    self.reload_engine()
                elif action == 'login':
                    self.events.put(('status', '请在官方浏览器窗口完成登录；托管暂时等待登录完成'))
                    try:
                        login_account = self.vault.read()
                        if value:
                            login_account['password'] = value
                        cookies = sign_in(login_account, interactive=True)
                        value = None
                        login_account = None
                        self.vault.update(cookies=cookies)
                        self.reload_engine()
                        self.events.put(('log', '登录已验证，Cookie 已加密保存'))
                    except Exception as exc:
                        # Playwright exceptions used to be collapsed into a generic
                        # message, making a browser flash look like a crash.
                        safe = str(exc).replace('\r', ' ').replace('\n', ' ')[:500]
                        self.events.put(('log', f'官方登录未完成（{type(exc).__name__}）：{safe}'))
                        self.events.put(('status', '官方登录失败；请根据日志处理后重试'))
                elif action in ('confirm', 'cancel'):
                    if not self.engine:
                        raise ValueError('请先登录')
                    if action == 'confirm':
                        self.engine.confirm(value)
                    else:
                        self.engine.request_cancel(value)
                    self.last_tick = 0
                elif action == 'immediate':
                    if not self.engine:
                        raise ValueError('请先登录')
                    plan_data = self.store.get('plans', value)
                    if not plan_data:
                        raise ValueError('计划不存在')
                    plan = Plan(**plan_data).validate()
                    self.engine.book(plan, plan.slots[0])
                    self.events.put(('log', '立即抢场任务已执行；请在“我的场地”核对订单'))
                if self.engine and time.monotonic() - self.last_tick >= 1:
                    self.last_tick = time.monotonic()
                    self.engine.tick()
                self.events.put(('status', ('托管运行中' if self.engine else '尚未登录') +
                                 (' · 定时抢场已开启' if self.store.get('settings', 'scheduler_enabled', False) else ' · 新预约暂停')))
            except LoginRequired:
                if self.engine:
                    self.engine.last_review = 0
                self.events.put(('status', '登录或接口检查失败；请打开官方登录窗口'))
                account = self.vault.read()
                today = now().date().isoformat()
                attempts = self.store.get('settings', 'login_attempts', {})
                count = attempts.get('count', 0) if attempts.get('date') == today else 0
                if account.get('password') and count < 3 and time.monotonic() - self.last_auto_login > 600:
                    self.last_auto_login = time.monotonic()
                    self.store.put('settings', 'login_attempts', {'date': today, 'count': count + 1})
                    try:
                        cookies = sign_in(account, interactive=False)
                        self.vault.update(cookies=cookies)
                        self.reload_engine()
                        self.events.put(('log', '已使用本地保存的密码刷新登录'))
                    except Exception:
                        self.emit('自动登录未完成，可能需要验证码；请手动登录，否则付款或取消可能失败')
                self.stop.wait(5)
            except Exception as exc:
                # Never print raw HTTP, browser exceptions, credentials or tracebacks to UI logs.
                if time.monotonic() - self.last_error_notice > 30:
                    self.last_error_notice = time.monotonic()
                    self.emit(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '操作未完成，请检查登录和网络，必要时到官网核实订单')
                self.stop.wait(2)

    def drain(self):
        for _ in range(100):
            try:
                kind, message = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'show':
                self.root.deiconify()
                self.root.lift()
            elif kind == 'quit':
                self.quit()
                return
            elif kind == 'status':
                self.status.set(message)
            else:
                self.log.configure(state='normal')
                self.log.insert('end', f'{now():%H:%M:%S}  {message}\n')
                if int(self.log.index('end-1c').split('.')[0]) > 200:
                    self.log.delete('1.0', '50.0')
                self.log.see('end')
                self.log.configure(state='disabled')
                if kind == 'notice':
                    self.root.bell()
                    if self.tray:
                        try:
                            self.tray.notify(message, '深大预约助手')
                        except Exception:
                            pass
        self.refresh()
        self.root.after(1000, self.drain)

    def setup_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            image = Image.new('RGB', (64, 64), '#7b1742')
            ImageDraw.Draw(image).ellipse((14, 14, 50, 50), outline='white', width=5)
            self.tray = pystray.Icon('sports-for-szu', image, '深大预约助手', menu=pystray.Menu(
                pystray.MenuItem('显示窗口', lambda *_: self.events.put(('show', '')), default=True),
                pystray.MenuItem('退出', lambda *_: self.events.put(('quit', '')))))
            self.tray.run_detached()
        except Exception:
            self.tray = None

    def hide(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()  # Never silently exit if tray support is unavailable.

    def quit(self):
        active = sum(not j.get('terminal') for j in self.store.all('orders'))
        if not messagebox.askyesno('退出会停止托管', f'仍有 {active} 笔未结束订单。退出后无法自动付款或取消；确定退出？'):
            return
        self.stop.set()
        if self.tray:
            self.tray.stop()
        self.root.destroy()
        # Worker is daemonized: all side-effect intents were committed before dispatch.


def main():
    root = tk.Tk()
    guard = None
    try:
        directory = data_dir()
        guard = SingleInstance(directory)
        App(root, directory)
        root.mainloop()
    except RuntimeError as exc:
        messagebox.showerror('无法启动', str(exc) + '\n\n如果你刚才重复双击了 start.cmd，请先关闭已有的深大预约助手窗口。')
    except Exception as exc:
        messagebox.showerror('无法启动', '启动失败：' + type(exc).__name__ + ': ' + str(exc)[:300] +
                             '\n\n请先运行 setup.cmd；不要手动删除未结束订单数据。')
    finally:
        if guard:
            guard.close()
