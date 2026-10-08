"""Главное окно Kryostat."""
import os
import sys
import time

import psutil
from PySide6.QtCore import QEvent, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QPushButton, QStackedWidget, QStatusBar,
                               QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget)
from core import (admin, autorun, config, limits, monitor, sysinfo, syslimit, tweaks,
                  usage, winupdate)
from core.i18n import tr
from . import theme
from .icons import icon
from .palette import CommandPalette
from .theme import OK, GREEN, MUTED, RED, TEXT2, mix
from .titlebar import EdgeGrips, TitleBar, setup_native_frame
from .widgets import GlitchLabel, Toast
from .workers import run_async, wait_all


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


def asset(name: str) -> str:
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", name)


def _refresh_ms():
    """Период опроса: 1–5 с. Старое значение 1500 мс → 1 с."""
    ms = int(config.load().get("refresh_ms", 1000) or 1000)
    return 1000 if ms < 1750 else min(5000, ms)


class Collector(QThread):
    """Сбор данных о процессах в отдельном потоке — UI не подвисает."""
    data = Signal(dict)

    def __init__(self):
        super().__init__()
        self.running = True
        self.interval = _refresh_ms()
        self.visible = True
        self.need_conns = True
        self.need_services = False
        self._wake = False

    def stop(self):
        self.running = False
        self.requestInterruption()

    def run(self):
        from core.sampler_proc import RemoteSampler
        remote = RemoteSampler()           # отдельный процесс: опрос psutil не держит GIL интерфейса
        services, conns, tick, svc_tick = [], {}, 0, -999
        self.msleep(500)    # первый замер CPU сразу после «прайминга» даёт мусор — даём накопить интервал
        while self.running and not self.isInterruptionRequested():
            t0 = time.monotonic()
            try:
                # дорогие запросы — только когда их результат кто-то видит:
                # сетевые соединения — для «Монитора»/«Процессов», раз в 2 цикла;
                # список служб — только на странице «Процессы и службы», раз в ~10 с
                want_conns = self.need_conns and tick % 2 == 0
                want_svc = self.need_services and (tick - svc_tick >= 6 or not services)
                d = remote.sample(want_conns, want_svc)
                if want_conns:
                    conns = d.get("conns") or {}
                elif not self.need_conns:
                    conns = {}
                if want_svc:
                    services = d.get("services") or []
                    svc_tick = tick
                self.data.emit({"rows": d["rows"], "services": services,
                                "system": d["system"], "conns": conns})
                tick += 1
            except Exception as e:
                config.log(f"collector: {e}")
            # окно скрыто (в трее) — опрашиваем реже: данные нужны только для лимитов и подсказки
            interval = self.interval if self.visible else max(self.interval, 5000)
            waited = int((time.monotonic() - t0) * 1000)    # период — от начала замера
            while waited < interval and self.running \
                    and not self.isInterruptionRequested() and not self._wake:
                self.msleep(100)
                waited += 100
            self._wake = False
        remote.close()

    def wake(self):
        """Сразу сделать замер (например, при открытии окна или переходе на вкладку)."""
        self._wake = True


NAV = [
    ("ОБЗОР", None, None),
    ("Монитор", "dashboard", "gauge"),
    ("Компьютер", "system", "monitor"),
    ("Процессы и службы", "processes", "list"),
    ("УПРАВЛЕНИЕ", None, None),
    ("Программы", "apps", "package"),
    ("Автозапуск", "startup", "power"),
    ("Лимиты ресурсов", "limits", "sliders"),
    ("Оптимизация Windows", "tweaks", "zap"),
    ("Обновления", "updates", "refresh"),
    ("Антивирус", "defender", "shield"),
    ("Реестр", "registry", "registry"),
    ("СИСТЕМА", None, None),
    ("Терминал", "terminal", "terminal"),
    ("Настройки", "settings", "settings"),
]

