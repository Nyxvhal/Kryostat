"""Палитра команд (Ctrl+K): быстрый переход к любой странице и частым действиям."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget)
from PySide6.QtGui import QColor
from core.i18n import tr
from .icons import icon
from .theme import GREEN, MUTED


class CommandPalette(QWidget):
    """Полупрозрачная подложка на всё окно + карточка с поиском по командам."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setObjectName("PaletteVeil")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#PaletteVeil { background: rgba(0,0,0,0.45); }")
        self.hide()
        self.card = QFrame(self)
        self.card.setObjectName("Palette")
        sh = QGraphicsDropShadowEffect(self.card)
        sh.setBlurRadius(48)
        sh.setOffset(0, 12)
        sh.setColor(QColor(0, 0, 0, 160))
        self.card.setGraphicsEffect(sh)
        v = QVBoxLayout(self.card)
        v.setContentsMargins(12, 12, 12, 10)
        v.setSpacing(8)
        self.input = QLineEdit()
        self.input.setObjectName("PaletteInput")
        self.input.setPlaceholderText("Куда перейти или что сделать?")
        self.input.addAction(icon("search", MUTED, 18), QLineEdit.LeadingPosition)
        self.input.textChanged.connect(self._filter)
        self.input.installEventFilter(self)
        v.addWidget(self.input)
        self.list = QListWidget()
        self.list.setIconSize(QSize(18, 18))
        self.list.setUniformItemSizes(True)
        self.list.setStyleSheet("QListWidget { border: none; background: transparent; }"
                                "QListWidget::item { padding: 8px 6px; border-radius: 8px; }")
        self.list.itemActivated.connect(self._run)
        self.list.itemClicked.connect(self._run)
        v.addWidget(self.list, 1)
        foot = QHBoxLayout()
        for k, t in (("↑↓", "выбор"), ("Enter", "выполнить"), ("Esc", "закрыть")):
            kb = QLabel(k)
            kb.setObjectName("Kbd")
            foot.addWidget(kb)
            lb = QLabel(t)
            lb.setObjectName("Small")
            foot.addWidget(lb)
            foot.addSpacing(8)
        foot.addStretch(1)
        v.addLayout(foot)
        self._items = []

    # ------------------------------------------------------------ команды
    def _commands(self):
        from .main_window import NAV
        w = self.win
        out = []
        section = ""
        for label, key, ico in NAV:
            if key is None:
                section = label
                continue
            out.append((ico, label, tr(section).capitalize() if section else "", lambda k=key: w.go(k)))
        acts = [
            ("sliders", "Применить лимиты", w.tray_apply_limits),
            ("zap", "Завершить отключённые программы", w.tray_kill),
            ("folder", "Перезапустить Проводник", w.tray_explorer),
            ("clock", "Продлить паузу обновлений", w.tray_extend),
            ("palette", "Сменить тему оформления", lambda: w.go("settings")),
            ("globe", "Сменить язык интерфейса", lambda: w.go("settings")),
            ("chev_left", "Свернуть / развернуть меню", w.toggle_compact),
            ("shield", "Перезапустить от администратора", w.elevate),
            ("refresh", "Перезапустить Kryostat", w.restart_app),
            ("x", "Выход", w.quit_app),
        ]
        for ico, label, fn in acts:
            out.append((ico, label, tr("Действие"), fn))
        return out

    def open(self):
        self._items = self._commands()
        self.input.blockSignals(True)
        self.input.clear()
        self.input.blockSignals(False)
        self._filter("")
        self.reposition()
        self.show()
        self.raise_()
        self.input.setFocus()

    def reposition(self):
        self.setGeometry(self.win.rect())
        w = min(620, self.width() - 80)
        h = min(470, self.height() - 140)
        self.card.setGeometry((self.width() - w) // 2, 90, w, h)

    def _filter(self, text):
        q = text.strip().lower()
        self.list.clear()
        for ico, label, group, fn in self._items:
            shown = tr(label)
            hay = f"{label} {shown} {group}".lower()
            if q and not all(part in hay for part in q.split()):
                continue
            it = QListWidgetItem(icon(ico, GREEN, 18), f"{shown}")
            it.setData(Qt.UserRole, fn)
            it.setToolTip(group)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run(self, item):
        fn = item.data(Qt.UserRole) if item else None
        self.hide()
        if callable(fn):
            fn()

    # ------------------------------------------------------------ события
    def eventFilter(self, obj, e):
        if obj is self.input and e.type() == e.Type.KeyPress:
            k = e.key()
            if k in (Qt.Key_Down, Qt.Key_Up):
                r = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                if self.list.count():
                    self.list.setCurrentRow(max(0, min(self.list.count() - 1, r)))
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self._run(self.list.currentItem())
                return True
            if k == Qt.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, e)

    def mousePressEvent(self, e):
        if not self.card.geometry().contains(e.position().toPoint()):
            self.hide()
        super().mousePressEvent(e)
