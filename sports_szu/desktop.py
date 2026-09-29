"""Native Windows entry point. No worker starts until the user accepts the terms."""
import threading
from .security import SingleInstance, Vault, data_dir
from .store import Store
from .service import DesktopService
from .webserver import UIServer


def main():
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from PySide6.QtGui import QIcon
    from .window import MainWindow, TermsDialog, ICON
    from .terms import TERMS_VERSION
    from .models import now
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('自动预约助手')
    app.setWindowIcon(QIcon(str(ICON)))
    app.setQuitOnLastWindowClosed(False)
    guard = server = store = worker = service = window = None
    try:
        directory = data_dir()
        guard = SingleInstance(directory)
        if TermsDialog().exec() != QDialog.DialogCode.Accepted:
            return
        store = Store(directory / 'state.sqlite')
        store.put('settings', 'terms_acceptance', {'version': TERMS_VERSION, 'at': now().isoformat()})
        service = DesktopService(store, Vault(directory))
        server = UIServer(service)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        worker = threading.Thread(target=service.run, name='booking-worker', daemon=True)
        window = MainWindow(server, service, worker)
        window.show()
        worker.start()
        app.exec()
    except Exception as exc:
        QMessageBox.critical(None, '无法启动',
            str(exc) if isinstance(exc, RuntimeError) else
            f'启动失败（{type(exc).__name__}）。请运行 setup.cmd 或使用完整发布包，不要删除订单数据。')
    finally:
        if service:
            service.stop.set()
            service.booking_stop.set()
        if worker and worker.is_alive():
            worker.join(timeout=35)
        if server:
            server.shutdown()
            server.server_close()
        if window:
            window.view.setPage(None)
            window.page.deleteLater()
            app.processEvents()
        if store and (not worker or not worker.is_alive()):
            store.close()
        if guard:
            guard.close()
