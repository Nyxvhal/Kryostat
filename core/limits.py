"""Ограничение ресурсов процессов: RAM и CPU через Job Objects, приоритеты, диск, сеть (QoS)."""
import ctypes, os
from ctypes import wintypes
import psutil
from .admin import IS_WINDOWS, powershell
from . import config

PRIORITIES = {
    "realtime": getattr(psutil, "REALTIME_PRIORITY_CLASS", 256),
    "high": getattr(psutil, "HIGH_PRIORITY_CLASS", 128),
    "above": getattr(psutil, "ABOVE_NORMAL_PRIORITY_CLASS", 32768),
    "normal": getattr(psutil, "NORMAL_PRIORITY_CLASS", 32),
    "below": getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 16384),
    "idle": getattr(psutil, "IDLE_PRIORITY_CLASS", 64),
}
PRIORITY_LABELS = {"realtime": "Реального времени", "high": "Высокий", "above": "Выше среднего",
                   "normal": "Обычный", "below": "Ниже среднего", "idle": "Низкий"}

# --- WinAPI Job Objects ---------------------------------------------------
if IS_WINDOWS:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
    JobObjectExtendedLimitInformation = 9
    JobObjectCpuRateControlInformation = 15
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
    JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    JOB_OBJECT_SET_ATTRIBUTES, JOB_OBJECT_QUERY = 0x0002, 0x0004

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong)]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

    # Явные прототипы WinAPI: без них HANDLE усекается до int и вызовы могут падать на x64
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.OpenJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            wintypes.LPVOID, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    k32.IsProcessInJob.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL

_jobs: dict[str, int] = {}      # имя процесса -> handle джоба
_assigned: set[int] = set()     # PID, уже помещённые в джоб
_applied: dict[int, str] = {}   # PID -> подпись применённого правила
_net_applied: dict[str, int] = {}   # имя exe -> применённый лимит сети (КБит/с)


def _job_name(key: str) -> str:
    return f"Kryostat_{key}".replace(".", "_")


def _set_job_limits(h, ram_mb: int, cpu_percent: int):
    """Выставить (или СНЯТЬ, если 0) лимиты RAM и CPU у джоба."""
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    if ram_mb and ram_mb > 0:
        info.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_PROCESS_MEMORY
                                                 | JOB_OBJECT_LIMIT_JOB_MEMORY)
        info.ProcessMemoryLimit = int(ram_mb) * 1024 * 1024
        info.JobMemoryLimit = int(ram_mb) * 1024 * 1024
    k32.SetInformationJobObject(h, JobObjectExtendedLimitInformation,
                                ctypes.byref(info), ctypes.sizeof(info))
    cpu = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
    if cpu_percent and 0 < cpu_percent < 100:
        cpu.ControlFlags = (JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                            | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP)
        cpu.CpuRate = max(1, min(10000, int(cpu_percent * 100)))
    k32.SetInformationJobObject(h, JobObjectCpuRateControlInformation,
                                ctypes.byref(cpu), ctypes.sizeof(cpu))


def _get_job(key: str, ram_mb: int, cpu_percent: int):
    """Создать/обновить job object с лимитами RAM и CPU."""
    h = _jobs.get(key)
    if not h:
        h = k32.CreateJobObjectW(None, _job_name(key))
        if not h:
            return None
        _jobs[key] = h
    _set_job_limits(h, ram_mb, cpu_percent)
    return h


def _clear_job(key: str):
    """Снять лимиты RAM/CPU с уже существующего джоба (если он есть)."""
    if not IS_WINDOWS:
        return
    h = _jobs.get(key)
    opened = False
    if not h:
        h = k32.OpenJobObjectW(JOB_OBJECT_SET_ATTRIBUTES | JOB_OBJECT_QUERY, False, _job_name(key))
        opened = True
    if not h:
        return
    _set_job_limits(h, 0, 0)
    if opened:
        k32.CloseHandle(h)


def _sig(rule: dict) -> str:
    return "|".join(str(rule.get(k)) for k in
                    ("ram_mb", "cpu_percent", "affinity_cores", "priority", "io_low"))


