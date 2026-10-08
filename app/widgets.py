"""Переиспользуемые виджеты Kryostat."""
from PySide6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF,
                            QTimer, Qt, Property, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSizePolicy,
                               QStyledItemDelegate, QVBoxLayout, QWidget)
from .icons import icon
from core.i18n import tr
from .theme import (OK, AMBER, BG2, BG3, BG4, GREEN, GREEN_SOFT, LINE, MUTED, PURPLE, RED,
                    TEXT, TEXT2, STRONG, ON_ACCENT, rgba)


def level_color(p: float) -> str:
    return OK if p < 65 else (AMBER if p < 88 else RED)


class Card(QFrame):
    def __init__(self, title: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(20, 16, 20, 18)
        self.v.setSpacing(12)
        self.header = None
        if title:
            self.header = QHBoxLayout()
            self.header.setSpacing(8)
            lbl = QLabel(tr(title).upper())
            lbl.setObjectName("H2")
            self.header.addWidget(lbl)
            if subtitle:
                s = QLabel(subtitle)
                s.setObjectName("Small")
                self.header.addWidget(s)
            self.header.addStretch(1)
            self.v.addLayout(self.header)

    def add(self, w):
        self.v.addWidget(w)
        return w

    def add_action(self, w):
        if self.header:
            self.header.addWidget(w)
        return w


class Sparkline(QWidget):
    """График истории значения с заливкой."""

    def __init__(self, color=GREEN, maximum=100.0, parent=None):
        super().__init__(parent)
        self.values = [0.0]
        self.color = QColor(color)
        self.maximum = maximum
        self.auto_scale = False
        self.setMinimumHeight(52)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_values(self, values, maximum=None, color=None):
        self.values = list(values) or [0.0]
        if maximum is not None:
            self.maximum = max(1e-6, maximum)
        if color:
            self.color = QColor(color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        top = 4

        p.setPen(QPen(QColor(LINE), 1, Qt.DotLine))
        for i in range(1, 4):
            y = int(top + (h - top) * i / 4)       # drawLine в PySide6 не принимает float
            p.drawLine(0, y, w, y)

        vals = self.values
        mx = max(self.maximum, max(vals) if self.auto_scale else 0) or 1
        n = len(vals)
        if n < 2:
            p.end()
            return
        step = w / (n - 1)
        path = QPainterPath()
        fill = QPainterPath()
        fill.moveTo(0, h)
        for i, v in enumerate(vals):
            x = i * step
            y = top + (h - top) * (1 - min(1.0, max(0.0, v / mx)))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
            fill.lineTo(x, y)
        fill.lineTo(w, h)
        fill.closeSubpath()

        grad = QLinearGradient(0, 0, 0, h)
        c = QColor(self.color)
        c.setAlpha(80)
        grad.setColorAt(0, c)
        c2 = QColor(self.color)
        c2.setAlpha(0)
        grad.setColorAt(1, c2)
        p.fillPath(fill, QBrush(grad))
        p.setPen(QPen(self.color, 1.8))
        p.drawPath(path)
        p.end()


class CoreBars(QWidget):
    """Загрузка по каждому ядру."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_values(self, values):
        self.values = list(values)
        self.update()

    def paintEvent(self, _):
        if not self.values:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.values)
        gap = 3
        bw = max(3.0, min(34.0, (self.width() - gap * (n - 1)) / n))
        total = bw * n + gap * (n - 1)
        x0 = max(0.0, (self.width() - total) / 2)
        h = self.height()
        for i, v in enumerate(self.values):
            x = x0 + i * (bw + gap)
            p.setBrush(QColor(BG4))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x, 0, bw, h), 2, 2)
            bh = h * min(1.0, v / 100.0)
            p.setBrush(QColor(level_color(v)))
            p.drawRoundedRect(QRectF(x, h - bh, bw, bh), 2, 2)
        p.end()


class StatTile(QFrame):
    """Плитка метрики: заголовок, крупное значение, график, подпись."""

    def __init__(self, title: str, icon_name: str = "gauge", color=GREEN, parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")
        self.base_color = color
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 15, 18, 15)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(7)
        ico = QLabel()
        ico.setPixmap(icon(icon_name, MUTED, 15).pixmap(15, 15))
        top.addWidget(ico)
        t = QLabel(tr(title).upper())
        t.setObjectName("H2")
        top.addWidget(t)
        top.addStretch(1)
        self.badge = QLabel("")
        self.badge.setObjectName("Small")
        top.addWidget(self.badge)
        v.addLayout(top)

        self.value = QLabel("—")
        self.value.setObjectName("Big")
        self._value_color = None
        v.addWidget(self.value)

        self.spark = Sparkline(color)
        self.spark.setMinimumHeight(46)
        v.addWidget(self.spark, 1)

        self.sub = QLabel("")
        self.sub.setObjectName("Small")
        v.addWidget(self.sub)
        self.setMinimumWidth(190)

    def set(self, value_text, history=None, maximum=100.0, sub="", badge="",
            percent=None, autoscale=False):
        self.value.setText(value_text)
        self.sub.setText(sub)
        self.badge.setText(badge)
        col = level_color(percent) if percent is not None else self.base_color
        if col != self._value_color:          # setStyleSheet дорогой (перерасчёт стиля) — только при смене
            self._value_color = col
            self.value.setStyleSheet(f"color:{col};")
        if history is not None:
            self.spark.auto_scale = autoscale
            self.spark.set_values(history, maximum, col)


class UsageRow(QWidget):
    """Строка «название — полоса — значение», для дисков и т.п."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.name = QLabel("—")
        self.name.setStyleSheet(f"font-weight:600;color:{TEXT};")
        self.val = QLabel("")
        self.val.setObjectName("Small")
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(self.val)
        v.addLayout(top)
        self.bar = _Bar()
        v.addWidget(self.bar)

    def set(self, name, percent, detail):
        self.name.setText(name)
        self.val.setText(detail)
        self.bar.set_value(percent)


class _Bar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setFixedHeight(7)

    def set_value(self, v):
        self.value = max(0.0, min(100.0, float(v)))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(BG4))
        p.drawRoundedRect(QRectF(r), 3.5, 3.5)
        p.setBrush(QColor(level_color(self.value)))
        p.drawRoundedRect(QRectF(0, 0, r.width() * self.value / 100.0, r.height()), 3.5, 3.5)
        p.end()


class SearchBox(QLineEdit):
    def __init__(self, placeholder="Поиск…", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icon("search", MUTED, 16), QLineEdit.LeadingPosition)
        self.setMinimumWidth(240)


class Toast(QFrame):
    """Всплывающее уведомление в правом нижнем углу."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 11, 16, 11)
        h.setSpacing(10)
        self.icon = QLabel()
        h.addWidget(self.icon)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(420)
        h.addWidget(self.label)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.effect.setOpacity(0.0)
        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fading_out = False
        self.anim.finished.connect(self._anim_finished)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fade_out)
        self.hide()

    def show_message(self, text, kind="info"):
        colors = {"info": GREEN, "warn": AMBER, "error": RED}
        names = {"info": "check", "warn": "alert", "error": "x"}
        c = colors.get(kind, GREEN)
        self.setStyleSheet(f"QFrame#Toast {{ border:1px solid {rgba(c, 0.55)}; "
                           f"border-left:3px solid {c}; }}")
        self.icon.setPixmap(icon(names.get(kind, "check"), c, 17).pixmap(17, 17))
        self.label.setText(text)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._fading_out = False
        self.anim.stop()
        self.anim.setStartValue(self.effect.opacity())
        self.anim.setEndValue(1.0)
        self.anim.start()
        self.timer.start(5000 if kind == "info" else 8000)

    def fade_out(self):
        self._fading_out = True
        self.anim.stop()
        self.anim.setStartValue(self.effect.opacity())
        self.anim.setEndValue(0.0)
        self.anim.start()

    def _anim_finished(self):
        # прятать только после затухания: раньше слот hide оставался подключённым
        # и скрывал каждое следующее уведомление сразу после появления
        if self._fading_out:
            self.hide()

    def reposition(self):
        if self.parent():
            p = self.parent()
            bottom = p.statusBar().height() if hasattr(p, "statusBar") else 0
            self.move(max(0, p.width() - self.width() - 26),
                      max(0, p.height() - self.height() - bottom - 18))


def pill_style(color=GREEN) -> str:
    return (f"background:{rgba(color, 0.12)};color:{color};border:1px solid {rgba(color, 0.38)};"
            f"border-radius:11px;padding:3px 11px;font-size:11px;font-weight:600;")


def pill(text: str, color=GREEN) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(pill_style(color))
    l.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    l.setAlignment(Qt.AlignCenter)
    return l


def muted(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("Hint")
    l.setWordWrap(True)
    return l


class GlitchLabel(QWidget):
    """Заголовок в стиле логотипа: белый текст с циановым и маджентовым «сдвигом»."""

    def __init__(self, text="", size=22, parent=None):
        super().__init__(parent)
        self._text = text
        self._font = QFont("Segoe UI", 1)
        self._font.setPixelSize(size)
        self._font.setWeight(QFont.Black)
        self._font.setLetterSpacing(QFont.AbsoluteSpacing, 0.5)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._update_size()

    def _update_size(self):
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self._font)
        self.setFixedHeight(fm.height() + 6)
        self.setMinimumWidth(fm.horizontalAdvance(self._text) + 8)

    def setText(self, text):
        self._text = text
        self._update_size()
        self.update()

    def text(self):
        return self._text

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(self._font)
        r = self.rect().adjusted(3, 0, -3, 0)
        flags = Qt.AlignLeft | Qt.AlignVCenter
        p.setPen(QColor(PURPLE))
        p.drawText(r.translated(-2, 1), flags, self._text)
        p.setPen(QColor(GREEN))
        p.drawText(r.translated(2, -1), flags, self._text)
        p.setPen(QColor(STRONG))
        p.drawText(r, flags, self._text)
        # тонкие «сканлинии» поверх текста
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        for y in range(0, self.height(), 3):
            p.drawLine(0, y, self.width(), y)
        p.end()


class PageTitle(QWidget):
    """Заголовок страницы: акцентная полоска + крупный текст (+ необязательная подпись)."""

    def __init__(self, text="", subtitle="", parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        bar = QFrame()
        bar.setFixedSize(4, 26)
        bar.setStyleSheet(f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {GREEN},"
                          f"stop:1 {PURPLE});border-radius:2px;")
        h.addWidget(bar, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(text)
        self.label.setObjectName("PageTitle")
        col.addWidget(self.label)
        self.sub = QLabel(subtitle)
        self.sub.setObjectName("PageSub")
        self.sub.setVisible(bool(subtitle))
        col.addWidget(self.sub)
        h.addLayout(col)

    def setText(self, text):
        self.label.setText(text)

    def text(self):
        return self.label.text()


def title(text: str, subtitle: str = "") -> QWidget:
    return PageTitle(text, subtitle)


class RowDelegate(QStyledItemDelegate):
    """Высота строк таблиц без padding в стилях (иначе ломается попадание по флажкам)."""

    def __init__(self, extra=12, parent=None):
        super().__init__(parent)
        self.extra = extra

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(s.height() + self.extra)
        return s


def kv_grid(pairs, cols=2):
    """Сетка «название — значение». Возвращает (виджет, {ключ: QLabel значения})."""
    w = QWidget()
    g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0)
    g.setHorizontalSpacing(18)
    g.setVerticalSpacing(7)
    labels = {}
    for i, (key, name) in enumerate(pairs):
        r, c = divmod(i, cols)
        k = QLabel(name)
        k.setObjectName("KV")
        val = QLabel("—")
        val.setObjectName("KVValue")
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        val.setWordWrap(True)
        g.addWidget(k, r, c * 2)
        g.addWidget(val, r, c * 2 + 1)
        g.setColumnStretch(c * 2 + 1, 1)
        labels[key] = val
    return w, labels


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFixedHeight(1)
    return f


def tool_button(text, icon_name=None, object_name=None, on_click=None, tip=""):
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name, {"Primary": ON_ACCENT, "Danger": RED}.get(object_name, TEXT2), 16))
    if object_name:
        b.setObjectName(object_name)
    if on_click:
        b.clicked.connect(on_click)
    if tip:
        b.setToolTip(tip)
    return b


class SortItem:
    """Примесь для числовой сортировки в QTreeWidget."""
    SORT_ROLE = Qt.UserRole + 11

    def __lt__(self, other):
        # вызывается тысячи раз при каждой сортировке — минимум работы на сравнение
        tw = self.treeWidget()
        col = tw.sortColumn() if tw is not None else 0
        a = self.data(col, SortItem.SORT_ROLE)
        if a is not None:
            b = other.data(col, SortItem.SORT_ROLE)
            if b is not None:
                try:
                    return a < b
                except TypeError:
                    return str(a) < str(b)
        return (self.text(col) or "").lower() < (other.text(col) or "").lower()


# ------------------------------------------------------------ значки файлов
_ICON_CACHE = {}
_ICON_PROVIDER = None


def file_icon(path, budget=None):
    """Значок программы по пути к exe (кэшируется). budget — список-счётчик [n]:
    сколько новых значков ещё можно загрузить в этом проходе (SHGetFileInfo небыстрый,
    поэтому сотни значков подгружаются порциями, а не все в один кадр).
    Возвращает QIcon или None, если загрузку отложили."""
    global _ICON_PROVIDER
    from PySide6.QtCore import QFileInfo
    from PySide6.QtWidgets import QFileIconProvider
    key = (path or "").lower()
    ic = _ICON_CACHE.get(key)
    if ic is not None:
        return ic
    if budget is not None:
        if budget[0] <= 0:
            return None
        budget[0] -= 1
    if _ICON_PROVIDER is None:
        _ICON_PROVIDER = QFileIconProvider()
    import os
    if path and os.path.isfile(path):
        ic = _ICON_PROVIDER.icon(QFileInfo(path))
        if ic.isNull():
            ic = icon("cpu", MUTED, 16)
    else:
        ic = icon("cpu", MUTED, 16)
    _ICON_CACHE[key] = ic
    return ic
