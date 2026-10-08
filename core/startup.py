"""Автозапуск: реестр, папки Startup, планировщик. Дубликаты и обратимое отключение."""
import hashlib
import os
import re
import struct
import subprocess
from .admin import IS_WINDOWS, CREATE_NO_WINDOW, decode, run
from . import cmdlog, config

try:
    import winreg
except ImportError:
    winreg = None

# (улей, ключ, подпись, ключ StartupApproved или None)
# Все ключи открываются в 64-битном представлении реестра (KEY_WOW64_64KEY), поэтому
# 32-битная сборка Kryostat видит те же записи, а WOW6432Node указывается явно.
_SA = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
RUN_KEYS = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run", _SA + r"\Run"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce", None),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run", None),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run", _SA + r"\Run"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\RunOnce", None),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run", None),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", _SA + r"\Run32"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce", None),
]
APPROVED_KEY = {(h, k): sa for h, k, sa in RUN_KEYS}
HIVES = {"HKCU": getattr(winreg, "HKEY_CURRENT_USER", None),
         "HKLM": getattr(winreg, "HKEY_LOCAL_MACHINE", None)} if winreg else {}
WOW64 = getattr(winreg, "KEY_WOW64_64KEY", 0) if winreg else 0

BACKUP_SUBKEY = r"Software\Kryostat\DisabledStartup"
OFF_SUFFIX = ".kryostat-off"

# приложения из Microsoft Store (StartupTask): состояние хранится в DWORD «State»
APPX_ROOT = (r"Software\Classes\Local Settings\Software\Microsoft\Windows"
             r"\CurrentVersion\AppModel")
APPX_STATE_ON = {2, 4}                 # 2 — включено, 4 — включено политикой
APPX_STATE_POLICY = {3, 4}             # задано политикой — менять нельзя


def _open(hive, path, write=False):
    access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if write else 0) | WOW64
    return winreg.OpenKey(hive, path, 0, access)


def _create(hive, path):
    return winreg.CreateKeyEx(hive, path, 0,
                              winreg.KEY_READ | winreg.KEY_SET_VALUE | WOW64)


def _approved_disabled(hive_name, sa_key, name) -> bool:
    """Отключена ли запись в Диспетчере задач / Параметрах (StartupApproved)."""
    if not sa_key:
        return False
    try:
        with _open(HIVES[hive_name], sa_key) as k:
            val, _ = winreg.QueryValueEx(k, name)
        return bool(val) and isinstance(val, (bytes, bytearray)) and bool(val[0] & 1)
    except OSError:
        return False


def _approved_set(hive_name, sa_key, name, enabled: bool):
    """Тот же механизм, что у Диспетчера задач: 02… — включено, 03… + время — отключено."""
    import time
    if enabled:
        data = b"\x02" + b"\x00" * 11
    else:
        ft = int((time.time() + 11644473600) * 10_000_000)
        data = b"\x03\x00\x00\x00" + struct.pack("<Q", ft)
    with _create(HIVES[hive_name], sa_key) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, data)
    cmdlog.action(f'reg: {hive_name}\\{sa_key} · «{name}» = '
                  f'{"включено" if enabled else "отключено"}')


def _startup_folders():
    """(подпись, папка, улей для StartupApproved\\StartupFolder)."""
    out = []
    appdata = os.environ.get("APPDATA")
    pdata = os.environ.get("ProgramData")
    if appdata:
        out.append(("Папка автозагрузки · пользователь",
                    os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    "HKCU"))
    if pdata:
        out.append(("Папка автозагрузки · все пользователи",
                    os.path.join(pdata, r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    "HKLM"))
    return out


FOLDER_SA = _SA + r"\StartupFolder"


# Универсальные «хосты»: по имени такого exe нельзя судить о программе
# (rundll32 a.dll и rundll32 b.dll — разные вещи), поэтому они не участвуют
# в поиске дубликатов и не завершаются по имени.
GENERIC_HOSTS = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "rundll32.exe", "wscript.exe", "cscript.exe",
    "mshta.exe", "regsvr32.exe", "msiexec.exe", "explorer.exe", "schtasks.exe", "wmic.exe",
    "conhost.exe", "python.exe", "pythonw.exe", "py.exe", "java.exe", "javaw.exe",
    "node.exe", "dotnet.exe", "wsl.exe", "bash.exe", "start.exe", "taskhostw.exe",
}

