"""Official browser login only. Captchas are left to the user, never bypassed."""
import time
from urllib.parse import urlparse, quote
from .api import INDEX, SchoolAPI, LoginRequired

LOGIN = 'https://authserver.szu.edu.cn/authserver/login?service=' + quote(INDEX, safe='')


def sign_in(account, interactive=True):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=not interactive)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(LOGIN, wait_until='domcontentloaded', timeout=30000)
            # Enter saved secrets only on the exact official HTTPS login origin.
            url = urlparse(page.url)
            if url.scheme == 'https' and url.hostname == 'authserver.szu.edu.cn':
                if page.locator('#username').count():
                    page.locator('#username').fill(account.get('username', ''))
                if account.get('password') and page.locator('#password').count():
                    page.locator('#password').fill(account['password'])
                if not interactive:
                    # No captcha solver; a failed login is surfaced for manual completion.
                    page.locator('#password').press('Enter')
            deadline = time.monotonic() + (180 if interactive else 25)
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise LoginRequired('登录窗口已关闭')
                url = urlparse(page.url)
                if url.scheme == 'https' and url.hostname == 'ehall.szu.edu.cn' and url.path.startswith('/qljfwapp/'):
                    cookies = await_cookies(context)
                    SchoolAPI(cookies).orders()  # Reject a mere URL redirect with no usable session.
                    return cookies
                page.wait_for_timeout(500)
            raise LoginRequired('未完成登录；如有验证码，请在登录窗口手动完成')
        finally:
            browser.close()


def await_cookies(context):
    # Do not persist CAS/authserver cookies or unrelated browser data.
    return [c for c in context.cookies([INDEX])
            if c.get('domain', '').lstrip('.') in ('ehall.szu.edu.cn', 'szu.edu.cn')]
