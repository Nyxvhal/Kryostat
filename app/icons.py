"""Векторные иконки интерфейса (рисуются в коде, без внешних файлов)."""
from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_P = {
    "gauge": "M12 21a9 9 0 1 1 9-9M12 12l5-3",
    "list": "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
    "power": "M12 3v9M18.4 6.6a9 9 0 1 1-12.8 0",
    "sliders": "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
    "zap": "M13 2 3 14h9l-1 8 10-12h-9z",
    "refresh": "M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.8-3.4L23 10M1 14l4.7 4.4A9 9 0 0 0 20.5 15",
    "settings": "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"
                "M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3"
                " 1.6 1.6 0 0 0-1 1.5v.2a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3"
                "l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H2a2 2 0 1 1 0-4h.1"
                "a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3"
                "H8a1.6 1.6 0 0 0 1-1.5V2a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1"
                "a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V8a1.6 1.6 0 0 0 1.5 1h.2a2 2 0 1 1 0 4h-.1"
                "a1.6 1.6 0 0 0-1.5 1z",
    "search": "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
    "trash": "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6",
    "shield": "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
    "cpu": "M4 4h16v16H4zM9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3",
    "chip": "M6 6h12v12H6z M2 10h4M2 14h4M18 10h4M18 14h4M10 2v4M14 2v4M10 18v4M14 18v4",
    "hdd": "M3 15h18M6 19h.01M10 19h.01M4 15l2.5-9h11L20 15v4H4z",
    "wifi": "M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 20h.01M1.5 9a15 15 0 0 1 21 0",
    "clock": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
    "x": "M18 6 6 18M6 6l12 12",
    "check": "M20 6 9 17l-5-5",
    "play": "M6 4l14 8-14 8z",
    "stop": "M6 6h12v12H6z",
    "download": "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
    "alert": "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
    "copy": "M9 9h11v11H9zM5 15H4V4h11v1",
    "minus": "M5 12h14",
    "plus": "M12 5v14M5 12h14",
    "package": "M21 8 12 3 3 8v8l9 5 9-5zM3 8l9 5 9-5M12 13v8",
    "loader": "M12 3a9 9 0 1 0 9 9",
    "win_min": "M5 12.5h14",
    "win_max": "M5.5 5.5h13v13h-13z",
    "win_restore": "M8.5 8.5h10v10h-10zM5.5 15.5v-10h10",
    "win_close": "M6 6l12 12M18 6 6 18",
    "terminal": "M3 4h18v16H3zM7 9l3 3-3 3M12 15h5",
    "monitor": "M3 4h18v12H3zM8 20h8M12 16v4",
    "registry": "M3 3h7v7H3zM3 14h7v7H3zM14 14h7v7h-7zM17.5 2.5l4 4-4 4-4-4z",
    "chev_left": "M15 18l-6-6 6-6",
    "chev_right": "M9 18l6-6-6-6",
    "palette": "M12 22a10 10 0 1 1 10-10c0 2.8-2.2 4-4 4h-2a2 2 0 0 0-1.5 3.3A1.7 1.7 0 0 1 12 22z"
               "M7.5 10.5h.01M10.5 7h.01M15 7.5h.01M17 11h.01",
    "globe": "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20",
    "command": "M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z",
    "enter": "M9 10l-5 5 5 5M20 4v7a4 4 0 0 1-4 4H4",
    "folder": "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z",
}

_cache: dict[tuple, QIcon] = {}


def icon(name: str, color: str = "#8FA79A", size: int = 20, stroke: float = 1.8) -> QIcon:
    key = (name, color, size, stroke)
    if key in _cache:
        return _cache[key]
    path = _P.get(name, _P["list"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
           f'fill="none" stroke="{color}" stroke-width="{stroke}" '
           f'stroke-linecap="round" stroke-linejoin="round"><path d="{path}"/></svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    scale = 2                                   # рисуем в 2x — чёткие иконки на HiDPI
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.transparent)
    pr = QPainter(pm)
    pr.setRenderHint(QPainter.Antialiasing)
    renderer.render(pr, QRectF(0, 0, size, size))
    pr.end()
    ic = QIcon(pm)
    _cache[key] = ic
    return ic
