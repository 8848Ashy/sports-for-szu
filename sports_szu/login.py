"""Official browser login only. Captchas are left to the user, never bypassed."""
import time
from pathlib import Path
import sys
from urllib.parse import urlparse, quote
from .api import INDEX, LoginRequired

LOGIN = 'https://authserver.szu.edu.cn/authserver/login?service=' + quote(INDEX, safe='')

def prepare_login(page, account, progress=lambda message: None):
    """Fill the visible official form and submit once when no captcha is pending."""
    url = urlparse(page.url)
    if url.scheme != 'https' or url.hostname != 'authserver.szu.edu.cn':
        return 'other_origin'
    usernames = page.locator('input#username:visible')
    passwords = page.locator('input#password:visible')
    try:
        usernames.first.wait_for(state='visible', timeout=8000)
        passwords.first.wait_for(state='visible', timeout=8000)
    except Exception:
        progress('请在官方窗口选择账号密码登录并完成登录')
        return 'manual'
    if not usernames.count() or not passwords.count():
        progress('请在官方窗口选择账号密码登录并完成登录')
        return 'manual'
    usernames.first.fill(account.get('username', ''))
    password = account.get('password')
    if not password:
        progress('没有保存登录密码，请在官方窗口输入密码并登录')
        return 'manual'
    passwords.first.fill(password)
    # The school's username blur can asynchronously make its captcha field visible.
    passwords.first.focus()
    page.wait_for_timeout(800)
    url = urlparse(page.url)
    if url.scheme != 'https' or url.hostname != 'authserver.szu.edu.cn':
        return 'other_origin'
    captcha = page.locator(
        'input[name*="captcha" i]:visible, input[id*="captcha" i]:visible, '
        'input[name*="verifycode" i]:visible, input[id*="verifycode" i]:visible, '
        'input[name*="yzm" i]:visible, input[id*="yzm" i]:visible')
    if any(not captcha.nth(i).input_value().strip() for i in range(captcha.count())):
        progress('账号密码已填写；学校要求验证码，请完成后点击登录')
        return 'captcha'
    if not account.get('username'):
        progress('请在官方窗口填写账号并登录')
        return 'manual'
    passwords.first.press('Enter')
    progress('账号密码已填写并提交，正在等待学校登录结果')
    return 'submitted'

def sign_in(account, interactive=True, progress=lambda message: None):
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
            status = prepare_login(page, account, progress)
            if status == 'captcha' and not interactive:
                raise LoginRequired('学校要求验证码，请打开官方登录窗口完成')
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
