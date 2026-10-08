"""Ограничение ресурсов всей системы.

1. Частота процессора — максимальное состояние CPU в текущей схеме питания (powercfg).
   Действует на всё, включая Windows, и полностью обратимо.
2. Общий «бюджет» для всех программ — один Job Object, в который попадают все
   сторонние процессы: суммарный предел CPU и ОЗУ на все программы вместе,
   приоритет CPU и диска. Системные процессы Windows и сам Kryostat не трогаются.
3. Схемы питания и Turbo Boost."""
import ctypes
import os
import re

import psutil

from .admin import IS_WINDOWS, run
from . import config, limits

KEY = "__system__"
SUB = "SUB_PROCESSOR"
DEFAULT = {"enabled": False, "cpu_percent": 0, "ram_mb": 0, "priority": None,
           "io_low": False, "exclude": [], "skip_rules": True}
ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"

_assigned: set = set()
_tuned: set = set()


def settings() -> dict:
    s = dict(DEFAULT)
    s.update(config.load().get("system_limit") or {})
    return s


def save(s: dict):
    config.update(lambda cfg: cfg.__setitem__("system_limit", s))


# ------------------------------------------------------------ powercfg
def _hexes(text):
    return [int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{8})", text)]


def cpu_max() -> tuple:
    """(от сети %, от батареи %) — максимальное состояние процессора."""
    if not IS_WINDOWS:
        return 100, 100
    rc, out = run(["powercfg", "/q", "SCHEME_CURRENT", SUB, "PROCTHROTTLEMAX"])
    h = _hexes(out)
    return (h[-2], h[-1]) if len(h) >= 2 else (100, 100)


def set_cpu_max(percent: int, battery: int = None):
    p = max(5, min(100, int(percent)))
    b = max(5, min(100, int(battery if battery is not None else percent)))
    r1 = run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB, "PROCTHROTTLEMAX", str(p)])
    r2 = run(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", SUB, "PROCTHROTTLEMAX", str(b)])
    run(["powercfg", "/setactive", "SCHEME_CURRENT"])
    ok = r1[0] == 0 and r2[0] == 0
    config.log(f"Максимум CPU: {p}% от сети, {b}% от батареи → {'OK' if ok else r1[1]}")
    return ok, r1[1] or r2[1]


def turbo() -> int:
    """Режим Turbo Boost: 0 — выключен, 1 — включён, 2 — агрессивный…"""
    if not IS_WINDOWS:
        return 2
    rc, out = run(["powercfg", "/q", "SCHEME_CURRENT", SUB, "PERFBOOSTMODE"])
    h = _hexes(out)
    return h[-2] if len(h) >= 2 else 2


def set_turbo(on: bool):
    v = "2" if on else "0"
    run(["powercfg", "/attributes", SUB, "PERFBOOSTMODE", "-ATTRIB_HIDE"])
    r1 = run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB, "PERFBOOSTMODE", v])
    r2 = run(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", SUB, "PERFBOOSTMODE", v])
    run(["powercfg", "/setactive", "SCHEME_CURRENT"])
    return r1[0] == 0 and r2[0] == 0, r1[1]


def plans():
    """[(guid, имя, активна)]"""
    if not IS_WINDOWS:
        return []
    rc, out = run(["powercfg", "/list"])
    res = []
    for line in out.splitlines():
        m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})\s+\((.+?)\)\s*(\*)?", line)
        if m:
            res.append((m.group(1), m.group(2), bool(m.group(3))))
    return res


def set_plan(guid):
    rc, out = run(["powercfg", "/setactive", guid])
    return rc == 0, out


def add_ultimate():
    rc, out = run(["powercfg", "-duplicatescheme", ULTIMATE])
    m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})", out)
    if rc == 0 and m:
        set_plan(m.group(1))
    return rc == 0, out


# ------------------------------------------------------------ общий бюджет
def _eligible(p_name, exe, pid, s, ruled):
    n = (p_name or "").lower()
    if not n or limits_protected(pid, n):
        return False
    from .procs import WINDOWS_DIR
    if not exe or exe.lower().startswith(WINDOWS_DIR):
        return False                       # системные файлы Windows и недоступные процессы
    if n in {x.lower() for x in s.get("exclude") or []}:
        return False
    if s.get("skip_rules", True) and n in ruled:
        return False                       # у программы своё правило — оно важнее
    return True


def limits_protected(pid, name):
    from .procs import is_protected
    return is_protected(pid, name) or name in ("kryostat.exe", "python.exe", "pythonw.exe") \
        and pid == os.getpid()


def apply(rows=None):
    """Добавить в общий бюджет новые процессы. Дёшево: работаем только с новыми PID."""
    s = settings()
    if not (IS_WINDOWS and s.get("enabled")):
        return 0
    ruled = {k.lower() for k, r in (config.load().get("limits") or {}).items()
             if r.get("enabled", True)}
    has_job = bool(s.get("cpu_percent") or s.get("ram_mb"))
    h = limits._get_job(KEY, int(s.get("ram_mb") or 0), int(s.get("cpu_percent") or 0)) \
        if has_job else None
    if rows is None:
        rows = [{"pid": p.info["pid"], "name": p.info["name"], "exe": p.info.get("exe") or "",
                 "kind": "process"} for p in psutil.process_iter(["pid", "name", "exe"])]
    alive, added = set(), 0
    for r in rows:
        if r.get("kind") != "process":
            continue
        pid = r["pid"]
        alive.add(pid)
        if pid in _assigned or pid in _tuned:
            continue
        if not _eligible(r.get("name"), r.get("exe"), pid, s, ruled):
            continue
        try:
            p = psutil.Process(pid)
            if s.get("priority"):
                p.nice(limits.PRIORITIES[s["priority"]])
            if s.get("io_low"):
                p.ionice(psutil.IOPRIO_LOW)
        except Exception:
            pass
        _tuned.add(pid)
        if h:
            ph = limits.k32.OpenProcess(limits.PROCESS_SET_QUOTA | limits.PROCESS_TERMINATE
                                        | limits.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if ph:
                try:
                    if limits.k32.AssignProcessToJobObject(h, ph):
                        _assigned.add(pid)
                        added += 1
                finally:
                    limits.k32.CloseHandle(ph)
    _assigned.intersection_update(alive)
    _tuned.intersection_update(alive)
    return added


def release():
    """Снять общий бюджет (сами процессы остаются в задании, но без ограничений)."""
    if not IS_WINDOWS:
        return
    limits._clear_job(KEY)
    for pid in list(_tuned):
        try:
            p = psutil.Process(pid)
            p.nice(limits.PRIORITIES["normal"])
            p.ionice(psutil.IOPRIO_NORMAL)
        except Exception:
            pass
    _tuned.clear()
    _assigned.clear()
    config.log("Общий лимит системы снят")


def stats():
    """Сколько процессов в бюджете и сколько они сейчас потребляют."""
    cpu = ram = 0.0
    n = 0
    for pid in list(_tuned):
        try:
            p = psutil.Process(pid)
            ram += p.memory_info().rss
            n += 1
        except Exception:
            continue
    return n, ram
