"""Official login form simulation; no real credentials or school connections."""
import os
import unittest


@unittest.skipUnless(os.environ.get('SZU_TEST_BROWSER') == '1', 'Browser integration opt-in')
class LoginFlowTests(unittest.TestCase):
    def test_saved_password_submits_once_and_captcha_waits(self):
        from playwright.sync_api import sync_playwright
        from sports_szu.login import prepare_login
        template = """<script>window.submissions=0</script>
            <div hidden><input id="username"><input id="password"></div>
            <form onsubmit="event.preventDefault();window.submissions++">
              <input id="username"><input id="password" type="password">%s
              <button type="submit">Login</button>
            </form>"""
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page()
            account = {'username': 'test-user', 'password': 'SYNTHETIC-PASSWORD'}
            for extra, expected in (('', 'submitted'), ('<input id="captchaResponse">', 'captcha')):
                html = template % extra
                page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
                page.goto('https://authserver.szu.edu.cn/authserver/login')
                messages = []
                self.assertEqual(prepare_login(page, account, messages.append), expected)
                self.assertEqual(page.locator('input#password:visible').input_value(), account['password'])
                self.assertEqual(page.evaluate('window.submissions'), 1 if expected == 'submitted' else 0)
                self.assertFalse(any(account['password'] in message for message in messages))
                page.unroute('**/*')
            page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=template % ''))
            page.goto('https://authserver.szu.edu.cn/authserver/login')
            self.assertEqual(prepare_login(page, {'username': 'test-user'}), 'manual')
            self.assertEqual(page.evaluate('window.submissions'), 0)
            page.goto('https://other.example/login')
            self.assertEqual(prepare_login(page, account), 'other_origin')
            self.assertEqual(page.locator('input#password:visible').input_value(), '')
            browser.close()