# страницы создаются лениво — при первом открытии (или в простое после запуска);
# модуль страницы тоже импортируется только тогда
PAGE_CLASSES = {
    "dashboard": ("page_dashboard", "DashboardPage"),
    "system": ("page_system", "SystemPage"),
    "processes": ("page_processes", "ProcessesPage"),
    "apps": ("page_apps", "AppsPage"),
    "startup": ("page_startup", "StartupPage"),
    "limits": ("page_limits", "LimitsPage"),
    "tweaks": ("page_tweaks", "TweaksPage"),
    "updates": ("page_updates", "UpdatesPage"),
    "defender": ("page_defender", "DefenderPage"),
    "registry": ("page_registry", "RegistryPage"),
    "terminal": ("page_terminal", "TerminalPage"),
    "settings": ("page_settings", "SettingsPage"),
}


class LazyPages:
    """Словарь страниц, которые создаются при первом обращении."""

    def __init__(self, win):
        self.win = win
        self._pages = {}

    def __getitem__(self, key):
        page = self._pages.get(key)
        if page is None:
            import importlib
            mod, cls = PAGE_CLASSES[key]
            t0 = time.perf_counter()
            klass = getattr(importlib.import_module(f"app.{mod}"), cls)
            page = klass(self.win.state)
            self._pages[key] = page
            self.win.stack.addWidget(page)
            config.log(f"страница {key}: {int((time.perf_counter() - t0) * 1000)} мс") \
                if os.environ.get("KRYOSTAT_PROFILE") else None
        return page

    def get(self, key):
        """Только если страница уже создана (не создаёт новую)."""
        return self._pages.get(key)

    def created(self, key):
        return key in self._pages

    def items(self):
        return list(self._pages.items())

    def __contains__(self, key):
        return key in PAGE_CLASSES


