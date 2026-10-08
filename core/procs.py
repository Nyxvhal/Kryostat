"""Инвентаризация процессов и служб: запущенных и НЕ запущенных."""
import os
import time
import psutil
from .admin import IS_WINDOWS, run

# Критичные для работы Windows процессы -> подсвечиваются красным и защищены от завершения
CRITICAL = {
    "system", "system idle process", "registry", "memory compression", "secure system",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "lsaiso.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe", "sihost.exe", "ctfmon.exe",
    "spoolsv.exe", "taskhostw.exe", "runtimebroker.exe", "shellexperiencehost.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "audiodg.exe", "conhost.exe",
    "wudfhost.exe", "dashost.exe", "msmpeng.exe", "securityhealthservice.exe",
    "explorer.exe", "logonui.exe", "userinit.exe", "dllhost.exe", "wmiprvse.exe",
    "lsaiso.exe", "wlanext.exe", "spoolsv.exe", "trustedinstaller.exe",
}

CRITICAL_SERVICES = {
    "rpcss", "dcomlaunch", "plugplay", "power", "lsm", "eventlog", "schedule",
    "profsvc", "themes", "audiosrv", "audioendpointbuilder", "winmgmt", "cryptsvc",
    "bfe", "mpssvc", "nsi", "dhcp", "dnscache", "gpsvc", "samss",
    "brokerinfrastructure", "systemeventsbroker", "coremessagingregistrar",
    "usermanager", "statesyscache", "tiledatamodelsvc", "winlogon", "wlansvc",
}

WINDOWS_DIR = (os.environ.get("SystemRoot") or r"C:\Windows").lower()
_MY_PID = os.getpid()


def is_critical(name: str, exe: str | None = None) -> bool:
    return (name or "").lower() in CRITICAL


def is_protected(pid: int, name: str) -> bool:
    """Нельзя завершать: критичные процессы Windows и сам Kryostat."""
    return pid == _MY_PID or is_critical(name)


ATTRS = ["pid", "ppid", "name", "status", "create_time",
         "memory_info", "num_threads", "cpu_percent", "io_counters"]


def prime_cpu():
    """Первый замер CPU — обязателен, иначе все нули."""
    for p in psutil.process_iter():
        try:
            p.cpu_percent(None)
        except Exception:
            pass


def _info(p):
    """Поля процесса. Раньше одна ошибка любого поля (OSError у защищённых или 32-битных
    процессов — бывает у memory_info/io_counters) выкидывала процесс из списка целиком —
    поэтому Kryostat показывал не все процессы, которые видно в «Мониторе ресурсов»."""
    try:
        return p.as_dict(ATTRS, ad_value=None)
    except psutil.NoSuchProcess:
        raise
    except Exception:
        pass
    out = {"pid": p.pid}
    for a in ATTRS[1:]:
        try:
            out[a] = getattr(p, a)() if a != "cpu_percent" else p.cpu_percent(None)
        except psutil.NoSuchProcess:
            raise
        except Exception:
            out[a] = None
    return out


