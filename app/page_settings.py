"""Настройки Kryostat."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QFormLayout, QHBoxLayout,
                               QLabel, QComboBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)
from core import admin, autorun, config, i18n
from . import theme
from .theme import OK, GREEN, MUTED, RED
from .widgets import Card, muted, pill, title, tool_button
from .workers import run_async


class SettingsPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._loading = True
        cfg = config.load()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(title("Настройки", "Запуск, оформление, язык и поведение Kryostat"))
        head.addStretch(1)
        self.pill_admin = pill("—", MUTED)
        head.addWidget(self.pill_admin)
        v.addLayout(head)

        v.addLayout(self._look_row(cfg))

        row = QHBoxLayout()
        row.setSpacing(14)

        c1 = Card("Запуск вместе с Windows")
        f1 = QVBoxLayout()
        f1.setSpacing(9)
        self.auto = QCheckBox("Запускать Kryostat при входе в Windows")
        self.auto.setChecked(bool(cfg.get("autostart_self")))   # уточняется в фоне
        self.auto.toggled.connect(self._toggle_auto)
        self.minimized = QCheckBox("Запускать свёрнутым в трей")
        self.minimized.setChecked(cfg.get("start_minimized", True))
        self.minimized.toggled.connect(self._on_minimized)
        self.kill = QCheckBox("Сразу завершать программы, отключённые в автозапуске")
        self.kill.setChecked(cfg.get("kill_on_boot", True))
        self.kill.toggled.connect(self._save)
        for w in (self.auto, self.minimized, self.kill):
            f1.addWidget(w)
        c1.v.addLayout(f1)
        c1.v.addWidget(muted("Для старта с правами администратора без запроса UAC "
                             "создаётся задача планировщика KryostatBoot."))
        c1.v.addStretch(1)
        row.addWidget(c1, 1)

        c2 = Card("Поведение")
        f2 = QFormLayout()
        f2.setSpacing(11)
        f2.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.refresh = QComboBox()
        for ms, label in ((1000, "1 секунда"), (2000, "2 секунды"), (3000, "3 секунды"),
                          (5000, "5 секунд")):
            self.refresh.addItem(label, ms)
        cur = cfg.get("refresh_ms", 1000)
        i = self.refresh.findData(cur)
        self.refresh.setCurrentIndex(i if i >= 0 else (0 if cur < 1750 else 1))
        self.refresh.setFixedWidth(150)
        self.refresh.currentIndexChanged.connect(self._save)
        f2.addRow("Период обновления", self.refresh)
        c2.v.addLayout(f2)
        self.confirm = QCheckBox("Спрашивать подтверждение перед завершением процесса")
        self.confirm.setChecked(cfg.get("confirm_kill", True))
        self.confirm.toggled.connect(self._save)
        self.scan_tasks = QCheckBox("Сканировать задачи планировщика (медленнее)")
        self.scan_tasks.setChecked(cfg.get("scan_tasks", True))
        self.scan_tasks.toggled.connect(self._save)
        c2.v.addWidget(self.confirm)
        c2.v.addWidget(self.scan_tasks)
        c2.v.addStretch(1)
        row.addWidget(c2, 1)
        v.addLayout(row)

        c3 = Card("Права и данные")
        self.admin_lbl = QLabel()
        self.admin_lbl.setWordWrap(True)
        c3.v.addWidget(self.admin_lbl)
        b = QHBoxLayout()
        b.addWidget(tool_button("Перезапустить от администратора", "shield", "Primary",
                                self.state["elevate"]))
        b.addWidget(tool_button("Папка настроек", "folder",
                                on_click=lambda: admin.run(f'explorer "{config.BASE_DIR}"')))
        b.addWidget(tool_button("Журнал действий", "list",
                                on_click=lambda: admin.run(f'notepad "{config.LOG_PATH}"')))
        b.addStretch(1)
        c3.v.addLayout(b)
        c3.v.addWidget(muted(f"Настройки и журнал: {config.BASE_DIR}"))
        v.addWidget(c3)

        c4 = Card("О программе")
        c4.v.addWidget(muted(
            f"Kryostat {config.VERSION} — контроль процессов, автозапуска, служб и ресурсов Windows.\n"
            "Все изменения обратимы: автозапуск восстанавливается галочкой, твики — "
            "кнопкой отката, лимиты — удалением правила."))
        c4.v.addStretch(1)
        v.addWidget(c4)
        v.addStretch(1)
        self._loading = False
        self.refresh_state()

    # ------------------------------------------------------ оформление и язык
    def _look_row(self, cfg):
        row = QHBoxLayout()
        row.setSpacing(14)
        t = cfg.get("theme") or {}
        self._base = t.get("base") or "midnight"
        self._accent = t.get("accent") or "violet"

        c = Card("Оформление")
        f = QFormLayout()
        f.setSpacing(12)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.base = QComboBox()
        for key, name in theme.BASE_NAMES.items():
            self.base.addItem(name, key)
        i = self.base.findData(self._base)
        self.base.setCurrentIndex(i if i >= 0 else 0)
        self.base.setFixedWidth(220)
        self.base.currentIndexChanged.connect(lambda _i: self._set_theme(base=self.base.currentData()))
        f.addRow("Фон", self.base)

        sw = QHBoxLayout()
        sw.setSpacing(6)
        self.sw_group = QButtonGroup(self)
        self.sw_group.setExclusive(True)
        self.swatches = {}
        for key, hexc in theme.ACCENTS.items():
            b = QPushButton()
            b.setObjectName("Swatch")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(theme.ACCENT_NAMES.get(key, key))
            b.setStyleSheet(f"QPushButton#Swatch {{ background:{hexc}; }}")
            b.clicked.connect(lambda _=False, k=key: self._set_theme(accent=k))
            self.sw_group.addButton(b)
            self.swatches[key] = b
            sw.addWidget(b)
        self.btn_custom = QPushButton("Свой…")
        self.btn_custom.setObjectName("Chip")
        self.btn_custom.setCheckable(True)
        self.btn_custom.setCursor(Qt.PointingHandCursor)
        self.btn_custom.clicked.connect(self._pick_color)
        self.sw_group.addButton(self.btn_custom)
        sw.addWidget(self.btn_custom)
        sw.addStretch(1)
        f.addRow("Акцент", sw)
        c.v.addLayout(f)
        self.theme_hint = muted("Стиль окна меняется сразу. Цвета графиков и значков "
                                "полностью обновятся после перезапуска.")
        c.v.addWidget(self.theme_hint)
        rr = QHBoxLayout()
        self.btn_restart_theme = tool_button("Перезапустить сейчас", "refresh",
                                             on_click=lambda: self.state["restart"]())
        self.btn_restart_theme.setVisible(False)
        rr.addWidget(self.btn_restart_theme)
        rr.addStretch(1)
        c.v.addLayout(rr)
        c.v.addStretch(1)
        self._sync_swatches()
        row.addWidget(c, 3)

        c2 = Card("Язык интерфейса")
        f2 = QFormLayout()
        f2.setSpacing(12)
        f2.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lang = QComboBox()
        langs = i18n.available()
        sys_code = i18n.system_language()
        sys_name = langs.get(sys_code) or sys_code
        self.lang.addItem(f"Как в системе ({sys_name})", "auto")
        for code, name in langs.items():
            self.lang.addItem(name, code)
        i = self.lang.findData(cfg.get("language", "auto"))
        self.lang.setCurrentIndex(i if i >= 0 else 0)
        self.lang.setFixedWidth(240)
        self.lang.currentIndexChanged.connect(self._set_lang)
        f2.addRow("Язык", self.lang)
        c2.v.addLayout(f2)
        c2.v.addWidget(muted("По умолчанию Kryostat говорит на языке Windows. Если перевода "
                             "на язык системы нет — включается английский."))
        lr = QHBoxLayout()
        self.btn_restart_lang = tool_button("Перезапустить и применить", "refresh", "Primary",
                                            lambda: self.state["restart"]())
        self.btn_restart_lang.setVisible(False)
        lr.addWidget(self.btn_restart_lang)
        lr.addStretch(1)
        c2.v.addLayout(lr)
        c2.v.addStretch(1)
        row.addWidget(c2, 2)
        return row

    def _sync_swatches(self):
        b = self.swatches.get(self._accent)
        if b:
            b.setChecked(True)
            self.btn_custom.setText("Свой…")
        else:
            self.btn_custom.setChecked(True)
            self.btn_custom.setText(str(self._accent).upper())

    def _pick_color(self):
        cur = QColor(theme.resolve_accent(self._accent))
        c = QColorDialog.getColor(cur, self, "Цвет акцента")
        if c.isValid():
            self._set_theme(accent=c.name().upper())
        else:
            self._sync_swatches()

    def _set_theme(self, base=None, accent=None):
        if self._loading:
            return
        if base:
            self._base = base
        if accent:
            self._accent = accent
        self._sync_swatches()
        if "apply_theme" in self.state:
            self.state["apply_theme"](self._base, self._accent)
        self.btn_restart_theme.setVisible(True)

    def _set_lang(self, _i):
        if self._loading:
            return
        code = self.lang.currentData()
        config.update(lambda cfg: cfg.__setitem__("language", code))
        will = i18n.resolve(code)
        self.btn_restart_lang.setVisible(will != i18n.current())

    def refresh_state(self):
        is_admin = admin.is_admin()
        self.pill_admin.setText("администратор" if is_admin else "обычные права")
        self.admin_lbl.setText(
            "Запущено с правами администратора — доступны все функции."
            if is_admin else
            "Запущено без прав администратора. Управление службами, политиками "
            "обновлений и лимитами для системных процессов недоступно.")
        self.admin_lbl.setStyleSheet(f"color:{OK if is_admin else RED};")
        # значения могли измениться на другой странице (например, «завершать при старте»)
        cfg = config.load()
        self._loading = True
        self.kill.setChecked(cfg.get("kill_on_boot", True))
        self.minimized.setChecked(cfg.get("start_minimized", True))
        self.confirm.setChecked(cfg.get("confirm_kill", True))
        self.scan_tasks.setChecked(cfg.get("scan_tasks", True))
        self._loading = False

        def got(on):          # schtasks /query — в фоне, иначе страница открывается с подлагом
            if isinstance(on, Exception):
                return
            self._loading = True
            self.auto.setChecked(bool(on))
            self._loading = False
        run_async(autorun.enabled, got)

    def _toggle_auto(self, on):
        if self._loading:
            return
        minimized = self.minimized.isChecked()

        def done(res):
            ok, msg = (False, str(res)) if isinstance(res, Exception) else res
            if ok:
                self.state["toast"]("Автозапуск Kryostat " + ("включён" if on else "выключен"))
            else:
                self._loading = True                  # откатываем галочку, раз не получилось
                self.auto.setChecked(not on)
                self._loading = False
                self.state["toast"](f"Ошибка: {msg[:140]}", "error")
        run_async(lambda: autorun.set_enabled(on, minimized), done)

    def _on_minimized(self, *_):
        self._save()
        if not self._loading and self.auto.isChecked():
            # параметры запуска записаны в задаче/реестре — обновляем их
            run_async(lambda: autorun.set_enabled(True, self.minimized.isChecked()))

    def _save(self, *_):
        if self._loading:
            return
        vals = {"start_minimized": self.minimized.isChecked(),
                "kill_on_boot": self.kill.isChecked(),
                "refresh_ms": int(self.refresh.currentData()),
                "confirm_kill": self.confirm.isChecked(),
                "scan_tasks": self.scan_tasks.isChecked()}
        config.update(lambda cfg: cfg.update(vals))
        if "set_refresh" in self.state:
            self.state["set_refresh"](vals["refresh_ms"])
