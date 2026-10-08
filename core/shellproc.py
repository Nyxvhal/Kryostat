"""Запуск оболочек для терминала с нужными правами.

* как у Kryostat — обычный дочерний процесс (QProcess в интерфейсе);
* «обычный пользователь», когда Kryostat запущен от администратора, — процесс создаётся
  с токеном Проводника (CreateProcessWithTokenW), то есть без прав администратора;
* «администратор», когда Kryostat запущен без прав, — через UAC запускается
  вспомогательный процесс `--shell-host`, который соединяется с Kryostat по локальному
  сокету и передаёт ввод/вывод оболочки.
"""
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading

from .admin import IS_WINDOWS, CREATE_NO_WINDOW

# ------------------------------------------------------------------ оболочки
_SHELLS = None


def available_shells():
    """[(key, подпись, программа, аргументы, тип маркера)].
    Список считается один раз: поиск pwsh/Git Bash по PATH на Windows небыстрый,
    а раньше он повторялся при каждом обновлении заголовка вкладки."""
    global _SHELLS
    if _SHELLS is None:
        _SHELLS = _detect_shells()
    return list(_SHELLS)


def _detect_shells():
    out = []
    if IS_WINDOWS:
        out.append(("powershell", "Windows PowerShell", "powershell.exe",
                    ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-"], "ps"))
        pwsh = shutil.which("pwsh.exe") or next(
            (p for p in (r"C:\Program Files\PowerShell\7\pwsh.exe",) if os.path.exists(p)), None)
        if pwsh:
            out.append(("pwsh", "PowerShell 7", pwsh,
                        ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-"], "ps"))
        out.append(("cmd", "Командная строка (cmd)", "cmd.exe", ["/d", "/q", "/k"], "cmd"))
        for gb in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.exists(gb):
                out.append(("gitbash", "Git Bash", gb, ["--noprofile", "--norc"], "sh"))
                break
    else:
        out.append(("sh", "sh", "/bin/sh", [], "sh"))
        if shutil.which("bash"):
            out.append(("bash", "bash", shutil.which("bash"), ["--noprofile", "--norc"], "sh"))
    return out


def shell_by_key(key):
    shells = available_shells()
    return next((s for s in shells if s[0] == key), shells[0])


def short_label(key_or_label):
    """Короткая подпись оболочки для вкладок и кнопок."""
    lbl = key_or_label
    if not any(lbl == s[1] for s in available_shells()):
        lbl = shell_by_key(key_or_label)[1]
    return lbl.replace("Командная строка (cmd)", "cmd").replace("Windows PowerShell", "PowerShell")


def open_external(key, cwd, admin=False):
    """Открыть оболочку в отдельном окне Windows (по желанию пользователя)."""
    _k, _l, prog, _a, _m = shell_by_key(key)
    if not IS_WINDOWS:
        return False
    import ctypes
    verb = "runas" if admin else "open"
    ctypes.windll.shell32.ShellExecuteW(None, verb, prog, None, cwd or None, 1)
    return True


# ------------------------------------------------------------ протокол сокета
# кадр: 1 байт канала + 4 байта длины + данные
CH_OUT, CH_ERR, CH_EXIT, CH_IN, CH_KILL = 0, 1, 2, 5, 9


def frame(ch, data: bytes) -> bytes:
    return struct.pack("<BI", ch, len(data)) + data


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError
        buf += chunk
    return buf


def shell_host_main(argv):
    """Точка входа вспомогательного процесса: --shell-host PORT TOKEN KEY CWD"""
    i = argv.index("--shell-host")
    port, token, key = int(argv[i + 1]), argv[i + 2], argv[i + 3]
    cwd = argv[i + 4] if len(argv) > i + 4 else os.path.expanduser("~")
    _k, _l, prog, args, _m = shell_by_key(key)
    s = socket.create_connection(("127.0.0.1", port), timeout=15)
    s.settimeout(None)
    s.sendall(token.encode() + b"\n")
    p = subprocess.Popen([prog] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, cwd=cwd if os.path.isdir(cwd) else None,
                         creationflags=CREATE_NO_WINDOW)
    lock = threading.Lock()

    def pump(stream, ch):
        try:
            while True:
                data = stream.read1(65536) if hasattr(stream, "read1") else stream.read(4096)
                if not data:
                    break
                with lock:
                    s.sendall(frame(ch, data))
        except OSError:
            pass

    for st, ch in ((p.stdout, CH_OUT), (p.stderr, CH_ERR)):
        threading.Thread(target=pump, args=(st, ch), daemon=True).start()

    def waiter():
        code = p.wait()
        try:
            with lock:
                s.sendall(frame(CH_EXIT, struct.pack("<i", code)))
        except OSError:
            pass
        os._exit(0)
    threading.Thread(target=waiter, daemon=True).start()
    try:
        while True:
            ch, n = struct.unpack("<BI", _recv_exact(s, 5))
            data = _recv_exact(s, n) if n else b""
            if ch == CH_IN:
                p.stdin.write(data)
                p.stdin.flush()
            elif ch == CH_KILL:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)
    except (ConnectionError, OSError):
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    os._exit(0)


def launch_elevated_host(port, token, key, cwd):
    """Запустить --shell-host через UAC. False — пользователь отказал."""
    import ctypes
    args = ["--shell-host", str(port), token, key, cwd]
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, subprocess.list2cmdline(args)
    else:
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pyw):
            exe = pyw
        main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        params = subprocess.list2cmdline([main] + args)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 0)
    return rc > 32


# --------------------------------------------- запуск без прав администратора
class UnelevatedProcess:
    """Процесс с токеном Проводника (пользователь без повышения) и каналами stdin/out/err."""

    def __init__(self, cmdline, cwd):
        import ctypes
        import msvcrt
        from ctypes import wintypes
        import psutil
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        uenv = ctypes.WinDLL("userenv", use_last_error=True)
        self.k32 = k32
        H = wintypes.HANDLE

        class SA(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", wintypes.LPVOID),
                        ("bInheritHandle", wintypes.BOOL)]

        class SI(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                        ("lpReserved2", wintypes.LPVOID), ("hStdInput", H),
                        ("hStdOutput", H), ("hStdError", H)]

        class PI(ctypes.Structure):
            _fields_ = [("hProcess", H), ("hThread", H), ("dwProcessId", wintypes.DWORD),
                        ("dwThreadId", wintypes.DWORD)]

        k32.OpenProcess.restype = H
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CreatePipe.argtypes = [ctypes.POINTER(H), ctypes.POINTER(H), ctypes.POINTER(SA),
                                   wintypes.DWORD]
        k32.SetHandleInformation.argtypes = [H, wintypes.DWORD, wintypes.DWORD]
        k32.CloseHandle.argtypes = [H]
        k32.WaitForSingleObject.argtypes = [H, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
        k32.TerminateProcess.argtypes = [H, wintypes.UINT]
        adv.OpenProcessToken.argtypes = [H, wintypes.DWORD, ctypes.POINTER(H)]
        adv.DuplicateTokenEx.argtypes = [H, wintypes.DWORD, wintypes.LPVOID, ctypes.c_int,
                                         ctypes.c_int, ctypes.POINTER(H)]
        adv.CreateProcessWithTokenW.argtypes = [H, wintypes.DWORD, wintypes.LPCWSTR,
                                                wintypes.LPWSTR, wintypes.DWORD, wintypes.LPVOID,
                                                wintypes.LPCWSTR, ctypes.POINTER(SI),
                                                ctypes.POINTER(PI)]
        uenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(wintypes.LPVOID), H, wintypes.BOOL]
        uenv.DestroyEnvironmentBlock.argtypes = [wintypes.LPVOID]

        # 1. Проводник текущего сеанса
        sess = wintypes.DWORD()
        k32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sess))
        target = None
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info["name"] or "").lower() != "explorer.exe":
                continue
            ps = wintypes.DWORD()
            if k32.ProcessIdToSessionId(p.info["pid"], ctypes.byref(ps)) and ps.value == sess.value:
                target = p.info["pid"]
                break
        if not target:
            raise OSError("Не найден Проводник (explorer.exe) — запуск без прав невозможен")
        hp = k32.OpenProcess(0x0400, False, target)          # PROCESS_QUERY_INFORMATION
        if not hp:
            raise OSError(f"OpenProcess: {ctypes.get_last_error()}")
        tok, prim = H(), H()
        try:
            if not adv.OpenProcessToken(hp, 0x0002 | 0x0008 | 0x0001, ctypes.byref(tok)):
                raise OSError(f"OpenProcessToken: {ctypes.get_last_error()}")
        finally:
            k32.CloseHandle(hp)
        # MAXIMUM_ALLOWED, SecurityImpersonation, TokenPrimary
        ok = adv.DuplicateTokenEx(tok, 0x02000000, None, 2, 1, ctypes.byref(prim))
        k32.CloseHandle(tok)
        if not ok:
            raise OSError(f"DuplicateTokenEx: {ctypes.get_last_error()}")

        # 2. каналы: дочерние концы наследуемые, наши — нет
        sa = SA(ctypes.sizeof(SA), None, True)
        pipes = []
        for _ in range(3):
            r, w = H(), H()
            if not k32.CreatePipe(ctypes.byref(r), ctypes.byref(w), ctypes.byref(sa), 0):
                raise OSError(f"CreatePipe: {ctypes.get_last_error()}")
            pipes.append((r, w))
        (in_r, in_w), (out_r, out_w), (err_r, err_w) = pipes
        for h in (in_w, out_r, err_r):
            k32.SetHandleInformation(h, 1, 0)

        si = SI()
        si.cb = ctypes.sizeof(SI)
        si.dwFlags = 0x100 | 0x1                            # USESTDHANDLES | USESHOWWINDOW
        si.wShowWindow = 0
        si.hStdInput, si.hStdOutput, si.hStdError = in_r, out_w, err_w
        pi = PI()
        env = wintypes.LPVOID()
        have_env = bool(uenv.CreateEnvironmentBlock(ctypes.byref(env), prim, False))
        flags = 0x08000000 | 0x00000400                     # CREATE_NO_WINDOW | UNICODE_ENVIRONMENT
        buf = ctypes.create_unicode_buffer(cmdline)
        ok = adv.CreateProcessWithTokenW(prim, 0, None, buf, flags, env if have_env else None,
                                         cwd if cwd and os.path.isdir(cwd) else None,
                                         ctypes.byref(si), ctypes.byref(pi))
        err = ctypes.get_last_error()
        if have_env:
            uenv.DestroyEnvironmentBlock(env)
        k32.CloseHandle(prim)
        for h in (in_r, out_w, err_w):
            k32.CloseHandle(h)
        if not ok:
            for h in (in_w, out_r, err_r):
                k32.CloseHandle(h)
            raise OSError(f"CreateProcessWithTokenW: ошибка {err}")
        k32.CloseHandle(pi.hThread)
        self.hproc = pi.hProcess
        self.pid = pi.dwProcessId
        self.stdin = os.fdopen(msvcrt.open_osfhandle(in_w.value, 0), "wb", 0)
        self.stdout = os.fdopen(msvcrt.open_osfhandle(out_r.value, 0), "rb", 0)
        self.stderr = os.fdopen(msvcrt.open_osfhandle(err_r.value, 0), "rb", 0)

    def wait(self):
        from ctypes import wintypes
        self.k32.WaitForSingleObject(self.hproc, 0xFFFFFFFF)
        code = wintypes.DWORD()
        self.k32.GetExitCodeProcess(self.hproc, self._ct_ref(code))
        return int(code.value)

    @staticmethod
    def _ct_ref(x):
        import ctypes
        return ctypes.byref(x)

    def kill(self):
        try:
            subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"], capture_output=True,
                           timeout=10, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
        self.k32.TerminateProcess(self.hproc, 1)