class ProcessSampler:
    """Хранит состояние между замерами, чтобы корректно считать скорости."""

    def __init__(self):
        self._io: dict[int, tuple[int, int]] = {}
        self._static: dict[tuple, tuple[str, str]] = {}   # (pid, create_time) -> (exe, user)
        self._last = time.monotonic()
        self._ncpu = psutil.cpu_count() or 1
        prime_cpu()

    def _static_attrs(self, p, pid, ctime):
        """Путь и пользователь не меняются — запрашиваем один раз (username на Windows дорогой)."""
        key = (pid, ctime)
        v = self._static.get(key)
        if v is None:
            try:
                exe = p.exe() or ""
            except Exception:
                exe = ""
            try:
                user = p.username() or ""
            except Exception:
                user = ""
            v = (exe, user)
            self._static[key] = v
        return v

    def sample(self):
        now = time.monotonic()
        dt = max(0.2, now - self._last)
        self._last = now
        rows, io_now, alive = [], {}, set()
        for p in psutil.process_iter():
            try:
                i = _info(p)
                pid = i["pid"]
                ctime = i.get("create_time") or 0
                alive.add((pid, ctime))
                io = i.get("io_counters")      # запрашивается вместе с остальными полями
                io_now[pid] = (io.read_bytes, io.write_bytes) if io else (0, 0)
                prev = self._io.get(pid, io_now[pid])
                disk = max(0, (io_now[pid][0] - prev[0]) + (io_now[pid][1] - prev[1])) / dt
                mem = i["memory_info"].rss if i.get("memory_info") else 0
                name = i.get("name") or "?"
                exe, user = self._static_attrs(p, pid, ctime)
                # System Idle Process «потребляет» всё свободное время CPU — это не нагрузка
                cpu = 0.0 if pid == 0 else min(100.0, (i.get("cpu_percent") or 0.0) / self._ncpu)
                rows.append({
                    "key": f"p{pid}",
                    "pid": pid, "ppid": i.get("ppid"), "name": name,
                    "exe": exe, "user": user,
                    "status": i.get("status") or "", "threads": i.get("num_threads") or 0,
                    "cpu": round(cpu, 1),
                    "ram": mem, "disk": disk, "started": ctime,
                    "critical": is_critical(name),
                    "protected": is_protected(pid, name),
                    "kind": "process", "running": True, "display": "",
                    "start_type": "",
                })
            except psutil.NoSuchProcess:
                continue
            except Exception:
                continue
        self._io = io_now
        if len(self._static) > len(alive) + 200:      # чистим завершившиеся процессы
            self._static = {k: v for k, v in self._static.items() if k in alive}
        return rows


def services():
    """Все службы Windows, включая ОСТАНОВЛЕННЫЕ (не запущенные)."""
    out = []
    if not IS_WINDOWS:
        return out
    try:
        for s in psutil.win_service_iter():
            try:
                d = s.as_dict()          # один запрос конфигурации + один статуса вместо шести
                name = d["name"]
                status = d.get("status") or ""
                start_type = d.get("start_type") or ""
                display = d.get("display_name") or ""
                pid = d.get("pid") or 0
                binpath = d.get("binpath") or ""
                user = d.get("username") or ""
            except Exception:
                continue
            out.append({
                "key": f"s{name}",
                "pid": pid, "ppid": None, "name": name, "display": display,
                "exe": binpath, "user": user, "status": status,
                "start_type": start_type, "threads": 0, "cpu": 0.0, "ram": 0,
                "disk": 0.0, "started": 0,
                "critical": name.lower() in CRITICAL_SERVICES,
                "protected": True,
                "kind": "service", "running": status == "running",
            })
    except Exception:
        pass
    return out


def kill(pid: int, name: str = "", force: bool = False):
    try:
        p = psutil.Process(pid)
        nm = name or p.name()
        if is_protected(pid, nm):
            return False, "Защищённый процесс — завершение заблокировано"
        p.kill() if force else p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            p.kill()
        return True, "Завершён"
    except psutil.NoSuchProcess:
        return True, "Уже завершён"
    except psutil.AccessDenied:
        return False, "Отказано в доступе — нужны права администратора"
    except Exception as e:
        return False, str(e)


def service_action(name: str, action: str):
    """action: start | stop | disable | manual | auto"""
    cmds = {
        "start": f'sc start "{name}"',
        "stop": f'sc stop "{name}"',
        "disable": f'sc config "{name}" start= disabled',
        "manual": f'sc config "{name}" start= demand',
        "auto": f'sc config "{name}" start= auto',
    }
    if action not in cmds:
        return False, "Неизвестное действие"
    if action != "start" and name.lower() in CRITICAL_SERVICES:
        return False, "Критичная служба Windows — изменение заблокировано"
    rc, out = run(cmds[action])
    # 1056 — уже запущена, 1062 — уже остановлена
    if rc in (1056, 1062):
        return True, "Уже в нужном состоянии"
    if rc == 5:
        return False, "Отказано в доступе — нужны права администратора"
    return rc == 0, out or "Готово"