class MainWindow(QMainWindow):
    def __init__(self, boot_mode=False):
        super().__init__()
        self.setWindowTitle("Kryostat")
        self.setWindowIcon(QIcon(asset("kryostat.ico")))
        # своя рамка: системный заголовок убираем, рамка/тень/Snap остаются (см. titlebar.py)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint
                            | Qt.WindowMinMaxButtonsHint | Qt.WindowSystemMenuHint)
        self.titlebar = TitleBar(self, asset("k_mark128.png"))
        self.setMenuWidget(self.titlebar)
        self.resize(1460, 900)
        self.setMinimumSize(1060, 680)
        self._quitting = False
        self._last = None
        self._cur_key = None
        self.release_instance = None      # заполняются из main.py (единственный экземпляр)
        self.reacquire_instance = None
        self._boot_mode = boot_mode
        self._boot_sweeps = []
        self._first_data = True
        self._sys_busy = False
        self._tick = 0
        self._boot_time = psutil.boot_time()
        self.usage = usage.Tracker()
        self._syslimit_on = bool(syslimit.settings().get("enabled"))
        self._compact = bool(config.load().get("sidebar_compact", False))

        self.state = {
            "toast": self.toast,
            "open_limits": self.open_limits,
            "elevate": self.elevate,
            "clipboard": self.copy,
            "usage": self.usage,
            "syslimit_changed": self._syslimit_changed,
            "set_refresh": self._set_refresh,
            "apply_theme": self.apply_theme,
            "restart": self.restart_app,
            "go": self.go,
        }

        root = QWidget(objectName="Root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._sidebar())

        right = QWidget(objectName="PageArea")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self.stack = QStackedWidget()
        rv.addWidget(self.stack, 1)
        h.addWidget(right, 1)
        self.setCentralWidget(root)

        self.pages = LazyPages(self)

        self.setStatusBar(QStatusBar())
        self.statusBar().setSizeGripEnabled(False)
        self.status_left = QLabel("Готово")
        self.status_left.setObjectName("StatusChip")
        self.statusBar().addWidget(self.status_left, 1)
        self.status_up = QLabel("")
        self.status_up.setObjectName("StatusChip")
        self.statusBar().addPermanentWidget(self.status_up)
        self.status_right = QLabel("")
        self.status_right.setObjectName("StatusChip")
        self.statusBar().addPermanentWidget(self.status_right)

        self.toast_box = Toast(self)
        self.palette_box = CommandPalette(self)
        self.go(config.load().get("last_page") or "dashboard"
                if config.load().get("remember_page", True) else "dashboard")
        self._build_tray()
        self._shortcuts()

        self.collector = Collector()
        self.collector.data.connect(self.on_data)
        self._update_needs()
        QTimer.singleShot(0, self.collector.start)      # сначала окно, потом сбор данных

        self._bg_busy = False
        self.bg = QTimer(self)
        self.bg.timeout.connect(self.background_tick)
        self.bg.start(60_000)

        # остальные страницы создаём в простое, по одной: переход по меню потом мгновенный,
        # а первый кадр окна не ждёт двенадцать страниц
        self._warm_queue = [k for _l, k, _i in NAV if k]
        self._warm = QTimer(self)
        self._warm.setInterval(60)
        self._warm.timeout.connect(self._warm_step)
        if not boot_mode:
            QTimer.singleShot(1200, self._warm.start)
        QTimer.singleShot(2000, lambda: self._apply_syslimit(None))
        self._update_uptime()
        if boot_mode:
            QTimer.singleShot(1200, self.run_boot_actions)
        self.grips = EdgeGrips(self)
        self.grips.update()
        self._native_frame = setup_native_frame(self, mix(theme.LINE2, theme.GREEN, 0.35))

    def _warm_step(self):
        if not self.isVisible():
            return                       # в трее страницы не нужны — создадим при открытии
        while self._warm_queue:
            key = self._warm_queue.pop(0)
            if not self.pages.created(key):
                try:
                    self.pages[key]
                    if key == "startup":
                        QTimer.singleShot(200, self.pages["startup"].reload)
                except Exception as e:
                    config.log(f"страница {key}: {e!r}")
                return
        self._warm.stop()

    def _shortcuts(self):
        QShortcut(QKeySequence("Ctrl+K"), self, self.open_palette)
        QShortcut(QKeySequence("Ctrl+P"), self, self.open_palette)
        QShortcut(QKeySequence("Ctrl+B"), self, self.toggle_compact)
        keys = [k for _l, k, _i in NAV if k]
        for i, k in enumerate(keys[:9]):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, lambda k=k: self.go(k))

    def open_palette(self):
        self.palette_box.open()

    # ----------------------------------------------------------- sidebar
    def _sidebar(self):
        side = QWidget(objectName="Sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        self.side = side
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 16, 12, 12)
        sv.setSpacing(2)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.setContentsMargins(4, 0, 0, 0)
        logo = self.brand_logo = QLabel()
        pm = QPixmap(asset("k_mark128.png"))
        if not pm.isNull():
            dpr = max(1.0, self.devicePixelRatioF())
            pm = pm.scaled(int(34 * dpr), int(34 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
        brand.addWidget(logo)
        self.brand_text = QWidget()
        tx = QVBoxLayout(self.brand_text)
        tx.setContentsMargins(0, 0, 0, 0)
        tx.setSpacing(0)
        n = GlitchLabel("KRYOSTAT", 17)
        s = QLabel(f"v{config.VERSION}")
        s.setObjectName("BrandSub")
        tx.addWidget(n)
        tx.addWidget(s)
        brand.addWidget(self.brand_text)
        brand.addStretch(1)
        self.btn_collapse = QToolButton(objectName="Collapse")
        self.btn_collapse.setIconSize(QSize(16, 16))
        self.btn_collapse.setCursor(Qt.PointingHandCursor)
        self.btn_collapse.clicked.connect(self.toggle_compact)
        brand.addWidget(self.btn_collapse)
        sv.addLayout(brand)
        sv.addSpacing(14)

        self.btn_search = QPushButton(objectName="NavSearch")
        self.btn_search.setIcon(icon("search", MUTED, 15))
        self.btn_search.setCursor(Qt.PointingHandCursor)
        self.btn_search.clicked.connect(self.open_palette)
        sv.addWidget(self.btn_search)
        sv.addSpacing(4)

        self.nav_buttons = {}
        self.nav_sections = []
        for label, key, ico in NAV:
            if key is None:
                lbl = QLabel(label)
                lbl.setObjectName("NavSection")
                sv.addWidget(lbl)
                self.nav_sections.append(lbl)
                continue
            b = QPushButton(label)
            b.setObjectName("Nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(icon(ico, MUTED, 18))
            b.setIconSize(QSize(18, 18))
            b.setProperty("label", b.text())
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            sv.addWidget(b)
            self.nav_buttons[key] = b

        sv.addStretch(1)
        self.admin_chip = QFrame(objectName="AdminChip")
        ac = QHBoxLayout(self.admin_chip)
        ac.setContentsMargins(10, 8, 8, 8)
        ac.setSpacing(8)
        self.badge_dot = QLabel("●")
        ac.addWidget(self.badge_dot)
        self.badge = QLabel("")
        self.badge.setObjectName("Small")
        self.badge.setWordWrap(True)
        ac.addWidget(self.badge, 1)
        self.btn_elevate = QToolButton(objectName="Collapse")
        self.btn_elevate.setIcon(icon("shield", GREEN, 16))
        self.btn_elevate.setToolTip("Перезапустить от администратора")
        self.btn_elevate.setCursor(Qt.PointingHandCursor)
        self.btn_elevate.clicked.connect(self.elevate)
        ac.addWidget(self.btn_elevate)
        sv.addWidget(self.admin_chip)
        self._update_badge()
        self._apply_compact()
        return side

    def toggle_compact(self):
        self._compact = not self._compact
        self._apply_compact()
        val = self._compact
        config.update(lambda cfg: cfg.__setitem__("sidebar_compact", val))

    def _apply_compact(self):
        c = self._compact
        self.side.setFixedWidth(72 if c else 244)
        self.brand_text.setVisible(not c)
        self.brand_logo.setVisible(not c)
        for lbl in self.nav_sections:
            lbl.setVisible(not c)
        for key, b in self.nav_buttons.items():
            label = b.property("label") or ""
            b.setText("" if c else "  " + label)
            b.setToolTip(label if c else "")
            b.setStyleSheet("text-align:center;padding:10px 0;" if c else "")
        self.btn_search.setText("" if c else "  " + tr("Поиск и команды") + "   Ctrl+K")
        self.btn_search.setToolTip(tr("Поиск и команды") + " (Ctrl+K)" if c else "")
        self.btn_search.setStyleSheet("text-align:center;padding:8px 0;" if c else "")
        self.btn_collapse.setIcon(icon("chev_right" if c else "chev_left", MUTED, 16))
        self.btn_collapse.setToolTip(("Развернуть меню" if c else "Свернуть меню") + " (Ctrl+B)")
        self.badge.setVisible(not c)
        self.badge_dot.setVisible(not c)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(QIcon(asset("kryostat.ico")), self)
        m = QMenu()
        m.addAction(icon("gauge", MUTED), "Открыть Kryostat", self.show_normal)
        m.addSeparator()
        m.addAction(icon("sliders", MUTED), "Применить лимиты", self.tray_apply_limits)
        m.addAction(icon("clock", MUTED), "Продлить паузу обновлений", self.tray_extend)
        m.addAction(icon("zap", MUTED), "Завершить отключённые программы", self.tray_kill)
        m.addAction(icon("folder", MUTED), "Перезапустить Проводник", self.tray_explorer)
        m.addSeparator()
        m.addAction(icon("refresh", MUTED), "Перезагрузка", lambda: self.tray_power("restart"))
        m.addAction(icon("power", MUTED), "Выключение", lambda: self.tray_power("shutdown"))
        m.addSeparator()
        m.addAction(icon("x", MUTED), "Выход", self.quit_app)
        self.tray.setContextMenu(m)
        self.tray.setToolTip("Kryostat")
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

    def _tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_normal()

    def tray_apply_limits(self):
        run_async(limits.apply_all, lambda n: self.toast(
            f"Ошибка: {n}" if isinstance(n, Exception) else f"Лимиты применены к {n} процессам"))

    def tray_extend(self):
        if not admin.is_admin():
            self.toast("Нужны права администратора", "error")
            return
        run_async(winupdate.extend, lambda r: self.toast(
            f"Ошибка: {r}" if isinstance(r, Exception) else r[1][:140],
            "error" if isinstance(r, Exception) or not r[0] else "info"))

    def tray_power(self, key):
        name = {"restart": "Перезагрузить компьютер", "shutdown": "Выключить компьютер"}[key]
        box = QMessageBox(QMessageBox.Question, "Kryostat",
                          f"{name}?\n\nНесохранённые данные в открытых программах могут быть потеряны.",
                          QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setWindowIcon(self.windowIcon())
        if box.exec() != QMessageBox.Yes:
            return
        self.usage.save()
        run_async(lambda: sysinfo.power(key), lambda r: None if not isinstance(r, Exception) and r[0]
                  else self.toast(f"Не удалось: {r if isinstance(r, Exception) else r[1]}", "error"))

    def tray_explorer(self):
        run_async(tweaks.restart_explorer, lambda r: self.toast(
            f"Ошибка: {r}" if isinstance(r, Exception) else r[1],
            "error" if isinstance(r, Exception) or not r[0] else "info"))

    def tray_kill(self):
        run_async(autorun.kill_disabled_now, lambda r: self.toast(
            f"Ошибка: {r}" if isinstance(r, Exception) else f"Завершено: {r[0]}"))

    # --------------------------------------------------------------- nav
    def go(self, key):
        """Переключение мгновенное: сначала показываем страницу, а всё тяжёлое
        (перерисовка таблиц, запросы к реестру/schtasks) — следующим тиком и в фоне."""
        if key not in PAGE_CLASSES:
            key = "dashboard"
        if self._cur_key == key:
            return
        prev = self._cur_key
        self._cur_key = key
        icons = {n[1]: n[2] for n in NAV if n[1]}
        for k in (prev, key):
            b = self.nav_buttons.get(k)
            if b:
                b.setChecked(k == key)
                b.setIcon(icon(icons[k], GREEN if k == key else MUTED, 18))
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.titlebar.set_page(next((n[0] for n in NAV if n[1] == key), ""))
        self._update_needs()
        if hasattr(self, "collector") and (self.collector.need_services or self.collector.need_conns):
            self.collector.wake()
        QTimer.singleShot(0, lambda k=key: self._after_go(k))
        if prev is not None and config.load().get("last_page") != key:
            config.update(lambda cfg: cfg.__setitem__("last_page", key))

    def _after_go(self, key):
        if self._cur_key != key:
            return                                   # пользователь уже ушёл дальше
        page = self.pages[key]
        if self._last and key in ("dashboard", "processes"):
            self._refresh_page(key, self._last)
        if hasattr(page, "on_show"):
            page.on_show()
        elif key == "startup":
            page.sync()
            if not page.items:
                page.reload()
        elif key == "updates":
            page.reload()
        elif key == "settings":
            page.refresh_state()
        elif key == "terminal":
            page.focus_input()
        if key == "limits" and self._last:
            page.update_names(self._last["rows"], True)

    def _update_needs(self):
        c = getattr(self, "collector", None)
        if c is None:
            return
        cur = self._cur_key
        vis = self.isVisible() and not self.isMinimized()
        c.visible = vis
        c.need_conns = vis and cur in ("dashboard", "processes")
        c.need_services = vis and cur == "processes"

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange:
            self._update_needs()
            self.titlebar.sync_max()
            if hasattr(self, "grips"):
                self.grips.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._update_needs()
        if getattr(self, "collector", None):
            self.collector.wake()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._update_needs()

    def open_limits(self, name):
        self.pages["limits"].prefill(name)
        self.go("limits")

    def toast(self, msg, kind="info"):
        config.log(msg)
        msg = tr(msg)
        self.toast_box.show_message(msg, kind)
        self.status_left.setText(msg)

    def copy(self, text):
        QGuiApplication.clipboard().setText(text or "")
        self.toast("Скопировано в буфер обмена")

    def _update_badge(self):
        ok = admin.is_admin()
        self.badge.setText("Режим администратора" if ok
                           else "Без прав администратора")
        self.badge.setStyleSheet(f"color:{TEXT2 if ok else RED};font-size:11px;")
        self.badge_dot.setStyleSheet(f"color:{OK if ok else RED};font-size:10px;")
        self.btn_elevate.setVisible(not ok)
        self.admin_chip.setToolTip("Режим администратора" if ok else "Без прав администратора")
        if hasattr(self, "titlebar"):
            self.titlebar.set_badge("администратор" if ok else "")

    def elevate(self):
        if admin.is_admin():
            self.toast("Kryostat уже запущен от имени администратора")
            return
        # освобождаем «единственный экземпляр», иначе новый процесс решит, что приложение уже запущено
        if self.release_instance:
            self.release_instance()
        if admin.relaunch_as_admin(["--restarted"]):
            self.quit_app()
        else:
            if self.reacquire_instance:
                self.reacquire_instance()
            self.toast("Повышение прав отменено", "warn")

    # -------------------------------------------------------------- data
    def _refresh_page(self, key, d):
        if key == "dashboard":
            self.pages["dashboard"].refresh(d["system"], d["rows"], d["conns"])
        elif key == "processes":
            self.pages["processes"].refresh(d["rows"], d["services"], d["conns"])

    def on_data(self, d):
        s = d["system"]
        self._last = d
        try:
            self.usage.feed(d["rows"], self._first_data)
        except Exception as e:
            config.log(f"usage: {e!r}")
        self._first_data = False
        if self._syslimit_on:
            self._apply_syslimit(d["rows"])
        # страницы перерисовываем только если они на экране: в трее и на других вкладках
        # это пустая трата CPU (список процессов — сотни строк)
        visible = self.isVisible() and not self.isMinimized()
        if visible and self._cur_key in ("dashboard", "processes"):
            self._refresh_page(self._cur_key, d)
        self._tick += 1
        lim = self.pages.get("limits")
        if lim is not None:
            on_limits = visible and self._cur_key == "limits"
            if on_limits or self._tick % 5 == 0:     # в фоне — реже: нужен только список имён
                lim.update_names(d["rows"], on_limits)
        if not visible:
            self.tray.setToolTip(f'Kryostat · CPU {s["cpu"]:.0f}% · RAM {s["ram_percent"]:.0f}%')
            return
        self.status_right.setText(
            f'CPU {s["cpu"]:.0f} %     RAM {s["ram_percent"]:.0f} %     '
            f'↓ {monitor.human(s["net_down"])}/с   ↑ {monitor.human(s["net_up"])}/с     '
            f'{s["processes"]} процессов')
        self.tray.setToolTip(f'Kryostat · CPU {s["cpu"]:.0f}% · RAM {s["ram_percent"]:.0f}%')
        self._update_uptime()

    def _update_uptime(self):
        self.status_up.setText(f"аптайм {_dur(time.time() - self._boot_time)}")

    def _set_refresh(self, ms):
        if getattr(self, "collector", None):
            self.collector.interval = int(ms)
            self.collector.wake()

    def _syslimit_changed(self, on):
        self._syslimit_on = bool(on)

    def _apply_syslimit(self, rows):
        """Общий бюджет системы: новые процессы — в фоне, не чаще одного прохода за раз."""
        if self._sys_busy:
            return
        self._sys_busy = True
        rows = list(rows) if rows is not None else None

        def done(res):
            self._sys_busy = False
            if isinstance(res, Exception):
                config.log(f"syslimit.apply: {res!r}")
        run_async(lambda: syslimit.apply(rows), done)

    def background_tick(self):
        self.collector.interval = _refresh_ms()
        if self._bg_busy:
            return
        self._bg_busy = True
        # общий бюджет системы — через тот же «однопоточный» вызов, что и в on_data
        self._syslimit_on = bool(syslimit.settings().get("enabled"))
        if self._syslimit_on:
            self._apply_syslimit(None)

        def work():
            extended = winupdate.tick()
            limits.apply_all()
            return extended

        def done(res):
            self._bg_busy = False
            if isinstance(res, Exception):
                config.log(f"background_tick: {res!r}")
            elif res:
                self.toast("Пауза обновлений Windows продлена автоматически")
        run_async(work, done)

    def run_boot_actions(self):
        """Автозапуск: тяжёлые действия — в фоне. Отключённые программы глушим повторно
        (через 8, 25 и 60 с): часть из них стартует с задержкой, уже после нас."""
        def work():
            tweaks.apply_saved()
            limits.apply_all()
            winupdate.tick()
            return autorun.kill_disabled_now()

        def done(res):
            if not isinstance(res, Exception) and res[0]:
                self.tray.showMessage("Kryostat", f"Завершено программ автозапуска: {res[0]}",
                                      QSystemTrayIcon.MessageIcon.Information, 4000)
        run_async(work, done)
        for delay in (8_000, 25_000, 60_000):
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(lambda: run_async(autorun.kill_disabled_now))
            t.start(delay)
            self._boot_sweeps.append(t)

    # ------------------------------------------------------------- window
    def show_normal(self):
        self.showNormal()
        if self._last and self._cur_key in ("dashboard", "processes"):
            self._refresh_page(self._cur_key, self._last)
        if self._warm_queue and not self._warm.isActive():
            QTimer.singleShot(800, self._warm.start)
        self.raise_()
        self.activateWindow()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toast_box.reposition()
        if self.palette_box.isVisible():
            self.palette_box.reposition()
        if hasattr(self, "grips"):
            self.grips.update()

    def closeEvent(self, e):
        if self._quitting:
            e.accept()
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            # без трея свёрнутое окно нельзя было бы вернуть — просто выходим
            e.ignore()
            self.quit_app()
            return
        e.ignore()
        self.hide()
        self.tray.showMessage("Kryostat",
                              "Свёрнуто в трей и продолжает работать. "
                              "Выход — правый клик по значку.",
                              QSystemTrayIcon.MessageIcon.Information, 2500)

    # ------------------------------------------------------- тема / рестарт
    def apply_theme(self, base, accent):
        """Сменить тему на лету: стиль всего окна пересобирается сразу,
        мелкие элементы с цветом «внутри» (графики, значки) — после перезапуска."""
        p = theme.set_theme(base, accent)
        QApplication.instance().setStyleSheet(theme.qss(os.path.dirname(asset("kryostat.ico")), p))
        icons = {n[1]: n[2] for n in NAV if n[1]}
        for k, b in self.nav_buttons.items():
            b.setIcon(icon(icons[k], p["GREEN"] if k == self._cur_key else p["MUTED"], 18))
        config.update(lambda cfg: cfg.__setitem__("theme", {"base": base, "accent": accent}))
        setup_native_frame(self, mix(p["LINE2"], p["GREEN"], 0.35))

    def restart_app(self):
        """Перезапустить Kryostat (новый язык / тема применяются полностью)."""
        import subprocess
        if getattr(sys, "frozen", False):
            cmd = [sys.executable]
        else:
            cmd = [sys.executable, os.path.abspath(sys.argv[0])]
        cmd += ["--restarted", "--no-elevate"]
        if self.release_instance:
            self.release_instance()
        try:
            flags = 0x00000008 if os.name == "nt" else 0          # DETACHED_PROCESS
            subprocess.Popen(cmd, close_fds=True, creationflags=flags)
        except Exception as e:
            if self.reacquire_instance:
                self.reacquire_instance()
            self.toast(f"Не удалось перезапустить: {e}", "error")
            return
        self.quit_app()

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        self.bg.stop()
        for t in self._boot_sweeps:
            t.stop()
        try:
            self.collector.stop()
            self.collector.wait(1500)
        except Exception:
            pass
        try:
            if self.pages.get("registry") is not None:
                self.pages["registry"].shutdown()
        except Exception:
            pass
        wait_all(2000)
        try:
            self.usage.save()
        except Exception as e:
            config.log(f"usage.save: {e!r}")
        try:
            syslimit.release()
        except Exception as e:
            config.log(f"syslimit.release: {e!r}")
        try:
            if self.pages.get("terminal") is not None:
                self.pages["terminal"].shutdown()
        except Exception:
            pass
        self.tray.hide()
        QApplication.quit()