# наши собственные записи автозапуска — не показываем и не считаем дубликатами
SELF_NAMES = {"kryostat", "kryostatboot"}


def _norm_target(cmd: str) -> str:
    """Имя исполняемого файла — основа для поиска дубликатов."""
    if not cmd:
        return ""
    c = cmd.strip()
    m = re.match(r'^"([^"]+)"', c)
    if m:
        path = m.group(1)
    else:
        # команда без кавычек: берём до первого пробела, но учитываем .exe в пути
        m2 = re.match(r"^(.*?\.(?:exe|bat|cmd|com|scr|lnk|vbs|ps1))(\s|$)", c, re.I)
        path = m2.group(1) if m2 else c.split(" ")[0]
    path = os.path.expandvars(path).strip('"')
    # basename корректно режет и обратные слэши, и прямые (в том числе вне Windows)
    base = path.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    return "" if base in GENERIC_HOSTS else base


def _norm_cmd(cmd: str) -> str:
    c = re.sub(r"\s+", " ", os.path.expandvars((cmd or "").strip())).lower()
    return "" if c in ("", "com handler", "n/a", "н/д") else c


def resolve_lnk(path: str) -> str:
    """Цель ярлыка .lnk (разбор бинарного формата, без COM/PowerShell). Пустая строка, если не вышло."""
    try:
        with open(path, "rb") as f:
            d = f.read(1 << 16)
        if len(d) < 0x4C or struct.unpack_from("<I", d, 0)[0] != 0x4C:
            return ""
        flags = struct.unpack_from("<I", d, 0x14)[0]
        off = 0x4C
        if flags & 0x01:                                   # HasLinkTargetIDList
            off += 2 + struct.unpack_from("<H", d, off)[0]
        if not flags & 0x02:                               # нет LinkInfo
            return ""
        base = off
        size, hdr, lflags, _vol, local_off, _net, suffix_off = struct.unpack_from("<7I", d, base)
        if not lflags & 0x01 or not local_off:
            return ""
        if hdr >= 0x24:                                    # есть Unicode-варианты путей
            lo_u, so_u = struct.unpack_from("<2I", d, base + 0x1C)

            def ustr(o):
                end = o
                while end + 1 < len(d) and d[end:end + 2] != b"\x00\x00":
                    end += 2
                return d[o:end].decode("utf-16-le", "replace")
            local = ustr(base + lo_u) if lo_u else ""
            suffix = ustr(base + so_u) if so_u else ""
            if local:
                return local + suffix

        def astr(o):
            end = d.index(b"\x00", o)
            raw = d[o:end]
            for enc in ("mbcs", "cp1251", "latin-1"):
                try:
                    return raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return ""
        return astr(base + local_off) + (astr(base + suffix_off) if suffix_off else "")
    except Exception:
        return ""


def _uid(location, name, command):
    return hashlib.md5(f"{location}|{name}|{command}".encode("utf-8", "ignore")).hexdigest()[:12]


def _backup_name(key: str, name: str) -> str:
    """Имя значения в резервном ключе. Включает исходный ключ, поэтому одноимённые
    записи из Run и RunOnce / HKLM и WOW6432Node не затирают друг друга."""
    return f"{key}\\{name}"


