"""Перевод интерфейса на уровне Qt.

Все тексты, которые попадают в виджеты (надписи, кнопки, подсказки, пункты меню,
заголовки колонок, ячейки таблиц, окна сообщений…), проходят через core.i18n.tr.
Внутренняя логика и данные (конфиг, реестр, команды) остаются как есть — поэтому
перевод не может ничего сломать. Для исходного (русского) языка ничего не подменяется,
накладных расходов ноль.

install() нужно вызвать ДО импорта страниц приложения (они делают
`from PySide6.QtWidgets import QLabel`, и должны получить уже «переводящие» классы)."""
from core.i18n import is_source, tr

_done = False


def _t(x):
    if type(x) is str:
        return tr(x)
    if type(x) is list and x and type(x[0]) is str:
        return [tr(i) if type(i) is str else i for i in x]
    return x


def _first(args):
    """Перевести первый текстовый аргумент (или список строк)."""
    for i, a in enumerate(args):
        if type(a) is str or (type(a) is list and a and type(a[0]) is str):
            return args[:i] + (_t(a),) + args[i + 1:]
    return args


def _all(args):
    return tuple(_t(a) for a in args)


def _patch(cls, name, mode=_first, static=False):
    orig = getattr(cls, name, None)
    if orig is None:
        return
    if static:
        def w(*a, **k):
            return orig(*mode(a), **k)
        setattr(cls, name, staticmethod(w))
    else:
        def w(self, *a, **k):
            return orig(self, *mode(a), **k)
        setattr(cls, name, w)


def _patch_n(cls, name, n):
    """Перевести только первые n аргументов (остальное — данные/значения по умолчанию)."""
    orig = getattr(cls, name, None)
    if orig is None:
        return

    def w(*a, **k):
        return orig(*(_all(a[:n]) + a[n:]), **k)
    setattr(cls, name, staticmethod(w))


def _ctor_subclass(module, name, mode=_first):
    base = getattr(module, name)

    def __init__(self, *a, **k):
        if "text" in k:
            k["text"] = _t(k["text"])
        if "title" in k:
            k["title"] = _t(k["title"])
        base.__init__(self, *mode(a), **k)
    sub = type(name, (base,), {"__init__": __init__, "__module__": base.__module__,
                               "__qualname__": base.__qualname__})
    setattr(module, name, sub)
    return sub


def install():
    global _done
    if _done or is_source():
        return
    _done = True
    from PySide6 import QtGui, QtWidgets as W
    from PySide6.QtGui import QAction, QPainter

    # ---- методы (действуют и на подклассы, и на виджеты, созданные Qt)
    for cls, names in (
        (W.QLabel, ("setText",)),
        (W.QAbstractButton, ("setText",)),
        (W.QWidget, ("setToolTip", "setWindowTitle")),
        (W.QLineEdit, ("setPlaceholderText",)),
        (W.QPlainTextEdit, ("setPlaceholderText",)),
        (W.QTextEdit, ("setPlaceholderText",)),
        (W.QGroupBox, ("setTitle",)),
        (W.QMenu, ("setTitle", "addAction", "addMenu", "addSection")),
        (W.QComboBox, ("addItem", "insertItem", "setItemText", "addItems", "setPlaceholderText")),
        (W.QTabWidget, ("addTab", "insertTab", "setTabText", "setTabToolTip")),
        (W.QTabBar, ("addTab", "insertTab", "setTabText", "setTabToolTip")),
        (W.QTreeWidget, ("setHeaderLabels", "setHeaderLabel")),
        (W.QTableWidget, ("setHorizontalHeaderLabels", "setVerticalHeaderLabels")),
        (W.QTreeWidgetItem, ("setText", "setToolTip")),
        (W.QTableWidgetItem, ("setText", "setToolTip")),
        (W.QListWidgetItem, ("setText", "setToolTip")),
        (W.QListWidget, ("addItem", "addItems", "insertItem")),
        (W.QFormLayout, ("addRow", "insertRow")),
        (W.QSpinBox, ("setSuffix", "setPrefix", "setSpecialValueText")),
        (W.QDoubleSpinBox, ("setSuffix", "setPrefix", "setSpecialValueText")),
        (W.QProgressBar, ("setFormat",)),
        (W.QDialogButtonBox, ("addButton",)),
        (W.QStatusBar, ("showMessage",)),
        (W.QToolBar, ("addAction",)),
        (W.QInputDialog, ("setLabelText", "setOkButtonText", "setCancelButtonText")),
        (QAction, ("setText", "setToolTip", "setIconText")),
    ):
        for n in names:
            _patch(cls, n)
    for n in ("setText", "setInformativeText", "setWindowTitle"):
        _patch(W.QMessageBox, n)
    _patch(W.QMessageBox, "addButton")
    for n in ("information", "question", "warning", "critical", "about"):
        _patch(W.QMessageBox, n, _all, static=True)
    _patch_n(W.QInputDialog, "getText", 3)
    _patch_n(W.QInputDialog, "getInt", 3)
    _patch_n(W.QInputDialog, "getDouble", 3)
    _patch_n(W.QInputDialog, "getItem", 3)
    _patch_n(W.QInputDialog, "getMultiLineText", 3)
    _patch_n(W.QFileDialog, "getExistingDirectory", 2)
    for n in ("getOpenFileName", "getOpenFileNames", "getSaveFileName"):
        orig = getattr(W.QFileDialog, n)

        def w(*a, _o=orig, **k):          # (parent, caption, dir, filter)
            a = list(a)
            if len(a) > 1:
                a[1] = _t(a[1])
            if len(a) > 3:
                a[3] = _t(a[3])
            for key in ("caption", "filter"):
                if key in k:
                    k[key] = _t(k[key])
            return _o(*a, **k)
        setattr(W.QFileDialog, n, staticmethod(w))
    _patch(W.QToolTip, "showText", _all, static=True)
    _patch(W.QSystemTrayIcon, "setToolTip")
    _patch(W.QSystemTrayIcon, "showMessage", _all)
    _patch(QPainter, "drawText", _all)

    # ---- конструкторы с текстом
    for name in ("QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QGroupBox", "QMenu",
                 "QTreeWidgetItem", "QTableWidgetItem", "QListWidgetItem", "QCommandLinkButton"):
        _ctor_subclass(W, name)
    _ctor_subclass(W, "QMessageBox", _all)
    _ctor_subclass(QtGui, "QAction")
