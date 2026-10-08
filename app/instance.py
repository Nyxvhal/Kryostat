"""Защита от двойного запуска: именованный мьютекс Windows + локальный сокет для «показать окно»."""
import ctypes
import os
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

IS_WINDOWS = os.name == "nt"
MUTEX_NAME = "Global\\KryostatSingleInstance"      # то же имя указано в installer.iss (AppMutex)
PIPE_NAME = "KryostatActivate"
ERROR_ALREADY_EXISTS = 183


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self):
        super().__init__()
        self._handle = None
        self._server = None
        if IS_WINDOWS:
            self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._k32.CreateMutexW.restype = ctypes.c_void_p
            self._k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            self._k32.CloseHandle.argtypes = [ctypes.c_void_p]

    def _try_mutex(self) -> bool:
        if not IS_WINDOWS:
            return True
        ctypes.set_last_error(0)
        h = self._k32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.get_last_error()
        if not h:                       # нет доступа — мьютекс есть, но создан другим уровнем прав
            return False
        if err == ERROR_ALREADY_EXISTS:
            self._k32.CloseHandle(h)
            return False
        self._handle = h
        return True

    def acquire(self, wait_seconds: float = 0.0) -> bool:
        """True — мы единственный экземпляр. wait_seconds нужен при перезапуске «от администратора»,
        пока старый процесс ещё закрывается."""
        deadline = time.monotonic() + wait_seconds
        while True:
            if self._try_mutex():
                self._listen()
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)

    def _listen(self):
        QLocalServer.removeServer(PIPE_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._server.listen(PIPE_NAME)

    def _on_connection(self):
        while self._server and self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.disconnectFromServer()
        self.activated.emit()

    def notify_existing(self) -> bool:
        """Попросить уже запущенный экземпляр показать окно."""
        sock = QLocalSocket()
        sock.connectToServer(PIPE_NAME)
        if sock.waitForConnected(500):
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(300)
            sock.disconnectFromServer()
            return True
        return False

    def release(self):
        if self._server:
            self._server.close()
            self._server = None
        if self._handle and IS_WINDOWS:
            self._k32.CloseHandle(self._handle)
        self._handle = None
