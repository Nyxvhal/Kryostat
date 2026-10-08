"""Монитор: что именно потребляет ресурсы."""
import time
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)
from core import monitor
from .theme import AMBER, BLUE, GREEN, MUTED, PURPLE, RED, TEXT2
from .widgets import (Card, CoreBars, SortItem, StatTile, UsageRow, file_icon, pill, title)


class _Item(SortItem, QTreeWidgetItem):
    pass


class DashboardPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._disk_keys, self._disk_rows = None, {}
        self._net_mode = None
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(title("Монитор ресурсов"))
        head.addStretch(1)
        self.pill_uptime = pill("аптайм —", MUTED)
        self.pill_procs = pill("— процессов", MUTED)
        head.addWidget(self.pill_procs)
        head.addWidget(self.pill_uptime)
        v.addLayout(head)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_cpu = StatTile("Процессор", "cpu", GREEN)
        self.t_ram = StatTile("Память", "chip", BLUE)
        self.t_disk = StatTile("Диск", "hdd", AMBER)
        self.t_net = StatTile("Сеть", "wifi", PURPLE)
        for t in (self.t_cpu, self.t_ram, self.t_disk, self.t_net):
            tiles.addWidget(t)
        v.addLayout(tiles)

        mid = QHBoxLayout()
        mid.setSpacing(14)

        cores = Card("Загрузка по ядрам")
        self.cores = CoreBars()
        cores.v.addWidget(self.cores, 1)
        self.cores_lbl = QLabel("")
        self.cores_lbl.setObjectName("Small")
        cores.v.addWidget(self.cores_lbl)
        cores.setMaximumHeight(250)
        mid.addWidget(cores, 1)

        self.card_disks = Card("Накопители")
        self.disks_box = QVBoxLayout()
        self.disks_box.setSpacing(10)
        self.card_disks.v.addLayout(self.disks_box)
        self.card_disks.v.addStretch(1)
        self.card_disks.setMaximumHeight(250)
        mid.addWidget(self.card_disks, 1)
        v.addLayout(mid)

        tops = QHBoxLayout()
        tops.setSpacing(14)
        self.top_cpu = self._top("Больше всех CPU", "Процесс", "CPU")
        self.top_ram = self._top("Больше всех памяти", "Процесс", "RAM")
        self.top_disk = self._top("Больше всех диска", "Процесс", "Диск/с")
        self.top_net = self._top("Больше всех сети", "Процесс", "↓ приём / ↑ отдача")
        for c, _ in (self.top_cpu, self.top_ram, self.top_disk, self.top_net):
            tops.addWidget(c)
        v.addLayout(tops, 1)

    def _top(self, name, c1, c2):
        card = Card(name)
        t = QTreeWidget()
        t.setHeaderLabels([c1, c2])
        t.setRootIsDecorated(False)
        t.setAlternatingRowColors(False)
        t.setFocusPolicy(Qt.NoFocus)
        t.setSelectionMode(QTreeWidget.NoSelection)
        t.header().setStretchLastSection(False)
        t.header().resizeSection(0, 120)
        t.setIconSize(QSize(16, 16))
        t.header().setSectionResizeMode(1, t.header().ResizeMode.Stretch)
        t.setMinimumHeight(230)
        card.v.addWidget(t)
        return card, t

    # ------------------------------------------------------------------
    def refresh(self, s, rows, conns):
        h = s["history"]
        self.t_cpu.set(f"{s['cpu']:.0f} %", h["cpu"], 100,
                       f"ядер: {s['cores_phys'] or '?'} · потоков: {s['cores_log']}"
                       + (f" · {s['cpu_freq']/1000:.1f} ГГц" if s["cpu_freq"] else ""),
                       percent=s["cpu"])
        self.t_ram.set(f"{s['ram_percent']:.0f} %", h["ram"], 100,
                       f"{monitor.human(s['ram_used'])} из {monitor.human(s['ram_total'])}"
                       f" · свободно {monitor.human(s['ram_free'])}",
                       percent=s["ram_percent"])
        self.t_disk.set(f"{monitor.human(s['disk_read'] + s['disk_write'])}/с",
                        h["disk"], max(h["disk"]) or 1,
                        f"чтение {monitor.human(s['disk_read'])}/с · "
                        f"запись {monitor.human(s['disk_write'])}/с",
                        autoscale=True)
        self.t_net.set(f"{monitor.human(s['net_down'])}/с",
                       h["net_down"], max(max(h["net_down"]), max(h["net_up"])) or 1,
                       f"приём {monitor.human(s['net_down'])}/с · "
                       f"отдача {monitor.human(s['net_up'])}/с",
                       autoscale=True)

        self.cores.set_values(s["cpu_per_core"])
        busiest = max(s["cpu_per_core"]) if s["cpu_per_core"] else 0
        self.cores_lbl.setText(
            f"Ядер: {len(s['cpu_per_core'])} · самое загруженное {busiest:.0f} % · "
            f"подкачка {s['swap_percent']:.0f} % "
            f"({monitor.human(s['swap_used'])} из {monitor.human(s['swap_total'])})")

        self.pill_uptime.setText(f"аптайм {monitor.human_time(time.time() - s['boot_time'])}")
        self.pill_procs.setText(f"{s['processes']} процессов")

        keys = [d["device"] for d in s["disks"]]
        if keys != self._disk_keys:
            self._disk_keys = keys
            while self.disks_box.count():
                w = self.disks_box.takeAt(0).widget()
                if w:
                    w.deleteLater()
            self._disk_rows = {}
            for d in s["disks"]:
                r = UsageRow()
                self._disk_rows[d["device"]] = r
                self.disks_box.addWidget(r)
        for d in s["disks"]:
            r = self._disk_rows.get(d["device"])
            if r:
                r.set(f'{d["device"]}  ({d["fstype"] or "—"})', d["percent"],
                      f'{monitor.human(d["free"])} свободно из {monitor.human(d["total"])}')

        procs_only = [r for r in rows if r["kind"] == "process"]
        self._fill(self.top_cpu[1], monitor.top(procs_only, "cpu"),
                   "cpu", lambda r: f'{r["cpu"]:.1f} %')
        self._fill(self.top_ram[1], monitor.top(procs_only, "ram"),
                   "ram", lambda r: monitor.human(r["ram"]))
        self._fill(self.top_disk[1], monitor.top(procs_only, "disk"),
                   "disk", lambda r: monitor.human(r["disk"]) + "/с")
        tree = self.top_net[1]
        if s.get("net_per_process"):
            if self._net_mode != "rate":
                self._net_mode = "rate"
                tree.setHeaderLabels(["Процесс", "↓ приём / ↑ отдача"])
                tree.setToolTip("")
            net_rows = sorted(({**r, "net": (r.get("net_down") or 0) + (r.get("net_up") or 0)}
                               for r in procs_only), key=lambda r: r["net"], reverse=True)
            net_rows = [r for r in net_rows if r["net"] >= 1][:6]
            od, ou = s.get("net_other_down", 0), s.get("net_other_up", 0)
            if od + ou >= 2048:
                net_rows.append({"name": "Другое (UDP/QUIC, система)", "pid": 0, "exe": "",
                                 "net": od + ou, "net_down": od, "net_up": ou})
            self._fill(tree, net_rows, "net", lambda r: f'↓ {monitor.human(r["net_down"])}/с  '
                                                        f'↑ {monitor.human(r["net_up"])}/с')
        else:
            if self._net_mode != "conn":
                self._net_mode = "conn"
                tree.setHeaderLabels(["Процесс", "Соединений"])
                tree.setToolTip("Скорость по процессам доступна в режиме администратора: "
                                + (s.get("net_note") or ""))
            net_rows = sorted(({**r, "conn": conns.get(r["pid"], 0)} for r in procs_only),
                              key=lambda r: r["conn"], reverse=True)[:7]
            self._fill(tree, [r for r in net_rows if r["conn"]],
                       "conn", lambda r: str(r["conn"]))

    def _fill(self, tree, rows, key, fmt):
        tree.clear()
        budget = [12]
        for r in rows:
            it = _Item([r["name"], fmt(r)])
            ic = file_icon(r.get("exe"), budget) if r.get("exe") else None
            if ic is not None:
                it.setIcon(0, ic)
            it.setData(1, SortItem.SORT_ROLE, r.get(key, 0))
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            if r.get("critical"):
                it.setForeground(0, QBrush(QColor(RED)))
            it.setToolTip(0, r.get("exe") or r["name"])
            tree.addTopLevelItem(it)
        if not rows:
            tree.addTopLevelItem(_Item(["нет данных", ""]))
