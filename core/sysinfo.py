"""Информация о компьютере, питание (выключение/перезагрузка/безопасный режим)
и статистика запуска программ (UserAssist Windows)."""
import codecs
import datetime
import json
import os
import platform
import socket
import struct
import time

import psutil

from .admin import IS_WINDOWS, powershell, run
from . import config

try:
    import winreg
except ImportError:
    winreg = None

WOW64 = getattr(winreg, "KEY_WOW64_64KEY", 0) if winreg else 0


def _reg(hive, path, name, default=None):
    if not winreg:
        return default
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | WOW64) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return default


def _filetime(ft: int):
    if not ft:
        return None
    try:
        return datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=ft // 10)
    except (OverflowError, ValueError):
        return None


# ------------------------------------------------------------------ сводка
def quick() -> dict:
    """Быстрые сведения (без PowerShell) — показываются сразу."""
    vm = psutil.virtual_memory()
    boot = psutil.boot_time()
    d = {
        "computer": socket.gethostname(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "domain": os.environ.get("USERDOMAIN", ""),
        "os": platform.platform(),
        "arch": platform.machine(),
        "cpu": platform.processor(),
        "cores": f"{psutil.cpu_count(logical=False) or '?'} ядер / {psutil.cpu_count()} потоков",
        "ram": f"{vm.total / 1024**3:.1f} ГБ",
        "boot": datetime.datetime.fromtimestamp(boot).strftime("%d.%m.%Y %H:%M:%S"),
        "uptime": time.time() - boot,
        "python": platform.python_version(),
    }
    if IS_WINDOWS and winreg:
        cv = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        H = winreg.HKEY_LOCAL_MACHINE
        name = _reg(H, cv, "ProductName", "Windows")
        build = int(_reg(H, cv, "CurrentBuildNumber", "0") or 0)
        if build >= 22000 and "Windows 10" in name:
            name = name.replace("Windows 10", "Windows 11")     # ProductName врёт на 11
        d["os"] = f'{name} {_reg(H, cv, "DisplayVersion", "") or _reg(H, cv, "ReleaseId", "")}'
        d["build"] = f'{build}.{_reg(H, cv, "UBR", 0)}'
        inst = _reg(H, cv, "InstallDate")
        if inst:
            d["installed"] = datetime.datetime.fromtimestamp(int(inst)).strftime("%d.%m.%Y")
        d["owner"] = _reg(H, cv, "RegisteredOwner", "")
        d["cpu"] = (_reg(H, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                         "ProcessorNameString", d["cpu"]) or "").strip()
        bios = r"HARDWARE\DESCRIPTION\System\BIOS"
        d["board"] = " ".join(x for x in (_reg(H, bios, "BaseBoardManufacturer", ""),
                                          _reg(H, bios, "BaseBoardProduct", "")) if x)
        d["system"] = " ".join(x for x in (_reg(H, bios, "SystemManufacturer", ""),
                                           _reg(H, bios, "SystemProductName", "")) if x)
        d["bios"] = f'{_reg(H, bios, "BIOSVendor", "")} {_reg(H, bios, "BIOSVersion", "")} ' \
                    f'({_reg(H, bios, "BIOSReleaseDate", "")})'
        sb = _reg(H, r"SYSTEM\CurrentControlSet\Control\SecureBoot\State",
                  "UEFISecureBootEnabled")
        d["secureboot"] = "включена" if sb == 1 else ("выключена" if sb == 0 else "нет / BIOS")
        d["safemode"] = "да" if _reg(H, r"SYSTEM\CurrentControlSet\Control\SafeBoot\Option",
                                     "OptionValue") else "нет"
    try:
        f = psutil.cpu_freq()
        if f:
            d["freq"] = f"{f.current / 1000:.2f} ГГц (макс. {f.max / 1000:.2f})"
    except Exception:
        pass
    bat = None
    try:
        bat = psutil.sensors_battery()
    except Exception:
        pass
    if bat:
        left = "" if bat.power_plugged or bat.secsleft in (psutil.POWER_TIME_UNLIMITED,
                                                            psutil.POWER_TIME_UNKNOWN) \
            else f", осталось {int(bat.secsleft // 3600)} ч {int(bat.secsleft % 3600 // 60)} мин"
        d["battery"] = f'{bat.percent:.0f} % · {"от сети" if bat.power_plugged else "от батареи"}{left}'
    else:
        d["battery"] = "нет"
    return d


_HW_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$o=[ordered]@{}
$o.gpu=@(Get-CimInstance Win32_VideoController | % { [ordered]@{name=$_.Name; ram=$_.AdapterRAM; driver=$_.DriverVersion; res="$($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution) @ $($_.CurrentRefreshRate) Гц"} })
$o.disks=@(Get-PhysicalDisk | % { [ordered]@{name=$_.FriendlyName; type="$($_.MediaType)"; bus="$($_.BusType)"; size=$_.Size; health="$($_.HealthStatus)"} })
$o.ram=@(Get-CimInstance Win32_PhysicalMemory | % { [ordered]@{maker=$_.Manufacturer; part="$($_.PartNumber)".Trim(); size=$_.Capacity; speed=$_.ConfiguredClockSpeed; slot=$_.DeviceLocator} })
$o.net=@(Get-NetAdapter | ? Status -eq 'Up' | % { [ordered]@{name=$_.Name; desc=$_.InterfaceDescription; speed=$_.LinkSpeed; mac=$_.MacAddress} })
$os=Get-CimInstance Win32_OperatingSystem
$o.lastboot="$($os.LastBootUpTime)"
$o.activation=(Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL AND Name LIKE 'Windows%'" | select -First 1).LicenseStatus
$o.tpm=(Get-CimInstance -Namespace root/cimv2/Security/MicrosoftTpm -ClassName Win32_Tpm | select -First 1).SpecVersion
$o.av=@(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | % { $_.displayName })
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$o | ConvertTo-Json -Depth 4 -Compress
"""


def hardware() -> dict:
    """Подробные сведения через CIM (несколько секунд) — грузятся в фоне."""
    if not IS_WINDOWS:
        return {}
    rc, out = powershell(_HW_PS, timeout=60)
    try:
        start = out.find("{")
        return json.loads(out[start:]) if start >= 0 else {}
    except ValueError:
        return {}


def last_shutdowns(n=8):
    """Последние включения/выключения из журнала System (события 6005/6006/6008/41/1074)."""
    if not IS_WINDOWS:
        return []
    ps = (f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          f"Get-WinEvent -FilterHashtable @{{LogName='System'; Id=6005,6006,6008,41,1074}} "
          f"-MaxEvents {n} -ErrorAction SilentlyContinue | "
          f"% {{ '{{0:dd.MM.yyyy HH:mm:ss}}|{{1}}' -f $_.TimeCreated, $_.Id }}")
    rc, out = powershell(ps, timeout=30)
    names = {"6005": "включение (старт журнала)", "6006": "корректное выключение",
             "6008": "неожиданное выключение", "41": "сбой питания / перезагрузка без выключения",
             "1074": "выключение/перезагрузка по запросу"}
    res = []
    for line in out.splitlines():
        t, _, i = line.partition("|")
        if i.strip() in names:
            res.append((t.strip(), names[i.strip()]))
    return res


# ------------------------------------------------------------------ питание
POWER = {
    "shutdown": ("Выключить", ["shutdown", "/s", "/t", "0"]),
    "restart": ("Перезагрузить", ["shutdown", "/r", "/t", "0"]),
    "hybrid": ("Выключить (быстрый запуск)", ["shutdown", "/s", "/hybrid", "/t", "0"]),
    "full": ("Полное выключение (без быстрого запуска)", ["shutdown", "/s", "/full", "/t", "0"]),
    "logoff": ("Выйти из системы", ["shutdown", "/l"]),
    "lock": ("Заблокировать", ["rundll32.exe", "user32.dll,LockWorkStation"]),
    "sleep": ("Спящий режим", ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]),
    "hibernate": ("Гибернация", ["shutdown", "/h"]),
    "bios": ("Перезагрузка в BIOS/UEFI", ["shutdown", "/r", "/fw", "/t", "0"]),
    "advanced": ("Особые варианты загрузки", ["shutdown", "/r", "/o", "/t", "0"]),
    "abort": ("Отменить запланированное выключение", ["shutdown", "/a"]),
}


def power(action: str):
    if not IS_WINDOWS:
        return False, "Только Windows"
    name, cmd = POWER[action]
    config.log(f"Питание: {name}")
    rc, out = run(cmd)
    return rc == 0, out or name


def schedule(action: str, minutes: int):
    flag = {"shutdown": "/s", "restart": "/r"}[action]
    rc, out = run(["shutdown", flag, "/t", str(max(0, int(minutes * 60)))])
    return rc == 0, out or f"Запланировано через {minutes} мин"


def safe_mode(kind: str = "minimal", reboot: bool = True):
    """Безопасный режим: minimal — обычный, network — с сетью, off — вернуть обычную загрузку."""
    if not IS_WINDOWS:
        return False, "Только Windows"
    if kind == "off":
        rc, out = run(["bcdedit", "/deletevalue", "{current}", "safeboot"])
        ok = rc == 0 or "не найден" in out.lower() or "not found" in out.lower()
        rc2, _ = run(["bcdedit", "/deletevalue", "{current}", "safebootalternateshell"])
    else:
        rc, out = run(["bcdedit", "/set", "{current}", "safeboot", kind])
        ok = rc == 0
    if not ok:
        return False, out or "bcdedit не выполнен (нужны права администратора)"
    if reboot:
        run(["shutdown", "/r", "/t", "3"])
    return True, out


def safe_mode_pending() -> bool:
    """Включён ли флаг safeboot в текущей записи загрузки."""
    if not IS_WINDOWS:
        return False
    rc, out = run(["bcdedit", "/enum", "{current}"])
    return "safeboot" in out.lower()


# ------------------------------------------------------- статистика запусков
UA_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
KNOWN_FOLDERS = {
    "{6D809377-6AF0-444B-8957-A3773F02200E}": os.environ.get("ProgramW6432", r"C:\Program Files"),
    "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
    "{F38BF404-1D43-42F2-9305-67DE0B28FC23}": os.environ.get("SystemRoot", r"C:\Windows"),
    "{0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8}": r"%ProgramData%\Microsoft\Windows\Start Menu\Programs",
    "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}": r"%APPDATA%\Microsoft\Windows\Start Menu\Programs",
    "{9E3995AB-1F9C-4F13-B827-48B24B6C7174}": r"%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned",
}


def userassist():
    """Сколько раз программы запускались через Проводник/Пуск и сколько были в фокусе.
    Данные Windows: ключ UserAssist, имена закодированы ROT13."""
    out = {}
    if not (IS_WINDOWS and winreg):
        return []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UA_KEY)
    except OSError:
        return []
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                guid = winreg.EnumKey(root, i)
                ck = winreg.OpenKey(root, guid + r"\Count")
            except OSError:
                continue
            with ck:
                for j in range(winreg.QueryInfoKey(ck)[1]):
                    try:
                        name, data, _ = winreg.EnumValue(ck, j)
                    except OSError:
                        continue
                    if not isinstance(data, bytes) or len(data) < 68:
                        continue
                    path = codecs.decode(name, "rot13")
                    if path.startswith("UEME_") or path.lower().endswith(".lnk") and "\\" not in path:
                        continue
                    for g, real in KNOWN_FOLDERS.items():
                        if path.upper().startswith(g):
                            path = os.path.expandvars(real) + path[len(g):]
                            break
                    count = struct.unpack_from("<I", data, 4)[0]
                    focus_ms = struct.unpack_from("<I", data, 12)[0]
                    last = _filetime(struct.unpack_from("<Q", data, 60)[0])
                    if not count and not focus_ms:
                        continue
                    key = path.lower()
                    prev = out.get(key)
                    if prev:
                        prev["count"] += count
                        prev["focus"] += focus_ms / 1000
                        if last and (not prev["last"] or last > prev["last"]):
                            prev["last"] = last
                    else:
                        base = path.replace("/", "\\").rsplit("\\", 1)[-1]
                        out[key] = {"name": base, "path": path, "count": count,
                                    "focus": focus_ms / 1000, "last": last}
    return sorted(out.values(), key=lambda x: -x["count"])
