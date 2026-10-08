"""Мониторинг ресурсов системы с историей для графиков."""
import collections
import time
import psutil

HISTORY = 90  # точек истории (~2 минуты при 1.5 с)


class SystemMonitor:
    def __init__(self):
        self._net = None
        self._disk = None
        self._t = None
        self.hist = {k: collections.deque([0.0] * HISTORY, maxlen=HISTORY)
                     for k in ("cpu", "ram", "net_down", "net_up", "disk")}
        psutil.cpu_percent(None)
        self._n = 0
        self._disks = []
        self._freq = 0
        self._boot = psutil.boot_time()
        self._cores_phys = psutil.cpu_count(logical=False) or 0
        self._cores_log = psutil.cpu_count() or 0

    def _scan_disks(self):
        disks, seen = [], set()
        for part in psutil.disk_partitions(all=False):
            if part.device in seen or "cdrom" in (part.opts or ""):
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue
            seen.add(part.device)
            disks.append({"device": part.device.rstrip("\\") or part.device,
                          "mount": part.mountpoint, "fstype": part.fstype,
                          "total": u.total, "used": u.used, "free": u.free,
                          "percent": u.percent})
        return disks

    def sample(self, nproc=None) -> dict:
        now = time.monotonic()
        dt = max(0.2, now - (self._t or now - 1))
        self._t = now

        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        net = psutil.net_io_counters()
        dio = psutil.disk_io_counters()

        up = down = 0.0
        if self._net:
            up = max(0, net.bytes_sent - self._net.bytes_sent) / dt
            down = max(0, net.bytes_recv - self._net.bytes_recv) / dt
        dread = dwrite = 0.0
        if self._disk and dio:
            dread = max(0, dio.read_bytes - self._disk.read_bytes) / dt
            dwrite = max(0, dio.write_bytes - self._disk.write_bytes) / dt
        self._net, self._disk = net, dio

        # разделы дисков и частота CPU меняются редко, а опрос дорогой (особенно сетевые
        # диски на Windows) — обновляем раз в ~10 замеров
        if self._n % 10 == 0:
            try:
                self._disks = self._scan_disks()
            except Exception:
                pass
        if self._n % 5 == 0:
            try:
                f = psutil.cpu_freq()
                self._freq = f.current if f else 0
            except Exception:
                self._freq = 0
        self._n += 1
        disks = self._disks

        per_core = psutil.cpu_percent(None, percpu=True)
        cpu = sum(per_core) / len(per_core) if per_core else 0.0
        freq_mhz = self._freq

        data = {
            "cpu": cpu, "cpu_per_core": per_core, "cpu_freq": freq_mhz,
            "cores_phys": self._cores_phys,
            "cores_log": self._cores_log,
            "ram_percent": vm.percent, "ram_used": vm.used,
            "ram_total": vm.total, "ram_free": vm.available,
            "swap_percent": sw.percent, "swap_used": sw.used, "swap_total": sw.total,
            "net_up": up, "net_down": down,
            "disk_read": dread, "disk_write": dwrite,
            "disks": disks, "processes": nproc if nproc is not None else len(psutil.pids()),
            "boot_time": self._boot,
        }
        self.hist["cpu"].append(cpu)
        self.hist["ram"].append(vm.percent)
        self.hist["net_down"].append(down)
        self.hist["net_up"].append(up)
        self.hist["disk"].append(dread + dwrite)
        data["history"] = {k: list(v) for k, v in self.hist.items()}
        return data


def connections_by_pid():
    """Количество активных сетевых соединений на процесс."""
    out = {}
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.pid:
                out[c.pid] = out.get(c.pid, 0) + 1
    except Exception:
        pass
    return out


def top(rows, key, n=7):
    return sorted(rows, key=lambda r: r.get(key, 0) or 0, reverse=True)[:n]


def human(num, suffix="Б"):
    num = float(num or 0)
    if num < 1:
        return f"0 {suffix}"
    for unit in ("", "К", "М", "Г", "Т"):
        if abs(num) < 1024:
            return f"{num:.0f} {unit}{suffix}" if unit in ("", "К") else f"{num:.1f} {unit}{suffix}"
        num /= 1024
    return f"{num:.1f} П{suffix}"


def human_time(seconds) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d} д {h} ч {m} мин"
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин {s} с"
