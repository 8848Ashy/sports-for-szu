"""Official browser login only. Captchas are left to the user, never bypassed."""
import time
from pathlib import Path
import sys
from urllib.parse import urlparse, quote
from .api import INDEX, LoginRequired

LOGIN = 'https://authserver.szu.edu.cn/authserver/login?service=' + quote(INDEX, safe='')


def sign_in(account, interactive=True):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        try:
            # Portable builds use the installed Microsoft Edge, not a browser download.
            options = {'channel': 'msedge'} if getattr(sys, 'frozen', False) or not Path(runtime.chromium.executable_path).is_file() else {}
            browser = runtime.chromium.launch(headless=not interactive, **options)
        except Exception as exc:
            raise RuntimeError('登录浏览器启动失败。请重新运行 setup.cmd 安装 Chromium；原始错误：' + str(exc)[:180]) from exc
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(LOGIN, wait_until='domcontentloaded', timeout=30000)
            # Enter saved secrets only on the exact official HTTPS login origin.
            url = urlparse(page.url)
            if url.scheme == 'https' and url.hostname == 'authserver.szu.edu.cn':
                # The CAS page contains several tabs/forms with duplicate IDs.
                # Fill only the visible, first matching field; strict locators
                # otherwise abort before the user can complete captcha.
                usernames = page.locator('input#username:visible')
                passwords = page.locator('input#password:visible')
                if usernames.count():
                    usernames.first.fill(account.get('username', ''))
                if account.get('password') and passwords.count():
                    passwords.first.fill(account['password'])
                if not interactive:
                    # No captcha solver; a failed login is surfaced for manual completion.
                    if passwords.count():
                        passwords.first.press('Enter')
            deadline = time.monotonic() + (180 if interactive else 25)
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise LoginRequired('登录窗口已关闭')
                url = urlparse(page.url)
                if url.scheme == 'https' and url.hostname == 'ehall.szu.edu.cn' and url.path.startswith('/qljfwapp/'):
                    cookies = await_cookies(context)
                    if not cookies:
                        raise LoginRequired('已跳转但没有取得 ehall Cookie，请重新登录')
                    return cookies
                page.wait_for_timeout(500)
            raise LoginRequired('未完成登录；如有验证码，请在登录窗口手动完成')
        finally:
            browser.close()


def await_cookies(context):
    # Do not persist CAS/authserver cookies or unrelated browser data.
    return [c for c in context.cookies([INDEX])
            if c.get('domain', '').lstrip('.') in ('ehall.szu.edu.cn', 'szu.edu.cn')]
