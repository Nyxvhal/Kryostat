"""Антивирус: Microsoft Defender — состояние, защита, исключения, проверки, угрозы."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar,
                               QSlider, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)
from core import admin, defender
from .icons import icon
from .theme import OK, AMBER, GREEN, MUTED, PURPLE, RED, TEXT2
from .widgets import Card, RowDelegate, kv_grid, muted, pill, pill_style, title, tool_button
from .workers import run_async

KIND_LABELS = {
    "path": "Файл или папка",
    "proc": "Процесс",
    "ext": "Расширение",
    "cfa_app": "Разрешённое приложение (доступ к папкам)",
    "cfa_dir": "Защищённая папка",
}
KIND_STATUS = {"path": "ex_path", "proc": "ex_proc", "ext": "ex_ext",
               "cfa_app": "cfa_apps", "cfa_dir": "cfa_dirs"}


def _tree(headers, widths):
    t = QTreeWidget()
    t.setItemDelegate(RowDelegate(10, t))
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False)
    t.setAlternatingRowColors(True)
    t.setUniformRowHeights(True)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.header().setStretchLastSection(True)
    for i, w in enumerate(widths):
        t.setColumnWidth(i, w)
    return t


def _yes(v):
    if v is None:
        return "—"
    return "включено" if v else "выключено"


class DefenderPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.st = {}
        self._loaded = False
        self._busy = 0
        self._tamper_blocked = False      # выключить не дала защита от подделки
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(title("Антивирус"))
        head.addStretch(1)
        self.pill_state = pill("—", MUTED)
        head.addWidget(self.pill_state)
        head.addWidget(tool_button("Обновить", "refresh", on_click=self.reload))
        head.addWidget(tool_button("Открыть Безопасность Windows", "shield",
                                   on_click=lambda: run_async(defender.open_security_center)))
        v.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._protect_tab(), icon("shield", MUTED, 15), "Защита")
        self.tabs.addTab(self._excl_tab(), icon("list", MUTED, 15), "Исключения")
        self.tabs.addTab(self._scan_tab(), icon("search", MUTED, 15), "Проверка и угрозы")
        self.tabs.currentChanged.connect(self._tab_changed)
        v.addWidget(self.tabs, 1)

    # ------------------------------------------------------------ защита
    def _protect_tab(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 12, 0, 0)
        g.setSpacing(12)

        c1 = Card("Состояние Defender")
        box, self.kv = kv_grid([
            ("rt", "Реальное время"), ("av", "Антивирус"), ("am", "Служба"),
            ("tamper", "Защита от подделки"), ("mode", "Режим"), ("engine", "Движок"),
            ("sigver", "Сигнатуры"), ("sigdate", "Обновлены"), ("quick", "Быстрая проверка"),
            ("full", "Полная проверка")], 2)
        c1.v.addWidget(box)
        self.err = muted("")
        self.err.setStyleSheet(f"color:{AMBER};")
        self.err.hide()
        c1.v.addWidget(self.err)

        c2 = Card("Антивирусы в системе", "Центр безопасности Windows")
        self.others = _tree(["Антивирус", "Состояние", "Базы", "Путь"], [220, 110, 110])
        c2.v.addWidget(self.others, 1)

        c3 = Card("Защита в реальном времени")
        self.rt = QCheckBox("Проверять файлы и программы в момент запуска и открытия")
        self.rt.clicked.connect(self._toggle_rt)
        c3.v.addWidget(self.rt)
        self.tamper_hint = muted(defender.TAMPER_HINT)
        self.tamper_hint.setStyleSheet(f"color:{AMBER};")
        self.tamper_hint.hide()
        c3.v.addWidget(self.tamper_hint)
        c3.v.addWidget(muted("Windows сама включит защиту снова через некоторое время "
                             "или после перезагрузки, если не установлен другой антивирус."))

        c4 = Card("Настройки")
        self.checks = {}
        for key, (label, _a, _b) in defender.SETTINGS.items():
            cb = QCheckBox(label)
            cb.clicked.connect(lambda on, k=key: self._toggle(k, on))
            c4.v.addWidget(cb)
            self.checks[key] = cb
        sl = QHBoxLayout()
        sl.addWidget(QLabel("Нагрузка на CPU при проверке"))
        self.cpu = QSlider(Qt.Horizontal)
        self.cpu.setRange(5, 100)
        self.cpu.setValue(50)
        self.cpu.setPageStep(5)
        self.cpu_lbl = QLabel("50 %")
        self.cpu_lbl.setFixedWidth(48)
        self.cpu.valueChanged.connect(lambda x: self.cpu_lbl.setText(f"{x} %"))
        self.cpu.sliderReleased.connect(self._set_cpu)
        sl.addWidget(self.cpu, 1)
        sl.addWidget(self.cpu_lbl)
        c4.v.addLayout(sl)
        c4.v.addWidget(muted("Средняя доля процессора, которую Defender может занимать во время "
                             "проверки. Меньше — тише, но проверка идёт дольше."))
        c4.v.addStretch(1)

        g.addWidget(c1, 0, 0)
        g.addWidget(c2, 1, 0)
        g.addWidget(c3, 0, 1)
        g.addWidget(c4, 1, 1)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        g.setRowStretch(1, 1)
        self._set_enabled(False)
        return w

    def _set_enabled(self, on):
        self.rt.setEnabled(on)
        for cb in self.checks.values():
            cb.setEnabled(on)
        self.cpu.setEnabled(on)

    # ------------------------------------------------------------ исключения
    def _excl_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(10)
        v.addWidget(muted(
            "Исключения — файлы, папки, процессы и расширения, которые Defender не проверяет. "
            "«Разрешённые приложения» и «Защищённые папки» относятся к контролируемому доступу "
            "к папкам (защите от шифровальщиков). Добавляйте только то, чему доверяете."))
        bar = QHBoxLayout()
        self.ex_kind = QComboBox()
        for k in defender.KINDS:
            self.ex_kind.addItem(KIND_LABELS.get(k, k), k)
        self.ex_kind.setMinimumWidth(250)
        self.ex_kind.currentIndexChanged.connect(self._kind_changed)
        bar.addWidget(self.ex_kind)
        self.ex_value = QLineEdit()
        self.ex_value.returnPressed.connect(self._add_excl)
        bar.addWidget(self.ex_value, 1)
        self.ex_browse = tool_button("Обзор…", "folder", on_click=self._browse)
        bar.addWidget(self.ex_browse)
        bar.addWidget(tool_button("Добавить", "check", "Primary", self._add_excl))
        bar.addWidget(tool_button("Удалить", "trash", "Danger", self._remove_excl))
        v.addLayout(bar)
        self.ex_tree = _tree(["Тип", "Значение"], [290])
        self.ex_tree.setSortingEnabled(True)
        self.ex_tree.itemDoubleClicked.connect(
            lambda it, _c: self.state["clipboard"](it.text(1)))
        v.addWidget(self.ex_tree, 1)
        self._kind_changed()
        return w

    def _kind_changed(self, *_):
        k = self.ex_kind.currentData()
        ph = {"path": r"C:\Games\  или  C:\Tools\app.exe", "proc": "app.exe или полный путь",
              "ext": ".iso", "cfa_app": r"C:\Program Files\App\app.exe",
              "cfa_dir": r"D:\Документы"}
        self.ex_value.setPlaceholderText(ph.get(k, ""))
        self.ex_browse.setEnabled(k != "ext")

    def _browse(self):
        k = self.ex_kind.currentData()
        if k == "path":
            m = QMenu(self)
            a_file = m.addAction(icon("copy", MUTED), "Файл…")
            a_dir = m.addAction(icon("folder", MUTED), "Папка…")
            act = m.exec(self.ex_browse.mapToGlobal(self.ex_browse.rect().bottomLeft()))
            if act is None:
                return
            pick_dir = act is a_dir
        else:
            pick_dir = k == "cfa_dir"
        if pick_dir:
            p = QFileDialog.getExistingDirectory(self, "Выберите папку")
        else:
            flt = "Программы (*.exe);;Все файлы (*)" if k in ("proc", "cfa_app") else "Все файлы (*)"
            p, _ = QFileDialog.getOpenFileName(self, "Выберите файл", "", flt)
        if p:
            self.ex_value.setText(os.path.normpath(p))

    def _fill_excl(self):
        t = self.ex_tree
        t.setSortingEnabled(False)
        t.clear()
        for k in defender.KINDS:
            for val in self.st.get(KIND_STATUS[k]) or []:
                it = QTreeWidgetItem([KIND_LABELS.get(k, k), str(val)])
                it.setData(0, Qt.UserRole, k)
                t.addTopLevelItem(it)
        t.setSortingEnabled(True)
        t.sortByColumn(0, Qt.AscendingOrder)

    def _add_excl(self):
        k, val = self.ex_kind.currentData(), self.ex_value.text().strip().strip('"')
        if not val:
            self.state["toast"]("Укажите значение исключения", "warn")
            return
        if k == "ext" and not val.startswith("."):
            val = "." + val
        if not self._admin_ok():
            return

        def done(r):
            self._done(r, f"Добавлено: {val}")
            if not isinstance(r, Exception) and r[0]:
                self.ex_value.clear()
            self.reload()
        run_async(lambda: defender.add(k, val), done)

    def _remove_excl(self):
        it = self.ex_tree.currentItem()
        if not it:
            self.state["toast"]("Выберите исключение в списке", "warn")
            return
        k, val = it.data(0, Qt.UserRole), it.text(1)
        if QMessageBox.question(self, "Исключения", f"Удалить исключение?\n\n{val}") != QMessageBox.Yes:
            return
        if not self._admin_ok():
            return
        run_async(lambda: defender.remove(k, val),
                  lambda r: (self._done(r, f"Удалено: {val}"), self.reload()))

    # ------------------------------------------------------------ проверка
    def _scan_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(12)
        c = Card("Проверка")
        tiles = QGridLayout()
        tiles.setSpacing(10)
        self.scan_buttons = []
        for i, (key, ico, name, sub) in enumerate([
                ("quick", "zap", "Быстрая проверка", "Места, где обычно прячутся угрозы"),
                ("full", "hdd", "Полная проверка", "Все файлы и программы — долго"),
                ("custom", "folder", "Выбранная папка", "Проверить только выбранную папку"),
                ("offline", "power", "Автономная проверка", "Перезагрузка и проверка до Windows")]):
            b = tool_button(f"  {name}\n  {sub}", ico)
            b.setObjectName("Tile")
            b.setIcon(icon(ico, PURPLE if key == "offline" else GREEN, 20))
            b.setMinimumHeight(62)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._scan(k))
            tiles.addWidget(b, i // 2, i % 2)
            self.scan_buttons.append(b)
        c.v.addLayout(tiles)
        self.scan_lbl = QLabel("")
        self.scan_lbl.setObjectName("Small")
        self.scan_bar = QProgressBar()
        self.scan_bar.setRange(0, 0)
        self.scan_bar.setTextVisible(False)
        self.scan_bar.setFixedHeight(6)
        self.scan_bar.hide()
        c.v.addWidget(self.scan_bar)
        c.v.addWidget(self.scan_lbl)
        row.addWidget(c, 2)

        c2 = Card("Базы сигнатур")
        box, self.kv_sig = kv_grid([("sigver", "Версия"), ("sigdate", "Обновлены"),
                                    ("sigage", "Возраст")], 1)
        c2.v.addWidget(box)
        self.btn_sig = tool_button("Обновить сигнатуры", "download", "Primary", self._update_sig)
        c2.v.addWidget(self.btn_sig, 0, Qt.AlignLeft)
        c2.v.addStretch(1)
        row.addWidget(c2, 1)
        v.addLayout(row)

        c3 = Card("Обнаруженные угрозы", "последние 50")
        c3.add_action(tool_button("Обновить список", "refresh", on_click=self.reload_threats))
        c3.add_action(tool_button("Журнал защиты", "shield",
                                  on_click=lambda: run_async(defender.open_security_center)))
        self.th_tree = _tree(["Когда", "Угроза", "Действие", "Где"], [160, 260, 120])
        c3.v.addWidget(self.th_tree, 1)
        self.th_empty = muted("Угроз не обнаружено.")
        c3.v.addWidget(self.th_empty)
        v.addWidget(c3, 1)
        return w

    def _scan(self, kind):
        if not self._admin_ok():
            return
        path = None
        if kind == "custom":
            path = QFileDialog.getExistingDirectory(self, "Папка для проверки")
            if not path:
                return
            path = os.path.normpath(path)
        if kind == "offline":
            if QMessageBox.question(
                    self, "Автономная проверка",
                    "Компьютер перезагрузится примерно через минуту, проверка займёт около "
                    "15 минут. Сохраните открытые документы.\n\nПродолжить?") != QMessageBox.Yes:
                return
            run_async(defender.offline_scan, lambda r: self._done(r))
            return
        names = {"quick": "Быстрая проверка", "full": "Полная проверка",
                 "custom": f"Проверка {path}"}
        self._busy += 1
        self.scan_bar.show()
        self.scan_lbl.setText(f"{names[kind]} выполняется… Можно продолжать работать.")
        for b in self.scan_buttons[:3]:
            b.setEnabled(False)

        def done(r):
            self._busy -= 1
            for b in self.scan_buttons[:3]:
                b.setEnabled(True)
            self.scan_bar.hide()
            ok = not isinstance(r, Exception) and r[0]
            self.scan_lbl.setText(f"{names[kind]}: {'завершена' if ok else 'ошибка'}")
            self._done(r, f"{names[kind]} завершена")
            self.reload_threats()
            self.reload()
        run_async(lambda: defender.scan(kind, path), done)

    def _update_sig(self):
        if not self._admin_ok():
            return
        self.btn_sig.setEnabled(False)
        self.btn_sig.setText("Обновляю…")

        def done(r):
            self.btn_sig.setEnabled(True)
            self.btn_sig.setText("Обновить сигнатуры")
            self._done(r, "Базы сигнатур обновлены")
            self.reload()
        run_async(defender.update_signatures, done)

    def reload_threats(self):
        def got(rows):
            if isinstance(rows, Exception):
                rows = []
            self.th_tree.clear()
            for r in rows:
                if not isinstance(r, dict):
                    continue
                act = r.get("action")
                it = QTreeWidgetItem([str(r.get("time") or "—"),
                                      str(r.get("name") or f'ID {r.get("id")}'),
                                      "обезврежена" if act else ("не удалось" if act is False else "—"),
                                      str(r.get("res") or "")])
                it.setForeground(1, QBrush(QColor(RED)))
                it.setForeground(2, QBrush(QColor(OK if act else AMBER)))
                it.setToolTip(3, str(r.get("res") or ""))
                self.th_tree.addTopLevelItem(it)
            self.th_empty.setVisible(self.th_tree.topLevelItemCount() == 0)
        run_async(defender.threats, got)

    # ------------------------------------------------------------ данные
    def on_show(self):
        if not self._loaded:
            self._loaded = True
            self.reload()
            self.reload_threats()

    def _tab_changed(self, i):
        if i == 2 and self._loaded:
            self.reload_threats()

    def reload(self):
        self.pill_state.setText("загрузка…")
        self.pill_state.setStyleSheet(pill_style(MUTED))
        run_async(defender.status, self._fill)
        run_async(defender.others, self._fill_others)

    def _fill(self, st):
        if isinstance(st, Exception):
            st = {"error": str(st)}
        self.st = st or {}
        err = self.st.get("error")
        self.err.setVisible(bool(err))
        self.err.setText(f"Defender недоступен: {err}" if err else "")
        self._set_enabled(not err)
        if err:
            self.pill_state.setText("Defender недоступен")
            self.pill_state.setStyleSheet(pill_style(AMBER))
            for lbl in list(self.kv.values()) + list(self.kv_sig.values()):
                lbl.setText("—")
            self._fill_excl()
            return
        rt = bool(st.get("rt"))
        self.pill_state.setText("защита включена" if rt else "защита в реальном времени выключена")
        self.pill_state.setStyleSheet(pill_style(OK if rt else RED))
        vals = {"rt": _yes(st.get("rt")), "av": _yes(st.get("av")), "am": _yes(st.get("am")),
                "tamper": _yes(st.get("tamper")), "mode": st.get("mode") or "—",
                "engine": st.get("engine") or "—", "sigver": st.get("sigver") or "—",
                "sigdate": st.get("sigdate") or "—", "quick": st.get("quick") or "никогда",
                "full": st.get("full") or "никогда"}
        for k, lbl in self.kv.items():
            lbl.setText(str(vals.get(k, "—")))
        self.kv["rt"].setStyleSheet(f"color:{OK if rt else RED};")
        age = st.get("sigage")
        self.kv_sig["sigver"].setText(str(st.get("sigver") or "—"))
        self.kv_sig["sigdate"].setText(str(st.get("sigdate") or "—"))
        self.kv_sig["sigage"].setText("—" if age is None else f"{age} дн.")
        self.kv_sig["sigage"].setStyleSheet(f"color:{AMBER if (age or 0) > 3 else TEXT2};")
        self.rt.setChecked(rt)
        if not st.get("tamper"):
            self._tamper_blocked = False
        self.tamper_hint.setVisible(bool(st.get("tamper")) and (not rt or self._tamper_blocked))
        for k, cb in self.checks.items():
            cb.setChecked(defender.is_on(k, st))
        if not self.cpu.isSliderDown():
            self.cpu.setValue(int(st.get("scanavg") or 50))
        self._fill_excl()

    def _fill_others(self, rows):
        if isinstance(rows, Exception):
            rows = []
        self.others.clear()
        for r in rows:
            it = QTreeWidgetItem([str(r.get("name") or "?"),
                                  "работает" if r.get("on") else "выключен",
                                  "актуальны" if r.get("uptodate") else "устарели",
                                  str(r.get("path") or "")])
            it.setForeground(1, QBrush(QColor(OK if r.get("on") else MUTED)))
            it.setForeground(2, QBrush(QColor(OK if r.get("uptodate") else AMBER)))
            self.others.addTopLevelItem(it)
        if not rows:
            self.others.addTopLevelItem(QTreeWidgetItem(["нет данных", "", "", ""]))

    def _admin_ok(self):
        if admin.is_admin():
            return True
        self.state["toast"]("Нужны права администратора", "error")
        return False

    def _done(self, r, ok_text=None):
        if isinstance(r, Exception):
            self.state["toast"](f"Ошибка: {r}", "error")
        elif not r[0]:
            self.state["toast"](str(r[1])[:220] or "Не удалось", "error")
        else:
            self.state["toast"](ok_text or str(r[1])[:200])

    def _toggle_rt(self, on):
        if not self._admin_ok():
            self.rt.setChecked(not on)
            return
        if not on and QMessageBox.question(
                self, "Защита в реальном времени",
                "Выключить защиту в реальном времени?\n\nКомпьютер останется без "
                "антивирусной защиты, пока она выключена.") != QMessageBox.Yes:
            self.rt.setChecked(True)
            return
        self.rt.setEnabled(False)

        def done(r):
            self.rt.setEnabled(True)
            if not isinstance(r, Exception) and not r[0] and r[1] == defender.TAMPER_HINT:
                self._tamper_blocked = True
                self.tamper_hint.show()
                self.state["toast"]("Не удалось: мешает защита от подделки", "warn")
            else:
                self._done(r, "Защита в реальном времени " + ("включена" if on else "выключена"))
            self.reload()
        run_async(lambda: defender.set_realtime(on), done)

    def _toggle(self, key, on):
        if not self._admin_ok():
            self.checks[key].setChecked(not on)
            return
        label = defender.SETTINGS[key][0]
        run_async(lambda: defender.set_setting(key, on),
                  lambda r: (self._done(r, f"{label}: {'вкл' if on else 'выкл'}"), self.reload()))

    def _set_cpu(self):
        if not self._admin_ok():
            return
        p = self.cpu.value()
        run_async(lambda: defender.set_cpu_load(p),
                  lambda r: self._done(r, f"Нагрузка на CPU при проверке: {p} %"))
