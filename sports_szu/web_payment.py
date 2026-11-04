"""Interactive school-page payment via the official UI; never replay uncertain submits."""
import sys
from pathlib import Path
from urllib.parse import urlparse

BOOKING_URL = 'https://ehall.szu.edu.cn/qljfwapp/sys/lwSzuCgyy/index.do#/myBooking'


def _visible(locator):
    try:
        return locator.is_visible(timeout=500)
    except Exception:
        return False


def _click_first(page, selectors, timeout=1500):
    for selector in selectors:
        try:
            candidate = page.locator(selector).first
            if candidate.is_visible(timeout=timeout):
                candidate.click()
                return True
        except Exception:
            continue
    return False


def _find_keyboard_buttons(page, password):
    """Resolve every digit before clicking any key, so partial entry is avoided."""
    buttons = []
    for digit in password:
        found = None
        selectors = [
            f'.key-button.key-{digit}', f'button.key-{digit}',
            f'.keyboard-key:has-text("{digit}")',
            f'.num-key:has-text("{digit}")', f'.key-item:has-text("{digit}")',
        ]
        for selector in selectors:
            candidate = page.locator(selector).first
            if _visible(candidate):
                found = candidate
                break
        if found is None:
            try:
                for candidate in page.locator('button, [role=button], .key, td').all():
                    if _visible(candidate) and candidate.text_content().strip() == digit:
                        found = candidate
                        break
            except Exception:
                pass
        if found is None:
            return None
        buttons.append(found)
    return buttons


def pay_with_password(cookies, order_id, password, headless=True):
    """Attempt one official-page payment, returning a non-secret status string.

    This intentionally stops on ambiguous page structure or result. Callers must
    persist an attempt marker before invoking it and must verify order state later.
    """
    from playwright.sync_api import sync_playwright

    password = str(password or '')
    if not password.isdigit() or not 4 <= len(password) <= 12:
        return '支付密码格式不支持；未提交付款'
    with sync_playwright() as runtime:
        options = {'channel': 'msedge'} if getattr(sys, 'frozen', False) else {}
        browser = runtime.chromium.launch(headless=headless, **options)
        try:
            context = browser.new_context()
            browser_cookies = []
            for item in cookies:
                if isinstance(item, dict):
                    name, value = item.get('name'), item.get('value')
                    domain, path = item.get('domain', ''), item.get('path', '/')
                else:
                    name, value = item.name, item.value
                    domain, path = item.domain, item.path
                if name and domain.lstrip('.') in ('ehall.szu.edu.cn', 'szu.edu.cn'):
                    browser_cookies.append({'name': name, 'value': value,
                        'domain': '.ehall.szu.edu.cn', 'path': path or '/', 'secure': True})
            context.add_cookies(browser_cookies)
            page = context.new_page()
            page.goto(BOOKING_URL, wait_until='domcontentloaded', timeout=30000)
            if urlparse(page.url).hostname != 'ehall.szu.edu.cn':
                return '登录已失效'
            # Payment UI selectors vary by deployment; fail closed if the exact
            # order cannot be uniquely located rather than paying another row.
            rows = page.locator('tr').filter(has_text=str(order_id))
            if rows.count() != 1:
                return '无法唯一定位订单；未提交付款'
            row = rows.first
            pay_button = row.get_by_text('体育经费', exact=False)
            if pay_button.count() != 1:
                return '找不到该订单的体育经费支付入口；未提交付款'
            # The school may open the payment panel in a new tab.
            before = list(context.pages)
            pay_button.click()
            page.wait_for_timeout(1000)
            pages = [item for item in context.pages if item not in before]
            if pages:
                page = pages[-1]
            if not _click_first(page, ['#btnNext', 'text=下一步', 'button:has-text("下一步")']):
                return '支付页面结构未识别；未提交付款'
            page.wait_for_timeout(500)
            password_input = page.locator('#password, input[type=password], .password-input').first
            if not _visible(password_input):
                return '未发现支付密码输入步骤；未提交付款'
            buttons = _find_keyboard_buttons(page, password)
            if not buttons:
                return '未识别完整的支付虚拟键盘；未提交付款'
            for button in buttons:
                button.click()
                page.wait_for_timeout(100)
            if not _click_first(page, ['#btnPay', 'text=确认支付', 'button:has-text("确认支付")']):
                return '未找到确认支付按钮；未提交付款'
            page.wait_for_timeout(2500)
            body = page.locator('body').inner_text(timeout=3000)
            if any(text in body for text in ('支付成功', '缴费成功', '付款成功')):
                return '密码支付成功，等待订单状态核实'
            if any(text in body for text in ('支付失败', '密码错误', '余额不足')):
                return '密码支付失败，请到官网核实'
            return '支付结果不确定，请到官网核实；不会自动重试'
        finally:
            browser.close()
