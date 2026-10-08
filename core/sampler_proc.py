"""Сбор данных о процессах в ОТДЕЛЬНОМ процессе.

psutil держит GIL, пока опрашивает процессы, соединения и службы. На Windows один
проход по ~300 процессам + net_connections + список служб занимает сотни миллисекунд,
и всё это время интерфейс (даже в другом потоке) стоял — отсюда рывки окна при
любой видеокарте. В отдельном процессе GIL свой, интерфейс не ждёт."""


class _State:
    def __init__(self):
        from . import monitor, netstat, procs
        self.sampler = procs.ProcessSampler()
        self.mon = monitor.SystemMonitor()
        self.net = netstat.NetSampler()


def collect(st, conns=False, services=False):
    """Один замер: процессы + система + трафик по процессам (+ соединения, службы)."""
    from . import monitor, procs
    rows = st.sampler.sample()
    system = st.mon.sample(len(rows))
    try:
        by_pid, ok, note = st.net.sample()
    except Exception as e:
        by_pid, ok, note = {}, False, f"ошибка: {e}"
    tcp_down = tcp_up = 0.0
    for r in rows:
        d, u = by_pid.get(r["pid"], (0.0, 0.0)) if r["pid"] else (0.0, 0.0)
        r["net_down"], r["net_up"] = d, u
        tcp_down += d
        tcp_up += u
    system["net_per_process"] = ok
    system["net_note"] = note
    # остаток, который не виден по TCP (UDP/QUIC, ICMP, системные службы)
    system["net_other_down"] = max(0.0, system["net_down"] - tcp_down) if ok else 0.0
    system["net_other_up"] = max(0.0, system["net_up"] - tcp_up) if ok else 0.0
    out = {"rows": rows, "system": system}
    if conns:
        out["conns"] = monitor.connections_by_pid()
    if services:
        out["services"] = procs.services()
    return out


def serve(conn):
    """Цикл дочернего процесса: запрос {"conns": bool, "services": bool} -> данные."""
    st = _State()
    while True:
        try:
            req = conn.recv()
        except (EOFError, OSError):
            break
        if req is None:
            break
        try:
            out = collect(st, req.get("conns"), req.get("services"))
        except Exception as e:                 # не роняем процесс из-за одного замера
            out = {"error": repr(e)}
        try:
            conn.send(out)
        except (EOFError, OSError):
            break


class RemoteSampler:
    """Сторона интерфейса. Если отдельный процесс не запустился — опрос в своём потоке."""

    def __init__(self):
        self.proc = self.conn = None
        self.st = None
        try:
            import multiprocessing as mp
            ctx = mp.get_context("spawn")
            parent, child = ctx.Pipe()
            p = ctx.Process(target=serve, args=(child,), daemon=True, name="kryostat-sampler")
            p.start()
            child.close()
            self.proc, self.conn = p, parent
        except Exception:
            self.proc = self.conn = None

    def sample(self, conns, services, timeout=15.0):
        if self.conn is not None:
            try:
                self.conn.send({"conns": conns, "services": services})
                if self.conn.poll(timeout):       # ожидание без GIL
                    d = self.conn.recv()
                    if "error" in d:
                        raise RuntimeError(d["error"])
                    return d
                raise TimeoutError("sampler не ответил")
            except (EOFError, OSError, BrokenPipeError, TimeoutError):
                self.close()                      # процесс умер или завис — дальше сами
        if self.st is None:
            self.st = _State()
        return collect(self.st, conns, services)

    def close(self):
        conn, proc, self.conn, self.proc = self.conn, self.proc, None, None
        try:
            if conn is not None:
                conn.send(None)
                conn.close()
        except Exception:
            pass
        try:
            if proc is not None:
                proc.join(0.5)
                if proc.is_alive():
                    proc.terminate()
        except Exception:
            pass
