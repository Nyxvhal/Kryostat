"""Редактор реестра: дерево разделов с ленивой загрузкой, значения, поиск,
избранное, экспорт/импорт .reg. Перед каждым изменением — резервная копия раздела."""
import os
import queue
import threading

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QSplitter, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)
from core import admin, config, regtools
from .icons import icon
from .theme import OK, AMBER, GREEN, MUTED, PURPLE, RED, TEXT2
from .widgets import Card, RowDelegate, SearchBox, muted, pill, title, tool_button
from .workers import run_async

PATH_ROLE = Qt.UserRole
DUMMY_ROLE = Qt.UserRole + 1
LOADED_ROLE = Qt.UserRole + 2
VALUE_ROLE = Qt.UserRole + 3
EDIT_TYPES = ["REG_SZ", "REG_EXPAND_SZ", "REG_MULTI_SZ", "REG_DWORD", "REG_QWORD", "REG_BINARY"]


def _err(e) -> str:
    if isinstance(e, PermissionError) or getattr(e, "winerror", None) == 5:
        return "Отказано в доступе (нужны права администратора или раздел защищён системой)"
    if isinstance(e, FileNotFoundError) or getattr(e, "winerror", None) == 2:
        return "Раздел или значение не найдено"
    return str(e) or e.__class__.__name__


def _children(path):
    """Подразделы с признаком «есть ли у них свои подразделы» (в фоне)."""
    return [(n, regtools.has_children(path + "\\" + n)) for n in regtools.subkeys(path)]


def _chain(path):
    """Для перехода по адресу: подразделы каждого уровня по пути к ключу."""
    h, sub = regtools.split(path)
    cur, out = h, []
    parts = [p for p in sub.split("\\") if p]
    for p in parts:
        out.append((cur, _children(cur)))
        names = {n.lower(): n for n, _ in out[-1][1]}
        real = names.get(p.lower())
        if real is None:
            raise FileNotFoundError(f"Раздел не найден: {cur}\\{p}")
        cur = cur + "\\" + real
    regtools.subkeys(cur)                       # проверка доступа к самому ключу
    return cur, out


def _type_name(t):
    return regtools.TYPES.get(t, f"тип {t}")


def _to_text(data, tname):
    if data is None:
        return ""
    if tname in ("REG_DWORD", "REG_QWORD"):
        return f"0x{int(data):x}"
    if tname == "REG_BINARY" or isinstance(data, bytes):
        return " ".join(f"{x:02x}" for x in bytes(data))
    if tname == "REG_MULTI_SZ":
        return "\n".join(data or [])
    return str(data)


class ValueDialog(QDialog):
    """Создание и изменение значения."""

    def __init__(self, parent, name="", tname="REG_SZ", data=None, edit=False):
        super().__init__(parent)
        self.setWindowTitle("Изменить значение" if edit else "Новое значение")
        self.setMinimumWidth(520)
        self.result_value = None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("(По умолчанию) — оставьте пустым")
        self.name.setReadOnly(edit)
        self.type = QComboBox()
        self.type.addItems(EDIT_TYPES)
        if tname in EDIT_TYPES:
            self.type.setCurrentText(tname)
        self.type.setEnabled(not edit or tname in EDIT_TYPES)
        self.data = QPlainTextEdit(_to_text(data, tname))
        self.data.setMinimumHeight(150)
        self.hint = muted("")
        form.addRow("Имя", self.name)
        form.addRow("Тип", self.type)
        form.addRow("Данные", self.data)
        v.addLayout(form)
        v.addWidget(self.hint)
        self.error = QLabel("")
        self.error.setStyleSheet(f"color:{RED};")
        self.error.setWordWrap(True)
        v.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Сохранить")
        bb.button(QDialogButtonBox.Ok).setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.type.currentTextChanged.connect(self._hint)
        self._hint(self.type.currentText())
        (self.data if edit else self.name).setFocus()

    def _hint(self, t):
        self.hint.setText({
            "REG_SZ": "Строка.",
            "REG_EXPAND_SZ": "Строка с переменными среды, например %SystemRoot%\\System32.",
            "REG_MULTI_SZ": "Несколько строк — по одной на строке.",
            "REG_DWORD": "Число 32 бит: десятичное (1) или шестнадцатеричное (0x1).",
            "REG_QWORD": "Число 64 бит: десятичное или шестнадцатеричное (0x…).",
            "REG_BINARY": "Байты в шестнадцатеричном виде: 01 0a ff …",
        }.get(t, ""))

    def _ok(self):
        t = self.type.currentText()
        try:
            data = regtools.parse(self.data.toPlainText(), t)
        except ValueError as e:
            msg = str(e)
            if "invalid literal" in msg or "base 16" in msg:
                msg = "ожидается число — десятичное (10) или шестнадцатеричное (0xA)"
            elif "non-hexadecimal" in msg:
                msg = "ожидаются байты в шестнадцатеричном виде: 01 0a ff"
            self.error.setText(f"Неверные данные: {msg}")
            return
        self.result_value = (self.name.text(), t, data)
        self.accept()


class RegistryPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._started = False
        self._cur = ""
        self._val_token = 0
        self._search_q = None
        self._search_stop = None
        self._search_n = 0
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(title("Реестр"))
        head.addStretch(1)
        self.pill = pill("резервная копия перед каждым изменением", OK)
        head.addWidget(self.pill)
        self.btn_fav = tool_button("Избранное", "list")
        fav = QMenu(self)
        for name, path in regtools.FAVORITES:
            fav.addAction(name, lambda p=path: self.goto(p)).setToolTip(path)
        fav.setToolTipsVisible(True)
        self.btn_fav.setMenu(fav)
        head.addWidget(self.btn_fav)
        head.addWidget(tool_button("Экспорт", "download", on_click=self._export,
                                   tip="Сохранить выбранный раздел в .reg"))
        head.addWidget(tool_button("Импорт", "folder", on_click=self._import,
                                   tip="Добавить в реестр данные из .reg"))
        head.addWidget(tool_button("Резервные копии", "copy", on_click=self._open_backups))
        v.addLayout(head)

        bar = QHBoxLayout()
        self.addr = QLineEdit()
        self.addr.setPlaceholderText(r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion")
        self.addr.returnPressed.connect(lambda: self.goto(self.addr.text()))
        bar.addWidget(self.addr, 1)
        bar.addWidget(tool_button("Перейти", "play", "Primary", lambda: self.goto(self.addr.text())))
        bar.addWidget(tool_button("Копировать путь", "copy",
                                  on_click=lambda: self.state["clipboard"](self._long(self._cur))))
        v.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.keys = QTreeWidget()
        self.keys.setItemDelegate(RowDelegate(6, self.keys))
        self.keys.setHeaderLabels(["Раздел"])
        self.keys.setUniformRowHeights(True)
        self.keys.setSelectionMode(QAbstractItemView.SingleSelection)
        self.keys.itemExpanded.connect(self._expand)
        self.keys.currentItemChanged.connect(lambda cur, _p: self._select(cur))
        self.keys.setContextMenuPolicy(Qt.CustomContextMenu)
        self.keys.customContextMenuRequested.connect(self._key_menu)
        split.addWidget(self.keys)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        tb = QHBoxLayout()
        tb.setSpacing(6)
        tb.addWidget(tool_button("Создать значение", "check", "Primary", self._new_value))
        tb.addWidget(tool_button("Изменить", "settings", on_click=self._edit_value))
        tb.addWidget(tool_button("Переименовать", on_click=self._rename_value))
        tb.addWidget(tool_button("Удалить значение", "trash", "Danger", self._delete_value))
        tb.addStretch(1)
        tb.addWidget(tool_button("Создать раздел", "folder", on_click=self._new_key))
        tb.addWidget(tool_button("Удалить раздел", "trash", "Danger", self._delete_key))
        rv.addLayout(tb)
        self.vals = QTreeWidget()
        self.vals.setItemDelegate(RowDelegate(8, self.vals))
        self.vals.setHeaderLabels(["Имя", "Тип", "Данные"])
        self.vals.setRootIsDecorated(False)
        self.vals.setAlternatingRowColors(True)
        self.vals.setUniformRowHeights(True)
        self.vals.setSelectionMode(QAbstractItemView.SingleSelection)
        self.vals.setColumnWidth(0, 240)
        self.vals.setColumnWidth(1, 130)
        self.vals.header().setStretchLastSection(True)
        self.vals.itemDoubleClicked.connect(lambda *_: self._edit_value())
        self.vals.setContextMenuPolicy(Qt.CustomContextMenu)
        self.vals.customContextMenuRequested.connect(self._val_menu)
        rv.addWidget(self.vals, 1)
        self.val_info = QLabel("")
        self.val_info.setObjectName("Small")
        rv.addWidget(self.val_info)
        split.addWidget(right)
        split.setSizes([380, 820])

        vsplit = QSplitter(Qt.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(split)
        vsplit.addWidget(self._search_card())
        vsplit.setSizes([520, 330])
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 2)
        v.addWidget(vsplit, 1)

        self._drain = QTimer(self)
        self._drain.setInterval(150)
        self._drain.timeout.connect(self._drain_search)

    def _search_card(self):
        c = Card("Поиск", "в выбранном разделе и глубже")
        row = QHBoxLayout()
        self.s_text = SearchBox("Что искать: имя раздела, значения или данные…")
        self.s_text.returnPressed.connect(self._search)
        row.addWidget(self.s_text, 1)
        self.s_keys = QCheckBox("Разделы")
        self.s_names = QCheckBox("Имена")
        self.s_data = QCheckBox("Данные")
        for cb in (self.s_keys, self.s_names, self.s_data):
            cb.setChecked(True)
            row.addWidget(cb)
        self.btn_find = tool_button("Найти", "search", "Primary", self._search)
        self.btn_cancel = tool_button("Отмена", "stop", on_click=self._cancel_search)
        self.btn_cancel.setEnabled(False)
        row.addWidget(self.btn_find)
        row.addWidget(self.btn_cancel)
        c.v.addLayout(row)
        self.s_tree = QTreeWidget()
        self.s_tree.setItemDelegate(RowDelegate(6, self.s_tree))
        self.s_tree.setHeaderLabels(["Раздел", "Имя", "Данные"])
        self.s_tree.setRootIsDecorated(False)
        self.s_tree.setAlternatingRowColors(True)
        self.s_tree.setUniformRowHeights(True)
        self.s_tree.setColumnWidth(0, 520)
        self.s_tree.setColumnWidth(1, 180)
        self.s_tree.setMinimumHeight(120)
        self.s_tree.itemDoubleClicked.connect(self._open_found)
        c.v.addWidget(self.s_tree, 1)
        self.s_status = QLabel("Двойной клик по результату — перейти к разделу.")
        self.s_status.setObjectName("Small")
        c.v.addWidget(self.s_status)
        return c

    # ------------------------------------------------------------ дерево
    def on_show(self):
        if self._started:
            return
        self._started = True
        if regtools.winreg is None:
            self.val_info.setText("Реестр доступен только в Windows")
            return
        for long, short in regtools.ROOTS:
            it = QTreeWidgetItem([long])
            it.setData(0, PATH_ROLE, short)
            it.setIcon(0, icon("hdd", PURPLE, 15))
            self._add_dummy(it)
            self.keys.addTopLevelItem(it)
        last = config.load().get("registry_last") or r"HKCU\Software"
        self.goto(last, quiet=True)

    def _add_dummy(self, it):
        d = QTreeWidgetItem(["загрузка…"])
        d.setData(0, DUMMY_ROLE, True)
        d.setForeground(0, QBrush(QColor(MUTED)))
        it.addChild(d)

    @staticmethod
    def _long(path):
        if not path:
            return ""
        h, _, rest = path.partition("\\")
        return regtools.LONG.get(h, h) + ("\\" + rest if rest else "")

    def _fill_children(self, it, kids):
        it.takeChildren()
        base = it.data(0, PATH_ROLE)
        items = []
        for name, hc in kids:
            c = QTreeWidgetItem([name])
            c.setData(0, PATH_ROLE, base + "\\" + name)
            c.setIcon(0, icon("folder", MUTED, 15))
            if hc:
                self._add_dummy(c)
            items.append(c)
        it.addChildren(items)
        it.setData(0, LOADED_ROLE, True)

    def _expand(self, it):
        if it.data(0, LOADED_ROLE) or it.childCount() != 1 or not it.child(0).data(0, DUMMY_ROLE):
            return
        it.setData(0, LOADED_ROLE, True)
        path = it.data(0, PATH_ROLE)

        def got(kids):
            if isinstance(kids, Exception):
                it.takeChildren()
                it.setData(0, LOADED_ROLE, False)
                self.state["toast"](f"{self._long(path)}: {_err(kids)}", "warn")
                return
            self._fill_children(it, kids)
        run_async(lambda: _children(path), got)

    def _reload_children(self, it):
        path = it.data(0, PATH_ROLE)

        def got(kids):
            if not isinstance(kids, Exception):
                self._fill_children(it, kids)
                it.setExpanded(True)
        run_async(lambda: _children(path), got)

    def _find_child(self, it, name):
        for i in range(it.childCount()):
            c = it.child(i)
            if not c.data(0, DUMMY_ROLE) and c.text(0).lower() == name.lower():
                return c
        return None

    def goto(self, path, quiet=False):
        path = (path or "").strip()
        if not path or regtools.winreg is None:
            return
        try:
            regtools.split(path)
        except ValueError as e:
            self.state["toast"](str(e), "warn")
            return

        def got(res):
            if isinstance(res, Exception):
                if not quiet:
                    self.state["toast"](_err(res), "warn")
                return
            final, levels = res
            h = final.split("\\", 1)[0]
            it = next((self.keys.topLevelItem(i) for i in range(self.keys.topLevelItemCount())
                       if self.keys.topLevelItem(i).data(0, PATH_ROLE) == h), None)
            if it is None:
                return
            for lvl_path, kids in levels:
                if not it.data(0, LOADED_ROLE):
                    self._fill_children(it, kids)
                it.setExpanded(True)
                nxt = final[len(lvl_path) + 1:].split("\\", 1)[0]
                c = self._find_child(it, nxt)
                if c is None:                         # дерево устарело — перечитываем уровень
                    self._fill_children(it, kids)
                    c = self._find_child(it, nxt)
                    if c is None:
                        return
                it = c
            self.keys.setCurrentItem(it)
            self.keys.scrollToItem(it, QAbstractItemView.PositionAtCenter)
        run_async(lambda: _chain(path), got)

    # ------------------------------------------------------------ значения
    def _select(self, it):
        if it is None or it.data(0, DUMMY_ROLE):
            return
        self._cur = it.data(0, PATH_ROLE)
        self.addr.setText(self._long(self._cur))
        self.reload_values()
        cur = self._cur
        config.update(lambda c: c.__setitem__("registry_last", cur))

    def reload_values(self, select_name=None):
        path = self._cur
        if not path:
            return
        self._val_token += 1
        tok = self._val_token
        self.val_info.setText("Загрузка…")

        def got(rows):
            if tok != self._val_token:
                return                                # пользователь уже выбрал другой раздел
            self.vals.clear()
            if isinstance(rows, Exception):
                self.val_info.setText(_err(rows))
                return
            has_default = any(n == "" for n, _d, _t in rows)
            if not has_default:
                it = QTreeWidgetItem(["(По умолчанию)", "REG_SZ", "(значение не задано)"])
                it.setData(0, VALUE_ROLE, ("", None, None))
                for c in range(3):
                    it.setForeground(c, QBrush(QColor(MUTED)))
                self.vals.addTopLevelItem(it)
            pick = None
            for name, data, t in rows:
                it = QTreeWidgetItem([name or "(По умолчанию)", _type_name(t), regtools.fmt(data, t)])
                it.setData(0, VALUE_ROLE, (name, data, t))
                it.setIcon(0, icon("list" if t in (regtools.TYPE_BY_NAME.get("REG_SZ"),
                                                   regtools.TYPE_BY_NAME.get("REG_EXPAND_SZ"),
                                                   regtools.TYPE_BY_NAME.get("REG_MULTI_SZ"))
                                   else "chip", GREEN if t in regtools.TYPES else MUTED, 14))
                it.setToolTip(2, regtools.fmt(data, t)[:1000])
                self.vals.addTopLevelItem(it)
                if select_name is not None and name == select_name:
                    pick = it
            if pick:
                self.vals.setCurrentItem(pick)
            self.val_info.setText(f"{self._long(path)}  ·  значений: {len(rows)}")
        run_async(lambda: regtools.values(path), got)

    def _cur_value(self):
        it = self.vals.currentItem()
        if not it:
            self.state["toast"]("Выберите значение в списке", "warn")
            return None
        return it.data(0, VALUE_ROLE)

    def _write(self, fn, ok_text, after=None):
        """Изменение в фоне. set_value / delete_value / rename_value / delete_key сами
        вызывают regtools.backup(path) до записи; для create_key копию делаем явно."""
        def done(r):
            if isinstance(r, Exception):
                self.state["toast"](_err(r), "error")
            else:
                self.state["toast"](ok_text)
            if after:
                after(r)
        run_async(fn, done)

    def _need_key(self):
        if not self._cur:
            self.state["toast"]("Сначала выберите раздел", "warn")
            return False
        return True

    def _new_value(self):
        if not self._need_key():
            return
        d = ValueDialog(self)
        if d.exec() != QDialog.Accepted or not d.result_value:
            return
        name, t, data = d.result_value
        path = self._cur
        exists = name != "" and any(
            (self.vals.topLevelItem(i).data(0, VALUE_ROLE) or ("",))[0] == name
            for i in range(self.vals.topLevelItemCount()))
        if exists and QMessageBox.question(self, "Значение", f"Значение «{name}» уже есть. "
                                                             f"Заменить?") != QMessageBox.Yes:
            return
        self._write(lambda: regtools.set_value(path, name, t, data),
                    f"Создано значение «{name or '(По умолчанию)'}»",
                    lambda _r: self.reload_values(name))

    def _edit_value(self):
        v = self._cur_value()
        if v is None:
            return
        name, data, t = v
        tname = regtools.TYPES.get(t, "REG_SZ") if t is not None else "REG_SZ"
        if tname not in EDIT_TYPES:
            self.state["toast"](f"Тип {tname} нельзя изменить в этом редакторе", "warn")
            return
        d = ValueDialog(self, name, tname, data, edit=True)
        if d.exec() != QDialog.Accepted or not d.result_value:
            return
        _n, nt, nd = d.result_value
        path = self._cur
        self._write(lambda: regtools.set_value(path, name, nt, nd),
                    f"Значение «{name or '(По умолчанию)'}» изменено",
                    lambda _r: self.reload_values(name))

    def _rename_value(self):
        v = self._cur_value()
        if v is None:
            return
        old = v[0]
        if old == "" or v[2] is None:
            self.state["toast"]("Значение «(По умолчанию)» переименовать нельзя", "warn")
            return
        new, ok = QInputDialog.getText(self, "Переименовать", "Новое имя:", text=old)
        new = (new or "").strip()
        if not ok or not new or new == old:
            return
        path = self._cur
        self._write(lambda: regtools.rename_value(path, old, new),
                    f"«{old}» → «{new}»", lambda _r: self.reload_values(new))

    def _delete_value(self):
        v = self._cur_value()
        if v is None:
            return
        name = v[0]
        if v[2] is None:
            return
        if QMessageBox.question(self, "Удалить значение",
                                f"Удалить значение «{name or '(По умолчанию)'}»?\n\n"
                                f"Резервная копия раздела будет сохранена.") != QMessageBox.Yes:
            return
        path = self._cur
        self._write(lambda: regtools.delete_value(path, name),
                    f"Значение «{name or '(По умолчанию)'}» удалено",
                    lambda _r: self.reload_values())

    def _val_menu(self, pos):
        m = QMenu(self)
        m.addAction(icon("check", MUTED), "Создать значение", self._new_value)
        if self.vals.itemAt(pos):
            m.addAction(icon("settings", MUTED), "Изменить", self._edit_value)
            m.addAction("Переименовать", self._rename_value)
            m.addAction(icon("copy", MUTED), "Копировать данные",
                        lambda: self.state["clipboard"](self.vals.currentItem().text(2)))
            m.addSeparator()
            m.addAction(icon("trash", RED), "Удалить", self._delete_value)
        m.exec(self.vals.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ разделы
    def _new_key(self):
        if not self._need_key():
            return
        name, ok = QInputDialog.getText(self, "Новый раздел", f"Имя раздела внутри\n{self._long(self._cur)}:")
        name = (name or "").strip().strip("\\")
        if not ok or not name:
            return
        path = self._cur
        it = self.keys.currentItem()

        def after(r):
            if not isinstance(r, Exception) and it is not None:
                self._reload_children(it)
                QTimer.singleShot(400, lambda: self.goto(r[1], quiet=True))
        # create_key сам резервную копию не делает — сохраняем родительский раздел
        self._write(lambda: (regtools.backup(path), regtools.create_key(path, name)),
                    f"Раздел «{name}» создан", after)

    def _delete_key(self):
        if not self._need_key():
            return
        path = self._cur
        if "\\" not in path:
            self.state["toast"]("Корневые разделы удалять нельзя", "warn")
            return
        if QMessageBox.warning(self, "Удалить раздел",
                               f"Удалить раздел со всеми подразделами и значениями?\n\n"
                               f"{self._long(path)}\n\nПеред удалением будет сохранена "
                               f"резервная копия .reg.",
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        it = self.keys.currentItem()

        def after(r):
            if isinstance(r, Exception) or it is None:
                return
            parent = it.parent()
            if parent is not None:
                parent.removeChild(it)
                self.keys.setCurrentItem(parent)
        self._write(lambda: regtools.delete_key(path),
                    f"Раздел удалён: {self._long(path)}", after)

    def _key_menu(self, pos):
        it = self.keys.itemAt(pos)
        if not it or it.data(0, DUMMY_ROLE):
            return
        self.keys.setCurrentItem(it)
        m = QMenu(self)
        m.addAction(icon("folder", MUTED), "Создать раздел", self._new_key)
        m.addAction(icon("check", MUTED), "Создать значение", self._new_value)
        m.addAction(icon("refresh", MUTED), "Обновить", lambda: self._reload_children(it))
        m.addAction(icon("copy", MUTED), "Копировать путь",
                    lambda: self.state["clipboard"](self._long(it.data(0, PATH_ROLE))))
        m.addAction(icon("download", MUTED), "Экспорт…", self._export)
        m.addAction(icon("search", MUTED), "Искать здесь…", lambda: self.s_text.setFocus())
        m.addSeparator()
        m.addAction(icon("trash", RED), "Удалить раздел", self._delete_key)
        m.exec(self.keys.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ .reg
    def _export(self):
        if not self._need_key():
            return
        path = self._cur
        safe = path.replace("\\", "_")[-60:]
        fn, _ = QFileDialog.getSaveFileName(self, "Экспорт раздела", f"{safe}.reg",
                                            "Файлы реестра (*.reg)")
        if not fn:
            return

        def done(r):
            if isinstance(r, Exception) or r[0] != 0:
                self.state["toast"](f"Экспорт не удался: {_err(r) if isinstance(r, Exception) else r[1][:160]}",
                                    "error")
            else:
                self.state["toast"](f"Раздел сохранён: {fn}")
        run_async(lambda: regtools.export(path, os.path.normpath(fn)), done)

    def _import(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Импорт .reg", "", "Файлы реестра (*.reg)")
        if not fn:
            return
        if QMessageBox.question(self, "Импорт", f"Внести в реестр данные из файла?\n\n{fn}\n\n"
                                               f"Существующие значения с теми же именами "
                                               f"будут перезаписаны.") != QMessageBox.Yes:
            return

        def done(r):
            if isinstance(r, Exception) or r[0] != 0:
                self.state["toast"](f"Импорт не удался: {_err(r) if isinstance(r, Exception) else r[1][:160]}",
                                    "error")
            else:
                self.state["toast"]("Файл импортирован в реестр")
                self.reload_values()
        run_async(lambda: regtools.import_file(os.path.normpath(fn)), done)

    def _open_backups(self):
        os.makedirs(regtools.BACKUP_DIR, exist_ok=True)
        if admin.IS_WINDOWS:
            os.startfile(regtools.BACKUP_DIR)          # noqa — только Windows
        else:
            self.state["toast"](regtools.BACKUP_DIR)

    # ------------------------------------------------------------ поиск
    def _search(self):
        text = self.s_text.text().strip()
        if not text:
            self.state["toast"]("Введите текст для поиска", "warn")
            return
        if not self._need_key():
            return
        self._cancel_search()
        root = self._cur
        names, vals, data = self.s_keys.isChecked(), self.s_names.isChecked(), self.s_data.isChecked()
        if not (names or vals or data):
            self.state["toast"]("Отметьте, где искать", "warn")
            return
        q, stop = queue.Queue(), threading.Event()
        self._search_q, self._search_stop, self._search_n = q, stop, 0
        self.s_tree.clear()
        self.btn_find.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.s_status.setText(f"Поиск «{text}» в {self._long(root)}…")
        self.s_status.setStyleSheet(f"color:{GREEN};")

        def work():
            n = 0
            for hit in regtools.search(root, text, stop=stop.is_set,
                                       names=names, vals=vals, data=data):
                q.put(hit)
                n += 1
            return n

        def done(n):
            if self._search_q is not q:
                return                                 # уже запущен новый поиск
            self._drain.stop()
            self._drain_search(10_000)
            self.btn_find.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.s_status.setStyleSheet("")
            if isinstance(n, Exception):
                self.s_status.setText(f"Ошибка поиска: {_err(n)}")
            elif stop.is_set():
                self.s_status.setText(f"Поиск остановлен. Найдено: {self._search_n}")
            else:
                more = " (показаны первые 300)" if n >= 300 else ""
                self.s_status.setText(f"Готово. Найдено: {n}{more}. Двойной клик — перейти.")
        self._drain.start()
        run_async(work, done)

    def _drain_search(self, limit=200):
        q = self._search_q
        if q is None:
            return
        items = []
        try:
            while len(items) < limit:
                path, name, data = q.get_nowait()
                it = QTreeWidgetItem([self._long(path),
                                      "" if name is None else (name or "(По умолчанию)"),
                                      "" if data is None else str(data)[:300]])
                it.setData(0, PATH_ROLE, path)
                it.setData(1, VALUE_ROLE, name)
                if name is None:
                    it.setForeground(0, QBrush(QColor(PURPLE)))
                items.append(it)
        except queue.Empty:
            pass
        if items:
            self.s_tree.addTopLevelItems(items)
            self._search_n += len(items)
            if self.btn_cancel.isEnabled():
                self.s_status.setText(f"Идёт поиск… найдено {self._search_n}")

    def _cancel_search(self):
        if self._search_stop is not None:
            self._search_stop.set()

    def _open_found(self, it, _col=0):
        path = it.data(0, PATH_ROLE)
        name = it.data(1, VALUE_ROLE)
        self.goto(path)
        if name is not None:
            QTimer.singleShot(500, lambda: self._pick_value(path, name))

    def _pick_value(self, path, name):
        if self._cur.lower() != path.lower():
            return
        for i in range(self.vals.topLevelItemCount()):
            it = self.vals.topLevelItem(i)
            if (it.data(0, VALUE_ROLE) or ("",))[0] == name:
                self.vals.setCurrentItem(it)
                self.vals.scrollToItem(it)
                return

    def shutdown(self):
        self._cancel_search()
