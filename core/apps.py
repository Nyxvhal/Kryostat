"""Установленные программы: список и удаление.

Классические программы берутся из разделов Uninstall реестра (как «Программы и
компоненты»), приложения Microsoft Store — через Get-AppxPackage."""
import datetime
import json
import os
import re
import shlex
import subprocess
import sys

from . import cmdlog

IS_WINDOWS = sys.platform == "win32"
UNINSTALL_KEYS = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "Все пользователи"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "Все пользователи (32-бит)"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "Текущий пользователь"),
]
_GUID = re.compile(r"\{[0-9A-Fa-f\-]{36}\}")


def _val(k, name):
    import winreg
    try:
        v, _ = winreg.QueryValueEx(k, name)
        return v
    except OSError:
        return None


def _icon_path(raw, location):
    """DisplayIcon вида '"C:\\x\\app.exe",0' → путь к файлу."""
    if not raw:
        return ""
    p = str(raw).strip().strip('"')
    p = re.sub(r'",?\s*-?\d+$|,\s*-?\d+$', "", p).strip().strip('"')
    p = os.path.expandvars(p)
    return p if os.path.isfile(p) else ""


def _date(raw):
    s = str(raw or "").strip()
    if re.fullmatch(r"\d{8}", s):
        try:
            return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:])).isoformat()
        except ValueError:
            return ""
    return ""


