"""HTML desktop window using the already-installed Chromium runtime."""
import subprocess
import tempfile
import threading
import time
from .security import SingleInstance, Vault, data_dir
from .store import Store
from .service import DesktopService
from .webserver import UIServer


def main():
    import ctypes
    guard = server = tray = None
    server_started = False
    processes = []
    profiles = []
    try:
        directory = data_dir()
        guard = SingleInstance(directory)
        service = DesktopService(Store(directory / 'state.sqlite'), Vault(directory))
        server = UIServer(service)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            executable = runtime.chromium.executable_path
        from pathlib import Path
        if not Path(executable).is_file():
            raise RuntimeError('登录浏览器尚未安装，请运行 setup.cmd 安装 Chromium')
        def show():
            profile = tempfile.TemporaryDirectory(prefix='szu-ui-')
            profiles.append(profile)
            process = subprocess.Popen([executable, '--app=' + server.launch_url(),
                '--user-data-dir=' + profile.name, '--window-size=470,920', '--no-first-run',
                '--no-default-browser-check', '--disable-background-networking'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
        def hide():
            # Close only this launcher's UI processes; order management stays alive.
            for process in processes:
                if process.poll() is None:
                    process.terminate()
        def quit_app():
            service.stop.set()
            hide()
        service.close_window = hide
        service.quit_app = quit_app
        threading.Thread(target=server.serve_forever, daemon=True).start()
        server_started = True
        worker = threading.Thread(target=service.run, name='booking-worker', daemon=True)
        worker.start()
        show()
        try:
            import pystray
            from PIL import Image, ImageDraw
            icon = Image.new('RGB', (64, 64), '#191919')
            draw = ImageDraw.Draw(icon)
            draw.ellipse((12, 12, 52, 52), outline='white', width=3)
            draw.line((16, 18, 48, 46), fill='white', width=3)
            tray = pystray.Icon('sports-for-szu', icon, '自动预约助手', menu=pystray.Menu(
                pystray.MenuItem('打开预约助手', lambda *_: show(), default=True),
                pystray.MenuItem('退出（停止托管）', lambda *_: confirm_quit())))
            def confirm_quit():
                if ctypes.windll.user32.MessageBoxW(None, '退出后将停止查询、付款和自动取消。确定退出？', '退出预约助手', 0x24) == 6:
                    quit_app()
            def notify(message):
                try:
                    tray.notify(message, '自动预约助手')
                except Exception:
                    pass
            service.notification = notify
            tray.run_detached()
        except Exception:
            tray = None
            service.close_window = quit_app
        while not service.stop.wait(0.5):
            if tray is None and all(p.poll() is not None for p in processes):
                service.stop.set()
        worker.join(timeout=15)
    except Exception as exc:
        # Startup errors have no user credentials; show concise actionable failure.
        ctypes.windll.user32.MessageBoxW(None, str(exc) if isinstance(exc, RuntimeError) else
            f'无法启动（{type(exc).__name__}）。请运行 setup.cmd，或检查已有预约助手窗口。', '自动预约助手', 0x10)
    finally:
        if tray:
            tray.stop()
        if server:
            if server_started:
                server.shutdown()
            server.server_close()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for profile in profiles:
            try:
                profile.cleanup()
            except OSError:
                pass
        if guard:
            guard.close()
