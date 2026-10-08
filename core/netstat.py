"""Сетевой трафик по процессам (Windows).

Windows сама считает байты для каждого TCP-соединения (TCP Extended Statistics —
то же, чем пользуется «Монитор ресурсов»). Включаем сбор для каждого соединения
(SetPerTcpConnectionEStats, нужны права администратора), читаем счётчики
(GetPerTcpConnectionEStats) и суммируем по PID владельца.

UDP (в том числе QUIC — часть трафика YouTube/Chrome) так не считается: этот остаток
показывается отдельной строкой «Другое» = общий трафик адаптеров − сумма по TCP."""
import ctypes
import sys
import time

IS_WINDOWS = sys.platform == "win32"
AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_ALL = 5
TCP_STATE_LISTEN = 2
TcpConnectionEstatsData = 1


if IS_WINDOWS:
    from ctypes import wintypes
    DWORD, ULONG64, ULONG = wintypes.DWORD, ctypes.c_uint64, wintypes.ULONG

    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [("dwState", DWORD), ("dwLocalAddr", DWORD), ("dwLocalPort", DWORD),
                    ("dwRemoteAddr", DWORD), ("dwRemotePort", DWORD), ("dwOwningPid", DWORD)]

    class MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
        _fields_ = [("ucLocalAddr", ctypes.c_ubyte * 16), ("dwLocalScopeId", DWORD),
                    ("dwLocalPort", DWORD), ("ucRemoteAddr", ctypes.c_ubyte * 16),
                    ("dwRemoteScopeId", DWORD), ("dwRemotePort", DWORD),
                    ("dwState", DWORD), ("dwOwningPid", DWORD)]

    class MIB_TCPROW(ctypes.Structure):
        _fields_ = [("dwState", DWORD), ("dwLocalAddr", DWORD), ("dwLocalPort", DWORD),
                    ("dwRemoteAddr", DWORD), ("dwRemotePort", DWORD)]

    class MIB_TCP6ROW(ctypes.Structure):
        _fields_ = [("State", DWORD), ("LocalAddr", ctypes.c_ubyte * 16),
                    ("dwLocalScopeId", DWORD), ("dwLocalPort", DWORD),
                    ("RemoteAddr", ctypes.c_ubyte * 16), ("dwRemoteScopeId", DWORD),
                    ("dwRemotePort", DWORD)]

    class TCP_ESTATS_DATA_RW_v0(ctypes.Structure):
        _fields_ = [("EnableCollection", ctypes.c_ubyte)]

    class TCP_ESTATS_DATA_ROD_v0(ctypes.Structure):
        _fields_ = [("DataBytesOut", ULONG64), ("DataSegsOut", ULONG64),
                    ("DataBytesIn", ULONG64), ("DataSegsIn", ULONG64),
                    ("SegsOut", ULONG64), ("SegsIn", ULONG64),
                    ("SoftErrors", ULONG), ("SoftErrorReason", ULONG),
                    ("SndUna", ULONG), ("SndNxt", ULONG), ("SndMax", ULONG),
                    ("ThruBytesAcked", ULONG64), ("RcvNxt", ULONG),
                    ("ThruBytesReceived", ULONG64)]