def _scan_registry():
    items = []
    if not (IS_WINDOWS and winreg):
        return items
    seen = set()
    for hive_name, path, sa_key in RUN_KEYS:
        hive = HIVES.get(hive_name)
        try:
            with _open(hive, path) as k:
                for i in range(winreg.QueryInfoKey(k)[1]):
                    try:
                        name, val, _ = winreg.EnumValue(k, i)
                    except OSError:
                        continue
                    if not name:                     # значение «(По умолчанию)»
                        continue
                    seen.add((hive_name, path.lower(), name.lower()))
                    off = _approved_disabled(hive_name, sa_key, name)
                    items.append({"name": name, "command": str(val),
                                  "location": f"{hive_name}\\{path}", "type": "registry",
                                  "hive": hive_name, "key": path, "approved": sa_key,
                                  "enabled": not off})
        except OSError:
            pass
    # записи, отключённые старым способом (перенесены в резервный ключ)
    for hive_name in ("HKCU", "HKLM"):
        try:
            with _open(HIVES[hive_name], BACKUP_SUBKEY) as k:
                for i in range(winreg.QueryInfoKey(k)[1]):
                    try:
                        valname, val, _ = winreg.EnumValue(k, i)
                    except OSError:
                        continue
                    orig_key, _, cmd = str(val).partition("||")
                    prefix = orig_key + "\\"
                    name = valname[len(prefix):] if valname.startswith(prefix) else valname
                    if (hive_name, orig_key.lower(), name.lower()) in seen:
                        continue                     # оригинал уже на месте
                    items.append({"name": name, "command": cmd,
                                  "location": f"{hive_name}\\{orig_key}", "type": "registry",
                                  "hive": hive_name, "key": orig_key, "legacy": True,
                                  "approved": APPROVED_KEY.get((hive_name, orig_key)),
                                  "enabled": False})
        except OSError:
            pass
    return items


def _scan_folders():
    items = []
    for label, folder, sa_hive in _startup_folders():
        if not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for fn in names:
            if fn.lower() == "desktop.ini":
                continue
            full = os.path.join(folder, fn)
            if os.path.isdir(full):
                continue
            legacy_off = fn.lower().endswith(OFF_SUFFIX)
            real = fn[: -len(OFF_SUFFIX)] if legacy_off else fn
            target = ""
            if real.lower().endswith(".lnk"):
                target = resolve_lnk(full)
            off = legacy_off or (IS_WINDOWS and winreg
                                 and _approved_disabled(sa_hive, FOLDER_SA, real))
            items.append({"name": real, "command": target or full, "location": label,
                          "type": "folder", "path": full, "sa_hive": sa_hive,
                          "legacy": legacy_off, "enabled": not off})
    return items


# ------------------------------------------------------------ планировщик
_LOGON_TAGS = {"LogonTrigger": "при входе", "BootTrigger": "при загрузке",
               "SessionStateChangeTrigger": "при разблокировке/подключении"}
_OTHER_TAGS = {"TimeTrigger": "по времени", "CalendarTrigger": "по расписанию",
               "IdleTrigger": "при простое", "EventTrigger": "по событию",
               "RegistrationTrigger": "при создании"}


def _strip_ns(xml: str) -> str:
    xml = re.sub(r"<\?xml[^>]*\?>", "", xml)
    xml = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", xml)
    return re.sub(r"<(/?)\w+:", r"<\1", xml)


