"""Собственная рамка окна Kryostat: фиолетовый заголовок с логотипом и кнопками.

Перетаскивание и изменение размера отдаются системе (startSystemMove/startSystemResize),
поэтому работает Aero Snap, а лишнего кода на каждое событие мыши нет."""
import ctypes
import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from .icons import icon
from .theme import TEXT2

IS_WINDOWS = sys.platform == "win32"
TITLE_H = 40


class _WinButton(QToolButton):
    """Кнопка окна: иконка светлеет при наведении."""

    def __init__(self, glyph, hover_color, parent=None):
        super().__init__(parent)
        self._hover_color = hover_color
        self.set_glyph(glyph)

    def set_glyph(self, glyph):
        self._glyph = glyph
        self.setIcon(icon(glyph, self._hover_color if self.underMouse() else TEXT2, 16, 1.4))

    def enterEvent(self, e):
        self.setIcon(icon(self._glyph, self._hover_color, 16, 1.4))
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setIcon(icon(self._glyph, TEXT2, 16, 1.4))
        super().leaveEvent(e)


class TitleBar(QWidget):
    def __init__(self, window, logo_path, parent=None):
        super().__init__(parent)
        self.win = window
        self.setObjectName("TitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(TITLE_H)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 0, 0)
        h.setSpacing(9)

        # заголовок без логотипа и текста — только кнопки окна (логотип есть в боковой панели)
        h.addStretch(1)

        self.b_min = self._btn("win_min", "Свернуть", "WinBtn", self.win.showMinimized)
        self.b_max = self._btn("win_max", "Развернуть", "WinBtn", self.toggle_max)
        self.b_close = self._btn("win_close", "Закрыть", "WinClose", self.win.close)
        for b in (self.b_min, self.b_max, self.b_close):
            h.addWidget(b)

    def _btn(self, ico, tip, name, fn):
        b = _WinButton(ico, "#FFFFFF" if name == "WinClose" else TEXT2, self)
        b.setObjectName(name)
        b.setIconSize(QSize(16, 16))
        b.setFixedSize(QSize(46, TITLE_H))
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(fn)
        return b

    # ---------------------------------------------------------------
    def set_page(self, text):
        self.win.setWindowTitle(f"Kryostat — {text}" if text else "Kryostat")   # для панели задач

    def set_badge(self, text):
        pass

    def toggle_max(self):
        if self.win.isMaximized():
            self.win.showNormal()
        else:
            self.win.showMaximized()

    def _sync_max(self):
        m = self.win.isMaximized()
        self.b_max.set_glyph("win_restore" if m else "win_max")
        self.b_max.setToolTip("Восстановить" if m else "Развернуть")

    def sync_max(self):
        self._sync_max()

    def is_caption_at(self, local_pos):
        """Пустое место заголовка (не кнопка) — за него можно тащить окно."""
        w = self.childAt(local_pos)
        return not isinstance(w, QToolButton)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.is_caption_at(e.position().toPoint()):
            wh = self.win.windowHandle()
            if wh is not None and wh.startSystemMove():
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self.is_caption_at(e.position().toPoint()):
            self.toggle_max()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


# ================================================================ края окна
class _Grip(QWidget):
    """Невидимая полоска по краю окна: потянуть — изменить размер.
    Курсор задан статически, поэтому на движение мыши Python не вызывается вообще."""

    def __init__(self, win, edges, cursor):
        super().__init__(win)
        self.win, self.edges = win, edges
        self.setCursor(cursor)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def paintEvent(self, _e):
        pass

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            wh = self.win.windowHandle()
            if wh is not None:
                wh.startSystemResize(self.edges)
                e.accept()
                return
        super().mousePressEvent(e)


class EdgeGrips:
    W = 5

    def __init__(self, win):
        self.win = win
        E = Qt.Edge
        spec = [(E.LeftEdge, Qt.SizeHorCursor), (E.RightEdge, Qt.SizeHorCursor),
                (E.TopEdge, Qt.SizeVerCursor), (E.BottomEdge, Qt.SizeVerCursor),
                (E.TopEdge | E.LeftEdge, Qt.SizeFDiagCursor),
                (E.BottomEdge | E.RightEdge, Qt.SizeFDiagCursor),
                (E.TopEdge | E.RightEdge, Qt.SizeBDiagCursor),
                (E.BottomEdge | E.LeftEdge, Qt.SizeBDiagCursor)]
        self.grips = [_Grip(win, e, c) for e, c in spec]

    def update(self):
        w, h, g = self.win.width(), self.win.height(), self.W
        c = g * 2                                     # углы чуть больше — проще попасть
        rects = [(0, c, g, h - 2 * c), (w - g, c, g, h - 2 * c),
                 (c, 0, w - 2 * c, g), (c, h - g, w - 2 * c, g),
                 (0, 0, c, c), (w - c, h - c, c, c), (w - c, 0, c, c), (0, h - c, c, c)]
        show = not (self.win.isMaximized() or self.win.isFullScreen())
        for grip, r in zip(self.grips, rects):
            grip.setGeometry(*r)
            grip.setVisible(show)
            if show:
                grip.raise_()


def _colorref(hex_color):
    c = hex_color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return r | (g << 8) | (b << 16)


def setup_native_frame(win, border_color="#5B2E9E"):
    """Один раз после создания окна (только Windows): скруглённые углы и цвет рамки
    Windows 11, сворачивание кликом по значку на панели задач. Обработчиков
    системных сообщений нет — они вызывались на каждое движение мыши и тормозили окно."""
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        hwnd = int(win.winId())
        get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_ = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get.restype = set_.restype = ctypes.c_ssize_t
        get.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        GWL_STYLE, WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = -16, 0x20000, 0x10000, 0x80000
        set_(hwnd, GWL_STYLE, get(hwnd, GWL_STYLE) | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)
        for attr, val in ((20, 1), (33, 2), (34, _colorref(border_color))):
            v = ctypes.c_int(val)
            dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(v),
                                         ctypes.sizeof(v))
        return True
    except Exception:
        return False
