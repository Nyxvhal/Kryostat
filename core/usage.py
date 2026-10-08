"""Собственная статистика Kryostat: сколько раз запускалась каждая программа
и сколько она проработала (по данным мониторинга процессов)."""
import json
import os
import threading
import time

from . import config

PATH = os.path.join(config.BASE_DIR, "usage.json")
_lock = threading.Lock()
_SKIP = {"system idle process", "system", "registry", "memory compression", "secure system",
         "svchost.exe", "conhost.exe", "runtimebroker.exe", "dllhost.exe", "backgroundtaskhost.exe",
         "smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe", "fontdrvhost.exe",
         "wmiprvse.exe", "searchprotocolhost.exe", "searchfilterhost.exe", "taskhostw.exe",
         "sihost.exe", "ctfmon.exe", "audiodg.exe", "wudfhost.exe", "dashost.exe"}


class Tracker:
    def __init__(self):
        self.data = self._load()           # name -> {"runs", "seconds", "first", "last", "exe"}
        self.seen = set()                  # (pid, create_time) уже учтённые
        self._t = None
        self._dirty = 0.0
        self.since = self.data.pop("__since__", {}).get("t") or time.time()

    def _load(self):
        try:
            with open(PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self):
        with _lock:
            d = dict(self.data)
            d["__since__"] = {"t": self.since}
            tmp = PATH + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False)
                os.replace(tmp, PATH)
            except OSError:
                pass

    def feed(self, rows, first=False):
        """rows — замер процессов. При первом вызове запущенные программы не считаем
        «запусками» (они стартовали до Kryostat), но время работы учитываем."""
        now = time.time()
        dt = 0 if self._t is None else min(30.0, now - self._t)
        self._t = now
        alive, running = set(), set()
        for r in rows:
            if r.get("kind") != "process":
                continue
            name = (r.get("name") or "").lower()
            if not name or name in _SKIP:
                continue
            key = (r["pid"], r.get("started"))
            alive.add(key)
            running.add(name)
            if key not in self.seen:
                self.seen.add(key)
                e = self.data.setdefault(name, {"runs": 0, "seconds": 0.0, "first": now,
                                                "last": now, "exe": r.get("exe") or "",
                                                "title": r.get("name")})
                if not first and (r.get("started") or now) >= self.since - 5:
                    e["runs"] += 1
                e["last"] = now
                if r.get("exe") and not e.get("exe"):
                    e["exe"] = r["exe"]
        for name in running:
            e = self.data.get(name)
            if e:
                e["seconds"] += dt
                e["last"] = now
        self.seen &= alive
        self._dirty += dt
        if self._dirty > 60:
            self._dirty = 0
            self.save()
        return running

    def reset(self):
        self.data = {}
        self.since = time.time()
        self.save()
