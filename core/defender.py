"""Microsoft Defender: состояние, защита в реальном времени, исключения, проверки.
Всё — через официальные командлеты Defender (Get/Set/Add/Remove-MpPreference)."""
import json

from .admin import IS_WINDOWS, powershell, run
from . import config

_UTF = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; "


def _q(s: str) -> str:
    """Строка для PowerShell в одинарных кавычках."""
    return "'" + str(s).replace("'", "''") + "'"


def _ps(script, timeout=90):
    rc, out = powershell(_UTF + script, timeout=timeout)
    return rc, out


def _json(out):
    i = min([p for p in (out.find("{"), out.find("[")) if p >= 0], default=-1)
    if i < 0:
        return None
    try:
        return json.loads(out[i:])
    except ValueError:
        return None


def status() -> dict:
    if not IS_WINDOWS:
        return {"error": "Только Windows"}
    rc, out = _ps(
        "$s=Get-MpComputerStatus; $p=Get-MpPreference; "
        "[ordered]@{"
        "rt=$s.RealTimeProtectionEnabled; av=$s.AntivirusEnabled; am=$s.AMServiceEnabled; "
        "tamper=$s.IsTamperProtected; behavior=$s.BehaviorMonitorEnabled; "
        "ioav=$s.IOAVProtectionEnabled; onaccess=$s.OnAccessProtectionEnabled; "
        "sigver=$s.AntivirusSignatureVersion; sigdate=\"$($s.AntivirusSignatureLastUpdated)\"; "
        "sigage=$s.AntivirusSignatureAge; quick=\"$($s.QuickScanEndTime)\"; "
        "full=\"$($s.FullScanEndTime)\"; engine=$s.AMEngineVersion; mode=\"$($s.AMRunningMode)\"; "
        "cloud=$p.MAPSReporting; samples=$p.SubmitSamplesConsent; pua=$p.PUAProtection; "
        "network=$p.EnableNetworkProtection; cfa=$p.EnableControlledFolderAccess; "
        "scanavg=$p.ScanAvgCPULoadFactor; lowcpu=$p.EnableLowCpuPriority; "
        "archives=(-not $p.DisableArchiveScanning); removable=(-not $p.DisableRemovableDriveScanning); "
        "ex_path=@($p.ExclusionPath); ex_proc=@($p.ExclusionProcess); "
        "ex_ext=@($p.ExclusionExtension); cfa_apps=@($p.ControlledFolderAccessAllowedApplications); "
        "cfa_dirs=@($p.ControlledFolderAccessProtectedFolders)"
        "} | ConvertTo-Json -Compress -Depth 3", timeout=60)
    d = _json(out) if rc == 0 else None
    if not isinstance(d, dict):
        return {"error": out[:300] or "Defender недоступен (отключён или установлен другой антивирус)"}
    for k in ("ex_path", "ex_proc", "ex_ext", "cfa_apps", "cfa_dirs"):
        v = d.get(k) or []
        d[k] = [x for x in (v if isinstance(v, list) else [v]) if x and "N/A" not in str(x)]
    return d


def others():
    """Все антивирусы, зарегистрированные в Центре безопасности."""
    if not IS_WINDOWS:
        return []
    rc, out = _ps("@(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct |"
                  " % { [ordered]@{name=$_.displayName; state=$_.productState; path=$_.pathToSignedProductExe} })"
                  " | ConvertTo-Json -Compress", timeout=30)
    d = _json(out)
    if isinstance(d, dict):
        d = [d]
    res = []
    for x in d or []:
        st = int(x.get("state") or 0)
        res.append({"name": x.get("name"), "path": x.get("path"),
                    "on": bool(st & 0x1000), "uptodate": not (st & 0x10)})
    return res


TAMPER_HINT = ("Включена «Защита от подделки» (Tamper Protection) — Windows не даёт программам "
               "менять настройки Defender. Отключите её вручную: Безопасность Windows → Защита от "
               "вирусов и угроз → Управление настройками → Защита от подделки.")


def _set(expr, label):
    rc, out = _ps(f"Set-MpPreference {expr}")
    config.log(f"Defender: {label} → {'OK' if rc == 0 else out[:200]}")
    return rc == 0, out or label


def set_realtime(on: bool):
    ok, out = _set(f"-DisableRealtimeMonitoring ${str(not on).lower()}",
                   "реальное время " + ("вкл" if on else "выкл"))
    if ok:
        st = status()
        if not st.get("error") and bool(st.get("rt")) != on:
            return False, TAMPER_HINT
    return ok, out


