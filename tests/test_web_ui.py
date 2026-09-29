"""Opt-in browser integration against an isolated in-memory account and fake school API."""
import os
from pathlib import Path
import threading
import unittest
from sports_szu.webserver import UIServer
from test_desktop_service import ServiceFixture


@unittest.skipUnless(os.environ.get('SZU_TEST_BROWSER') == '1', 'Set SZU_TEST_BROWSER=1 after installing Chromium')
class BrowserTests(ServiceFixture, unittest.TestCase):
    def test_userscript_layout_and_settings_workflow(self):
        from playwright.sync_api import sync_playwright, expect
        server = UIServer(self.service)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        output = Path('artifacts')
        output.mkdir(exist_ok=True)
        try:
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 450, 'height': 880})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(server.launch_url())
                page.get_by_text('测试用户 (test-user)', exact=True).wait_for()
                self.assertEqual(page.get_by_role('tab').count(), 4)
                self.assertFalse(page.locator('#schedule-form').is_visible())
                self.assertEqual(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), True)
                page.screenshot(path=str(output / 'ui-booking.png'))

                page.locator('[data-field="slots"]').click()
                page.locator('[data-slot="21:00-22:00"]').click()
                page.locator('#booking-form').get_by_role('button', name='保存设置').click()
                expect(page.locator('#summary-slots')).to_contain_text('21:00-22:00')
                self.assertIn('21:00-22:00', self.store.get('settings', 'booking_v2')['config']['slots'])

                page.get_by_role('tab', name='定时预约', exact=True).click()
                page.locator('#schedule-form input[name="name"]').fill('每周羽毛球')
                page.locator('#schedule-form button[type="submit"]').click()
                page.locator('#schedules-list .schedule-title').wait_for()
                self.assertEqual(len(self.store.get('settings', 'schedules_v2')), 1)
                page.screenshot(path=str(output / 'ui-schedule.png'))
                page.locator('#schedules-list [data-schedule-action="toggle"]').click()
                expect(page.locator('#schedules-list .tag')).to_have_text('已暂停')
                self.assertFalse(self.store.get('settings', 'schedules_v2')[0]['enabled'])

                page.get_by_role('tab', name='托管设置', exact=True).click()
                page.locator('#care-form input[name="cancel_minutes"]').fill('90')
                page.locator('#care-form button[type="submit"]').click()
                page.wait_for_timeout(200)
                self.assertEqual(self.store.get('settings', 'care_v2')['cancel_minutes'], 90)
                page.screenshot(path=str(output / 'ui-care.png'))

                page.get_by_role('tab', name='预约', exact=True).click()
                page.locator('#start').click()
                name, payload = self.service.commands.get(timeout=2)
                self.assertEqual(name, 'booking_start')
                self.service.command(name, payload)
                self.service.poll_task()  # Only FakeAPI, never the school.
                page.get_by_role('tab', name='我的场地', exact=True).click()
                page.locator('#orders-list [data-order]').first.wait_for()
                page.screenshot(path=str(output / 'ui-orders.png'))
                page.locator('[data-order-action="confirm"]').first.click()
                name, payload = self.service.commands.get(timeout=2)
                self.assertEqual(name, 'order_confirm')
                self.service.command(name, payload)
                self.assertTrue(self.store.get('orders', payload['id'])['confirmed'])

                page.locator('[data-order-action="cancel"]').first.click()
                expect(page.locator('#confirm-dialog')).to_be_visible()
                self.assertTrue(self.service.commands.empty())
                page.locator('#confirm-dialog button[value="no"]').click()
                self.assertTrue(self.service.commands.empty())
                page.locator('[data-order-action="cancel"]').first.click()
                page.locator('#confirm-yes').click()
                name, payload = self.service.commands.get(timeout=2)
                self.assertEqual(name, 'order_cancel')
                self.service.command(name, payload)
                self.service.engine.tick(run_schedules=False)
                self.assertEqual(self.api.cancel_calls, 1)

                page.locator('#theme').click()
                page.screenshot(path=str(output / 'ui-dark.png'))
                self.assertEqual(page.locator('html').get_attribute('data-theme'), 'dark')
                page.set_viewport_size({'width': 360, 'height': 640})
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                self.assertEqual(errors, [])
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
