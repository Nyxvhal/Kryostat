"""Лимиты ресурсов: правила для конкретных программ и ограничения всей системы."""
import os

import psutil
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QSlider, QSpinBox, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from core import admin, config, limits, monitor, syslimit
from .icons import icon
from .theme import OK, AMBER, GREEN, MUTED, PURPLE, RED, TEXT2
from .widgets import Card, SearchBox, SortItem, muted, pill, title, tool_button
from .workers import run_async


class _Item(SortItem, QTreeWidgetItem):
    pass


class LimitsPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._live = set()
        self._names = []
        self._apps = {}             # имя .exe -> агрегированные данные
        self._app_items = {}        # имя .exe -> строка в списке программ
        self._icons_cache = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(title("Лимиты ресурсов"))
        head.addStretch(1)
        self.pill_admin = pill("—", MUTED)
        head.addWidget(self.pill_admin)
        v.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        prog = QWidget()
        pv = QVBoxLayout(prog)
        pv.setContentsMargins(0, 12, 0, 0)
        pv.setSpacing(14)
        pv.addWidget(muted(
            "Правило привязывается к имени .exe и применяется ко всем его копиям, включая "
            "будущие запуски. ОЗУ и CPU ограничиваются через Job Objects Windows, "
            "скорость сети — через политику QoS. Требуются права администратора."))

        body = QHBoxLayout()
        body.setSpacing(14)

        editor = Card("Правило")
        form = QFormLayout()
        form.setSpacing(11)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.name = QLineEdit()
        self.name.setPlaceholderText("chrome.exe")
        self.completer = QCompleter([])
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.name.setCompleter(self.completer)

        self.ram = QSpinBox(); self.ram.setRange(0, 1_048_576); self.ram.setSingleStep(256)
        self.ram.setSuffix(" МБ"); self.ram.setSpecialValueText("без лимита")
        self.cpu = QSpinBox(); self.cpu.setRange(0, 100); self.cpu.setSuffix(" %")
        self.cpu.setSpecialValueText("без лимита")
        self.cores = QSpinBox(); self.cores.setRange(0, psutil.cpu_count() or 64)
        self.cores.setSpecialValueText("все ядра"); self.cores.setSuffix(" ядер")
        self.net = QSpinBox(); self.net.setRange(0, 10_000_000); self.net.setSingleStep(512)
        self.net.setSuffix(" КБит/с"); self.net.setSpecialValueText("без лимита")
        self.prio = QComboBox()
        self.prio.addItem("не менять", None)
        for k, lbl in limits.PRIORITY_LABELS.items():
            self.prio.addItem(lbl, k)
        self.io_low = QCheckBox("Низкий приоритет доступа к диску")
        self.enabled = QCheckBox("Правило активно")
        self.enabled.setChecked(True)

        for sb in (self.ram, self.cpu, self.cores, self.net):
            sb.setMinimumWidth(170)
            sb.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.addRow("Программа", self.name)
        form.addRow("Максимум ОЗУ", self.ram)
        form.addRow("Максимум CPU", self.cpu)
        form.addRow("Доступно ядер", self.cores)
        form.addRow("Скорость сети", self.net)
        form.addRow("Приоритет CPU", self.prio)
        form.addRow("", self.io_low)
        form.addRow("", self.enabled)
        editor.v.addLayout(form)

        presets = QHBoxLayout()
        presets.setSpacing(6)
        presets.addWidget(QLabel("Шаблон:"))
        for label, rule in (
            ("Тихий фон", {"cpu_percent": 10, "priority": "idle", "io_low": True}),
            ("Браузер", {"ram_mb": 3072, "cpu_percent": 45, "priority": "below"}),
            ("Обновлятор", {"cpu_percent": 5, "net_kbps": 2048,
                            "priority": "idle", "io_low": True}),
        ):
            presets.addWidget(tool_button(label, on_click=lambda _=False, r=rule: self._preset(r)))
        presets.addStretch(1)
        editor.v.addLayout(presets)

        btns = QHBoxLayout()
        btns.addWidget(tool_button("Сохранить и применить", "check", "Primary", self.save))
        btns.addWidget(tool_button("Очистить", on_click=self.clear))
        btns.addStretch(1)
        editor.v.addLayout(btns)
        editor.v.addStretch(1)
        editor.setFixedWidth(400)
        body.addWidget(editor)

        right = QVBoxLayout()
        right.setSpacing(14)

        apps = Card("Запущенные программы")
        apps.add_action(tool_button("Ограничить", "sliders", "Primary", self._limit_selected_app))
        self.app_search = SearchBox("Поиск программы…")
        self.app_search.textChanged.connect(lambda _: self._render_apps(force=True))
        apps.v.addWidget(self.app_search)
        self.apps_tree = QTreeWidget()
        self.apps_tree.setHeaderLabels(["Программа", "Процессов", "CPU", "ОЗУ", "Правило", "Путь"])
        self.apps_tree.setRootIsDecorated(False)
        self.apps_tree.setAlternatingRowColors(True)
        self.apps_tree.setUniformRowHeights(True)
        self.apps_tree.setSortingEnabled(True)
        self.apps_tree.sortByColumn(3, Qt.DescendingOrder)
        self.apps_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.apps_tree.itemClicked.connect(lambda it, _c: self._pick_app(it))
        self.apps_tree.itemDoubleClicked.connect(lambda it, _c: self._pick_app(it, focus=True))
        ah = self.apps_tree.header()
        ah.setSectionResizeMode(QHeaderView.Interactive)
        ah.setStretchLastSection(True)
        for i, w in enumerate([200, 80, 70, 90, 110]):
            self.apps_tree.setColumnWidth(i, w)
        apps.v.addWidget(self.apps_tree, 1)
        apps.v.addWidget(muted("Нажмите на программу, чтобы подставить её в правило слева. "
                               "Двойной клик — сразу перейти к настройке лимита."))
        right.addWidget(apps, 3)

        card = Card("Активные правила")
        card.add_action(tool_button("Применить все", "zap", on_click=self.apply_all))
        card.add_action(tool_button("Удалить", "trash", "Danger", self.delete))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Программа", "ОЗУ", "CPU", "Ядра", "Сеть",
                                   "Приоритет CPU / диска", "Состояние"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemSelectionChanged.connect(self.load_selected)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        for i, w in enumerate([150, 80, 65, 60, 100, 150]):
            self.tree.setColumnWidth(i, w)
        card.v.addWidget(self.tree)
        self.empty_hint = muted("Правил пока нет. Задайте ограничение в панели слева "
                                "или выберите процесс на странице «Процессы и службы» "
                                "и нажмите «Ограничить».")
        card.v.addWidget(self.empty_hint)
        card.v.addWidget(muted(
            "Подсказка: лимит ОЗУ — жёсткий. Если программа попытается выйти за него, "
            "Windows откажет ей в выделении памяти, и программа может закрыться сама."))
        right.addWidget(card, 2)
        body.addLayout(right, 1)
        pv.addLayout(body, 1)
        self.tabs.addTab(prog, icon("list", MUTED, 15), "Программы")
        self.tabs.addTab(self._system_tab(), icon("cpu", MUTED, 15), "Вся система")
        self.tabs.currentChanged.connect(self._tab_changed)
        v.addWidget(self.tabs, 1)
        self._sys_loaded = False
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(3000)
        self._stats_timer.timeout.connect(self._update_stats)
        self.reload()

    # ------------------------------------------------------------------
    def prefill(self, name: str):
        self.name.setText(name)
        self.name.setFocus()

    def _preset(self, rule):
        self.ram.setValue(rule.get("ram_mb", 0))
        self.cpu.setValue(rule.get("cpu_percent", 0))
        self.net.setValue(rule.get("net_kbps", 0))
        self.cores.setValue(rule.get("affinity_cores", 0))
        self.prio.setCurrentIndex(max(0, self.prio.findData(rule.get("priority"))))
        self.io_low.setChecked(bool(rule.get("io_low")))
        self.enabled.setChecked(True)

    def update_names(self, rows, visible=True):
        apps = {}
        for r in rows:
            if r["kind"] != "process" or not r.get("pid"):
                continue
            nm = r["name"]
            if nm.lower() in ("system", "registry", "memory compression", "secure system"):
                continue
            a = apps.get(nm.lower())
            if a is None:
                a = apps[nm.lower()] = {"name": nm, "n": 0, "cpu": 0.0, "ram": 0,
                                        "exe": r.get("exe") or ""}
            a["n"] += 1
            a["cpu"] += r["cpu"]
            a["ram"] += r["ram"]
            if not a["exe"] and r.get("exe"):
                a["exe"] = r["exe"]
        self._apps = apps
        names = sorted(a["name"] for a in apps.values())
        if names != self._names:       # не сбрасываем модель каждую секунду — иначе мигает список подсказок
            self._names = names
            model = self.completer.model()
            if hasattr(model, "setStringList"):
                model.setStringList(names)
        live = set(apps)
        changed = live != self._live
        self._live = live
        if not visible and not changed:
            return                     # страница не на экране — таблицы не трогаем
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            nm = it.data(0, Qt.UserRole)
            if not nm:
                continue
            running = nm.lower() in self._live
            it.setText(0, f'{"● " if running else "○ "}{nm}')
            it.setToolTip(0, "Программа запущена" if running else "Программа не запущена")
        self.pill_admin.setText("админ-режим" if admin.is_admin() else "нет прав админа")
        if visible:
            self._render_apps()

    def _render_apps(self, force=False):
        q = self.app_search.text().strip().lower()
        rules = {k.lower(): r for k, r in (config.load().get("limits") or {}).items()} \
            if force or not hasattr(self, "_rules_cache") else self._rules_cache
        self._rules_cache = rules
        wanted = {k: a for k, a in self._apps.items()
                  if not q or q in k or q in a["exe"].lower()}
        t = self.apps_tree
        t.setUpdatesEnabled(False)
        t.setSortingEnabled(False)
        for k in [k for k in self._app_items if k not in wanted]:
            it = self._app_items.pop(k)
            idx = t.indexOfTopLevelItem(it)
            if idx >= 0:
                t.takeTopLevelItem(idx)
        for k, a in wanted.items():
            it = self._app_items.get(k)
            if it is None:
                it = _Item()
                it.setData(0, Qt.UserRole, a["name"])
                for c in (1, 2, 3):
                    it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
                t.addTopLevelItem(it)
                self._app_items[k] = it
            r = rules.get(k)
            rule_txt = ("активно" if r.get("enabled", True) else "выключено") if r else ""
            vals = (a["name"], str(a["n"]), f'{a["cpu"]:.1f} %',
                    monitor.human(a["ram"]), rule_txt, a["exe"])
            for c, txt in enumerate(vals):
                if it.text(c) != txt:
                    it.setText(c, txt)
            for c, val in ((1, a["n"]), (2, round(a["cpu"], 1)), (3, a["ram"])):
                if it.data(c, SortItem.SORT_ROLE) != val:
                    it.setData(c, SortItem.SORT_ROLE, val)
            look = bool(r)
            if getattr(it, "_look", None) != look:
                it._look = look
                it.setForeground(4, QBrush(QColor(OK if r else MUTED)))
        t.setSortingEnabled(True)
        t.setUpdatesEnabled(True)

    def _pick_app(self, it, focus=False):
        name = it.data(0, Qt.UserRole) if it else None
        if not name:
            return
        rule = None
        for k, r in (config.load().get("limits") or {}).items():
            if k.lower() == name.lower():
                name, rule = k, r
        if rule is not None:
            self._fill(name, rule)
        else:
            self.clear()
            self.name.setText(name)
        if focus:
            self.ram.setFocus()
            self.ram.selectAll()

    def _limit_selected_app(self):
        it = self.apps_tree.currentItem()
        if not it:
            self.state["toast"]("Выберите программу в списке", "warn")
            return
        self._pick_app(it, focus=True)

    def reload(self):
        cfg = config.load()
        self.tree.clear()
        for name, r in (cfg.get("limits") or {}).items():
            prio = limits.PRIORITY_LABELS.get(r.get("priority"), "—")
            if r.get("io_low"):
                prio = f'{prio} / диск низкий'
            on = r.get("enabled", True)
            it = _Item([
                name,
                f'{r.get("ram_mb")} МБ' if r.get("ram_mb") else "—",
                f'{r.get("cpu_percent")} %' if r.get("cpu_percent") else "—",
                str(r.get("affinity_cores") or "все"),
                f'{r.get("net_kbps")} КБит/с' if r.get("net_kbps") else "—",
                prio,
                "активно" if on else "выключено",
            ])
            it.setData(0, Qt.UserRole, name)
            it.setForeground(6, QBrush(QColor(OK if on else MUTED)))
            if not on:
                for c in range(6):
                    it.setForeground(c, QBrush(QColor(MUTED)))
            self.tree.addTopLevelItem(it)
        self.empty_hint.setVisible(self.tree.topLevelItemCount() == 0)
        if hasattr(self, "apps_tree"):
            self._render_apps(force=True)

    def load_selected(self):
        it = self.tree.currentItem()
        if not it or not it.data(0, Qt.UserRole):
            return
        name = it.data(0, Qt.UserRole)
        r = (config.load().get("limits") or {}).get(name)
        if r is None:
            return
        self._fill(name, r)

    def _fill(self, name, r):
        self.name.setText(name)
        self.ram.setValue(int(r.get("ram_mb") or 0))
        self.cpu.setValue(int(r.get("cpu_percent") or 0))
        self.cores.setValue(int(r.get("affinity_cores") or 0))
        self.net.setValue(int(r.get("net_kbps") or 0))
        self.prio.setCurrentIndex(max(0, self.prio.findData(r.get("priority"))))
        self.io_low.setChecked(bool(r.get("io_low")))
        self.enabled.setChecked(bool(r.get("enabled", True)))

    def clear(self):
        self.name.clear()
        for s in (self.ram, self.cpu, self.cores, self.net):
            s.setValue(0)
        self.prio.setCurrentIndex(0)
        self.io_low.setChecked(False)
        self.enabled.setChecked(True)
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        self.tree.blockSignals(False)

    def save(self):
        name = self.name.text().strip().strip('"')
        if not name:
            self.state["toast"]("Укажите имя программы, например chrome.exe", "warn")
            return
        if "\\" in name or "/" in name:
            name = name.replace("/", "\\").rsplit("\\", 1)[-1]
        if not name.lower().endswith(".exe"):
            name += ".exe"
        rule = {
            "ram_mb": self.ram.value(), "cpu_percent": self.cpu.value(),
            "affinity_cores": self.cores.value(), "net_kbps": self.net.value(),
            "priority": self.prio.currentData(), "io_low": self.io_low.isChecked(),
            "enabled": self.enabled.isChecked(),
        }
        if not any((rule["ram_mb"], rule["cpu_percent"], rule["affinity_cores"],
                    rule["net_kbps"], rule["priority"], rule["io_low"])):
            self.state["toast"]("Правило пустое — задайте хотя бы одно ограничение", "warn")
            return
        limits.save_rule(name, rule)
        self.reload()

        def work():
            if rule["enabled"]:
                return limits.apply_rule(name, rule)
            limits.release_rule(name)          # правило выключено — снимаем действующие ограничения
            return 0, []

        def done(res):
            if isinstance(res, Exception):
                self.state["toast"](f"«{name}» сохранено, но применить не удалось: {res}", "error")
                return
            n, errs = res
            if errs:
                self.state["toast"](f'«{name}» сохранено. {errs[0][:140]}', "warn")
            elif not rule["enabled"]:
                self.state["toast"](f"Правило «{name}» сохранено, но выключено — ограничения сняты")
            elif n == 0:
                self.state["toast"](f"Правило «{name}» сохранено. Программа сейчас не запущена — "
                                    f"ограничения применятся при её запуске")
            else:
                self.state["toast"](f"Правило «{name}» сохранено и применено к {n} процессам")
        run_async(work, done)

    def delete(self):
        it = self.tree.currentItem()
        name = it.data(0, Qt.UserRole) if it else None
        if not name:
            self.state["toast"]("Выберите правило в списке", "warn")
            return
        limits.delete_rule_config(name)
        self.reload()
        self.clear()
        run_async(lambda: limits.delete_rule(name),
                  lambda _: self.state["toast"](f"Правило «{name}» удалено, ограничения сняты"))

    def apply_all(self):
        def done(n):
            if isinstance(n, Exception):
                self.state["toast"](f"Ошибка: {n}", "error")
            else:
                self.state["toast"](f"Правила применены к {n} процессам")
        run_async(limits.apply_all, done)

    # ================================================================ вся система
    def _system_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(14)
        v.addWidget(muted(
            "Ограничения для всего компьютера. Частота процессора задаётся в текущей схеме "
            "питания и действует на всё, включая Windows. Общий бюджет объединяет все сторонние "
            "программы в одну группу с общим пределом CPU и ОЗУ — системные процессы Windows "
            "и сам Kryostat не затрагиваются. Всё обратимо кнопкой «Снять»."))
        row = QHBoxLayout()
        row.setSpacing(14)

        # ---- процессор и питание
        cpu = Card("Процессор и питание")
        self.sl_ac, self.lbl_ac = self._slider()
        self.sl_dc, self.lbl_dc = self._slider()
        form = QFormLayout()
        form.setSpacing(11)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        for sl, lbl, name in ((self.sl_ac, self.lbl_ac, "От сети"),
                              (self.sl_dc, self.lbl_dc, "От батареи")):
            h = QHBoxLayout()
            h.addWidget(sl, 1)
            h.addWidget(lbl)
            form.addRow(name, h)
        self.turbo = QCheckBox("Turbo Boost (повышение частоты сверх базовой)")
        form.addRow("", self.turbo)
        cpu.v.addWidget(QLabel("Максимальное состояние процессора"))
        cpu.v.addLayout(form)
        cpu.v.addWidget(muted("Например, 80 % и выключенный Turbo Boost заметно снижают нагрев "
                              "и шум ноутбука ценой небольшой потери скорости."))
        b1 = QHBoxLayout()
        b1.addWidget(tool_button("Применить", "check", "Primary", self._apply_cpu))
        b1.addWidget(tool_button("Сбросить (100 %)", "refresh", on_click=self._reset_cpu))
        b1.addStretch(1)
        cpu.v.addLayout(b1)
        cpu.v.addSpacing(6)
        cpu.v.addWidget(QLabel("Схема питания"))
        pl = QHBoxLayout()
        self.plan = QComboBox()
        self.plan.setMinimumWidth(240)
        self.plan.activated.connect(self._set_plan)
        pl.addWidget(self.plan, 1)
        pl.addWidget(tool_button("Максимальная производительность", "zap", "Magenta",
                                 self._add_ultimate,
                                 tip="Добавить и включить скрытую схему «Максимальная производительность»"))
        cpu.v.addLayout(pl)
        cpu.v.addStretch(1)
        row.addWidget(cpu, 1)

        # ---- общий бюджет
        bud = Card("Общий бюджет программ")
        self.sys_state = pill("—", MUTED)
        bud.add_action(self.sys_state)
        f2 = QFormLayout()
        f2.setSpacing(11)
        f2.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.s_enabled = QCheckBox("Включить общий бюджет")
        self.s_cpu = QSpinBox(); self.s_cpu.setRange(0, 99); self.s_cpu.setSuffix(" %")
        self.s_cpu.setSpecialValueText("без лимита")
        self.s_ram = QSpinBox(); self.s_ram.setRange(0, 1_048_576); self.s_ram.setSingleStep(512)
        self.s_ram.setSuffix(" МБ"); self.s_ram.setSpecialValueText("без лимита")
        self.s_prio = QComboBox()
        self.s_prio.addItem("не менять", None)
        for k in ("above", "normal", "below", "idle"):
            self.s_prio.addItem(limits.PRIORITY_LABELS.get(k, k), k)
        self.s_io = QCheckBox("Низкий приоритет доступа к диску")
        self.s_skip = QCheckBox("Не трогать программы, у которых есть своё правило")
        self.s_excl = QPlainTextEdit()
        self.s_excl.setPlaceholderText("game.exe\nobs64.exe")
        self.s_excl.setFixedHeight(90)
        for sb in (self.s_cpu, self.s_ram):
            sb.setMinimumWidth(170)
        f2.addRow("", self.s_enabled)
        f2.addRow("Всего CPU", self.s_cpu)
        f2.addRow("Всего ОЗУ", self.s_ram)
        f2.addRow("Приоритет CPU", self.s_prio)
        f2.addRow("Диск", self.s_io)
        f2.addRow("Исключения", self.s_excl)
        f2.addRow("", self.s_skip)
        bud.v.addLayout(f2)
        bud.v.addWidget(muted("Исключения — имена .exe по одному в строке: эти программы "
                              "работают без общего ограничения. Лимит ОЗУ — жёсткий, "
                              "задавайте его с запасом."))
        b2 = QHBoxLayout()
        b2.addWidget(tool_button("Применить", "check", "Primary", self._apply_budget))
        b2.addWidget(tool_button("Снять", "x", "Danger", self._release_budget))
        b2.addStretch(1)
        bud.v.addLayout(b2)
        self.sys_stats = QLabel("")
        self.sys_stats.setObjectName("Small")
        bud.v.addWidget(self.sys_stats)
        bud.v.addStretch(1)
        row.addWidget(bud, 1)
        v.addLayout(row, 1)
        return w

    def _slider(self):
        sl = QSlider(Qt.Horizontal)
        sl.setRange(5, 100)
        sl.setValue(100)
        sl.setPageStep(5)
        lbl = QLabel("100 %")
        lbl.setFixedWidth(48)
        sl.valueChanged.connect(lambda x, l=lbl: l.setText(f"{x} %"))
        return sl, lbl

    def on_show(self):
        if self.tabs.currentIndex() == 1:
            self._load_system()

    def _tab_changed(self, i):
        if i == 1:
            self._load_system()
        else:
            self._stats_timer.stop()

    def _load_system(self):
        self._stats_timer.start()
        self._fill_budget(syslimit.settings())
        self._update_stats()

        def work():
            return syslimit.cpu_max(), syslimit.turbo(), syslimit.plans()

        def got(r):
            if isinstance(r, Exception):
                self.state["toast"](f"Не удалось прочитать схему питания: {r}", "warn")
                return
            (ac, dc), tb, plans = r
            self.sl_ac.setValue(int(ac))
            self.sl_dc.setValue(int(dc))
            self.turbo.setChecked(int(tb) != 0)
            self.plan.clear()
            for guid, name, active in plans:
                self.plan.addItem(("● " if active else "") + name, guid)
                if active:
                    self.plan.setCurrentIndex(self.plan.count() - 1)
            if not plans:
                self.plan.addItem("нет данных", None)
        run_async(work, got)

    def _fill_budget(self, s):
        self.s_enabled.setChecked(bool(s.get("enabled")))
        self.s_cpu.setValue(int(s.get("cpu_percent") or 0))
        self.s_ram.setValue(int(s.get("ram_mb") or 0))
        self.s_prio.setCurrentIndex(max(0, self.s_prio.findData(s.get("priority"))))
        self.s_io.setChecked(bool(s.get("io_low")))
        self.s_skip.setChecked(bool(s.get("skip_rules", True)))
        self.s_excl.setPlainText("\n".join(s.get("exclude") or []))
        self._budget_pill(bool(s.get("enabled")))

    def _budget_pill(self, on):
        from .widgets import pill_style
        self.sys_state.setText("действует" if on else "выключен")
        self.sys_state.setStyleSheet(pill_style(OK if on else MUTED))

    def _update_stats(self):
        if not self.isVisible():
            return

        def got(r):
            if isinstance(r, Exception):
                return
            n, ram = r
            self.sys_stats.setText(f"В бюджете: {n} процессов · ОЗУ {monitor.human(ram)}" if n
                                   else "Сейчас в бюджете нет процессов")
        run_async(syslimit.stats, got)

    def _admin_ok(self):
        if admin.is_admin():
            return True
        self.state["toast"]("Нужны права администратора", "error")
        return False

    def _done(self, r, ok_text):
        if isinstance(r, Exception):
            self.state["toast"](f"Ошибка: {r}", "error")
        elif isinstance(r, tuple) and not r[0]:
            self.state["toast"](str(r[1])[:200] or "Не удалось", "error")
        else:
            self.state["toast"](ok_text)

    def _apply_cpu(self):
        if not self._admin_ok():
            return
        ac, dc, tb = self.sl_ac.value(), self.sl_dc.value(), self.turbo.isChecked()

        def work():
            r1 = syslimit.set_cpu_max(ac, dc)
            r2 = syslimit.set_turbo(tb)
            return (r1[0] and r2[0], r1[1] or r2[1])
        run_async(work, lambda r: self._done(
            r, f"Максимум CPU: {ac} % от сети, {dc} % от батареи · Turbo Boost "
               f"{'включён' if tb else 'выключен'}"))

    def _reset_cpu(self):
        self.sl_ac.setValue(100)
        self.sl_dc.setValue(100)
        self.turbo.setChecked(True)
        self._apply_cpu()

    def _set_plan(self, _i=None):
        guid = self.plan.currentData()
        if not guid or not self._admin_ok():
            return
        run_async(lambda: syslimit.set_plan(guid),
                  lambda r: (self._done(r, f"Схема питания: {self.plan.currentText().lstrip('● ')}"),
                             self._load_system()))

    def _add_ultimate(self):
        if not self._admin_ok():
            return
        run_async(syslimit.add_ultimate,
                  lambda r: (self._done(r, "Схема «Максимальная производительность» включена"),
                             self._load_system()))

    def _collect_budget(self):
        excl = [x.strip().strip('"') for x in
                self.s_excl.toPlainText().replace(",", "\n").splitlines() if x.strip()]
        excl = [x if x.lower().endswith(".exe") else x + ".exe" for x in excl]
        s = syslimit.settings()
        s.update({"enabled": self.s_enabled.isChecked(), "cpu_percent": self.s_cpu.value(),
                  "ram_mb": self.s_ram.value(), "priority": self.s_prio.currentData(),
                  "io_low": self.s_io.isChecked(), "skip_rules": self.s_skip.isChecked(),
                  "exclude": excl})
        return s

    def _apply_budget(self):
        if not self._admin_ok():
            return
        s = self._collect_budget()
        if s["enabled"] and not any((s["cpu_percent"], s["ram_mb"], s["priority"], s["io_low"])):
            self.state["toast"]("Бюджет пустой — задайте CPU, ОЗУ или приоритет", "warn")
            return
        syslimit.save(s)
        self._budget_pill(s["enabled"])
        cb = self.state.get("syslimit_changed")
        if cb:
            cb(s["enabled"])

        def work():
            syslimit.release()                 # новые настройки — заново ко всем процессам
            return syslimit.apply() if s["enabled"] else 0

        def done(n):
            if isinstance(n, Exception):
                self.state["toast"](f"Ошибка: {n}", "error")
            elif not s["enabled"]:
                self.state["toast"]("Настройки сохранены, общий бюджет выключен")
            else:
                self.state["toast"]("Общий бюджет применён"
                                    + (f": {n} процессов в общем лимите" if n else ""))
            self._update_stats()
        run_async(work, done)

    def _release_budget(self):
        s = self._collect_budget()
        s["enabled"] = False
        syslimit.save(s)
        self.s_enabled.setChecked(False)
        self._budget_pill(False)
        cb = self.state.get("syslimit_changed")
        if cb:
            cb(False)
        run_async(syslimit.release, lambda r: (self._done(r, "Общий бюджет снят"),
                                               self._update_stats()))