SETTINGS = {
    # ключ: (подпись, выражение для «вкл», выражение для «выкл»)
    "behavior": ("Мониторинг поведения", "-DisableBehaviorMonitoring $false",
                 "-DisableBehaviorMonitoring $true"),
    "ioav": ("Проверка загруженных файлов и вложений", "-DisableIOAVProtection $false",
             "-DisableIOAVProtection $true"),
    "cloud": ("Облачная защита", "-MAPSReporting Advanced", "-MAPSReporting Disabled"),
    "samples": ("Автоматическая отправка образцов", "-SubmitSamplesConsent SendSafeSamples",
                "-SubmitSamplesConsent NeverSend"),
    "pua": ("Блокировать потенциально нежелательные программы", "-PUAProtection Enabled",
            "-PUAProtection Disabled"),
    "network": ("Защита сети", "-EnableNetworkProtection Enabled",
                "-EnableNetworkProtection Disabled"),
    "cfa": ("Контролируемый доступ к папкам (защита от шифровальщиков)",
            "-EnableControlledFolderAccess Enabled", "-EnableControlledFolderAccess Disabled"),
    "archives": ("Проверять архивы", "-DisableArchiveScanning $false",
                 "-DisableArchiveScanning $true"),
    "removable": ("Проверять флешки при полной проверке", "-DisableRemovableDriveScanning $false",
                  "-DisableRemovableDriveScanning $true"),
    "lowcpu": ("Проверки с низким приоритетом CPU", "-EnableLowCpuPriority $true",
               "-EnableLowCpuPriority $false"),
}


def is_on(key, st):
    v = st.get(key)
    if key == "cloud":
        return bool(v) and v != 0
    if key == "samples":
        return v not in (2, None)
    if key in ("pua", "network", "cfa"):
        return v == 1
    return bool(v)


def set_setting(key, on):
    label, a, b = SETTINGS[key]
    return _set(a if on else b, f"{label} {'вкл' if on else 'выкл'}")


def set_cpu_load(percent: int):
    return _set(f"-ScanAvgCPULoadFactor {max(5, min(100, int(percent)))}",
                f"нагрузка CPU при проверке {percent}%")


# Исключения: «не трогать» эти пути / процессы / расширения
KINDS = {"path": "ExclusionPath", "proc": "ExclusionProcess", "ext": "ExclusionExtension",
         "cfa_app": "ControlledFolderAccessAllowedApplications",
         "cfa_dir": "ControlledFolderAccessProtectedFolders"}


def add(kind, value):
    rc, out = _ps(f"Add-MpPreference -{KINDS[kind]} {_q(value)}")
    config.log(f"Defender: добавлено {KINDS[kind]} = {value} → {'OK' if rc == 0 else out[:200]}")
    return rc == 0, out


def remove(kind, value):
    rc, out = _ps(f"Remove-MpPreference -{KINDS[kind]} {_q(value)}")
    config.log(f"Defender: удалено {KINDS[kind]} = {value} → {'OK' if rc == 0 else out[:200]}")
    return rc == 0, out


def scan(kind="quick", path=None):
    if kind == "custom":
        script = f"Start-MpScan -ScanType CustomScan -ScanPath {_q(path)}"
    else:
        script = f"Start-MpScan -ScanType {'FullScan' if kind == 'full' else 'QuickScan'}"
    rc, out = _ps(script, timeout=6 * 3600)
    return rc == 0, out or "Проверка завершена"


def offline_scan():
    rc, out = _ps("Start-MpWDOScan")
    return rc == 0, out or "Компьютер перезагрузится для автономной проверки"


def update_signatures():
    rc, out = _ps("Update-MpSignature", timeout=600)
    return rc == 0, out or "Базы обновлены"


def threats():
    rc, out = _ps("@(Get-MpThreatDetection | select -First 50 | % { [ordered]@{"
                  "time=\"$($_.InitialDetectionTime)\"; res=@($_.Resources) -join '; '; "
                  "id=$_.ThreatID; action=$_.ActionSuccess; "
                  "name=(Get-MpThreat -ThreatID $_.ThreatID -ErrorAction SilentlyContinue).ThreatName} })"
                  " | ConvertTo-Json -Compress -Depth 3", timeout=60)
    d = _json(out)
    if isinstance(d, dict):
        d = [d]
    return d or []


def open_security_center(page="threat"):
    target = {"threat": "windowsdefender://threat", "settings": "windowsdefender://threatsettings",
              "ransom": "windowsdefender://ransomwareprotection"}.get(page, "windowsdefender:")
    return run(["cmd", "/c", "start", "", target])
