"""Диспетчер задач: все процессы + все службы (запущенные и остановленные)."""
import html
import os
import subprocess
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QHBoxLayout, QHeaderView,
                               QMenu, QMessageBox, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)
from core import config, limits, monitor, procs
from core.admin import CREATE_NO_WINDOW
from .icons import icon
from .theme import AMBER, BLUE, GREEN, MUTED, RED, TEXT, TEXT2
from .widgets import (SearchBox, SortItem, file_icon, pill, title, tool_button)
from .workers import run_async

COLS = ["Имя", "PID", "Состояние", "CPU", "Память", "Диск", "Сеть",
        "Потоки", "Пользователь", "Тип", "Путь"]
C_NAME, C_PID, C_STATE, C_CPU, C_RAM, C_DISK, C_NET, C_THR, C_USER, C_KIND, C_PATH = range(11)

FILTERS = [
    ("Всё", "all"), ("Процессы", "proc"), ("Службы", "svc"),
    ("Запущенные", "running"), ("Не запущенные", "stopped"),
    ("Критичные", "critical"), ("Сторонние", "thirdparty"),
]


class _Item(SortItem, QTreeWidgetItem):
    pass


class ProcessesPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.rows = {}
        self.items = {}
        self._paused = False
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(title("Процессы и службы"))
        head.addStretch(1)
        self.pill_info = pill("—", MUTED)
        head.addWidget(self.pill_info)
        v.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = SearchBox("Поиск по имени, PID или пути…")
        self.search.textChanged.connect(self.rebuild)
        bar.addWidget(self.search, 1)

        self.btn_pause = tool_button("Пауза", "stop", on_click=self.toggle_pause,
                                     tip="Заморозить список, чтобы спокойно выделить строки")
        self.btn_pause.setCheckable(True)
        self.btn_limit = tool_button("Ограничить", "sliders", "Primary", self.limit_selected)
        self.btn_kill = tool_button("Завершить", "x", "Danger", self.kill_selected)
        for b in (self.btn_pause, self.btn_limit, self.btn_kill):
            bar.addWidget(b)
        v.addLayout(bar)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, (label, key) in enumerate(FILTERS):
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setProperty("fkey", key)
            if i == 0:
                b.setChecked(True)
            self.group.addButton(b, i)
            chips.addWidget(b)
        self.group.idClicked.connect(lambda _: self.rebuild())
        chips.addStretch(1)
        chips.addWidget(pill("красные строки защищены", RED))
        v.addLayout(chips)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(C_CPU, Qt.DescendingOrder)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.menu)
        self.tree.itemDoubleClicked.connect(lambda *_: self.show_details())
        hdr = self.tree.header()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.headerItem().setToolTip(C_NET, "Скорость сети процесса (приём ↓ / отдача ↑) по TCP. "
                                                 "Нужны права администратора. Без них — число соединений.")
        for i, w in enumerate([260, 70, 130, 70, 90, 90, 170, 60, 150, 80, 400]):
            self.tree.setColumnWidth(i, w)
        for c in (C_PID, C_CPU, C_RAM, C_DISK, C_NET, C_THR):
            self.tree.headerItem().setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        v.addWidget(self.tree, 1)

    # --------------------------------------------------------------- state
    def toggle_pause(self):
        self._paused = self.btn_pause.isChecked()
        self.btn_pause.setText("Продолжить" if self._paused else "Пауза")

    def refresh(self, rows, services, conns):
        if self._paused:
            return
        self.conns = conns
        self.rows = {r["key"]: r for r in (rows + services)}
        self.rebuild()

    def _filter_key(self):
        b = self.group.checkedButton()
        return b.property("fkey") if b else "all"

    def _passes(self, r, q, f):
        if f == "proc" and r["kind"] != "process":
            return False
        if f == "svc" and r["kind"] != "service":
            return False
        if f == "running" and not r["running"]:
            return False
        if f == "stopped" and r["running"]:
            return False
        if f == "critical" and not r["critical"]:
            return False
        if f == "thirdparty":
            e = (r.get("exe") or "").lower().lstrip('"')
            if not e or e.startswith(procs.WINDOWS_DIR) or r["kind"] == "service" and "\\windows\\" in e:
                return False
        if q:
            hay = f'{r["name"]} {r.get("display","")} {r["pid"]} {r.get("exe","")}'.lower()
            if q not in hay:
                return False
        return True

    # ------------------------------------------------------------- render
    def rebuild(self):
        q = self.search.text().strip().lower()
        f = self._filter_key()
        conns = getattr(self, "conns", {})
        wanted = {k: r for k, r in self.rows.items() if self._passes(r, q, f)}

        self.tree.setUpdatesEnabled(False)
        sorting = self.tree.isSortingEnabled()
        self.tree.setSortingEnabled(False)

        for key in [k for k in self.items if k not in wanted]:
            it = self.items.pop(key)
            idx = self.tree.indexOfTopLevelItem(it)
            if idx >= 0:
                self.tree.takeTopLevelItem(idx)

        budget = [25]                       # новых значков за один проход — без рывков
        for key, r in wanted.items():
            it = self.items.get(key)
            if it is None:
                it = _Item()
                self.items[key] = it
                self.tree.addTopLevelItem(it)
                self._style(it, r)
            if not getattr(it, "_icon", False):
                if r["kind"] == "service":
                    it.setIcon(C_NAME, icon("settings", BLUE if r["running"] else MUTED, 16))
                    it._icon = True
                else:
                    ic = file_icon(r.get("exe"), budget)
                    if ic is not None:
                        it.setIcon(C_NAME, ic)
                        it._icon = True
            self._update(it, r, conns.get(r["pid"], 0))

        self.tree.setSortingEnabled(sorting)
        self.tree.setUpdatesEnabled(True)

        run = sum(1 for r in wanted.values() if r["running"])
        svc = sum(1 for r in wanted.values() if r["kind"] == "service")
        self.pill_info.setText(f"{len(wanted)} строк · {run} активно · "
                               f"{len(wanted) - run} остановлено · {svc} служб")

    def _style(self, it, r):
        if r["critical"]:
            br = QBrush(QColor(RED))
            fo = QFont(); fo.setBold(True)
            for c in range(len(COLS)):
                it.setForeground(c, br)
            it.setFont(C_NAME, fo)
            it.setToolTip(C_NAME, "Критично для работы Windows — завершение заблокировано")
        for c in (C_PID, C_CPU, C_RAM, C_DISK, C_NET, C_THR):
            it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        it.setData(0, Qt.UserRole, r["key"])

    def _update(self, it, r, net):
        name = f'{r["name"]} — {r["display"]}' if r.get("display") else r["name"]
        vals = {
            C_NAME: name,
            C_PID: str(r["pid"] or ""),
            C_STATE: self._state(r),
            C_CPU: f'{r["cpu"]:.1f}' if r["kind"] == "process" else "",
            C_RAM: monitor.human(r["ram"]) if r["ram"] else "",
            C_DISK: monitor.human(r["disk"]) + "/с" if r["disk"] > 1024 else "",
            C_NET: self._net_text(r, net),
            C_THR: str(r["threads"] or ""),
            C_USER: (r.get("user") or "").split("\\")[-1],
            C_KIND: "Служба" if r["kind"] == "service" else "Процесс",
            C_PATH: r.get("exe") or "",
        }
        for c, text in vals.items():
            if it.text(c) != text:
                it.setText(c, text)
        for c, val in ((C_PID, r["pid"] or 0), (C_CPU, r["cpu"]), (C_RAM, r["ram"]),
                       (C_DISK, r["disk"]),
                       (C_NET, (r.get("net_down") or 0) + (r.get("net_up") or 0) + net * 1e-6),
                       (C_THR, r["threads"])):
            if it.data(c, SortItem.SORT_ROLE) != val:
                it.setData(c, SortItem.SORT_ROLE, val)
        # цвета меняем только при изменении состояния: setForeground на сотнях строк
        # каждую секунду заставлял Qt перерисовывать всю таблицу
        if r["kind"] == "service" and not r["critical"]:
            look = ("svc", r["running"])
        elif not r["critical"] and r["kind"] == "process":
            look = ("proc", r["cpu"] > 20, r["ram"] > 1_500_000_000)
        else:
            look = None
        if look is not None and getattr(it, "_look", None) != look:
            it._look = look
            if look[0] == "svc":
                it.setForeground(C_NAME, QBrush(QColor(BLUE if look[1] else MUTED)))
            else:
                _, hot_cpu, hot_ram = look
                it.setForeground(C_CPU, QBrush(QColor(AMBER if hot_cpu else TEXT2)))
                it.setForeground(C_RAM, QBrush(QColor(AMBER if hot_ram else TEXT2)))
                it.setForeground(C_NAME, QBrush(QColor(TEXT if hot_cpu or hot_ram else TEXT2)))

    @staticmethod
    def _net_text(r, conns):
        d, u = r.get("net_down") or 0, r.get("net_up") or 0
        if d + u >= 1:
            return f"↓ {monitor.human(d)}/с  ↑ {monitor.human(u)}/с"
        return f"{conns} соед." if conns else ""

    def _state(self, r):
        if r["kind"] == "service":
            m = {"running": "Работает", "stopped": "Остановлена", "paused": "Пауза",
                 "start_pending": "Запускается", "stop_pending": "Останавливается"}
            t = {"automatic": "авто", "manual": "вручную", "disabled": "отключена",
                 "automatic_delayed": "авто (отлож.)"}
            return f'{m.get(r["status"], r["status"])} · {t.get(r.get("start_type"), "")}'
        return {"running": "Выполняется", "sleeping": "Ожидание",
                "stopped": "Приостановлен", "zombie": "Зомби"}.get(r["status"], r["status"])

    # ------------------------------------------------------------- actions
    def _selected(self):
        """Выбранные строки — берём свежие данные из self.rows по ключу."""
        out = []
        for i in self.tree.selectedItems():
            r = self.rows.get(i.data(0, Qt.UserRole))
            if r:
                out.append(r)
        return out

    def menu(self, pos):
        rows = self._selected()
        if not rows:
            return
        r = rows[0]
        m = QMenu(self)
        if r["kind"] == "process":
            m.addAction(icon("x", MUTED), "Завершить", self.kill_selected)
            m.addAction(icon("alert", MUTED), "Завершить принудительно",
                        lambda: self.kill_selected(force=True))
            m.addSeparator()
            pr = m.addMenu("Приоритет CPU")
            for key, label in limits.PRIORITY_LABELS.items():
                pr.addAction(label, lambda k=key: self._prio(k))
            m.addAction("Понизить приоритет диска", self._io)
            m.addSeparator()
            m.addAction(icon("sliders", MUTED), "Ограничить ресурсы…", self.limit_selected)
            m.addAction(icon("folder", MUTED), "Открыть расположение файла",
                        lambda: self._open(r))
            m.addAction(icon("copy", MUTED), "Копировать путь",
                        lambda: self.state["clipboard"](r.get("exe") or r["name"]))
            m.addAction("Подробности", self.show_details)
        else:
            m.addAction(icon("play", MUTED), "Запустить службу", lambda: self._svc("start"))
            m.addAction(icon("stop", MUTED), "Остановить службу", lambda: self._svc("stop"))
            m.addSeparator()
            m.addAction("Тип запуска: Авто", lambda: self._svc("auto"))
            m.addAction("Тип запуска: Вручную", lambda: self._svc("manual"))
            m.addAction("Тип запуска: Отключена", lambda: self._svc("disable"))
            m.addSeparator()
            m.addAction(icon("copy", MUTED), "Копировать имя службы",
                        lambda: self.state["clipboard"](r["name"]))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def kill_selected(self, _checked=False, force=False):
        rows = [r for r in self._selected() if r["kind"] == "process"]
        if not rows:
            self.state["toast"]("Выберите процесс в списке", "warn")
            return
        blocked = [r["name"] for r in rows if r["protected"]]
        rows = [r for r in rows if not r["protected"]]
        if blocked:
            self.state["toast"]("Защищено от завершения: " + ", ".join(sorted(set(blocked))[:4]), "warn")
        if not rows:
            return
        if config.load().get("confirm_kill", True):
            names = ", ".join(sorted({r["name"] for r in rows})[:6])
            if QMessageBox.question(
                    self, "Завершение процессов",
                    f"Завершить {len(rows)} процесс(ов)?\n\n{names}") != QMessageBox.Yes:
                return
        targets = [(r["pid"], r["name"]) for r in rows]

        def work():
            bad = []
            for pid, name in targets:
                ok, msg = procs.kill(pid, name, force)
                if not ok:
                    bad.append(f"{name}: {msg}")
            return bad

        def done(bad):
            if isinstance(bad, Exception):
                self.state["toast"](f"Ошибка: {bad}", "error")
            elif bad:
                self.state["toast"](f"Завершено {len(targets) - len(bad)}, "
                                    f"ошибок {len(bad)}. {bad[0]}", "warn")
            else:
                self.state["toast"](f"Завершено процессов: {len(targets)}")
        run_async(work, done)

    def limit_selected(self):
        rows = self._selected()
        if not rows:
            self.state["toast"]("Сначала выберите процесс", "warn")
            return
        procs_only = [r for r in rows if r["kind"] == "process"]
        if not procs_only:
            self.state["toast"]("Лимиты задаются для процессов — выберите строку-процесс", "warn")
            return
        self.state["open_limits"](procs_only[0]["name"])

    def _report(self, what, done, total):
        if total and done < total:
            self.state["toast"](f"{what}: {done} из {total}. Часть процессов недоступна "
                                f"(нужны права администратора)", "warn")
        else:
            self.state["toast"](f"{what}: {done}")

    def _prio(self, key):
        done = total = 0
        for r in self._selected():
            if r["kind"] == "process" and not r["protected"]:
                total += 1
                ok, _ = limits.apply_to_pid(r["pid"], {"priority": key})
                done += int(ok)
        self._report("Приоритет изменён", done, total)

    def _io(self):
        done = total = 0
        for r in self._selected():
            if r["kind"] == "process" and not r["protected"]:
                total += 1
                ok, _ = limits.apply_to_pid(r["pid"], {"io_low": True})
                done += int(ok)
        self._report("Приоритет диска понижен", done, total)

    def _svc(self, action):
        names = [r["name"] for r in self._selected() if r["kind"] == "service"]
        if not names:
            return

        def work():
            res = [procs.service_action(n, action) for n in names]
            return res

        def done(res):
            if isinstance(res, Exception):
                self.state["toast"](f"Ошибка: {res}", "error")
                return
            ok_n = sum(1 for ok, _ in res if ok)
            last = next((m for ok, m in res if not ok), "")
            self.state["toast"](f"Изменено служб: {ok_n}" + (f" · {last[:90]}" if last else ""),
                                "warn" if last else "info")
        run_async(work, done)

    def _open(self, r):
        p = (r.get("exe") or "").strip('"')
        if p and os.path.exists(p):
            subprocess.Popen(["explorer", f"/select,{p}"], creationflags=CREATE_NO_WINDOW)
        else:
            self.state["toast"]("Путь недоступен", "warn")

    def show_details(self):
        rows = self._selected()
        if not rows:
            return
        r = rows[0]
        import datetime
        started = (datetime.datetime.fromtimestamp(r["started"]).strftime("%d.%m.%Y %H:%M:%S")
                   if r.get("started") else "—")
        esc = html.escape
        text = (f'<b>{esc(r["name"])}</b><br><br>'
                f'PID: {r["pid"]}<br>'
                f'Тип: {"Служба" if r["kind"] == "service" else "Процесс"}<br>'
                f'Состояние: {esc(self._state(r))}<br>'
                f'Пользователь: {esc(r.get("user") or "—")}<br>'
                f'Потоков: {r["threads"]}<br>'
                f'CPU: {r["cpu"]:.1f} %<br>'
                f'Память: {monitor.human(r["ram"])}<br>'
                f'Запущен: {started}<br><br>'
                f'<span style="color:{MUTED}">{esc(r.get("exe") or "путь недоступен")}</span>')
        QMessageBox.information(self, "Подробности", text)