def apply_to_pid(pid: int, rule: dict, force: bool = True) -> tuple[bool, str]:
    """Применить правило к конкретному PID. ok=False, если хоть что-то не удалось."""
    if not IS_WINDOWS:
        return False, "Только Windows"
    if not force and _applied.get(pid) == _sig(rule):
        return True, "уже применено"
    msgs, errors = [], []
    try:
        p = psutil.Process(pid)
        key = p.name().lower()
    except Exception as e:
        return False, str(e)

    if rule.get("priority"):
        try:
            p.nice(PRIORITIES[rule["priority"]])
            msgs.append("приоритет CPU")
        except Exception as e:
            errors.append(f"приоритет: {e}")
    if rule.get("io_low"):
        try:
            p.ionice(psutil.IOPRIO_VERYLOW)
            msgs.append("низкий приоритет диска")
        except Exception as e:
            errors.append(f"диск: {e}")
    if rule.get("affinity_cores"):
        try:
            n = psutil.cpu_count() or 1
            p.cpu_affinity(list(range(min(int(rule["affinity_cores"]), n))))
            msgs.append("ядра CPU")
        except Exception as e:
            errors.append(f"ядра: {e}")

    if rule.get("ram_mb") or rule.get("cpu_percent"):
        h = _get_job(key, int(rule.get("ram_mb") or 0), int(rule.get("cpu_percent") or 0))
        if not h:
            errors.append("не удалось создать объект задания")
        elif pid not in _assigned:
            ph = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE
                                 | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if ph:
                try:
                    already = wintypes.BOOL(0)
                    if k32.IsProcessInJob(ph, h, ctypes.byref(already)) and already.value:
                        _assigned.add(pid)       # после перезапуска Kryostat процесс уже в нашем джобе
                        msgs.append("лимиты RAM/CPU активны")
                    elif k32.AssignProcessToJobObject(h, ph):
                        _assigned.add(pid)
                        msgs.append("лимиты RAM/CPU")
                    else:
                        err = ctypes.get_last_error()
                        if err == 5:
                            errors.append("лимиты RAM/CPU: нужен запуск от администратора "
                                          "(либо процесс уже в чужом задании)")
                        else:
                            _assigned.add(pid)   # не долбим повторно каждую минуту
                            errors.append(f"лимиты RAM/CPU: код {err}")
                finally:
                    k32.CloseHandle(ph)
            else:
                errors.append("нет доступа к процессу (нужен админ)")
        else:
            msgs.append("лимиты RAM/CPU активны")
    elif "ram_mb" in rule or "cpu_percent" in rule:
        _clear_job(key)                          # в сохранённом правиле лимитов нет — снимаем прежние
    if not errors:
        _applied[pid] = _sig(rule)
    else:
        _applied.pop(pid, None)
    text = ", ".join(msgs + errors) or "нет изменений"
    return not errors, text


def apply_rule(name: str, rule: dict, force: bool = True):
    """Применить правило ко всем процессам с таким именем."""
    applied, errs = 0, []
    live_all = set()
    for p in psutil.process_iter(["pid", "name"]):
        live_all.add(p.info["pid"])
        if (p.info.get("name") or "").lower() == name.lower():
            ok, msg = apply_to_pid(p.info["pid"], rule, force=force)
            if ok:
                applied += 1
            elif msg not in errs:
                errs.append(msg)
    for dead in [pid for pid in set(_assigned) | set(_applied) if pid not in live_all]:
        _assigned.discard(dead)
        _applied.pop(dead, None)

    net = int(rule.get("net_kbps") or 0)
    key = name.lower()
    if net:
        if force or _net_applied.get(key) != net:       # PowerShell — дорого, только при изменении
            ok, msg = set_network_limit(name, net)
            if ok:
                _net_applied[key] = net
            else:
                errs.append(msg)
    elif force:
        # пользователь убрал лимит сети из правила — снимаем политику QoS
        set_network_limit(name, 0)
        _net_applied.pop(key, None)
    return applied, errs


def set_network_limit(exe_name: str, kbps: int):
    """Ограничение скорости сети для приложения через политику QoS Windows."""
    policy = f"Kryostat-{os.path.splitext(exe_name)[0]}"
    if kbps <= 0:
        rc, out = powershell(f'Remove-NetQosPolicy -Name "{policy}" -Confirm:$false '
                             f'-ErrorAction SilentlyContinue')
        return rc == 0, "Лимит сети снят"
    bps = int(kbps) * 1000
    rc, out = powershell(
        f'Remove-NetQosPolicy -Name "{policy}" -Confirm:$false -ErrorAction SilentlyContinue; '
        f'New-NetQosPolicy -Name "{policy}" -AppPathNameMatchCondition "{exe_name}" '
        f'-ThrottleRateActionBitsPerSecond {bps} -ErrorAction Stop | Out-Null')
    return rc == 0, (out[:200] if rc != 0 else f"Сеть ограничена до {kbps} КБит/с")


def apply_all():
    """Применить все сохранённые правила (вызывается по таймеру и при старте)."""
    cfg = config.load()
    total = 0
    for name, rule in (cfg.get("limits") or {}).items():
        if rule.get("enabled", True):
            n, _ = apply_rule(name, rule, force=False)
            total += n
    return total


def save_rule(name: str, rule: dict):
    def _mut(cfg):
        cfg.setdefault("limits", {})[name.lower()] = rule
    config.update(_mut)
    config.log(f"Правило лимитов сохранено: {name} -> {rule}")


def release_rule(name: str):
    """Снять все действующие ограничения правила с запущенных процессов."""
    key = name.lower()
    try:
        _clear_job(key)
        if key in _net_applied:
            set_network_limit(name, 0)
            _net_applied.pop(key, None)
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info.get("name") or "").lower() != key:
                continue
            pid = p.info["pid"]
            _applied.pop(pid, None)
            try:
                p.cpu_affinity(list(range(psutil.cpu_count() or 1)))
            except Exception:
                pass
            try:
                p.nice(PRIORITIES["normal"])
            except Exception:
                pass
            try:
                p.ionice(psutil.IOPRIO_NORMAL)
            except Exception:
                pass
    except Exception as e:
        config.log(f"release_rule {name}: {e}")


def delete_rule_config(name: str):
    """Быстрая часть удаления: убрать правило из конфига (чтобы список обновился сразу)."""
    config.update(lambda cfg: cfg.get("limits", {}).pop(name.lower(), None))


def delete_rule(name: str):
    delete_rule_config(name)
    release_rule(name)
    set_network_limit(name, 0)
    h = _jobs.pop(name.lower(), None)
    if h and IS_WINDOWS:
        try:
            k32.CloseHandle(h)
        except Exception:
            pass
    config.log(f"Правило удалено: {name}")
