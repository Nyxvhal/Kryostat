"""Хранилище настроек Kryostat."""
import copy
import datetime
import json
import os
import threading

APP_NAME = "Kryostat"
VERSION = "1.0.0"


def _base_dir() -> str:
    candidates = [os.environ.get("ProgramData"), os.environ.get("LOCALAPPDATA"),
                  os.path.expanduser("~")]
    for root in candidates:
        if not root:
            continue
        path = os.path.join(root, APP_NAME)
        try:
            os.makedirs(path, exist_ok=True)
            # папка должна быть доступна на запись
            probe = os.path.join(path, ".w")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            return path
        except OSError:
            continue
    return os.getcwd()


BASE_DIR = _base_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "kryostat.log")
LOG_MAX = 1_000_000

DEFAULTS = {
    "disabled_startup": [],          # [{"name","command","location","key"}]
    "kill_on_boot": True,            # вырубать отключённые программы при старте ПК
    "autostart_self": False,
    "start_minimized": True,
    "limits": {},                    # "chrome.exe": {"ram_mb":2048,"cpu_percent":30,"priority":"below","net_kbps":0,"io_low":true}
    "update_defer": {"enabled": False, "last_run": None, "days_ahead": 1},
    "tweaks_applied": [],
    "auto_apply_tweaks": False,
    "refresh_ms": 1000,
    "theme": {"base": "midnight", "accent": "violet"},
    "language": "auto",              # auto = как в Windows; иначе код языка (ru, en, …)
    "sidebar_compact": False,
    "remember_page": True,
    "confirm_kill": True,
    "scan_tasks": True,
}

_lock = threading.RLock()


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "limits":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_cache = {"stamp": None, "data": None}


def _stamp():
    try:
        st = os.stat(CONFIG_PATH)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _load_unlocked() -> dict:
    """Чтение с кэшем: файл перечитывается только если он изменился на диске.
    Раньше каждый config.load() (а их десятки — терминал, таймеры, страницы)
    заново читал и разбирал JSON."""
    stamp = _stamp()
    if stamp is not None and stamp == _cache["stamp"]:
        return copy.deepcopy(_cache["data"])
    data = _read_unlocked()
    if stamp is not None:
        _cache["stamp"], _cache["data"] = stamp, copy.deepcopy(data)
    return data


def _read_unlocked() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config root is not an object")
        return _merge(DEFAULTS, data)
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except Exception as e:
        # повреждённый файл не затираем молча — сохраняем копию
        try:
            os.replace(CONFIG_PATH, CONFIG_PATH + ".bad")
        except OSError:
            pass
        log(f"config повреждён ({e}), сохранена копия config.json.bad")
        return copy.deepcopy(DEFAULTS)


def _save_unlocked(cfg: dict) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    _cache["stamp"], _cache["data"] = _stamp(), copy.deepcopy(cfg)


def load() -> dict:
    with _lock:
        return _load_unlocked()


def save(cfg: dict) -> None:
    with _lock:
        _save_unlocked(cfg)


def update(mutator) -> dict:
    """Атомарно: загрузить → изменить → сохранить.
    Исключает потерю изменений при записи из нескольких потоков."""
    with _lock:
        cfg = _load_unlocked()
        mutator(cfg)
        _save_unlocked(cfg)
        return cfg


def log(msg: str) -> None:
    try:
        from . import cmdlog
        cmdlog.info(msg)
    except Exception:
        pass
    try:
        with _lock:
            if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX:
                try:
                    os.replace(LOG_PATH, LOG_PATH + ".1")
                except OSError:
                    pass
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass
