"""Тема Kryostat.

Палитра собирается из двух частей:
  • основа (base) — фон, линии и текст: «Полночь», «Графит», «AMOLED», «Океан», «Светлая»;
  • акцент — любой цвет: готовые пресеты или свой HEX.

Имена констант сохранены (GREEN = основной акцент, PURPLE = вторичный), поэтому все
страницы получают выбранную палитру автоматически. Палитра вычисляется один раз при
импорте (из config.json), стиль можно пересобрать на лету — set_theme()."""
import colorsys

from core import config

BASES = {
    #           BG         BG2        BG3        BG4        LINE       LINE2      TEXT       TEXT2      MUTED      TERM
    "midnight": ("#09070F", "#0F0C18", "#161221", "#1E182C", "#262036", "#3A3052", "#F4F1FB", "#C7BFDB", "#8A82A3", "#06040B"),
    "graphite": ("#0F1012", "#15171A", "#1C1E22", "#24272C", "#2B2E34", "#3D414A", "#F2F3F5", "#C4C8CF", "#868B95", "#0B0C0E"),
    "amoled":   ("#000000", "#08080A", "#101013", "#18181D", "#1F1F25", "#33333C", "#F5F5F7", "#C8C8D0", "#85858F", "#000000"),
    "ocean":    ("#070C16", "#0C1320", "#121B2B", "#182337", "#1F2B40", "#30405E", "#EEF3FB", "#BCC8DC", "#7E8BA3", "#050911"),
    "light":    ("#F3F4F8", "#FFFFFF", "#F4F5F9", "#E9EBF2", "#E1E4EC", "#C9CEDB", "#141623", "#3A3F52", "#6B7186", "#FBFBFD"),
}
BASE_NAMES = {"midnight": "Полночь", "graphite": "Графит", "amoled": "AMOLED (чёрная)",
              "ocean": "Океан", "light": "Светлая"}

ACCENTS = {
    "violet": "#A855F7", "indigo": "#6366F1", "blue": "#3B82F6", "cyan": "#22D3EE",
    "teal": "#14B8A6", "emerald": "#22C55E", "lime": "#84CC16", "amber": "#F59E0B",
    "orange": "#F97316", "rose": "#F43F5E", "pink": "#EC4899", "red": "#EF4444",
}
ACCENT_NAMES = {"violet": "Фиолетовый", "indigo": "Индиго", "blue": "Синий", "cyan": "Циан",
                "teal": "Бирюзовый", "emerald": "Изумрудный", "lime": "Лайм", "amber": "Янтарный",
                "orange": "Оранжевый", "rose": "Розовый", "pink": "Маджента", "red": "Красный"}


# ------------------------------------------------------------------ цвет
def _rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(r, g, b):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(x)))) for x in (r, g, b))


def mix(a, b, t):
    """Смесь цветов: t=0 → a, t=1 → b."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(*(x + (y - x) * t for x, y in zip(ra, rb)))


def rgba(h, alpha):
    r, g, b = _rgb(h)
    return f"rgba({r},{g},{b},{alpha:.2f})"


def lum(h):
    r, g, b = (x / 255 for x in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hue_shift(h, deg):
    r, g, b = (x / 255 for x in _rgb(h))
    hh, l, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb((hh + deg / 360) % 1, l, s)
    return _hex(r * 255, g * 255, b * 255)


def valid_hex(h):
    try:
        return len(h.lstrip("#")) in (3, 6) and bool(_rgb(h))
    except Exception:
        return False


def resolve_accent(accent):
    if accent in ACCENTS:
        return ACCENTS[accent]
    if isinstance(accent, str) and valid_hex(accent):
        return _hex(*_rgb(accent))
    return ACCENTS["violet"]


# ----------------------------------------------------------- палитра
def _palette(base, accent):
    b = BASES.get(base) or BASES["midnight"]
    bg, bg2, bg3, bg4, line, line2, text, text2, muted, term = b
    light = base == "light"
    a = resolve_accent(accent)
    on_dark_accent = lum(a) < 0.62
    return {
        "BASE": base if base in BASES else "midnight", "LIGHT": light,
        "BG": bg, "BG2": bg2, "BG3": bg3, "BG4": bg4, "LINE": line, "LINE2": line2,
        "TEXT": text, "TEXT2": text2, "MUTED": muted, "TERM_BG": term,
        "GREEN": a,
        "GREEN_SOFT": mix(a, "#000000", 0.22),
        "GREEN_DEEP": mix(bg2, a, 0.16 if not light else 0.12),
        "ACCENT_LIGHT": mix(a, "#FFFFFF", 0.30) if not light else mix(a, "#000000", 0.18),
        "ON_ACCENT": "#FFFFFF" if on_dark_accent else "#0B0B10",
        "STRONG": "#FFFFFF" if not light else "#0B0C14",
        "PURPLE": _hue_shift(a, 48) if not light else mix(_hue_shift(a, 48), "#000000", 0.15),
        "VIOLET": mix(a, muted, 0.45),
        "SEL": mix(bg2, a, 0.24 if not light else 0.16),
        "HOVER": mix(bg2, a, 0.07) if not light else mix(bg2, a, 0.05),
        "OK": "#4ADE80" if not light else "#16A34A",
        "RED": "#FF5C7A" if not light else "#E11D48",
        "RED_BG": mix(bg2, "#FF5C7A", 0.14),
        "AMBER": "#FFC857" if not light else "#C27803",
        "BLUE": "#8EA2FF" if not light else "#4F46E5",
        "ALT": mix(bg2, text, 0.018 if not light else 0.012),
        "DISABLED": mix(bg2, text, 0.28),
    }


def _current():
    try:
        cfg = config.load()
    except Exception:
        cfg = {}
    t = cfg.get("theme") or {}
    accent = t.get("accent") or "violet"
    return _palette(t.get("base") or "midnight", accent)


P = _current()
globals().update({k: v for k, v in P.items()})
ACCENT = GREEN                  # noqa: F821 — имена приходят из palette
MONO = "'Cascadia Mono','JetBrains Mono','Consolas',monospace"
UI_FONT = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "sans-serif"]


def set_theme(base, accent):
    """Поменять палитру в модуле (новые виджеты получат её сразу) и вернуть словарь."""
    global P, ACCENT
    P = _palette(base, accent)
    globals().update({k: v for k, v in P.items()})
    ACCENT = P["GREEN"]
    return P


def _qss(A: str, p: dict) -> str:
    g = p
    acc = g["GREEN"]
    return f"""
QWidget {{ color: {g['TEXT']}; outline: none; }}
QWidget#Root, QMainWindow {{ background: {g['BG']}; }}
QWidget#Sidebar {{ background: {g['BG2']}; border-right: 1px solid {g['LINE']}; }}
QWidget#PageArea {{ background: {g['BG']}; }}

QLabel#BrandSub {{ color: {g['MUTED']}; font-family: {MONO}; font-size: 10px; letter-spacing: 1px; }}
QLabel#NavSection {{ color: {g['MUTED']}; font-size: 10px; font-weight: 700;
 letter-spacing: 1.6px; padding: 16px 12px 6px 14px; }}
QLabel#H2 {{ font-size: 11px; font-weight: 700; color: {g['MUTED']}; letter-spacing: 1.2px; }}
QLabel#PageTitle {{ font-size: 24px; font-weight: 700; color: {g['STRONG']}; }}
QLabel#PageSub {{ color: {g['MUTED']}; font-size: 12px; }}
QLabel#Muted {{ color: {g['MUTED']}; }}
QLabel#Hint {{ color: {g['MUTED']}; font-size: 12px; }}
QLabel#Big {{ font-size: 28px; font-weight: 700; color: {g['TEXT']}; }}
QLabel#Small {{ color: {g['MUTED']}; font-size: 11px; }}
QLabel#Mono {{ font-family: {MONO}; color: {g['TEXT2']}; }}
QLabel#KV {{ color: {g['MUTED']}; font-size: 12px; }}
QLabel#KVValue {{ color: {g['TEXT']}; font-size: 12px; font-weight: 600; }}
QLabel#Kbd {{ color: {g['MUTED']}; background: {g['BG3']}; border: 1px solid {g['LINE']};
  border-radius: 5px; padding: 1px 6px; font-size: 11px; font-family: {MONO}; }}
QFrame#AdminChip {{ background: {g['BG3']}; border: 1px solid {g['LINE']}; border-radius: 10px; }}

/* ---- навигация ---- */
QPushButton#Nav {{
  text-align: left; padding: 9px 12px; border: none; border-radius: 9px;
  background: transparent; color: {g['TEXT2']}; font-size: 13px; margin: 1px 0;
}}
QPushButton#Nav:hover {{ background: {g['BG3']}; color: {g['TEXT']}; }}
QPushButton#Nav:checked {{ background: {g['SEL']}; color: {g['STRONG']}; font-weight: 600; }}
QPushButton#NavSearch {{ text-align: left; padding: 8px 12px; border-radius: 9px;
  background: {g['BG3']}; border: 1px solid {g['LINE']}; color: {g['MUTED']}; }}
QPushButton#NavSearch:hover {{ border-color: {acc}; color: {g['TEXT']}; }}
QToolButton#Collapse {{ background: transparent; border: none; border-radius: 7px; padding: 5px; }}
QToolButton#Collapse:hover {{ background: {g['BG3']}; }}

/* ---- кнопки ---- */
QPushButton {{
  background: {g['BG3']}; border: 1px solid {g['LINE']}; border-radius: 8px;
  padding: 7px 14px; color: {g['TEXT']}; font-weight: 500;
}}
QPushButton:hover {{ background: {g['BG4']}; border-color: {g['LINE2']}; }}
QPushButton:pressed {{ background: {g['GREEN_DEEP']}; border-color: {acc}; }}
QPushButton:focus {{ border-color: {acc}; }}
QPushButton:disabled {{ color: {g['DISABLED']}; border-color: {g['LINE']}; background: {g['BG2']}; }}
QPushButton:checked {{ background: {g['SEL']}; border-color: {acc}; color: {g['STRONG']}; }}
QPushButton#Primary {{ background: {acc}; border: 1px solid {acc}; color: {g['ON_ACCENT']}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {g['ACCENT_LIGHT']}; border-color: {g['ACCENT_LIGHT']}; }}
QPushButton#Primary:pressed {{ background: {g['GREEN_SOFT']}; }}
QPushButton#Primary:disabled {{ background: {g['BG3']}; color: {g['DISABLED']}; border-color: {g['LINE']}; }}
QPushButton#Danger {{ border-color: {mix(g['LINE'], g['RED'], 0.35)}; color: {g['RED']}; }}
QPushButton#Danger:disabled {{ color: {g['DISABLED']}; border-color: {g['LINE']}; background: {g['BG2']}; }}
QPushButton#Danger:hover {{ background: {g['RED_BG']}; border-color: {g['RED']}; }}
QPushButton#Magenta {{ border-color: {mix(g['LINE'], g['PURPLE'], 0.4)}; color: {g['PURPLE']}; }}
QPushButton#Magenta:hover {{ background: {mix(g['BG2'], g['PURPLE'], 0.12)}; border-color: {g['PURPLE']}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {g['MUTED']}; padding: 6px 8px; }}
QPushButton#Ghost:hover {{ color: {acc}; background: {g['BG3']}; }}
QPushButton#Chip {{ background: {g['BG2']}; border: 1px solid {g['LINE']}; border-radius: 15px;
                    padding: 5px 14px; color: {g['TEXT2']}; font-size: 12px; }}
QPushButton#Chip:hover {{ color: {g['TEXT']}; border-color: {g['LINE2']}; background: {g['BG3']}; }}
QPushButton#Chip:checked {{ background: {g['SEL']}; border-color: {acc}; color: {g['STRONG']}; font-weight: 600; }}
QPushButton#Tile {{ background: {g['BG2']}; border: 1px solid {g['LINE']}; border-radius: 12px;
  padding: 14px; text-align: left; color: {g['TEXT']}; font-weight: 600; }}
QPushButton#Tile:hover {{ border-color: {acc}; background: {g['HOVER']}; }}
QPushButton#TileDanger {{ background: {g['BG2']}; border: 1px solid {mix(g['LINE'], g['RED'], 0.35)};
  border-radius: 12px; padding: 14px; text-align: left; color: {g['RED']}; font-weight: 600; }}
QPushButton#TileDanger:hover {{ border-color: {g['RED']}; background: {g['RED_BG']}; }}
QPushButton#Swatch {{ border-radius: 13px; padding: 0; min-width: 26px; max-width: 26px;
  min-height: 26px; max-height: 26px; border: 2px solid {g['BG2']}; }}
QPushButton#Swatch:checked {{ border: 2px solid {g['STRONG']}; }}
QToolButton {{ color: {g['TEXT']}; }}

