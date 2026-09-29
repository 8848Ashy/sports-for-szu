"""Loopback-only UI with one-use launch token, HttpOnly session and origin checks."""
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

ASSETS = Path(__file__).with_name('web')


class UIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service):
        super().__init__(('127.0.0.1', 0), Handler)
        self.service = service
        self.launch_token = secrets.token_urlsafe(32)
        self.session_token = secrets.token_urlsafe(32)
        self.origin = f'http://127.0.0.1:{self.server_port}'

    def launch_url(self):
        # Generate a new single-use link only for the local launcher/tray.
        self.launch_token = secrets.token_urlsafe(32)
        return self.origin + '/launch?token=' + self.launch_token


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Launch URL and credentials must never reach access logs.

    def host_ok(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def authenticated(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            value = cookies.get('szu_ui')
            return bool(value and secrets.compare_digest(value.value, self.server.session_token))
        except Exception:
            return False

    def send_headers(self, status, content_type='application/json; charset=utf-8', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def reply(self, status, data):
        self.send_headers(status)
        try:
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.host_ok():
            return self.reply(403, {'error': '无效的本机地址'})
        url = urlsplit(self.path)
        if url.path == '/launch':
            token = parse_qs(url.query).get('token', [''])[0]
            if not token or not self.server.launch_token or not secrets.compare_digest(token, self.server.launch_token):
                return self.reply(403, {'error': '启动链接已失效，请从托盘重新打开'})
            self.server.launch_token = ''
            self.send_headers(303, extra={'Location': '/', 'Set-Cookie': f'szu_ui={self.server.session_token}; HttpOnly; SameSite=Strict; Path=/'})
            return
        if not self.authenticated():
            return self.reply(401, {'error': '请从 start.cmd 或系统托盘打开预约助手'})
        if url.path == '/api/state':
            try:
                return self.reply(200, self.server.service.snapshot())
            except Exception:
                return self.reply(500, {'error': '无法读取本地数据，请检查文件权限'})
        assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if url.path not in assets:
            return self.reply(404, {'error': '没有这个页面'})
        name, content_type = assets[url.path]
        self.send_headers(200, content_type)
        self.wfile.write((ASSETS / name).read_bytes())

    def do_POST(self):
        if not self.host_ok() or not self.authenticated() or self.headers.get('Origin') != self.server.origin:
            return self.reply(403, {'error': '请求来源未授权'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': '只接受JSON操作'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 32768:
                return self.reply(413, {'error': '操作参数过大'})
            body = json.loads(self.rfile.read(size))
            if urlsplit(self.path).path != '/api/action':
                return self.reply(404, {'error': '未知操作地址'})
            return self.reply(200, self.server.service.action(body['action'], body.get('payload', {})))
        except (ValueError, KeyError, TypeError) as exc:
            return self.reply(400, {'error': str(exc) if isinstance(exc, ValueError) else '设置数据不完整'})
        except Exception:
            return self.reply(500, {'error': '操作失败，请检查本地权限或稍后重试'})
