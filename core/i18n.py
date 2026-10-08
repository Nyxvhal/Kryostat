"""Локализация Kryostat.

Исходный язык интерфейса — русский. Переводы лежат в assets/locales/<код>.json
({"meta": {...}, "strings": {"русская строка": "перевод"}}).

Язык выбирается автоматически по языку интерфейса Windows (или вручную в настройках).
Если для языка системы нет перевода — используется английский.

tr(s) переводит не только точные строки, но и составные: f-строки описаны в каталоге
шаблонами с {0}, {1}…, единицы измерения (КБ/с, ГГц, мин…) переводятся отдельно,
HTML-разметка сохраняется, строки вида «A · B — C» переводятся по частям."""
import json
import locale
import os
import re
import sys

SOURCE = "ru"
FALLBACK = "en"

_CYR = re.compile("[А-Яа-яЁё]")
_state = {"lang": SOURCE, "identity": True}
_map: dict = {}
_lower: dict = {}
_anchors: dict = {}            # литерал-якорь -> [(literal_len, regex, translation)]
_cache: dict = {}
_CACHE_MAX = 20000
_missing = set()


def _locales_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", "locales")


# ---------------------------------------------------------------- языки
def available():
    """{код: родное название} — все языки, для которых есть перевод (+ исходный русский)."""
    out = {SOURCE: "Русский"}
    d = _locales_dir()
    try:
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                code = f[:-5]
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        meta = json.load(fh).get("meta") or {}
                    out[code] = meta.get("native") or code
                except Exception:
                    continue
    except OSError:
        pass
    return out


def system_language():
    """Двухбуквенный код языка интерфейса ОС."""
    code = None
    if sys.platform == "win32":
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            code = locale.windows_locale.get(langid)
        except Exception:
            code = None
    if not code:
        for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
            v = os.environ.get(var)
            if v and v.split(".")[0] not in ("C", "POSIX"):
                code = v.split(":")[0]
                break
    if not code:
        try:
            code = locale.getlocale()[0]
        except Exception:
            code = None
    code = (code or "en").replace("-", "_").split("_")[0].split(".")[0].lower()
    return "en" if not code or code in ("c", "posix") else code


def resolve(setting="auto"):
    """Какой язык будет использован: явный выбор → язык системы → английский."""
    have = available()
    lang = system_language() if not setting or setting == "auto" else setting
    if lang in have:
        return lang
    return FALLBACK if FALLBACK in have else SOURCE


def current():
    return _state["lang"]


def is_source():
    return _state["identity"]


# ------------------------------------------------------------ единицы
_UNITS = {
    "en": {"Б": "B", "КБ": "KB", "МБ": "MB", "ГБ": "GB", "ТБ": "TB", "ПБ": "PB",
           "КБит": "Kbit", "Мбит": "Mbit", "ГГц": "GHz", "МГц": "MHz", "мс": "ms",
           "с": "s", "сек": "s", "мин": "min", "ч": "h", "д": "d", "дн.": "days", "дн": "d",
           "шт.": "pcs", "ядер": "cores", "потоков": "threads"},
}
_UNIT_RX = re.compile(r"(?<=[\d}])(\s?)(КБит|Мбит|ГГц|МГц|КБ|МБ|ГБ|ТБ|ПБ|Б|мс|сек|мин|дн\.|дн|ч|д|с|шт\.)"
                      r"(/с)?(?![А-Яа-яЁё])")


_PER_S = re.compile(r"(?<=[}\dA-Za-z])/с(?![А-Яа-яЁё])")


def _units(s):
    table = _state.get("units")
    if not table:
        return s

    def rep(m):
        u = table.get(m.group(2), m.group(2))
        return m.group(1) + u + ("/" + table.get("с", "s") if m.group(3) else "")
    s = _UNIT_RX.sub(rep, s)
    return _PER_S.sub("/" + table.get("с", "s"), s)


# ------------------------------------------------------------ каталог
def _compile_template(key, val):
    parts = re.split(r"(\{\d+\})", key)
    literals = [p for p in parts if p and not re.fullmatch(r"\{\d+\}", p)]
    if not any(_CYR.search(p) for p in literals):
        return None
    rx, order = "", []
    for p in parts:
        m = re.fullmatch(r"\{(\d+)\}", p)
        if m:
            order.append(int(m.group(1)))
            rx += "(.*?)"
        elif p:
            rx += re.escape(p.replace("{{", "{").replace("}}", "}"))
    anchor = max(literals, key=len)
    lit_len = sum(len(x) for x in literals)
    try:
        return anchor, lit_len, re.compile(rx, re.S), order, val
    except re.error:
        return None