/* ---- карточки ---- */
QFrame#Card {{ background: {g['BG2']}; border: 1px solid {g['LINE']}; border-radius: 14px; }}
QFrame#Tile {{ background: {g['BG2']}; border: 1px solid {g['LINE']}; border-radius: 14px; }}
QFrame#Tile:hover {{ border-color: {g['LINE2']}; }}
QFrame#Toast {{ background: {g['BG4']}; border: 1px solid {g['LINE2']}; border-radius: 12px; }}
QFrame#Divider {{ background: {g['LINE']}; max-height: 1px; border: none; }}
QFrame#Palette {{ background: {g['BG2']}; border: 1px solid {g['LINE2']}; border-radius: 16px; }}

/* ---- поля ввода ---- */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QDoubleSpinBox {{
  background: {g['BG3']}; border: 1px solid {g['LINE']}; border-radius: 8px;
  padding: 7px 11px; color: {g['TEXT']}; selection-background-color: {acc};
  selection-color: {g['ON_ACCENT']};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QDoubleSpinBox:hover {{ border-color: {g['LINE2']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QDoubleSpinBox:focus {{
  border-color: {acc}; background: {g['BG4']}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {g['DISABLED']}; }}
QLineEdit#PaletteInput {{ font-size: 15px; padding: 12px 14px; border-radius: 10px; }}
QPlainTextEdit, QTextEdit {{ font-family: {MONO}; font-size: 12px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
  background: {g['BG3']}; border: 1px solid {g['LINE2']}; padding: 4px; border-radius: 8px;
  selection-background-color: {g['SEL']}; selection-color: {g['STRONG']}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QSpinBox::down-button {{
  subcontrol-origin: border; width: 18px; background: transparent; border: none; }}
QSpinBox::up-button {{ subcontrol-position: top right; margin: 3px 3px 0 0; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 3px 3px 0; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {g['BG4']}; border-radius: 4px; }}
QSpinBox::up-arrow {{ image: url("{A}/arrow_up.png"); width: 9px; height: 6px; }}
QSpinBox::down-arrow {{ image: url("{A}/arrow_down.png"); width: 9px; height: 6px; }}
QSpinBox::up-arrow:hover {{ image: url("{A}/arrow_up_a.png"); }}
QSpinBox::down-arrow:hover {{ image: url("{A}/arrow_down_a.png"); }}
QComboBox::down-arrow {{ image: url("{A}/arrow_down.png"); width: 9px; height: 6px; margin-right: 9px; }}
QComboBox::down-arrow:hover {{ image: url("{A}/arrow_down_a.png"); }}
QTreeView::indicator, QTreeWidget::indicator {{ width: 16px; height: 16px; }}
QTreeView::indicator:unchecked, QTreeWidget::indicator:unchecked {{
  border: 1px solid {g['LINE2']}; border-radius: 4px; background: {g['BG3']}; }}
QTreeView::indicator:checked, QTreeWidget::indicator:checked {{
  border: 1px solid {acc}; border-radius: 4px; background: {acc}; image: url("{A}/check.png"); }}

/* ---- таблицы ----
   ВАЖНО: без padding у ::item — иначе Qt рисует флажок в одном месте, а клик ловит
   в другом (флажки «не нажимались»). Высоту строк задаёт RowDelegate. */
QTreeWidget, QTreeView, QTableWidget, QListWidget {{
  background: {g['BG2']}; border: 1px solid {g['LINE']}; border-radius: 10px;
  alternate-background-color: {g['ALT']}; gridline-color: {g['LINE']};
  show-decoration-selected: 1; selection-background-color: {g['SEL']};
}}
QTreeWidget::item, QTreeView::item, QTableWidget::item, QListWidget::item {{ border: none; }}
QTreeWidget::item:hover, QTreeView::item:hover, QListWidget::item:hover {{ background: {g['HOVER']}; }}
QTreeWidget::item:selected, QTreeView::item:selected, QTableWidget::item:selected,
QListWidget::item:selected {{ background: {g['SEL']}; color: {g['STRONG']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
  background: {g['BG2']}; color: {g['MUTED']}; border: none;
  border-bottom: 1px solid {g['LINE']}; padding: 8px 8px; font-weight: 600; font-size: 11px; }}
QHeaderView::section:hover {{ color: {g['TEXT']}; }}
QTreeWidget::branch, QTreeView::branch {{ background: transparent; }}
QTableCornerButton::section {{ background: {g['BG2']}; border: none; }}

/* ---- вкладки ---- */
QTabWidget::pane {{ border: 1px solid {g['LINE']}; border-radius: 12px; top: -1px; background: {g['BG2']}; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {g['MUTED']}; border: none;
  border-bottom: 2px solid transparent; padding: 8px 16px; margin-right: 4px; }}
QTabBar::tab:hover {{ color: {g['TEXT']}; }}
QTabBar::tab:selected {{ color: {g['STRONG']}; border-bottom: 2px solid {acc}; font-weight: 600; }}
QTabBar QToolButton {{ background: {g['BG3']}; border: 1px solid {g['LINE']}; color: {g['TEXT2']};
  border-radius: 6px; margin: 3px 1px; }}
QTabBar QToolButton:hover {{ border-color: {acc}; }}
QTabBar::scroller {{ width: 44px; }}

/* ---- терминал ---- */
QTabBar#TermTabs::tab {{ padding: 0 6px 0 12px; height: 32px; max-width: 260px; background: {g['BG2']};
  margin-right: 2px; border: 1px solid {g['LINE']}; border-bottom: none;
  border-top-left-radius: 8px; border-top-right-radius: 8px; }}
QTabBar#TermTabs::tab:selected {{ background: {g['TERM_BG']}; border-top: 2px solid {acc}; }}
QToolButton#TabClose {{ background: transparent; border: none; border-radius: 4px; padding: 0; margin: 0; }}
QToolButton#TabClose:hover {{ background: {g['RED_BG']}; }}
QToolButton#TabClose:pressed {{ background: {mix(g['RED_BG'], g['RED'], 0.3)}; }}
QToolButton#TabPlus {{ background: {g['BG3']}; border: 1px solid {g['LINE']}; border-radius: 7px;
  color: {g['TEXT2']}; padding: 0 4px; margin: 0 0 4px 6px; min-height: 26px; }}
QToolButton#TabPlus:hover {{ border-color: {acc}; color: {g['STRONG']}; background: {g['BG4']}; }}
QToolButton#TabPlus {{ padding-right: 18px; min-width: 22px; }}
QToolButton#TabPlus::menu-button {{ border: none; border-left: 1px solid {g['LINE']}; width: 16px;
  border-top-right-radius: 7px; border-bottom-right-radius: 7px; }}
QToolButton#TabPlus::menu-button:hover {{ background: {g['LINE']}; }}
QToolButton#TabPlus::menu-arrow {{ image: url("{A}/arrow_down.png"); width: 9px; height: 6px; }}
QPlainTextEdit#TermOut {{ background: {g['TERM_BG']}; border: none; border-radius: 0;
  padding: 10px 12px; color: {g['TEXT2']}; selection-background-color: {g['SEL']};
  selection-color: {g['STRONG']}; }}
