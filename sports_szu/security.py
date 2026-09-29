"""DPAPI at rest; no plaintext fallback, no secret export, no admin privileges."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import threading


def data_dir():
    if os.name != 'nt':
        raise RuntimeError('此版本仅支持 Windows（使用当前用户的 DPAPI 加密）')
    path = Path(os.environ['LOCALAPPDATA']) / 'SportsForSZU'
    path.mkdir(parents=True, exist_ok=True)
    return path


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('需要 Windows DPAPI；禁止明文降级')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError('本地凭据加解密失败，请重新登录')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.WinDLL('kernel32', use_last_error=True).LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(target.data, ctypes.c_void_p))


class Vault:
    def __init__(self, directory):
        self.path = Path(directory) / 'account.secret'
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return {}
            return json.loads(crypt(self.path.read_bytes(), decrypt=True))

    def update(self, **values):
        with self.lock:
            current = self.read()
            current.update(values)
            temp = self.path.with_suffix('.secret.tmp')
            temp.write_bytes(crypt(json.dumps(current, ensure_ascii=False).encode('utf-8')))
            os.replace(temp, self.path)


class SingleInstance:
    def __init__(self, directory):
        if os.name != 'nt':
            raise RuntimeError('预约助手需要 Windows 单实例支持')
        # A named Windows mutex avoids antivirus/ACL failures seen when locking
        # a file in %LOCALAPPDATA%. The handle remains owned by this process.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel = kernel
        self.handle = kernel.CreateMutexW(None, False, 'Local\\SportsForSZU.SingleInstance')
        if not self.handle:
            raise RuntimeError('无法创建单实例锁，请检查 Windows 权限')
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel.CloseHandle(self.handle)
            raise RuntimeError('预约助手已在运行，请检查任务栏或托盘')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
