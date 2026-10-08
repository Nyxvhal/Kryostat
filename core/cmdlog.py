"""Журнал действий Kryostat для встроенного терминала.

Сюда попадают все внешние команды, которые выполняет приложение (reg, schtasks,
PowerShell…), их вывод и код возврата, а также записи журнала (config.log).
Потокобезопасно: команды выполняются в фоновых потоках, а терминал подписывается
на события и получает их через Qt-сигнал в GUI-потоке."""
import datetime
import itertools
import subprocess
import threading
from collections import deque

_lock = threading.Lock()
_ids = itertools.count(1)
_history = deque(maxlen=600)
_listeners = []

OUT_MAX = 6000          # длинный вывод (schtasks /query) в терминале обрезаем


def _now():
    return datetime.datetime.now().strftime("%H:%M:%S")


def _emit(ev: dict):
    with _lock:
        _history.append(ev)
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(ev)
        except Exception:
            pass


def subscribe(fn):
    """fn(event) вызывается из любого потока. Возвращает уже накопленную историю."""
    with _lock:
        _listeners.append(fn)
        return list(_history)


def unsubscribe(fn):
    with _lock:
        if fn in _listeners:
            _listeners.remove(fn)


def cmd_text(cmd) -> str:
    if isinstance(cmd, str):
        return cmd
    try:
        return subprocess.list2cmdline([str(c) for c in cmd])
    except Exception:
        return " ".join(map(str, cmd))


def command_start(cmd) -> int:
    cid = next(_ids)
    _emit({"kind": "cmd", "id": cid, "time": _now(), "text": cmd_text(cmd),
           "thread": threading.current_thread().name})
    return cid


def command_end(cid: int, rc, out: str):
    out = out or ""
    if len(out) > OUT_MAX:
        out = out[:OUT_MAX] + f"\n… (вывод обрезан, всего {len(out)} символов)"
    _emit({"kind": "result", "id": cid, "time": _now(), "rc": rc, "text": out})


def action(text: str):
    """Действие без внешней команды: запись в реестр, переименование файла и т. п."""
    _emit({"kind": "action", "time": _now(), "text": text})


def info(text: str):
    _emit({"kind": "info", "time": _now(), "text": text})
