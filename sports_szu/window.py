"""Native Qt window containing the local HTML interface, not an external browser."""
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QUrl, Qt, QTimer
from PySide6.QtGui import QIcon, QAction
from PySide6.QtNetwork import QNetworkProxy
from PySide6.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon, QMenu,
    QMessageBox, QDialog, QVBoxLayout, QTextEdit, QCheckBox, QDialogButtonBox, QLabel)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from .terms import TERMS, TERMS_VERSION

ICON = Path(__file__).with_name('assets') / 'noodles.ico'


class TermsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('使用须知 · 自动预约助手')
        self.setWindowIcon(QIcon(str(ICON)))
        self.resize(560, 650)
        layout = QVBoxLayout(self)
        label = QLabel('请阅读并选择同意或退出；同意前不会启动预约和订单托管。')
        label.setWordWrap(True)
        layout.addWidget(label)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(TERMS)
        layout.addWidget(text)
        self.agreement = QCheckBox('我已阅读并理解以上规则与自动化风险')
        layout.addWidget(self.agreement)
        buttons = QDialogButtonBox()
        self.accept_button = buttons.addButton('同意并进入', QDialogButtonBox.ButtonRole.AcceptRole)
        self.accept_button.setEnabled(False)
        buttons.addButton('不同意，退出', QDialogButtonBox.ButtonRole.RejectRole)
        self.agreement.toggled.connect(self.accept_button.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class Signals(QObject):
    hide = Signal()
    quit = Signal()
    notification = Signal(str)


class LocalRequests(QWebEngineUrlRequestInterceptor):
    def __init__(self, origin, parent):
        super().__init__(parent)
        self.origin = origin

    def interceptRequest(self, info):
        url = info.requestUrl()
        if url.scheme() in ('data', 'about'):
            return
        allowed = QUrl(self.origin)
        if url.scheme() != allowed.scheme() or url.host() != allowed.host() or url.port() != allowed.port():
            info.block(True)


class LocalPage(QWebEnginePage):
    def __init__(self, profile, origin, parent):
        super().__init__(profile, parent)
        self.origin = origin

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.toString() == 'about:blank' or (
            url.scheme() == 'http' and url.host() == '127.0.0.1'
            and url.port() == QUrl(self.origin).port())

    def createWindow(self, window_type):
        return None


class MainWindow(QMainWindow):
    def __init__(self, server, service, worker=None):
        super().__init__()
        self.service, self.worker = service, worker
        self.exiting = False
        self.exit_prompt = None
        self.hide_notified = False
        self.setWindowTitle('自动预约助手')
        self.setWindowIcon(QIcon(str(ICON)))
        self.resize(470, 920)
        self.setMinimumSize(360, 520)
        QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        self.view = QWebEngineView(self)
        # Unnamed profile is off-the-record: no browser account, history or secrets on disk.
        self.profile = QWebEngineProfile(self.view)
        self.interceptor = LocalRequests(server.origin, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.page = LocalPage(self.profile, server.origin, self.view)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setCentralWidget(self.view)
        self.signals = Signals(self)
        self.signals.hide.connect(self.hide_to_tray)
        self.signals.quit.connect(self.request_quit)
        self.signals.notification.connect(self.notify)
        service.close_window = self.signals.hide.emit
        service.quit_app = self.signals.quit.emit
        service.notification = self.signals.notification.emit
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip('自动预约助手 · 后台运行中')
            menu = QMenu(self)
            open_action = QAction('打开预约助手', self)
            open_action.triggered.connect(self.restore)
            menu.addAction(open_action)
            exit_action = QAction('退出（停止托管）', self)
            exit_action.triggered.connect(self.request_quit)
            menu.addAction(exit_action)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.restore() if reason in (
                QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
            self.tray.show()
        self.view.loadFinished.connect(self.loaded)
        self.view.load(QUrl(server.launch_url()))

    def loaded(self, ok):
        if not ok:
            self.statusBar().showMessage('界面加载失败，请退出后重启；后台状态请到官网核实。')

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def notify(self, text):
        if self.tray:
            self.tray.showMessage('自动预约助手', text, QSystemTrayIcon.MessageIcon.Information, 6000)

    def hide_to_tray(self):
        if self.tray:
            self.hide()
            if not self.hide_notified:
                self.notify('已收起到托盘，预约与订单托管继续运行。退出请使用托盘菜单。')
                self.hide_notified = True
        else:
            self.showMinimized()

    def closeEvent(self, event):
        if self.exiting:
            event.accept()
        else:
            event.ignore()
            self.hide_to_tray()

    def request_quit(self):
        if self.exiting or self.exit_prompt:
            return
        count = sum(not job.get('terminal') for job in self.service.store.all('orders'))
        box = QMessageBox(self)
        box.setWindowTitle('退出预约助手')
        box.setText(f'当前有 {count} 笔未结束的托管订单。\n退出后将停止抢场、付款和自动取消。确定退出？')
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        self.exit_prompt = box
        def finished(result):
            self.exit_prompt = None
            box.deleteLater()
            if result == QMessageBox.StandardButton.Yes:
                self.begin_shutdown()
        box.finished.connect(finished)
        box.open()

    def begin_shutdown(self):
        if self.exiting:
            return
        self.exiting = True
        self.service.stop.set()
        self.service.booking_stop.set()
        self.view.setEnabled(False)
        self.show()
        self.statusBar().showMessage('正在退出：等待当前请求完成，避免中途打断付款或取消…')
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.timeout.connect(self.finish_shutdown)
        self.shutdown_timer.start(100)
        self.finish_shutdown()

    def finish_shutdown(self):
        if self.worker and self.worker.is_alive():
            return
        if self.tray:
            self.tray.hide()
        self.shutdown_timer.stop()
        QApplication.instance().quit()
