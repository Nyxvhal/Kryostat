"""Автозапуск: список, дубликаты, обратимое отключение."""
from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
import os
import subprocess
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QHBoxLayout,
                               QHeaderView, QMenu, QMessageBox, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from core import autorun, config, startup as st
from .icons import icon
from .theme import AMBER, GREEN, MUTED, RED, TEXT, TEXT2
from .widgets import SearchBox, SortItem, muted, pill, title, tool_button
from .workers import run_async

FILTERS = [("Всё", "all"), ("Включённые", "on"), ("Отключённые", "off"),
           ("Дубликаты", "dup"), ("Реестр", "registry"),
           ("Папка автозагрузки", "folder"), ("Microsoft Store", "appx"),
           ("Планировщик", "task")]
COLS = ["", "Автозапуск", "Дубликат", "Источник", "Команда"]


class _Item(SortItem, QTreeWidgetItem):
    pass


class ScanWorker(QThread):
    done = Signal(list)

    def __init__(self, include_tasks=True):
        super().__init__()
        self.include_tasks = include_tasks

    def run(self):
        try:
            self.done.emit(st.scan(self.include_tasks))
        except Exception as e:
            config.log(f"startup scan: {e}")
            self.done.emit([])


class StartupPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.items = []
        self._by_id = {}
        self._busy = False
        self.worker = None
        self._applying = False
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(title("Автозапуск"))
        head.addStretch(1)
        self.pill_info = pill("сканирование…", MUTED)
        head.addWidget(self.pill_info)
        v.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = SearchBox("Поиск по названию или команде…")
        self.search.textChanged.connect(self.render)
        bar.addWidget(self.search, 1)
        self.btn_scan = tool_button("Пересканировать", "refresh", on_click=self.reload)
        self.btn_on = tool_button("Включить", "check", on_click=lambda: self.toggle(True))
        self.btn_off = tool_button("Отключить", "minus", "Danger", lambda: self.toggle(False))
        self.btn_dup = tool_button("Убрать дубликаты", "copy", "Primary", self.fix_duplicates)
        for b in (self.btn_scan, self.btn_on, self.btn_off, self.btn_dup):
            bar.addWidget(b)
        v.addLayout(bar)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.group = QButtonGroup(self)
        for i, (label, key) in enumerate(FILTERS):
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setProperty("fkey", key)
            if i == 0:
                b.setChecked(True)
            self.group.addButton(b, i)
            chips.addWidget(b)
        self.group.idClicked.connect(lambda _: self.render())
        chips.addStretch(1)
        v.addLayout(chips)

        opts = QHBoxLayout()
        opts.setSpacing(10)
        cfg = config.load()
        self.chk_kill = QCheckBox("При старте ПК завершать программы, отключённые здесь")
        self.chk_kill.setChecked(cfg.get("kill_on_boot", True))
        self.chk_kill.toggled.connect(self._save_kill)
        opts.addWidget(self.chk_kill)
        opts.addWidget(tool_button("Завершить сейчас", "zap", on_click=self.kill_now))
        opts.addStretch(1)
        v.addLayout(opts)

        v.addWidget(muted(
            "Снимите галочку в списке, чтобы убрать программу из автозапуска — это обратимо: "
            "запись сохраняется и возвращается галочкой назад. "
            "Янтарным помечены дубликаты (одна и та же программа прописана несколько раз)."))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.menu)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        for i, w in enumerate([46, 280, 90, 330, 480]):
            self.tree.setColumnWidth(i, w)
        # Пробел — переключить выделенные записи
        QShortcut(QKeySequence(Qt.Key_Space), self.tree, activated=self._toggle_selected,
                  context=Qt.WidgetShortcut)
        v.addWidget(self.tree, 1)
        self.empty = muted("")
        self.empty.setAlignment(Qt.AlignCenter)
        v.addWidget(self.empty)

    # ------------------------------------------------------------------
    def _save_kill(self, on):
        config.update(lambda cfg: cfg.__setitem__("kill_on_boot", bool(on)))

    def sync(self):
        """Подтянуть настройки, которые можно изменить на другой странице."""
        self.chk_kill.blockSignals(True)
        self.chk_kill.setChecked(config.load().get("kill_on_boot", True))
        self.chk_kill.blockSignals(False)

    def reload(self):
        if self.worker and self.worker.isRunning():
            return
        self.pill_info.setText("сканирование…")
        self.btn_scan.setEnabled(False)
        self.worker = ScanWorker(config.load().get("scan_tasks", True))
        self.worker.done.connect(self._scanned)
        self.worker.start()

    def _scanned(self, items):
        self.items = items
        self._by_id = {i["id"]: i for i in items}
        self.btn_scan.setEnabled(True)
        self.render()

    def _filter_key(self):
        b = self.group.checkedButton()
        return b.property("fkey") if b else "all"

    def render(self):
        q = self.search.text().strip().lower()
        f = self._filter_key()
        self._applying = True
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        shown = 0
        nodes = []
        for it in self.items:
            if f == "on" and not it["enabled"]:
                continue
            if f == "off" and it["enabled"]:
                continue
            if f == "dup" and not it["duplicate"]:
                continue
            if f in ("registry", "folder", "task", "appx") and it["type"] != f:
                continue
            if q and q not in f'{it["name"]} {it["command"]} {it["location"]}'.lower():
                continue
            where = it["location"] + (f' · {it["when"]}' if it.get("when") else "")
            node = _Item(["", it["name"],
                          f'×{it["duplicate_count"]}' if it["duplicate"] else "",
                          where, it["command"]])
            node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            node.setData(0, Qt.UserRole, it["id"])
            node.setData(0, SortItem.SORT_ROLE, 1 if it["enabled"] else 0)
            node.setData(2, SortItem.SORT_ROLE, it["duplicate_count"])
            node.setToolTip(4, it["command"])
            node.setToolTip(3, where)
            if it["duplicate"]:
                for c in range(1, 5):
                    node.setForeground(c, QBrush(QColor(AMBER)))
                node.setToolTip(2, f'Программа «{it["dupkey"]}» прописана '
                                   f'{it["duplicate_count"]} раз(а)')
            elif not it["enabled"]:
                for c in range(1, 5):
                    node.setForeground(c, QBrush(QColor(MUTED)))
                fo = QFont(); fo.setItalic(True)
                node.setFont(1, fo)
            else:
                node.setForeground(1, QBrush(QColor(TEXT)))
            nodes.append((node, it))
            shown += 1
        self.tree.addTopLevelItems([n for n, _ in nodes])
        # настоящий QCheckBox вместо встроенного индикатора QTreeWidget: у встроенного
        # с нашими стилями (padding у ::item) область клика не совпадала с нарисованной
        # галочкой — клики «мимо» и галочка «не работала»
        for node, it in nodes:
            self.tree.setItemWidget(node, 0, self._make_check(it))
        self.tree.setSortingEnabled(True)
        self._applying = False
        dups = sum(1 for i in self.items if i["duplicate"])
        off = sum(1 for i in self.items if not i["enabled"])
        self.pill_info.setText(f"{len(self.items)} записей · показано {shown} · "
                               f"дубликатов {dups} · отключено {off}")
        if not self.items:
            self.empty.setText("Сканирование не нашло записей автозапуска. "
                               "Нажмите «Пересканировать».")
        elif not shown:
            self.empty.setText("Под текущий фильтр и поиск ничего не подходит.")
        else:
            self.empty.setText("")

    # ------------------------------------------------------------- actions
    def _apply(self, items, enable, on_done):
        """Переключить записи в фоне (реестр / schtasks могут отвечать долго)."""
        if self._busy:
            return
        todo = [it for it in items if it["enabled"] != enable]
        if not todo:
            on_done(0, [])
            return
        self._busy = True
        self.tree.setEnabled(False)

        def work():
            return [(it, *st.set_enabled(it, enable)) for it in todo]

        def done(res):
            self._busy = False
            self.tree.setEnabled(True)
            if isinstance(res, Exception):
                self.state["toast"](f"Ошибка: {res}", "error")
                self.render()
                return
            ok_n, errs = 0, []
            for it, ok, msg in res:
                if ok:
                    it["enabled"] = enable
                    ok_n += 1
                else:
                    errs.append(f'{it["name"]}: {msg}')
            self._recount()
            self.render()
            on_done(ok_n, errs)
        run_async(work, done)

    def _recount(self):
        """Пересчитать дубликаты после включения/отключения записей."""
        st.recount(self.items)

    def _make_check(self, it):
        box = QWidget()
        box.setAttribute(Qt.WA_TranslucentBackground)
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setAlignment(Qt.AlignCenter)
        cb = QCheckBox()
        cb.setChecked(bool(it["enabled"]))
        cb.setCursor(Qt.PointingHandCursor)
        cb.setEnabled(not self._busy and not it.get("policy"))
        tip = ("Задано групповой политикой" if it.get("policy") else
               "Снять галочку — убрать из автозапуска, поставить — вернуть")
        cb.setToolTip(tip)
        cb.toggled.connect(lambda on, iid=it["id"], w=cb: self.on_check(iid, on, w))
        h.addWidget(cb)
        return box

    def on_check(self, iid, want, widget):
        item = self._by_id.get(iid)
        if not item or self._applying:
            return
        if self._busy or want == item["enabled"]:
            widget.blockSignals(True)
            widget.setChecked(item["enabled"])
            widget.blockSignals(False)
            return

        def done(n, errs):
            if errs:
                self.state["toast"](errs[0], "error")
            else:
                self.state["toast"](f'{item["name"]}: '
                                    f'{"включён в автозапуск" if want else "убран из автозапуска"}')
        # перерисовку откладываем: нельзя удалять чекбокс внутри его же сигнала
        QTimer.singleShot(0, lambda: self._apply([item], want, done))

    def _toggle_selected(self):
        items = self._selected()
        if not items:
            return
        want = not all(i["enabled"] for i in items)
        self._apply(items, want, lambda n, errs: self.state["toast"](
            (f"Изменено {n}, ошибок {len(errs)}. {errs[0]}" if errs else
             f'{"Включено" if want else "Отключено"} записей: {n}'),
            "warn" if errs else "info"))

    def _selected(self):
        out = [self._by_id.get(i.data(0, Qt.UserRole)) for i in self.tree.selectedItems()]
        return [o for o in out if o]

    def menu(self, pos):
        items = self._selected()
        if not items:
            return
        m = QMenu(self)
        m.addAction(icon("check", MUTED), "Включить", lambda: self.toggle(True))
        m.addAction(icon("minus", MUTED), "Отключить", lambda: self.toggle(False))
        m.addSeparator()
        m.addAction(icon("copy", MUTED), "Копировать команду",
                    lambda: self.state["clipboard"](items[0]["command"]))
        path = self._file_of(items[0])
        if path:
            m.addAction(icon("folder", MUTED), "Открыть расположение файла",
                        lambda: subprocess.Popen(["explorer", f"/select,{path}"]))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    @staticmethod
    def _file_of(it):
        cmd = (it.get("command") or "").strip()
        if cmd.startswith('"'):
            cand = cmd[1:].split('"', 1)[0]
        else:
            import re
            m = re.match(r"^(.*?\.(?:exe|bat|cmd|com|lnk|vbs|ps1))(\s|$)", cmd, re.I)
            cand = m.group(1) if m else cmd.split(" ")[0]
        cand = os.path.expandvars(cand)
        if os.path.isfile(cand):
            return cand
        p = it.get("path")
        return p if p and os.path.exists(p) else ""

    def toggle(self, enable: bool = True):
        items = self._selected()
        if not items:
            self.state["toast"]("Выделите записи в списке", "warn")
            return

        def done(ok_n, errs):
            if errs:
                self.state["toast"](f'Изменено {ok_n}, ошибок {len(errs)}. {errs[0]}', "warn")
            else:
                self.state["toast"](f'{"Включено" if enable else "Отключено"} записей: {ok_n}')
        self._apply(items, enable, done)

    def fix_duplicates(self):
        victims = st.pick_duplicates(self.items)
        if not victims:
            self.state["toast"]("Активных дубликатов не найдено")
            return
        names = "\n".join(f'• {v["name"]} — {v["location"]}' for v in victims[:12])
        more = f"\n…и ещё {len(victims) - 12}" if len(victims) > 12 else ""
        if QMessageBox.question(
                self, "Убрать дубликаты",
                f"Для каждой программы останется одна запись автозапуска.\n"
                f"Будет отключено: {len(victims)}\n\n{names}{more}\n\n"
                f"Это обратимо: записи можно вернуть галочкой.") != QMessageBox.Yes:
            return
        self._apply(victims, False,
                    lambda n, errs: self.state["toast"](
                        f"Дубликаты отключены: {n}" + (f" · ошибок {len(errs)}: {errs[0]}" if errs else ""),
                        "warn" if errs else "info"))

    def kill_now(self):
        def done(res):
            if isinstance(res, Exception):
                self.state["toast"](f"Ошибка: {res}", "error")
                return
            n, names = res
            if n:
                self.state["toast"](f"Завершено процессов: {n} · {', '.join(names[:5])}")
            else:
                self.state["toast"]("Нечего завершать — отключённые программы не запущены")
        run_async(autorun.kill_disabled_now, done)
