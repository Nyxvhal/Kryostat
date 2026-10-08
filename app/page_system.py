"""Компьютер: сведения о системе, время работы, статистика программ, питание."""
import datetime
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QAbstractItemView, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPushButton, QSpinBox, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from core import admin, monitor, sysinfo
from .icons import icon
from .theme import OK, AMBER, GREEN, MUTED, PURPLE, RED, TEXT2
from .widgets import Card, RowDelegate, SearchBox, SortItem, kv_grid, muted, pill, title, tool_button
from .workers import run_async


class _Item(SortItem, QTreeWidgetItem):
    pass


def _dur(sec):
    sec = int(sec or 0)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d} д {h} ч {m} мин"
    if h:
        return f"{h} ч {m} мин"
    if m:
        return f"{m} мин {s} с"
    return f"{s} с"


def _when(ts):
    if not ts:
        return "—"
    if isinstance(ts, (int, float)):
        ts = datetime.datetime.fromtimestamp(ts)
    return ts.strftime("%d.%m.%Y %H:%M")


def _tree(headers, widths):
    t = QTreeWidget()
    t.setItemDelegate(RowDelegate(10, t))
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False)
    t.setAlternatingRowColors(True)
    t.setUniformRowHeights(True)
    t.setSortingEnabled(True)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    h = t.header()
    h.setStretchLastSection(True)
    for i, w in enumerate(widths):
        t.setColumnWidth(i, w)
    return t


class SystemPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._hw_loaded = False
        self._boot = None
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(title("Компьютер"))
        head.addStretch(1)
        self.pill_up = pill("аптайм —", GREEN)
        head.addWidget(self.pill_up)
        v.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._info_tab(), icon("cpu", MUTED, 15), "Сведения")
        self.tabs.addTab(self._usage_tab(), icon("clock", MUTED, 15), "Статистика программ")
        self.tabs.addTab(self._power_tab(), icon("power", MUTED, 15), "Питание и перезагрузка")
        self.tabs.currentChanged.connect(self._tab_changed)
        v.addWidget(self.tabs, 1)

        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick)
        self.clock.start(1000)

    # ------------------------------------------------------------ сведения
    def _info_tab(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 12, 0, 0)
        g.setSpacing(12)
        c1 = Card("Система")
        box, self.kv_sys = kv_grid([
            ("computer", "Имя компьютера"), ("user", "Пользователь"), ("os", "Windows"),
            ("build", "Сборка"), ("installed", "Установлена"), ("arch", "Архитектура"),
            ("activation", "Активация"), ("secureboot", "Secure Boot"), ("tpm", "TPM"),
            ("av", "Антивирус"), ("safemode", "Безопасный режим"), ("owner", "Владелец")], 2)
        c1.v.addWidget(box)
        c2 = Card("Время работы")
        box, self.kv_time = kv_grid([
            ("uptime", "Работает без перезагрузки"), ("boot", "Включён"),
            ("now", "Сейчас"), ("battery", "Питание")], 1)
        c2.v.addWidget(box)
        self.events = _tree(["Когда", "Событие"], [170])
        self.events.setMaximumHeight(200)
        c2.v.addWidget(QLabel("Последние включения и выключения:"))
        c2.v.addWidget(self.events)
        c3 = Card("Железо")
        box, self.kv_hw = kv_grid([
            ("cpu", "Процессор"), ("cores", "Ядра"), ("freq", "Частота"), ("ram", "ОЗУ"),
            ("ram_mod", "Модули ОЗУ"), ("gpu", "Видеокарта"), ("board", "Плата"),
            ("system", "Модель ПК"), ("bios", "BIOS"), ("disks", "Накопители"),
            ("net", "Сеть")], 1)
        c3.v.addWidget(box)
        c3.add_action(tool_button("Копировать всё", "copy", on_click=self._copy_info))
        g.addWidget(c1, 0, 0)
        g.addWidget(c2, 1, 0)
        g.addWidget(c3, 0, 1, 2, 1)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        return w

    def on_show(self):
        self._fill_quick()
        if not self._hw_loaded:
            self._hw_loaded = True
            run_async(sysinfo.hardware, self._fill_hw)
            run_async(sysinfo.last_shutdowns, self._fill_events)
        if self.tabs.currentIndex() == 1:
            self.reload_usage()
        elif self.tabs.currentIndex() == 2:
            self._check_safe()

    def _tab_changed(self, i):
        if i == 1:
            self.reload_usage()
        elif i == 2:
            self._check_safe()

    def _fill_quick(self):
        q = sysinfo.quick()
        self._boot = time.time() - q["uptime"]
        for k, lbl in list(self.kv_sys.items()) + list(self.kv_hw.items()) + list(self.kv_time.items()):
            if k in q and k != "uptime":
                lbl.setText(str(q[k]) or "—")
        self._tick()

    def _tick(self):
        if not self.isVisible() or self._boot is None:
            if self._boot is None:
                return
        up = time.time() - self._boot
        self.pill_up.setText(f"аптайм {_dur(up)}")
        if self.isVisible():
            self.kv_time["uptime"].setText(_dur(up))
            self.kv_time["now"].setText(datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S"))

    def _fill_hw(self, d):
        if isinstance(d, Exception) or not d:
            return
        gpus = d.get("gpu") or []
        gpus = gpus if isinstance(gpus, list) else [gpus]
        self.kv_hw["gpu"].setText("\n".join(
            f'{g.get("name")} · {monitor.human(g["ram"]) if g.get("ram") else "?"} · {g.get("res", "")}'
            for g in gpus) or "—")
        disks = d.get("disks") or []
        disks = disks if isinstance(disks, list) else [disks]
        self.kv_hw["disks"].setText("\n".join(
            f'{x.get("name")} · {x.get("type")}/{x.get("bus")} · '
            f'{monitor.human(x.get("size") or 0)} · {x.get("health")}' for x in disks) or "—")
        ram = d.get("ram") or []
        ram = ram if isinstance(ram, list) else [ram]
        self.kv_hw["ram_mod"].setText("\n".join(
            f'{x.get("slot")}: {monitor.human(x.get("size") or 0)} {x.get("speed") or "?"} МГц '
            f'{x.get("maker") or ""} {x.get("part") or ""}' for x in ram) or "—")
        net = d.get("net") or []
        net = net if isinstance(net, list) else [net]
        self.kv_hw["net"].setText("\n".join(
            f'{x.get("name")} · {x.get("speed")} · {x.get("desc")}' for x in net) or "—")
        act = {1: "активирована", 0: "не активирована", 5: "уведомления", 2: "льготный период"}
        self.kv_sys["activation"].setText(act.get(d.get("activation"), str(d.get("activation") or "—")))
        self.kv_sys["tpm"].setText(str(d.get("tpm") or "нет"))
        av = d.get("av") or []
        self.kv_sys["av"].setText(", ".join(av if isinstance(av, list) else [av]) or "—")

    def _fill_events(self, ev):
        if isinstance(ev, Exception):
            return
        self.events.clear()
        for t, what in ev:
            it = QTreeWidgetItem([t, what])
            if "неожидан" in what or "сбой" in what:
                it.setForeground(1, Qt.red)
            self.events.addTopLevelItem(it)

    def _copy_info(self):
        lines = []
        for d in (self.kv_sys, self.kv_time, self.kv_hw):
            for k, lbl in d.items():
                lines.append(f"{k}: {lbl.text()}")
        self.state["clipboard"]("\n".join(lines))

    # ------------------------------------------------------------ статистика
    def _usage_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(10)
        v.addWidget(muted(
            "«Kryostat» — данные самого Kryostat: сколько раз каждая программа запускалась и "
            "сколько проработала, пока Kryostat был запущен. «Windows» — счётчик Windows "
            "(UserAssist): запуски через Пуск/Проводник и время, когда окно было активным."))
        bar = QHBoxLayout()
        self.u_search = SearchBox("Поиск программы…")
        self.u_search.textChanged.connect(lambda _: self.reload_usage())
        bar.addWidget(self.u_search, 1)
        self.u_since = QLabel("")
        self.u_since.setObjectName("Small")
        bar.addWidget(self.u_since)
        bar.addWidget(tool_button("Обновить", "refresh", on_click=self.reload_usage))
        bar.addWidget(tool_button("Сбросить статистику", "trash", "Danger", self._reset_usage))
        v.addLayout(bar)
        t = QTabWidget()
        self.u_mine = _tree(["Программа", "Запусков", "Время работы", "Последний раз", "Путь"],
                            [220, 90, 140, 150])
        self.u_win = _tree(["Программа", "Запусков", "В фокусе", "Последний запуск", "Путь"],
                           [220, 90, 140, 150])
        t.addTab(self.u_mine, "Kryostat")
        t.addTab(self.u_win, "Windows")
        v.addWidget(t, 1)
        return w

    def reload_usage(self):
        q = self.u_search.text().strip().lower()
        tr = self.state.get("usage")
        self.u_mine.setSortingEnabled(False)
        self.u_mine.clear()
        if tr:
            self.u_since.setText(f"считается с {_when(tr.since)}")
            items = []
            for name, e in tr.data.items():
                title_ = e.get("title") or name
                if q and q not in name and q not in (e.get("exe") or "").lower():
                    continue
                it = _Item([title_, str(e.get("runs", 0)), _dur(e.get("seconds", 0)),
                            _when(e.get("last")), e.get("exe") or ""])
                it.setData(1, SortItem.SORT_ROLE, e.get("runs", 0))
                it.setData(2, SortItem.SORT_ROLE, e.get("seconds", 0))
                it.setData(3, SortItem.SORT_ROLE, e.get("last", 0))
                for c in (1, 2):
                    it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
                items.append(it)
            self.u_mine.addTopLevelItems(items)
        self.u_mine.setSortingEnabled(True)
        self.u_mine.sortByColumn(2, Qt.DescendingOrder)

        def got(rows):
            if isinstance(rows, Exception):
                return
            self.u_win.setSortingEnabled(False)
            self.u_win.clear()
            items = []
            for r in rows:
                if q and q not in r["path"].lower():
                    continue
                it = _Item([r["name"], str(r["count"]), _dur(r["focus"]), _when(r["last"]), r["path"]])
                it.setData(1, SortItem.SORT_ROLE, r["count"])
                it.setData(2, SortItem.SORT_ROLE, r["focus"])
                it.setData(3, SortItem.SORT_ROLE, r["last"].timestamp() if r["last"] else 0)
                for c in (1, 2):
                    it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
                items.append(it)
            self.u_win.addTopLevelItems(items)
            self.u_win.setSortingEnabled(True)
            self.u_win.sortByColumn(1, Qt.DescendingOrder)
        run_async(sysinfo.userassist, got)

    def _reset_usage(self):
        tr = self.state.get("usage")
        if tr and QMessageBox.question(self, "Сброс", "Сбросить статистику Kryostat?") == QMessageBox.Yes:
            tr.reset()
            self.reload_usage()

    # ------------------------------------------------------------ питание
    def _power_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(12)
        grid = QGridLayout()
        grid.setSpacing(10)
        items = [
            ("restart", "refresh", "Перезагрузить", "Сразу", True),
            ("shutdown", "power", "Выключить", "Сразу (с быстрым запуском)", True),
            ("full", "power", "Полное выключение", "Без быстрого запуска — «чистый» старт", True),
            ("sleep", "clock", "Спящий режим", "", False),
            ("hibernate", "hdd", "Гибернация", "Нужна включённая гибернация", False),
            ("lock", "shield", "Заблокировать", "Win + L", False),
            ("logoff", "x", "Выйти из системы", "Закроет все программы", True),
            ("bios", "chip", "Перезагрузка в BIOS / UEFI", "Только для UEFI", True),
            ("advanced", "settings", "Особые варианты загрузки", "Восстановление, параметры загрузки", True),
        ]
        for i, (key, ico, name, sub, danger) in enumerate(items):
            b = QPushButton(f"  {name}" + (f"\n  {sub}" if sub else ""))
            b.setObjectName("TileDanger" if key in ("shutdown", "full", "restart") else "Tile")
            b.setIcon(icon(ico, RED if b.objectName() == "TileDanger" else GREEN, 20))
            b.setMinimumHeight(62)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key, n=name, d=danger: self._power(k, n, d))
            grid.addWidget(b, i // 3, i % 3)
        v.addLayout(grid)

        row = QHBoxLayout()
        row.setSpacing(12)

        safe = Card("Безопасный режим", "следующая загрузка")
        safe.v.addWidget(muted(
            "Windows загрузится только с базовыми драйверами и службами — удобно, чтобы удалить "
            "вредоносную программу или проблемный драйвер. Компьютер перезагрузится через 3 с. "
            "Пока флаг не снят, каждая загрузка будет в безопасном режиме."))
        self.safe_state = QLabel("Состояние: проверяю…")
        self.safe_state.setObjectName("KVValue")
        safe.v.addWidget(self.safe_state)
        sb = QHBoxLayout()
        sb.addWidget(tool_button("Обычный", "shield", "Magenta",
                                 lambda: self._safe("minimal", "безопасном режиме")))
        sb.addWidget(tool_button("С сетью", "wifi", "Magenta",
                                 lambda: self._safe("network", "безопасном режиме с сетью")))
        sb.addWidget(tool_button("Вернуть обычную загрузку", "refresh", on_click=lambda: self._safe("off", "")))
        sb.addStretch(1)
        safe.v.addLayout(sb)
        safe.v.addStretch(1)
        row.addWidget(safe, 1)

        plan = Card("Запланировать")
        plan.v.addWidget(muted("Выключить или перезагрузить компьютер через заданное время. "
                               "Windows покажет предупреждение заранее."))
        pr = QHBoxLayout()
        pr.addWidget(QLabel("Через"))
        self.sched_min = QSpinBox()
        self.sched_min.setRange(1, 24 * 60)
        self.sched_min.setValue(30)
        self.sched_min.setSuffix(" мин")
        self.sched_min.setFixedWidth(130)
        pr.addWidget(self.sched_min)
        pr.addStretch(1)
        plan.v.addLayout(pr)
        pb = QHBoxLayout()
        pb.addWidget(tool_button("Выключение", "power", "Primary", lambda: self._schedule("shutdown")))
        pb.addWidget(tool_button("Перезагрузка", "refresh", on_click=lambda: self._schedule("restart")))
        pb.addWidget(tool_button("Отменить", "x", "Danger", self._abort))
        pb.addStretch(1)
        plan.v.addLayout(pb)
        plan.v.addStretch(1)
        row.addWidget(plan, 1)
        v.addLayout(row)
        v.addStretch(1)
        self._safe_checked = False
        return w

    def _need_admin(self):
        if admin.is_admin():
            return False
        self.state["toast"]("Нужны права администратора", "error")
        return True

    def _result(self, r, ok_text=None):
        if isinstance(r, Exception):
            self.state["toast"](f"Ошибка: {r}", "error")
        elif not r[0]:
            self.state["toast"](str(r[1])[:200] or "Не удалось", "error")
        else:
            self.state["toast"](ok_text or str(r[1])[:200])

    def _power(self, key, name, danger):
        if danger and QMessageBox.question(
                self, name, f"{name}?\n\nНесохранённые данные в открытых программах "
                            f"могут быть потеряны.") != QMessageBox.Yes:
            return
        run_async(lambda: sysinfo.power(key), self._result)

    def _safe(self, kind, what):
        if self._need_admin():
            return
        if kind == "off":
            text = "Убрать флаг безопасного режима и перезагрузить компьютер в обычном режиме?"
        else:
            text = f"Перезагрузить компьютер в {what}?\n\nВернуть обычную загрузку можно " \
                   f"этой же страницей или командой bcdedit /deletevalue {{current}} safeboot."
        if QMessageBox.question(self, "Безопасный режим", text) != QMessageBox.Yes:
            return

        def done(r):
            self._result(r, "Перезагрузка через 3 с…")
            self._check_safe()
        run_async(lambda: sysinfo.safe_mode(kind), done)

    def _check_safe(self):
        def got(on):
            if isinstance(on, Exception):
                self.safe_state.setText("Состояние: неизвестно")
                return
            self.safe_state.setText("Состояние: следующая загрузка — в безопасном режиме" if on
                                    else "Состояние: обычная загрузка")
            self.safe_state.setStyleSheet(f"color:{AMBER if on else OK};")
        run_async(sysinfo.safe_mode_pending, got)

    def _schedule(self, action):
        m = self.sched_min.value()
        name = "Выключение" if action == "shutdown" else "Перезагрузка"
        run_async(lambda: sysinfo.schedule(action, m),
                  lambda r: self._result(r, f"{name} через {m} мин. Отменить — кнопкой «Отменить»"))

    def _abort(self):
        run_async(lambda: sysinfo.power("abort"),
                  lambda r: self._result(r, "Запланированное выключение отменено"))