def parse_task_xml(name: str, xml: str):
    """Разобрать XML задачи (не зависит от языка Windows). None — если не разобралась."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(_strip_ns(xml).strip())
    except ET.ParseError:
        return None
    if root.tag != "Task":
        root = root.find(".//Task")
        if root is None:
            return None

    def flag(node, tag, default=True):
        el = node.find(tag) if node is not None else None
        if el is None or el.text is None:
            return default
        return el.text.strip().lower() == "true"

    enabled = flag(root.find("Settings"), "Enabled")
    hidden = flag(root.find("Settings"), "Hidden", False)
    triggers = []
    trig_root = root.find("Triggers")
    for t in (list(trig_root) if trig_root is not None else []):
        if not flag(t, "Enabled"):
            continue
        triggers.append(t.tag)
    cmds = []
    for ex in root.findall("Actions/Exec"):
        c = (ex.findtext("Command") or "").strip()
        a = (ex.findtext("Arguments") or "").strip()
        if c:
            cmds.append((f'"{c}"' if " " in c and not c.startswith('"') else c)
                        + (" " + a if a else ""))
    if not cmds and root.find("Actions/ComHandler") is not None:
        cmds.append("COM handler")
    author = (root.findtext("RegistrationInfo/Author") or "").strip()
    desc = (root.findtext("RegistrationInfo/Description") or "").strip()
    return {"task": name, "enabled": enabled, "hidden": hidden, "triggers": triggers,
            "command": " && ".join(cmds), "author": author, "description": desc}


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read(1 << 20)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "replace")
    try:
        return raw.decode("utf-16") if b"\x00" in raw[:64] else raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _tasks_dir():
    windir = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    # 32-битный процесс в 64-битной Windows видит System32 как SysWOW64 — обходим редирект
    native = os.path.join(windir, "Sysnative", "Tasks")
    return native if os.path.isdir(native) else os.path.join(windir, "System32", "Tasks")


def _task_sources():
    """[(имя задачи, xml)]: сначала файлы задач (быстро), затем schtasks /query /xml."""
    out, denied = [], False
    root = _tasks_dir()
    if os.path.isdir(root):
        for dirpath, dirnames, files in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            rel = "" if rel == "." else rel
            if rel.lower().startswith("microsoft\\windows") or \
                    rel.lower().replace("/", "\\").startswith("microsoft\\windows"):
                dirnames[:] = []
                continue
            for fn in files:
                try:
                    xml = _read_text(os.path.join(dirpath, fn))
                except PermissionError:
                    denied = True
                    continue
                except OSError:
                    continue
                name = "\\" + (rel.replace("/", "\\") + "\\" if rel else "") + fn
                out.append((name, xml))
    if out and not denied:
        return out
    # без прав администратора часть файлов не читается — спрашиваем планировщик
    cid = cmdlog.command_start(["schtasks", "/query", "/xml", "ONE"])
    try:
        p = subprocess.run(["schtasks", "/query", "/xml", "ONE"], capture_output=True,
                           timeout=90, creationflags=CREATE_NO_WINDOW)
        text = decode(p.stdout)
        cmdlog.command_end(cid, p.returncode, f"получено {len(text)} символов XML")
    except Exception as e:
        cmdlog.command_end(cid, 1, str(e))
        return out
    have = {n.lower() for n, _ in out}
    for m in re.finditer(r"<!--\s*(\\.*?)\s*-->\s*(<Task\b.*?</Task>)", text, re.S):
        name = m.group(1).strip()
        if name.lower() not in have:
            out.append((name, m.group(2)))
    return out


def _scan_tasks(all_tasks: bool = True):
    """Задачи планировщика сторонних программ.
    Задачи при входе/загрузке — всегда; остальные (обновлялки по расписанию) — если all_tasks."""
    items = []
    if not IS_WINDOWS:
        return items
    for name, xml in _task_sources():
        if name.lower().startswith("\\microsoft\\windows\\"):
            continue                       # системные задачи самой Windows не трогаем
        t = parse_task_xml(name, xml)
        if not t or not t["command"]:
            continue
        logon = [_LOGON_TAGS[x] for x in t["triggers"] if x in _LOGON_TAGS]
        other = [_OTHER_TAGS.get(x, "другое") for x in t["triggers"] if x not in _LOGON_TAGS]
        if not logon and not all_tasks:
            continue
        when = ", ".join(dict.fromkeys(logon + other)) or "без триггера"
        short = name.rsplit("\\", 1)[-1]
        folder = name.rsplit("\\", 1)[0] or "\\"
        items.append({"name": short, "command": t["command"],
                      "location": f"Планировщик · {folder}", "type": "task",
                      "task": name, "when": when, "boot": bool(logon),
                      "enabled": t["enabled"]})
    return items


# ------------------------------------------------- приложения Microsoft Store
def _appx_manifest_tasks(pfn: str):
    """{TaskId: (DisplayName, Executable)} из AppxManifest.xml установленного пакета."""
    out = {}
    pname, _, publisher = pfn.partition("_")
    try:
        with _open(HIVES["HKCU"], APPX_ROOT + r"\Repository\Packages") as k:
            n = winreg.QueryInfoKey(k)[0]
            fulls = []
            for i in range(n):
                try:
                    full = winreg.EnumKey(k, i)
                except OSError:
                    continue
                if full.lower().startswith(pname.lower() + "_") and \
                        full.lower().endswith("_" + publisher.lower()):
                    fulls.append(full)
            for full in sorted(fulls, reverse=True):
                try:
                    with _open(k, full) as pk:
                        root = winreg.QueryValueEx(pk, "PackageRootFolder")[0]
                except OSError:
                    continue
                man = os.path.join(root, "AppxManifest.xml")
                try:
                    xml = _strip_ns(_read_text(man))
                except OSError:
                    continue
                pdisp = re.search(r"<DisplayName>([^<]*)</DisplayName>", xml)
                pdisp = pdisp.group(1).strip() if pdisp else ""
                for m in re.finditer(r"<Application\b([^>]*)>(.*?)</Application>", xml, re.S):
                    attrs, body = m.group(1), m.group(2)
                    exe = re.search(r'Executable="([^"]+)"', attrs)
                    vis = re.search(r'<VisualElements\b[^>]*DisplayName="([^"]+)"', body)
                    for st in re.finditer(r"<StartupTask\b([^>]*)/?>", body):
                        a = st.group(1)
                        tid = re.search(r'TaskId="([^"]+)"', a)
                        sexe = re.search(r'Executable="([^"]+)"', a)
                        sdisp = re.search(r'DisplayName="([^"]+)"', a)
                        if not tid:
                            continue
                        disp = next((d for d in (sdisp and sdisp.group(1),
                                                 vis and vis.group(1), pdisp)
                                     if d and not d.startswith("ms-resource:")), "")
                        path = (sexe or exe).group(1) if (sexe or exe) else ""
                        out[tid.group(1)] = (disp, os.path.join(root, path) if path else "")
                if out:
                    break
    except OSError:
        pass
    return out


def _scan_appx():
    items = []
    if not (IS_WINDOWS and winreg):
        return items
    base = APPX_ROOT + r"\SystemAppData"
    try:
        with _open(HIVES["HKCU"], base) as k:
            for i in range(winreg.QueryInfoKey(k)[0]):
                try:
                    pfn = winreg.EnumKey(k, i)
                except OSError:
                    continue
                try:
                    pk = _open(k, pfn)
                except OSError:
                    continue
                with pk:
                    tasks = []
                    for j in range(winreg.QueryInfoKey(pk)[0]):
                        try:
                            tid = winreg.EnumKey(pk, j)
                            with _open(pk, tid) as tk:
                                state = int(winreg.QueryValueEx(tk, "State")[0])
                        except (OSError, ValueError, TypeError):
                            continue
                        tasks.append((tid, state))
                if not tasks:
                    continue
                meta = _appx_manifest_tasks(pfn)
                for tid, state in tasks:
                    disp, exe = meta.get(tid, ("", ""))
                    items.append({
                        "name": disp or pfn.split("_")[0].split(".")[-1] or pfn,
                        "command": exe or f"shell:AppsFolder\\{pfn}",
                        "location": f"Microsoft Store · {pfn.split('_')[0]}",
                        "type": "appx", "appx_key": f"{base}\\{pfn}\\{tid}",
                        "state": state, "policy": state in APPX_STATE_POLICY,
                        "enabled": state in APPX_STATE_ON})
    except OSError:
        pass
    return items


def scan(include_tasks: bool = True, all_tasks: bool = True):
    """Все элементы автозапуска + пометка дубликатов. Каждый источник — отдельно:
    сбой одного (нет прав, битый ключ) не должен опустошать весь список."""
    items = []
    for fn in (_scan_registry, _scan_folders, _scan_appx,
               (lambda: _scan_tasks(all_tasks)) if include_tasks else None):
        if fn is None:
            continue
        try:
            items += fn()
        except Exception as e:
            config.log(f"startup scan ({getattr(fn, '__name__', 'tasks')}): {e!r}")
    items = [it for it in items if it["name"].lower() not in SELF_NAMES
             and os.path.splitext(it["name"])[0].lower() not in SELF_NAMES]

    seen_ids = set()
    for it in items:
        uid = _uid(it["location"], it["name"], it["command"])
        while uid in seen_ids:
            uid = _uid(uid, it["name"], it["command"])
        seen_ids.add(uid)
        it["id"] = uid
        it["target"] = _norm_target(it["command"])
        it["dupkey"] = it["target"] or _norm_cmd(it["command"])
        for k in ("hive", "key", "path", "task", "approved", "sa_hive", "appx_key", "when"):
            it.setdefault(k, None)
        it.setdefault("legacy", False)
        it.setdefault("boot", it["type"] != "task")
    recount(items)
    items.sort(key=lambda x: (not x["duplicate"], x["name"].lower()))
    return items


def recount(items):
    """Пометить дубликаты. Учитываются только ВКЛЮЧЁННЫЕ записи, которые реально
    стартуют при входе (задачи «по расписанию» дубликатами не считаем)."""
    groups = {}
    for it in items:
        it["duplicate"], it["duplicate_count"] = False, 1
        if it["enabled"] and it.get("dupkey") and it.get("boot", True):
            groups.setdefault(it["dupkey"], []).append(it)
    for group in groups.values():
        if len(group) > 1:
            for it in group:
                it["duplicate"] = True
                it["duplicate_count"] = len(group)


_TYPE_RANK = {"registry": 0, "appx": 1, "task": 2, "folder": 3}


def pick_duplicates(items):
    """Из каждой группы дубликатов оставить по одной записи, вернуть остальные."""
    groups = {}
    for it in items:
        if it.get("duplicate") and it.get("enabled"):
            groups.setdefault(it["dupkey"], []).append(it)
    victims = []
    for g in groups.values():
        g.sort(key=lambda x: (_TYPE_RANK.get(x["type"], 9), x["location"], x["name"].lower()))
        victims.extend(g[1:])
    return victims


# ------------------------------------------------------------ переключение
def _registry_set(hive, key, name, cmd):
    kind = winreg.REG_EXPAND_SZ if "%" in cmd else winreg.REG_SZ
    with _create(hive, key) as k:
        winreg.SetValueEx(k, name, 0, kind, cmd)


def _restore_legacy(item):
    """Вернуть запись, которую старый способ отключения перенёс в резервный ключ."""
    hive = HIVES[item["hive"]]
    key, name = item["key"], item["name"]
    bname = _backup_name(key, name)
    cmd = item.get("command") or ""
    stored = None
    try:
        with _open(hive, BACKUP_SUBKEY, write=True) as b:
            for cand in (bname, name):            # новый формат, затем старый
                try:
                    val, _ = winreg.QueryValueEx(b, cand)
                except OSError:
                    continue
                saved_key, _, saved_cmd = str(val).partition("||")
                if cand == bname or saved_key == key:
                    stored = (cand, saved_cmd)
                    break
            if stored and stored[1]:
                cmd = stored[1]
            _registry_set(hive, key, name, cmd)   # сначала вернуть запись, потом удалить копию
            if stored:
                try:
                    winreg.DeleteValue(b, stored[0])
                except OSError:
                    pass
    except FileNotFoundError:
        _registry_set(hive, key, name, cmd)
    cmdlog.action(f'reg: {item["hive"]}\\{key} · «{name}» восстановлено из резервной копии')


def _backup_disable(item):
    """Отключение для ключей без StartupApproved (RunOnce, Policies): переносим значение
    в резервный ключ Software\\Kryostat\\DisabledStartup."""
    hive = HIVES[item["hive"]]
    key, name = item["key"], item["name"]
    bname = _backup_name(key, name)
    cmd = item.get("command") or ""
    try:
        with _open(hive, key, write=True) as k:
            try:
                cmd = str(winreg.QueryValueEx(k, name)[0])
            except OSError:
                pass
            with _create(hive, BACKUP_SUBKEY) as b:
                winreg.SetValueEx(b, bname, 0, winreg.REG_SZ, f"{key}||{cmd}")
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
                except OSError:
                    try:
                        winreg.DeleteValue(b, bname)
                    except OSError:
                        pass
                    raise
    except FileNotFoundError:
        with _create(hive, BACKUP_SUBKEY) as b:
            winreg.SetValueEx(b, bname, 0, winreg.REG_SZ, f"{key}||{cmd}")
    cmdlog.action(f'reg: {item["hive"]}\\{key} · «{name}» перенесено в резервную копию')


def _set_registry(item: dict, enabled: bool):
    sa = item.get("approved") or APPROVED_KEY.get((item["hive"], item["key"]))
    if enabled:
        if item.get("legacy"):
            _restore_legacy(item)
            item["legacy"] = False
        if sa:
            _approved_set(item["hive"], sa, item["name"], True)
    elif sa:
        _approved_set(item["hive"], sa, item["name"], False)
    else:
        _backup_disable(item)
        item["legacy"] = True


def _set_folder(item: dict, enabled: bool):
    p = item["path"]
    if enabled and p.lower().endswith(OFF_SUFFIX):       # отключено старой версией
        os.rename(p, p[: -len(OFF_SUFFIX)])
        item["path"] = p[: -len(OFF_SUFFIX)]
        cmdlog.action(f'ren "{p}" → «{os.path.basename(item["path"])}»')
    hive = item.get("sa_hive") or "HKCU"
    _approved_set(hive, FOLDER_SA, item["name"], enabled)
    item["legacy"] = False


def _set_appx(item: dict, enabled: bool):
    if item.get("policy"):
        raise PermissionError("Состояние задано групповой политикой")
    state = 2 if enabled else 1
    with _open(HIVES["HKCU"], item["appx_key"], write=True) as k:
        winreg.SetValueEx(k, "State", 0, winreg.REG_DWORD, state)
    item["state"] = state
    cmdlog.action(f'reg: HKCU\\{item["appx_key"]} · State = {state}')


def set_enabled(item: dict, enabled: bool):
    """Включить/отключить элемент автозапуска. Полностью обратимо."""
    if not IS_WINDOWS:
        return False, "Только Windows"
    t = item.get("type")
    try:
        if t == "registry":
            _set_registry(item, enabled)
        elif t == "folder":
            _set_folder(item, enabled)
        elif t == "appx":
            _set_appx(item, enabled)
        elif t == "task":
            verb = "/ENABLE" if enabled else "/DISABLE"
            rc, out = run(["schtasks", "/Change", "/TN", item["task"], verb])
            if rc != 0:
                return False, out or "Не удалось изменить задачу (нужны права администратора?)"
        else:
            return False, "Неизвестный тип записи"
    except PermissionError as e:
        return False, str(e) if "политик" in str(e) else "Нужны права администратора"
    except Exception as e:
        return False, str(e)

    def _mut(cfg):
        lst = [x for x in cfg.get("disabled_startup", []) if x.get("id") != item["id"]]
        if not enabled:
            lst.append({"id": item["id"], "name": item["name"], "command": item["command"],
                        "target": item.get("target") or _norm_target(item["command"]),
                        "location": item["location"], "type": t,
                        "hive": item.get("hive"), "key": item.get("key"),
                        "path": item.get("path"), "task": item.get("task")})
        cfg["disabled_startup"] = lst
    config.update(_mut)
    config.log(f"Автозапуск {'включён' if enabled else 'отключён'}: {item['name']}")
    return True, "Готово"