def load(lang):
    """Загрузить каталог; для исходного языка — ничего не делать."""
    _map.clear(); _lower.clear(); _anchors.clear(); _cache.clear()
    _state["lang"] = lang
    _state["identity"] = lang == SOURCE
    _state["units"] = None
    if lang == SOURCE:
        return
    strings = {}
    for code in ([FALLBACK, lang] if lang != FALLBACK else [lang]):
        try:
            with open(os.path.join(_locales_dir(), code + ".json"), encoding="utf-8") as f:
                data = json.load(f)
            strings.update({k: v for k, v in (data.get("strings") or {}).items() if v})
            if data.get("units"):
                _state["units"] = data["units"]
        except Exception:
            continue
    if not _state["units"]:
        _state["units"] = _UNITS.get(lang) or _UNITS["en"]
    for k, v in strings.items():
        if re.search(r"\{\d+\}", k):
            for key in {k, _units(k)}:
                t = _compile_template(key, v)
                if t:
                    anchor, lit_len, rx, order, val = t
                    _anchors.setdefault(anchor, []).append((lit_len, rx, order, val))
        else:
            k2 = k.replace("{{", "{").replace("}}", "}")
            v2 = v.replace("{{", "{").replace("}}", "}")
            _map[k2] = v2
            _map.setdefault(_units(k2), v2)
            _lower.setdefault(k2.lower(), v2)
    for lst in _anchors.values():
        lst.sort(key=lambda x: -x[0])


# ---------------------------------------------------------- перевод
_TAG = re.compile(r"<[A-Za-z/!][^>]*>")
_EDGE = re.compile(r"^([^\wА-Яа-яЁё]*)(.*?)([^\wА-Яа-яЁё]*)$", re.S)
_PAREN = re.compile(r"^(.*?)(\s*)\((.+)\)$", re.S)
_SEPS = ("\n", " · ", " — ", " – ", " | ", " / ", ": ", "; ", ", ", " → ")


def _case_like(src, val):
    if src.isupper() and len(src) > 1:
        return val.upper()
    if src[:1].isupper() and val[:1].islower():
        return val[:1].upper() + val[1:]
    if src[:1].islower() and val[:1].isupper() and not val[:2].isupper():
        return val[:1].lower() + val[1:]
    return val


def _templates(s, depth):
    cands = []
    for anchor, lst in _anchors.items():
        if anchor in s:
            cands.extend(lst)
    if not cands:
        return None
    cands.sort(key=lambda x: -x[0])
    for _n, rx, order, val in cands:
        m = rx.fullmatch(s)
        if not m:
            continue
        groups = {}
        for idx, g in zip(order, m.groups()):
            groups[idx] = _tr(g, depth + 1) if _CYR.search(g) else g
        try:
            out = re.sub(r"(?<!\{)\{(\d+)\}(?!\})", lambda mm: groups.get(int(mm.group(1)), mm.group(0)), val)
            return out.replace("{{", "{").replace("}}", "}")
        except Exception:
            return None
    return None


def _tr(s, depth=0):
    if not s or depth > 6 or not _CYR.search(s):
        return s
    v = _map.get(s)
    if v is not None:
        return v
    # пробелы по краям
    st = s.strip()
    if st != s:
        i = s.find(st)
        return s[:i] + _tr(st, depth + 1) + s[i + len(st):]
    # HTML: переводим только текст между тегами
    if "<" in s and _TAG.search(s):
        pieces = _TAG.split(s)
        tags = _TAG.findall(s)
        out = []
        for i, p in enumerate(pieces):
            out.append(_tr(p, depth + 1) if p.strip() else p)
            if i < len(tags):
                out.append(tags[i])
        return "".join(out)
    # единицы измерения
    u = _units(s)
    if u != s:
        if not _CYR.search(u):
            return u
        v = _map.get(u)
        if v is not None:
            return v
        s = u
    # регистр
    v = _lower.get(s.lower())
    if v is not None:
        return _case_like(s, v)
    # шаблоны (f-строки)
    v = _templates(s, depth)
    if v is not None:
        return v
    # символы по краям («● Текст», «Текст…», «// ТЕКСТ»)
    m = _EDGE.match(s)
    if m and (m.group(1) or m.group(3)) and m.group(2):
        inner = _tr(m.group(2), depth + 1)
        if inner != m.group(2):
            return m.group(1) + inner + m.group(3)
    # скобки в конце: «Текст (пояснение)»
    m = _PAREN.match(s)
    if m and m.group(1):
        a, b = _tr(m.group(1), depth + 1), _tr(m.group(3), depth + 1)
        if a != m.group(1) or b != m.group(3):
            return f"{a}{m.group(2)}({b})"
    # по частям
    for sep in _SEPS:
        if sep in s:
            parts = s.split(sep)
            tp = [_tr(p, depth + 1) for p in parts]
            if tp != parts:
                return sep.join(tp)
    if depth == 0 and os.environ.get("KRYOSTAT_I18N_MISSING"):
        _missing.add(s)
    return s


def tr(s):
    """Перевести строку интерфейса на текущий язык. Безопасно для любых значений."""
    if _state["identity"] or type(s) is not str or not s:
        return s
    r = _cache.get(s)
    if r is not None:
        return r
    if not _CYR.search(s):
        return s
    r = _tr(s)
    if len(_cache) > _CACHE_MAX:
        _cache.clear()
    _cache[s] = r
    return r


def tr_list(items):
    if _state["identity"]:
        return items
    return [tr(x) if type(x) is str else x for x in items]


def missing():
    return sorted(_missing)


def init(setting=None):
    """Определить и загрузить язык. setting: 'auto' | код языка | None (из config)."""
    if setting is None:
        try:
            from . import config
            setting = config.load().get("language", "auto")
        except Exception:
            setting = "auto"
    lang = resolve(setting)
    load(lang)
    return lang
