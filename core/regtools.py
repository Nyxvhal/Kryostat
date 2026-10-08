"""Редактор реестра: чтение, изменение, резервные копии (.reg) перед каждым изменением."""
import datetime
import os
import re

from .admin import IS_WINDOWS, run
from . import cmdlog, config

try:
    import winreg
except ImportError:
    winreg = None

ROOTS = [("HKEY_CLASSES_ROOT", "HKCR"), ("HKEY_CURRENT_USER", "HKCU"),
         ("HKEY_LOCAL_MACHINE", "HKLM"), ("HKEY_USERS", "HKU"),
         ("HKEY_CURRENT_CONFIG", "HKCC")]
SHORT = {long: short for long, short in ROOTS}
LONG = {short: long for long, short in ROOTS}
WOW64 = getattr(winreg, "KEY_WOW64_64KEY", 0) if winreg else 0
BACKUP_DIR = os.path.join(config.BASE_DIR, "registry-backups")

TYPES = {}
if winreg:
    TYPES = {winreg.REG_SZ: "REG_SZ", winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ",
             winreg.REG_DWORD: "REG_DWORD", winreg.REG_QWORD: "REG_QWORD",
             winreg.REG_BINARY: "REG_BINARY", winreg.REG_MULTI_SZ: "REG_MULTI_SZ",
             winreg.REG_NONE: "REG_NONE"}
TYPE_BY_NAME = {v: k for k, v in TYPES.items()}

