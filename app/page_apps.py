"""Установленные программы: поиск, удаление (обычное и тихое), приложения Microsoft Store."""
import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QHeaderView, QMenu, QMessageBox,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from core import admin, apps, monitor
from .icons import icon
from .theme import AMBER, BLUE, MUTED, PURPLE
from .widgets import SearchBox, SortItem, file_icon, muted, pill, pill_style, title, tool_button
from .workers import run_async

COLS = ["Программа", "Издатель", "Версия", "Размер", "Установлена", "Источник"]
C_NAME, C_PUB, C_VER, C_SIZE, C_DATE, C_SRC = range(6)
FILTERS = [("Все", "all"), ("Программы", "win32"), ("Microsoft Store", "store")]


class _Item(SortItem, QTreeWidgetItem):
    pass


class AppsPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.apps = []
        self._loaded = False
        self._loading = False
        self._busy = None
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(title("Программы"))
        head.addStretch(1)
        self.pill = pill("—", MUTED)
        head.addWidget(self.pill)
        v.addLayout(head)
        v.addWidget(muted("Все установленные программы и приложения Microsoft Store. «Удалить» "
                          "запускает родной деинсталлятор программы; «Тихо» — без вопросов, если "
                          "программа это поддерживает. Двойной клик — удалить."))

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = SearchBox("Поиск по названию или издателю…")
        self.search.textChanged.connect(self.render)
        bar.addWidget(self.search, 1)
        self.btn_uninst = tool_button("Удалить", "trash", "Danger", lambda: self.uninstall(False))
        self.btn_quiet = tool_button("Тихо", "zap", on_click=lambda: self.uninstall(True),
                                     tip="Удалить без окон деинсталлятора (если поддерживается)")
        self.btn_folder = tool_button("", "folder", on_click=self.open_folder, tip="Открыть папку программы")
        bar.addWidget(self.btn_uninst)
        bar.addWidget(self.btn_quiet)
        bar.addWidget(self.btn_folder)
        bar.addWidget(tool_button("Обновить", "refresh", on_click=self.reload))
        v.addLayout(bar)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.group = QButtonGroup(self)
        for i, (label, key) in enumerate(FILTERS):
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setProperty("fkey", key)
            b.setChecked(i == 0)
            self.group.addButton(b, i)
            chips.addWidget(b)
        self.group.idClicked.connect(lambda _: self.render())
        chips.addStretch(1)
        v.addLayout(chips)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(C_NAME, Qt.AscendingOrder)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.menu)
        self.tree.itemDoubleClicked.connect(lambda *_: self.uninstall(False))
        self.tree.itemSelectionChanged.connect(self._sync_buttons)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        for i, w in enumerate([380, 220, 130, 90, 110, 160]):
            self.tree.setColumnWidth(i, w)
        self.tree.headerItem().setTextAlignment(C_SIZE, Qt.AlignRight | Qt.AlignVCenter)
        v.addWidget(self.tree, 1)
        self._sync_buttons()

    # ----------------------------------------------------------------
    def on_show(self):
        if not self._loaded:
            self.reload()

    def reload(self):
        if self._loading:
            return
        self._loading = True
        self._set_pill("загрузка списка…", AMBER)

        def got_win32(res):
            if isinstance(res, Exception):
                self.state["toast"](f"Не удалось прочитать список программ: {res}", "error")
                res = []
            self.apps = res
            self._loaded = True
            self.render()
            run_async(apps.list_store, got_store)    # Store медленнее — догружаем вторым шагом

        def got_store(res):
            self._loading = False
            if not isinstance(res, Exception):
                self.apps = [a for a in self.apps if a["kind"] != "store"] + res
            self.render()
        run_async(apps.list_win32, got_win32)

    def _set_pill(self, text, color):
        self.pill.setText(text)
        self.pill.setStyleSheet(pill_style(color))

    def render(self):
        q = self.search.text().strip().lower()
        b = self.group.checkedButton()
        f = b.property("fkey") if b else "all"
        sel = self._current()
        sel_id = sel["id"] if sel else None
        self.tree.setUpdatesEnabled(False)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        shown, total = 0, 0
        for a in self.apps:
            if f != "all" and a["kind"] != f:
                continue
            if q and q not in f'{a["name"]} {a["publisher"]}'.lower():
                continue
            it = _Item([a["name"], a["publisher"], a["version"],
                        monitor.human(a["size"]) if a["size"] else "", a["date"], a["scope"]])
            it.setData(C_NAME, Qt.UserRole, a["id"])
            it.setData(C_SIZE, SortItem.SORT_ROLE, a["size"])
            it.setData(C_DATE, SortItem.SORT_ROLE, a["date"] or "0")
            it.setTextAlignment(C_SIZE, Qt.AlignRight | Qt.AlignVCenter)
            if a["kind"] == "store":
                it.setIcon(C_NAME, icon("download", PURPLE, 18))
            else:
                exe = a["icon"] or ""
                it.setIcon(C_NAME, file_icon(exe) if exe else icon("monitor", BLUE, 18))
            it.setToolTip(C_NAME, a.get("location") or a["name"])
            self.tree.addTopLevelItem(it)
            if a["id"] == sel_id:
                it.setSelected(True)
                self.tree.setCurrentItem(it)
            shown += 1
            total += a["size"]
        self.tree.setSortingEnabled(True)
        self.tree.setUpdatesEnabled(True)
        if self._busy:
            self._set_pill(f"удаление: {self._busy}…", AMBER)
        elif self._loaded:
            more = " · загрузка Store…" if self._loading else ""
            self._set_pill(f"{shown} программ · {monitor.human(total)}{more}", MUTED)
        self._sync_buttons()

    def _current(self):
        it = self.tree.currentItem()
        if it is None or not it.isSelected():
            return None
        aid = it.data(C_NAME, Qt.UserRole)
        return next((a for a in self.apps if a["id"] == aid), None)

    def _sync_buttons(self):
        a = self._current()
        free = self._busy is None
        self.btn_uninst.setEnabled(bool(a) and free)
        self.btn_quiet.setEnabled(bool(a and a.get("quiet")) or bool(a and a.get("msi"))
                                  or bool(a and a["kind"] == "store"))
        self.btn_quiet.setEnabled(self.btn_quiet.isEnabled() and free)
        self.btn_folder.setEnabled(bool(a and a.get("location") and os.path.isdir(a["location"])))

    # ---------------------------------------------------------------- действия
    def uninstall(self, quiet):
        a = self._current()
        if not a or self._busy:
            return
        if not admin.is_admin() and a["scope"].startswith("Все"):
            extra = "\n\nKryostat запущен без прав администратора — Windows может спросить разрешение."
        else:
            extra = ""
        how = "тихо (без окон деинсталлятора)" if quiet else "через деинсталлятор программы"
        if QMessageBox.question(self, "Удаление программы",
                                f"Удалить «{a['name']}» {how}?{extra}",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._busy = a["name"]
        self.render()

        def work():
            ok, msg = apps.uninstall(a, quiet)
            gone = not apps.still_installed(a)
            return ok, msg, gone

        def done(res):
            self._busy = None
            if isinstance(res, Exception):
                self.state["toast"](f"Ошибка удаления: {res}", "error")
            else:
                ok, msg, gone = res
                if gone:
                    self.state["toast"](f"«{a['name']}» удалена")
                    self.apps = [x for x in self.apps if x["id"] != a["id"]]
                elif ok:
                    # некоторые деинсталляторы продолжают работу в отдельном процессе
                    self.state["toast"](f"{msg}. Если программа ещё в списке — нажмите «Обновить» "
                                        "после завершения деинсталлятора", "warn")
                else:
                    self.state["toast"](f"«{a['name']}»: {msg}", "warn")
            self.render()
        run_async(work, done)

    def open_folder(self):
        a = self._current()
        if a and a.get("location") and os.path.isdir(a["location"]) and admin.IS_WINDOWS:
            os.startfile(a["location"])

    def remove_entry(self):
        a = self._current()
        if not a or a["kind"] != "win32":
            return
        if QMessageBox.question(
                self, "Убрать запись",
                f"Убрать «{a['name']}» из списка установленных программ?\n\nФайлы программы не "
                "удаляются — только запись в реестре (с резервной копией). Используйте, если "
                "программа уже удалена вручную, а запись осталась.",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        def done(res):
            if isinstance(res, Exception):
                self.state["toast"](f"Не удалось: {res}", "error")
            else:
                self.apps = [x for x in self.apps if x["id"] != a["id"]]
                self.state["toast"]("Запись удалена (резервная копия — в папке registry-backups)")
            self.render()
        run_async(lambda: apps.remove_entry(a), done)

    def menu(self, pos):
        a = self._current()
        if not a:
            return
        m = QMenu(self)
        m.addAction(icon("trash", MUTED, 16), "Удалить", lambda: self.uninstall(False))
        if a.get("quiet") or a.get("msi") or a["kind"] == "store":
            m.addAction(icon("zap", MUTED, 16), "Удалить тихо", lambda: self.uninstall(True))
        if a.get("location") and os.path.isdir(a["location"]):
            m.addAction(icon("folder", MUTED, 16), "Открыть папку", self.open_folder)
        m.addSeparator()
        m.addAction(icon("copy", MUTED, 16), "Копировать название",
                    lambda: self.state["clipboard"](a["name"]))
        if a["kind"] == "win32":
            m.addAction(icon("copy", MUTED, 16), "Копировать команду удаления",
                        lambda: self.state["clipboard"](a["uninstall"] or a["quiet"]))
            m.addSeparator()
            m.addAction(icon("x", MUTED, 16), "Убрать запись из списка…", self.remove_entry)
        m.exec(self.tree.viewport().mapToGlobal(pos))