QPlainTextEdit#TermOut:focus {{ background: {g['TERM_BG']}; border: none; }}
QFrame#TermInputBar {{ background: {g['BG3']}; border-top: 1px solid {g['LINE']}; }}
QFrame#TermBusy {{ background: {g['BG2']}; border-top: 1px solid {g['LINE']}; }}
QLabel#TermBusyText {{ color: {g['AMBER']}; }}
QLabel#TermBusyHint {{ color: {g['MUTED']}; }}
QProgressBar#TermBusyProg {{ background: {g['BG3']}; border: none; border-radius: 2px; }}
QProgressBar#TermBusyProg::chunk {{ background: {acc}; border-radius: 2px; }}
QLabel#TermPrompt {{ color: {g['ACCENT_LIGHT']}; font-weight: 600; }}
QLineEdit#TermInput {{ background: transparent; border: none; padding: 6px 4px; color: {g['TEXT']}; }}
QLineEdit#TermInput:focus {{ background: transparent; border: none; }}
QTabWidget#Term::pane {{ border: 1px solid {g['LINE2']}; border-radius: 0 8px 8px 8px;
  background: {g['TERM_BG']}; top: -1px; }}

/* ---- заголовок окна ---- */
QWidget#TitleBar {{ background: {g['BG']}; }}
QLabel#TitleText {{ color: {g['TEXT']}; font-size: 12px; font-weight: 600; }}
QLabel#TitleSub {{ color: {g['MUTED']}; font-size: 12px; }}
QLabel#TitleBadge {{ color: {g['ACCENT_LIGHT']}; background: {g['GREEN_DEEP']};
  border: 1px solid {mix(g['LINE'], acc, 0.4)}; border-radius: 9px; padding: 1px 9px;
  font-size: 11px; font-weight: 600; }}