class NetSampler:
    """sample() -> (by_pid {pid: (down_bps, up_bps)}, available: bool, note)."""

    def __init__(self):
        self.ok = IS_WINDOWS
        self.note = "" if IS_WINDOWS else "только Windows"
        self._last = {}            # ключ соединения -> (in, out)
        self._enabled = set()
        self._t = None
        if IS_WINDOWS:
            try:
                self.ip = ctypes.windll.iphlpapi
            except Exception:
                self.ok, self.note = False, "iphlpapi недоступна"

    # ------------------------------------------------------------ таблицы
    def _table(self, af):
        row_t = MIB_TCPROW_OWNER_PID if af == AF_INET else MIB_TCP6ROW_OWNER_PID
        size = DWORD(0)
        self.ip.GetExtendedTcpTable(None, ctypes.byref(size), False, af,
                                    TCP_TABLE_OWNER_PID_ALL, 0)
        for _ in range(3):
            buf = ctypes.create_string_buffer(size.value + 4096)
            size = DWORD(len(buf))
            rc = self.ip.GetExtendedTcpTable(buf, ctypes.byref(size), False, af,
                                             TCP_TABLE_OWNER_PID_ALL, 0)
            if rc == 0:
                n = DWORD.from_buffer(buf).value
                off = ctypes.sizeof(DWORD)       # dwNumEntries, дальше строки таблицы
                arr = (row_t * n).from_buffer(buf, off)
                return list(arr)
            if rc != 122:                 # ERROR_INSUFFICIENT_BUFFER
                return []
        return []

    def _row(self, r, af):
        if af == AF_INET:
            row = MIB_TCPROW(r.dwState, r.dwLocalAddr, r.dwLocalPort, r.dwRemoteAddr,
                             r.dwRemotePort)
            key = (4, r.dwLocalAddr, r.dwLocalPort, r.dwRemoteAddr, r.dwRemotePort,
                   r.dwOwningPid)
            return row, key, self.ip.GetPerTcpConnectionEStats, self.ip.SetPerTcpConnectionEStats
        row = MIB_TCP6ROW()
        row.State = r.dwState
        row.LocalAddr = r.ucLocalAddr
        row.dwLocalScopeId = r.dwLocalScopeId
        row.dwLocalPort = r.dwLocalPort
        row.RemoteAddr = r.ucRemoteAddr
        row.dwRemoteScopeId = r.dwRemoteScopeId
        row.dwRemotePort = r.dwRemotePort
        key = (6, bytes(r.ucLocalAddr), r.dwLocalPort, bytes(r.ucRemoteAddr), r.dwRemotePort,
               r.dwOwningPid)
        return row, key, self.ip.GetPerTcp6ConnectionEStats, self.ip.SetPerTcp6ConnectionEStats

    # ------------------------------------------------------------ замер
    def sample(self):
        if not self.ok:
            return {}, False, self.note
        now = time.monotonic()
        dt = max(0.2, now - self._t) if self._t else None
        self._t = now
        by_pid, seen, denied = {}, {}, 0
        rw_on = TCP_ESTATS_DATA_RW_v0(1)
        for af in (AF_INET, AF_INET6):
            try:
                rows = self._table(af)
            except Exception:
                rows = []
            for r in rows:
                if r.dwState == TCP_STATE_LISTEN or not r.dwOwningPid:
                    continue
                row, key, get, set_ = self._row(r, af)
                if key not in self._enabled:
                    rc = set_(ctypes.byref(row), TcpConnectionEstatsData, ctypes.byref(rw_on),
                              0, ctypes.sizeof(rw_on), 0)
                    if rc == 5:                       # ERROR_ACCESS_DENIED
                        denied += 1
                        continue
                    self._enabled.add(key)
                rod = TCP_ESTATS_DATA_ROD_v0()
                rc = get(ctypes.byref(row), TcpConnectionEstatsData, None, 0, 0, None, 0, 0,
                         ctypes.byref(rod), 0, ctypes.sizeof(rod))
                if rc != 0:
                    continue
                cur = (rod.DataBytesIn, rod.DataBytesOut)
                seen[key] = cur
                prev = self._last.get(key)
                if prev is not None and dt:
                    d_in = max(0, cur[0] - prev[0]) / dt
                    d_out = max(0, cur[1] - prev[1]) / dt
                    if d_in or d_out:
                        a = by_pid.get(r.dwOwningPid, (0.0, 0.0))
                        by_pid[r.dwOwningPid] = (a[0] + d_in, a[1] + d_out)
        self._last = seen
        self._enabled &= set(seen)              # закрытые соединения забываем
        if denied and not seen:
            return {}, False, "нужны права администратора"
        return by_pid, True, ""
