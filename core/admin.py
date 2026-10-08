"""Права администратора и запуск команд."""
import ctypes
import os
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000


def oem_encoding() -> str:
    """Кодировка вывода консольных утилит (cp866 на русской Windows, cp437/850 и т.д. на других)."""
    if IS_WINDOWS:
        try:
            return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:
            pass
    return "cp866"


def decode(data: bytes) -> str:
    if not data:
        return ""
    for enc in (oem_encoding(), "utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


_IS_ADMIN = None


def is_admin() -> bool:
    """Права процесса не меняются за время его жизни — проверяем один раз."""
    global _IS_ADMIN
    if _IS_ADMIN is None:
        if not IS_WINDOWS:
            _IS_ADMIN = False
        else:
            try:
                _IS_ADMIN = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                _IS_ADMIN = False
    return _IS_ADMIN


def relaunch_as_admin(extra_args=None) -> bool:
    """Перезапустить текущее приложение с UAC-повышением."""
    if not IS_WINDOWS or is_admin():
        return False
    args = list(sys.argv[1:])
    for a in (extra_args or []):
        if a.lower() not in {x.lower() for x in args}:
            args.append(a)
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, subprocess.list2cmdline(args)
    else:
        exe = sys.executable
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + args)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


def run(cmd, timeout=30):
    """Выполнить команду. Возвращает (код, вывод).
    Строка — через оболочку (cmd.exe), список — напрямую, без разбора кавычек."""
    if not IS_WINDOWS:
        return 1, "Доступно только в Windows"
    from . import cmdlog
    cid = cmdlog.command_start(cmd)
    rc, out = 1, ""
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        out = decode((p.stdout or b"") + (p.stderr or b"")).strip()
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out = "Превышено время ожидания команды"
    except Exception as e:
        out = str(e)
    cmdlog.command_end(cid, rc, out)
    return rc, out


def powershell(script, timeout=60):
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", script], timeout=timeout)