def list_win32():
    if not IS_WINDOWS:
        return []
    import winreg
    hives = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    out, seen = [], set()
    for hive, path, scope in UNINSTALL_KEYS:
        try:
            root = winreg.OpenKey(hives[hive], path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    k = winreg.OpenKey(root, sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
                except OSError:
                    continue
                with k:
                    name = _val(k, "DisplayName")
                    if not name or _val(k, "SystemComponent") == 1:
                        continue
                    if _val(k, "ParentKeyName") or _val(k, "ReleaseType") in (
                            "Update", "Hotfix", "Security Update"):
                        continue                         # обновления — не отдельные программы
                    unin = _val(k, "UninstallString") or ""
                    quiet = _val(k, "QuietUninstallString") or ""
                    if not unin and not quiet:
                        continue
                    ver = str(_val(k, "DisplayVersion") or "")
                    dedup = (str(name).lower(), ver)
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    loc = _val(k, "InstallLocation") or ""
                    size = _val(k, "EstimatedSize")
                    out.append({
                        "kind": "win32", "id": f"{hive}\\{path}\\{sub}",
                        "name": str(name).strip(), "publisher": str(_val(k, "Publisher") or ""),
                        "version": ver, "date": _date(_val(k, "InstallDate")),
                        "size": int(size) * 1024 if isinstance(size, int) else 0,
                        "location": str(loc), "icon": _icon_path(_val(k, "DisplayIcon"), loc),
                        "uninstall": str(unin), "quiet": str(quiet), "scope": scope,
                        "msi": bool(_val(k, "WindowsInstaller")) or "msiexec" in str(unin).lower(),
                    })
    return out


def list_store():
    if not IS_WINDOWS:
        return []
    script = ("Get-AppxPackage -PackageTypeFilter Main | Where-Object { -not $_.NonRemovable "
              "-and -not $_.IsFramework -and $_.SignatureKind -ne 'System' } | "
              "Select-Object Name,PackageFullName,Version,Publisher,InstallLocation | "
              "ConvertTo-Json -Compress")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, timeout=60, creationflags=0x08000000)
        data = json.loads((p.stdout or b"[]").decode("utf-8", "ignore") or "[]")
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for d in data or []:
        name = d.get("Name") or ""
        pub = re.sub(r"^CN=([^,]+).*$", r"\1", d.get("Publisher") or "")
        pretty = name.split(".", 1)[-1] if "." in name else name
        pretty = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", pretty)
        out.append({"kind": "store", "id": d.get("PackageFullName") or name,
                    "name": pretty or name, "publisher": pub, "version": str(d.get("Version") or ""),
                    "date": "", "size": 0, "location": d.get("InstallLocation") or "",
                    "icon": "", "uninstall": "", "quiet": "", "scope": "Microsoft Store",
                    "msi": False, "package": name})
    return out


def _split_cmd(cmd):
    """UninstallString → (программа, аргументы). Пути с пробелами без кавычек — частое дело."""
    cmd = os.path.expandvars(cmd.strip())
    if cmd.startswith('"'):
        exe, _, rest = cmd[1:].partition('"')
        return exe, rest.strip()
    low = cmd.lower()
    for ext in (".exe", ".bat", ".cmd"):
        i = low.find(ext)
        if i >= 0:
            return cmd[:i + len(ext)], cmd[i + len(ext):].strip()
    parts = shlex.split(cmd, posix=False)
    return parts[0], " ".join(parts[1:])


def uninstall(app, quiet=False):
    """Запустить удаление и дождаться завершения деинсталлятора. → (ok, текст)."""
    if not IS_WINDOWS:
        return False, "Доступно только в Windows"
    if app["kind"] == "store":
        cid = cmdlog.command_start(f"Remove-AppxPackage {app['id']}")
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            f"Remove-AppxPackage -Package '{app['id']}'"],
                           capture_output=True, timeout=600, creationflags=0x08000000)
        out = (p.stdout + p.stderr).decode("utf-8", "ignore").strip()
        cmdlog.command_end(cid, p.returncode, out)
        return p.returncode == 0, out or "Приложение удалено"
    cmd = (app["quiet"] if quiet and app["quiet"] else app["uninstall"]) or app["quiet"]
    m = _GUID.search(cmd)
    if app.get("msi") and m and "msiexec" in cmd.lower():
        # «/I{GUID}» открывает «изменить», а не удалить — всегда /X
        args = ["msiexec.exe", "/x", m.group(0)] + (["/qb", "/norestart"] if quiet else [])
        line = " ".join(args)
        popen = dict(args=args)
    else:
        exe, rest = _split_cmd(cmd)
        line = f'"{exe}" {rest}'.strip()
        popen = dict(args=line)
    cid = cmdlog.command_start(line)
    try:
        # без перехвата вывода: деинсталляторы порождают дочерние процессы, и
        # перехваченные каналы держали бы Kryostat до их завершения
        p = subprocess.Popen(**popen, close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = p.wait(timeout=3600)
    except OSError as e:
        if getattr(e, "winerror", None) != 740:          # 740 — нужно повышение прав
            if isinstance(e, FileNotFoundError):
                cmdlog.command_end(cid, 1, "деинсталлятор не найден")
                return False, "Деинсталлятор не найден — программа, вероятно, уже удалена. " \
                              "Можно убрать запись из списка."
            cmdlog.command_end(cid, 1, str(e))
            return False, str(e)
        args = popen["args"]
        exe, rest = (args[0], " ".join(args[1:])) if isinstance(args, list) else _split_cmd(args)
        ps = (f"$p = Start-Process -FilePath '{exe}' -Verb RunAs -Wait -PassThru"
              + (f" -ArgumentList '{rest.replace(chr(39), chr(39) * 2)}'" if rest else "")
              + "; exit $p.ExitCode")
        try:
            rc = subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=3600,
                                creationflags=0x08000000).returncode
        except Exception as e2:
            cmdlog.command_end(cid, 1, str(e2))
            return False, str(e2)
    except FileNotFoundError:
        cmdlog.command_end(cid, 1, "деинсталлятор не найден")
        return False, "Деинсталлятор не найден — программа, вероятно, уже удалена. " \
                      "Можно убрать запись из списка."
    except Exception as e:
        cmdlog.command_end(cid, 1, str(e))
        return False, str(e)
    cmdlog.command_end(cid, rc, "")
    if rc in (0, 3010, 1641):
        return True, "Удаление завершено" + (" — нужна перезагрузка" if rc in (3010, 1641) else "")
    if rc in (1602, 1223):
        return False, "Удаление отменено"
    return False, f"Деинсталлятор завершился с кодом {rc}"


def still_installed(app):
    if app["kind"] == "store":
        return any(a["id"] == app["id"] for a in list_store())
    if not IS_WINDOWS:
        return False
    import winreg
    hive, _, sub = app["id"].partition("\\")
    h = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        winreg.OpenKey(h, sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY).Close()
        return True
    except OSError:
        return False


def remove_entry(app):
    """Убрать «битую» запись из списка установленных (с резервной копией раздела)."""
    from . import regtools
    regtools.delete_key(app["id"])
    cmdlog.action(f"удалена запись программы «{app['name']}» ({app['id']})")
    return True
