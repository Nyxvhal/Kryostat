"""Автозапуск самого Kryostat и «глушение» отключённых программ при старте ПК."""
import getpass
import os
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

import psutil

from .admin import IS_WINDOWS, run
from . import config, startup as startup_mod

try:
    import winreg
except ImportError:
    winreg = None

RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Kryostat"
TASK_NAME = "KryostatBoot"


def _launch_parts(minimized: bool = True):
    """(исполняемый файл, список аргументов) для автозапуска."""
    flags = ["--boot"] + (["--minimized"] if minimized else [])
    if getattr(sys, "frozen", False):
        return sys.executable, flags
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return exe, [os.path.abspath(sys.argv[0])] + flags


def _task_xml(minimized: bool) -> str:
    exe, args = _launch_parts(minimized)
    user = os.environ.get("USERDOMAIN", "") + "\\" + (os.environ.get("USERNAME") or getpass.getuser())
    user = user.lstrip("\\")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Kryostat: запуск при входе в Windows с правами администратора</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(subprocess.list2cmdline(args))}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _run_value_set(on: bool, minimized: bool = True):
    if not winreg:
        return False
    try:
        if on:
            exe, args = _launch_parts(minimized)
            cmd = subprocess.list2cmdline([exe] + args)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_SUBKEY) as k:
                winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, cmd)
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_SUBKEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, VALUE)
        return True
    except FileNotFoundError:
        return not on
    except OSError:
        return False


def _run_value_exists() -> bool:
    if not winreg:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_SUBKEY) as k:
            winreg.QueryValueEx(k, VALUE)
        return True
    except OSError:
        return False


def task_exists() -> bool:
    rc, _ = run(["schtasks", "/Query", "/TN", TASK_NAME])
    return rc == 0


def enabled() -> bool:
    return _run_value_exists() or task_exists()


def register_task(minimized: bool = True):
    """Создать задачу планировщика (запуск с правами администратора без UAC-запроса).
    Через XML, чтобы отключить лимит «72 часа» и запрет запуска от батареи,
    которые schtasks /Create выставляет по умолчанию."""
    if not IS_WINDOWS:
        return False, "Только Windows"
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(_task_xml(minimized))
        return_code, out = run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"])
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return return_code == 0, out


def unregister_all():
    _run_value_set(False)
    if task_exists():
        run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])


def set_enabled(on: bool, minimized: bool = True):
    if not IS_WINDOWS:
        return False, "Только Windows"
    if on:
        ok, out = register_task(minimized)
        if ok:
            _run_value_set(False)        # одного способа достаточно, иначе будет двойной запуск
        else:
            # без прав администратора задачу создать нельзя — запасной вариант через Run
            ok = _run_value_set(True, minimized)
            out = out if not ok else ""
    else:
        unregister_all()
        ok, out = True, ""

    def _mut(cfg):
        if ok:
            cfg["autostart_self"] = bool(on)
        cfg["start_minimized"] = bool(minimized)
    config.update(_mut)
    return ok, out


def kill_disabled_now():
    """Завершить процессы программ, которые пользователь отключил в автозапуске."""
    cfg = config.load()
    if not cfg.get("kill_on_boot"):
        return 0, []
    # универсальные хосты (cmd, rundll32, …) по имени не трогаем — в том числе из старых конфигов
    targets = {x.get("target") for x in cfg.get("disabled_startup", []) if x.get("target")}
    targets -= startup_mod.GENERIC_HOSTS
    if not targets:
        return 0, []
    from .procs import is_protected, WINDOWS_DIR
    victims, names = [], set()
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            nm = (p.info.get("name") or "").lower()
            if nm not in targets or is_protected(p.info["pid"], nm):
                continue
            if (p.info.get("exe") or "").lower().startswith(WINDOWS_DIR):
                continue                  # системные файлы Windows не трогаем
            p.terminate()
            victims.append(p)
            names.add(nm)
        except Exception:
            continue
    if victims:
        _, alive = psutil.wait_procs(victims, timeout=1.5)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
        config.log(f"Завершено при старте: {len(victims)} ({', '.join(sorted(names))})")
    return len(victims), sorted(names)