QToolButton#WinBtn, QToolButton#WinClose {{ background: transparent; border: none;
  border-radius: 0; padding: 0; margin: 0; }}
QToolButton#WinBtn:hover {{ background: {g['BG4']}; }}
QToolButton#WinBtn:pressed {{ background: {g['LINE2']}; }}
QToolButton#WinClose:hover {{ background: #E81123; }}
QToolButton#WinClose:pressed {{ background: #A50E1B; }}
QWidget#WindowFrame {{ background: {g['BG']}; border: 1px solid {g['LINE2']}; }}
QWidget#WindowFrame[maximized="true"] {{ border: none; }}

/* ---- прочее ---- */
QProgressBar {{ background: {g['BG4']}; border: none; border-radius: 3px; height: 7px; text-align: center; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                       stop:0 {acc}, stop:1 {g['PURPLE']}); border-radius: 3px; }}
QSlider::groove:horizontal {{ height: 4px; background: {g['BG4']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {acc}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {g['STRONG'] if not g['LIGHT'] else '#FFFFFF'};
  border: 3px solid {acc}; width: 12px; height: 12px; margin: -7px 0; border-radius: 9px; }}

QCheckBox, QRadioButton {{ spacing: 9px; }}
QCheckBox::indicator {{ width: 17px; height: 17px; }}
QCheckBox::indicator:unchecked {{ border: 1px solid {g['LINE2']}; border-radius: 5px; background: {g['BG3']}; }}
QCheckBox::indicator:unchecked:hover {{ border-color: {acc}; }}
QCheckBox::indicator:checked {{ border: 1px solid {acc}; border-radius: 5px; background: {acc};
  image: url("{A}/check.png"); }}
QCheckBox::indicator:disabled {{ border-color: {g['LINE']}; background: {g['BG2']}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {g['DISABLED']}; }}
QRadioButton::indicator {{ width: 15px; height: 15px; border-radius: 8px;
  border: 1px solid {g['LINE2']}; background: {g['BG3']}; }}
QRadioButton::indicator:checked {{ border: 4px solid {acc}; background: {g['STRONG'] if not g['LIGHT'] else '#FFFFFF'}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 2px; }}
QScrollBar::handle:vertical {{ background: {g['LINE2']}; border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {g['MUTED']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 3px; }}
QScrollBar::handle:horizontal {{ background: {g['LINE2']}; border-radius: 3px; min-width: 36px; }}
QScrollBar::handle:horizontal:hover {{ background: {g['MUTED']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {g['BG4']}; color: {g['TEXT']}; border: 1px solid {g['LINE2']};
            border-radius: 6px; padding: 6px 9px; }}
QStatusBar {{ background: {g['BG']}; color: {g['MUTED']}; border-top: 1px solid {g['LINE']}; }}
QStatusBar QLabel {{ font-size: 11px; color: {g['MUTED']}; }}
QStatusBar::item {{ border: none; }}
QLabel#StatusChip {{ color: {g['TEXT2']}; font-size: 11px; padding: 2px 8px; }}
QMenu {{ background: {g['BG3']}; border: 1px solid {g['LINE2']}; padding: 6px; border-radius: 10px; }}
QMenu::item {{ padding: 7px 26px 7px 12px; color: {g['TEXT']}; border-radius: 6px; }}
QMenu::item:selected {{ background: {g['SEL']}; color: {g['STRONG']}; }}
QMenu::item:disabled {{ color: {g['DISABLED']}; }}
QMenu::separator {{ height: 1px; background: {g['LINE']}; margin: 5px 8px; }}
QMessageBox, QDialog, QInputDialog {{ background: {g['BG2']}; }}
QMessageBox QLabel {{ color: {g['TEXT']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSplitter::handle {{ background: {g['LINE']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
"""


def qss(assets_dir: str, palette: dict = None) -> str:
    """Готовый стиль; assets_dir подставляется в url(...) для стрелок и галочек."""
    return _qss(assets_dir.replace("\\", "/"), palette or P)