FAVORITES = [
    ("Автозапуск (пользователь)", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"),
    ("Автозапуск (все)", r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
    ("Установленные программы", r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("Установленные (32-бит)", r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("Службы", r"HKLM\SYSTEM\CurrentControlSet\Services"),
    ("Политики Windows", r"HKLM\SOFTWARE\Policies\Microsoft\Windows"),
    ("Проводник (настройки)", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"),
    ("Контекстное меню файлов", r"HKCR\*\shell"),
    ("Сведения о Windows", r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion"),
    ("Переменные среды (система)", r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ("Переменные среды (пользователь)", r"HKCU\Environment"),
    ("Мультимедиа / игры", r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"),
]


def split(path: str):
    """'HKLM\\Software\\X' → ('HKLM', 'Software\\X'). Понимает полные имена и «Компьютер\\»."""
    p = path.strip().strip("\\").replace("/", "\\")
    p = re.sub(r"^(Компьютер|Computer)\\", "", p, flags=re.I)
    head, _, rest = p.partition("\\")
    h = head.upper()
    h = SHORT.get(h, h)
    if h not in LONG:
        raise ValueError(f"Неизвестный раздел: {head}")
    return h, rest


def _hive(short):
    return getattr(winreg, LONG[short])


def open_key(path, write=False):
    h, sub = split(path)
    access = winreg.KEY_READ | (winreg.KEY_WRITE if write else 0) | WOW64
    return winreg.OpenKey(_hive(h), sub, 0, access)


def subkeys(path):
    out = []
    with open_key(path) as k:
        i = 0
        while True:
            try:
                out.append(winreg.EnumKey(k, i))
            except OSError:
                break
            i += 1
    return sorted(out, key=str.lower)


def has_children(path):
    try:
        with open_key(path) as k:
            return winreg.QueryInfoKey(k)[0] > 0
    except OSError:
        return False


def values(path):
    out = []
    with open_key(path) as k:
        i = 0
        while True:
            try:
                name, data, t = winreg.EnumValue(k, i)
            except OSError:
                break
            out.append((name, data, t))
            i += 1
    return sorted(out, key=lambda x: (x[0] != "", x[0].lower()))


def fmt(data, t) -> str:
    if data is None:
        return "(значение не задано)"
    if t in (getattr(winreg, "REG_DWORD", -1), getattr(winreg, "REG_QWORD", -1)):
        return f"0x{int(data):08x} ({int(data)})"
    if t == getattr(winreg, "REG_BINARY", -1) or isinstance(data, bytes):
        b = bytes(data or b"")
        s = " ".join(f"{x:02x}" for x in b[:64])
        return s + (" …" if len(b) > 64 else "")
    if t == getattr(winreg, "REG_MULTI_SZ", -1):
        return " | ".join(data or [])
    return str(data)


def parse(text: str, tname: str):
    """Текст из редактора → данные нужного типа."""
    if tname in ("REG_DWORD", "REG_QWORD"):
        s = text.strip().lower()
        v = int(s, 16) if s.startswith("0x") else int(s or "0")
        lim = 0xFFFFFFFF if tname == "REG_DWORD" else 0xFFFFFFFFFFFFFFFF
        if v < 0:
            v &= lim
        if v > lim:
            raise ValueError("Слишком большое число для " + tname)
        return v
    if tname == "REG_BINARY":
        hexs = re.sub(r"[^0-9a-fA-F]", "", text)
        if len(hexs) % 2:
            raise ValueError("Нечётное число шестнадцатеричных цифр")
        return bytes.fromhex(hexs)
    if tname == "REG_MULTI_SZ":
        return [x for x in text.replace("\r", "").split("\n")]
    return text


def backup(path) -> str:
    """Экспорт раздела в .reg до изменения. Возвращает путь к файлу (или '')."""
    if not IS_WINDOWS:
        return ""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    h, sub = split(path)
    safe = re.sub(r"[^\w.-]+", "_", f"{h}_{sub}")[-80:]
    fn = os.path.join(BACKUP_DIR, f"{datetime.datetime.now():%Y%m%d-%H%M%S}_{safe}.reg")
    rc, _ = run(["reg", "export", f"{h}\\{sub}" if sub else h, fn, "/y"], timeout=120)
    return fn if rc == 0 else ""


def set_value(path, name, tname, data):
    backup(path)
    with open_key(path, write=True) as k:
        winreg.SetValueEx(k, name, 0, TYPE_BY_NAME[tname], data)
    cmdlog.action(f"reg: {path} · «{name or '(По умолчанию)'}» = {fmt(data, TYPE_BY_NAME[tname])}")


def delete_value(path, name):
    backup(path)
    with open_key(path, write=True) as k:
        winreg.DeleteValue(k, name)
    cmdlog.action(f"reg: {path} · удалено значение «{name}»")


def create_key(path, name):
    h, sub = split(path)
    full = (sub + "\\" + name) if sub else name
    winreg.CreateKeyEx(_hive(h), full, 0, winreg.KEY_READ | WOW64).Close()
    cmdlog.action(f"reg: создан раздел {h}\\{full}")
    return f"{h}\\{full}"


def delete_key(path):
    """Удалить раздел со всеми подразделами (после резервной копии)."""
    h, sub = split(path)
    if not sub or sub.count("\\") < 1 and h in ("HKLM", "HKU"):
        raise PermissionError("Корневые разделы удалять нельзя")
    backup(path)
    rc, out = run(["reg", "delete", f"{h}\\{sub}", "/f"])
    if rc != 0:
        raise OSError(out or "Не удалось удалить раздел")
    return out


def rename_value(path, old, new):
    with open_key(path) as k:
        data, t = winreg.QueryValueEx(k, old)
    backup(path)
    with open_key(path, write=True) as k:
        winreg.SetValueEx(k, new, 0, t, data)
        winreg.DeleteValue(k, old)
    cmdlog.action(f"reg: {path} · «{old}» → «{new}»")


def export(path, fn):
    h, sub = split(path)
    return run(["reg", "export", f"{h}\\{sub}" if sub else h, fn, "/y"], timeout=300)


def import_file(fn):
    return run(["reg", "import", fn], timeout=300)


def search(path, text, limit=300, stop=lambda: False, names=True, vals=True, data=True):
    """Поиск по разделам/значениям/данным (генератор найденных (путь, имя, данные))."""
    t = text.lower()
    found = 0
    stack = [path]
    while stack and found < limit and not stop():
        cur = stack.pop()
        try:
            subs = subkeys(cur)
        except OSError:
            continue
        for s in reversed(subs):
            stack.append(cur + "\\" + s)
        if names and t in cur.rsplit("\\", 1)[-1].lower():
            found += 1
            yield cur, None, None
        if vals or data:
            try:
                vs = values(cur)
            except OSError:
                continue
            for n, d, ty in vs:
                if (vals and t in n.lower()) or (data and t in fmt(d, ty).lower()):
                    found += 1
                    yield cur, n, fmt(d, ty)
                    if found >= limit:
                        break
