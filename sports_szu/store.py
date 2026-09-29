import json
from pathlib import Path
import sqlite3
import threading
from .models import Plan


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        ''')
        self.db.commit()

    def put(self, table, key, value):
        assert table in ('plans', 'runs', 'orders', 'settings', 'tasks')
        with self.lock, self.db:
            self.db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?)',
                            (key, json.dumps(value, ensure_ascii=False)))

    def get(self, table, key, default=None):
        assert table in ('plans', 'runs', 'orders', 'settings', 'tasks')
        with self.lock:
            row = self.db.execute(f'SELECT data FROM {table} WHERE id=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def all(self, table):
        assert table in ('plans', 'runs', 'orders', 'settings', 'tasks')
        with self.lock:
            return [json.loads(x[0]) for x in self.db.execute(f'SELECT data FROM {table} ORDER BY rowid')]

    def delete_plan(self, key):
        with self.lock, self.db:
            self.db.execute('DELETE FROM plans WHERE id=?', (key,))

    def plans(self):
        return [Plan(**data).validate() for data in self.all('plans')]

    def patch_order(self, key, **values):
        with self.lock:
            row = self.get('orders', key)
            if row is None:
                raise ValueError('找不到订单')
            row.update(values)
            self.put('orders', key, row)
            return row

    def reserve_payment(self, key, cents, day, daily_limit):
        # Global process lock + SQLite commit before dispatch. Unknown payments
        # continue consuming budget: a timeout never grants a second debit.
        with self.lock:
            row = self.get('orders', key)
            spent = sum(x.get('reserved_cents', 0) for x in self.all('orders') if x.get('pay_day') == day)
            if row.get('pay_attempt') or spent + cents > daily_limit:
                return False
            self.patch_order(key, pay_attempt=True, reserved_cents=cents, pay_day=day,
                             message='付款请求已登记，等待核实')
            return True

    def close(self):
        with self.lock:
            self.db.close()
