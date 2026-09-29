"""Visible Qt integration with fake accounts and no school requests."""
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from test_desktop_service import ServiceFixture
from sports_szu.webserver import UIServer


@unittest.skipUnless(os.environ.get('SZU_TEST_NATIVE') == '1', 'Opt-in visible native window test')
class NativeWindowTests(ServiceFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait_until(self, condition, seconds=10):
        from PySide6.QtTest import QTest
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(30)
        self.fail('Native UI timed out')

    def js(self, window, script):
        result = []
        window.page.runJavaScript(script, lambda value: result.append(value))
        self.wait_until(lambda: bool(result))
        return result[0]

    def test_terms_require_explicit_agreement_and_allow_exit(self):
        from sports_szu.window import TermsDialog
        from PySide6.QtWidgets import QDialog
        dialog = TermsDialog()
        dialog.show()
        self.app.processEvents()
        Path('artifacts').mkdir(exist_ok=True)
        dialog.grab().save('artifacts/native-terms.png')
        self.assertFalse(dialog.accept_button.isEnabled())
        dialog.agreement.setChecked(True)
        self.assertTrue(dialog.accept_button.isEnabled())
        dialog.reject()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_declining_terms_starts_no_store_or_worker(self):
        from sports_szu.desktop import main
        from PySide6.QtWidgets import QDialog
        with patch('sports_szu.desktop.SingleInstance'), patch('sports_szu.desktop.Store') as store, \
             patch('sports_szu.window.TermsDialog.exec', return_value=QDialog.DialogCode.Rejected):
            main()
            store.assert_not_called()

    def test_native_content_cancel_hide_restore_and_single_exit_prompt(self):
        from sports_szu.window import MainWindow
        from PySide6.QtWidgets import QMessageBox
        server = UIServer(self.service)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.service.command('booking_start', {})
        self.service.poll_task()
        window = MainWindow(server, self.service)
        window.show()
        try:
            self.wait_until(lambda: self.js(window, "document.querySelector('#user-name')?.textContent.includes('测试用户')"))
            self.assertEqual(self.js(window, "document.querySelectorAll('[role=tab]').length"), 4)
            self.assertEqual(window.windowTitle(), '自动预约助手')
            self.assertTrue(window.profile.isOffTheRecord())
            self.js(window, "document.querySelector('[data-tab=orders]').click()")
            self.js(window, "document.querySelector('[data-order-action=cancel]').click()")
            self.assertTrue(self.js(window, "document.querySelector('#confirm-dialog').open"))
            self.assertTrue(self.service.commands.empty())
            self.js(window, "document.querySelector('#confirm-yes').click()")
            self.wait_until(lambda: not self.service.commands.empty())
            name, payload = self.service.commands.get_nowait()
            self.assertEqual(name, 'order_cancel')
            self.service.command(name, payload)
            self.service.engine.tick(run_schedules=False)
            self.assertEqual(self.api.cancel_calls, 1)
            self.wait_until(lambda: not self.js(window, "document.querySelector('#confirm-dialog').open"))
            from PySide6.QtTest import QTest
            QTest.qWait(350)
            Path('artifacts').mkdir(exist_ok=True)
            window.grab().save('artifacts/native-window.png')
            window.close()
            self.app.processEvents()
            self.assertFalse(self.service.stop.is_set())
            window.restore()
            self.assertTrue(window.isVisible())
            window.request_quit()
            first = window.exit_prompt
            window.request_quit()
            self.assertIs(window.exit_prompt, first)
            first.done(QMessageBox.StandardButton.No)
            self.assertFalse(self.service.stop.is_set())
            window.request_quit()
            window.exit_prompt.done(QMessageBox.StandardButton.Yes)
            self.assertTrue(self.service.stop.is_set())
        finally:
            if window.tray:
                window.tray.hide()
            window.exiting = True
            window.close()
            window.page.deleteLater()
            self.app.processEvents()
            server.shutdown()
            server.server_close()
