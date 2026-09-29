"""Release smoke test: isolated empty vault/store, no school worker or requests."""
import tempfile
import threading
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from .store import Store
from .service import DesktopService
from .webserver import UIServer
from .window import MainWindow, TermsDialog


def smoke():
    class EmptyVault:
        def read(self):
            return {}
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    result = [2]
    with tempfile.TemporaryDirectory(prefix='szu-release-test-') as folder:
        store = Store(Path(folder) / 'state.sqlite')
        service = DesktopService(store, EmptyVault())
        server = UIServer(service)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        terms = TermsDialog()
        assert not terms.accept_button.isEnabled()
        window = MainWindow(server, service)
        window.show()
        attempts = [0]
        timer = QTimer()
        def check():
            attempts[0] += 1
            def checked(ok):
                if ok:
                    result[0] = 0
                    app.quit()
                elif attempts[0] > 40:
                    app.quit()
            window.page.runJavaScript(
                "document.querySelectorAll('[role=tab]').length===4 && "
                "document.querySelector('#summary-date').textContent.length>0 && "
                "document.querySelector('.brand img').naturalWidth>0", checked)
        timer.timeout.connect(check)
        timer.start(300)
        app.exec()
        timer.stop()
        if window.tray:
            window.tray.hide()
        window.exiting = True
        window.close()
        server.shutdown()
        server.server_close()
        store.close()
    return result[0]
