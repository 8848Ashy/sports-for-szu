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


def pay_with_password(cookies, order_id, password, headless=True, trace=lambda message: None):
    """Attempt one official-page payment, returning a non-secret status string.

    This intentionally stops on ambiguous page structure or result. Callers must
    persist an attempt marker before invoking it and must verify order state later.
    """
    from playwright.sync_api import sync_playwright

    password = str(password or '')
    if not password.isdigit() or not 4 <= len(password) <= 12:
        trace('体育经费密码支付：密码格式不支持，未提交')
        return '支付密码格式不支持；未提交付款'
    trace('体育经费密码支付：启动官方支付页面')
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
                trace('体育经费密码支付：登录会话已失效')
                return '登录已失效'
            trace('体育经费密码支付：已打开预约记录，定位订单')
            # The table is populated asynchronously.  Waiting for the order
            # text avoids treating a still-empty grid as a payment failure.
            order_text = page.get_by_text(str(order_id), exact=False).first
            try:
                order_text.wait_for(state='visible', timeout=15000)
            except Exception:
                trace('体育经费密码支付：订单列表未加载，未提交')
                return '订单列表未加载；未提交付款'
            # Payment UI selectors vary by deployment; fail closed if the exact
            # order cannot be uniquely located rather than paying another row.
            rows = page.locator('tr').filter(has_text=str(order_id))
            if rows.count() != 1:
                trace('体育经费密码支付：无法唯一定位订单，未提交')
                return '无法唯一定位订单；未提交付款'
            row = rows.first
            # Current SZU UI exposes only a row-level “未支付” link.  The
            # sports-fund/campus-card buttons appear in the modal opened by it.
            unpaid = row.locator('a').filter(has_text='未支付').first
            if not _visible(unpaid):
                trace('体育经费密码支付：订单没有未支付入口，未提交')
                return '找不到该订单的未支付入口；未提交付款'
            unpaid.click()
            sports_button = page.get_by_text('(体育经费)支付', exact=True).first
            try:
                sports_button.wait_for(state='visible', timeout=8000)
            except Exception:
                # A few deployments omit the parentheses in the button text.
                sports_button = page.get_by_text('体育经费', exact=False).first
                if not _visible(sports_button):
                    trace('体育经费密码支付：未打开支付方式弹窗，未提交')
                    return '未打开支付方式弹窗；未提交付款'
            trace('体育经费密码支付：已打开未支付订单，选择体育经费')
            # The school may open the payment panel in a new tab.
            before = list(context.pages)
            try:
                with page.expect_response(
                    lambda response: response.url.endswith('/sportVenue/setYyinfoToMoney.do'),
                    timeout=15000) as response_info:
                    sports_button.click()
                payment_response = response_info.value
                try:
                    payment_result = payment_response.json()
                except Exception:
                    payment_result = {}
                safe_code = str(payment_result.get('code', ''))[:40]
                safe_msg = str(payment_result.get('msg', ''))[:200]
                trace(f'体育经费支付预处理 HTTP {payment_response.status} code={safe_code!r} msg={safe_msg!r}')
                if safe_code != '0':
                    return (f'学校拒绝体育经费支付预处理：{safe_msg or safe_code}；未提交付款')
            except Exception as exc:
                trace(f'体育经费支付预处理未返回结果：{type(exc).__name__}；未提交付款')
                return '体育经费支付预处理失败；未提交付款'
            page.wait_for_timeout(1000)
            pages = [item for item in context.pages if item not in before]
            if pages:
                page = pages[-1]
            # A successful pre-processing response submits the official form
            # to the university payment host.  Wait briefly for that navigation
            # before looking for the password controls, and fail closed if the
            # page goes somewhere unexpected.
            try:
                page.wait_for_url('**://olepay.szu.edu.cn/**', timeout=10000)
            except Exception:
                pass
            payment_host = urlparse(page.url).hostname
            if payment_host not in ('olepay.szu.edu.cn', 'ehall.szu.edu.cn'):
                trace('体育经费密码支付：支付页面域名异常，未提交密码')
                return '支付页面域名异常；未提交密码'
            trace('体育经费密码支付：已选择体育经费支付')
            if not _click_first(page, ['#btnNext', 'text=下一步', 'button:has-text("下一步")']):
                trace('体育经费密码支付：未找到下一步，未提交')
                return '支付页面结构未识别；未提交付款'
            trace('体育经费密码支付：已进入密码步骤')
            page.wait_for_timeout(500)
            password_input = page.locator('#password, input[type=password], .password-input').first
            if not _visible(password_input):
                trace('体育经费密码支付：未发现密码输入步骤，未提交')
                return '未发现支付密码输入步骤；未提交付款'
            buttons = _find_keyboard_buttons(page, password)
            if not buttons:
                trace('体育经费密码支付：虚拟键盘不完整，未提交')
                return '未识别完整的支付虚拟键盘；未提交付款'
            for button in buttons:
                button.click()
                page.wait_for_timeout(100)
            trace('体育经费密码支付：密码已输入，准备确认')
            if not _click_first(page, ['#btnPay', 'text=确认支付', 'button:has-text("确认支付")']):
                trace('体育经费密码支付：未找到确认支付按钮，未提交')
                return '未找到确认支付按钮；未提交付款'
            trace('体育经费密码支付：已提交确认，等待结果')
            page.wait_for_timeout(2500)
            body = page.locator('body').inner_text(timeout=3000)
            if any(text in body for text in ('支付成功', '缴费成功', '付款成功')):
                trace('体育经费密码支付：页面显示支付成功')
                return '密码支付成功，等待订单状态核实'
            if any(text in body for text in ('支付失败', '密码错误', '余额不足')):
                trace('体育经费密码支付：页面显示支付失败')
                return '密码支付失败，请到官网核实'
            trace('体育经费密码支付：结果不确定，停止重试')
            return '支付结果不确定，请到官网核实；不会自动重试'
        finally:
            browser.close()
