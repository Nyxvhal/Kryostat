"""Ежедневный автоперенос обновлений Windows."""
import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QHBoxLayout, QLabel,
                               QSpinBox, QVBoxLayout, QWidget)
from core import admin, config, winupdate
from .theme import OK, AMBER, GREEN, MUTED, RED
from .widgets import Card, muted, pill, pill_style, title, tool_button
from .workers import run_async


class UpdatesPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._loading = False
        # изменения спинбокса копим 700 мс, иначе каждый щелчок запускал бы запись в реестр
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(700)
        self._debounce.timeout.connect(self._save_now)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(title("Обновления Windows"))
        head.addStretch(1)
        self.pill_state = pill("—", MUTED)
        head.addWidget(self.pill_state)
        v.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)

        card = Card("Автопродление паузы")
        cfg = config.load()["update_defer"]
        form = QFormLayout()
        form.setSpacing(11)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.enabled = QCheckBox("Продлевать автоматически каждый день")
        self.enabled.setChecked(bool(cfg.get("enabled")))
        self.enabled.toggled.connect(self._schedule_save)
        self.days = QSpinBox()
        self.days.setRange(1, 35)
        self.days.setSuffix(" дн.")
        self.days.setValue(int(cfg.get("days_ahead", 1)))
        self.days.setFixedWidth(140)
        self.days.valueChanged.connect(self._schedule_save)
        form.addRow("Автопродление", self.enabled)
        form.addRow("Сдвигать на", self.days)
        card.v.addLayout(form)
        btns = QHBoxLayout()
        btns.addWidget(tool_button("Продлить сейчас", "clock", "Primary", self.extend))
        btns.addWidget(tool_button("Снять паузу", "refresh", "Danger", self.reset))
        btns.addWidget(tool_button("Центр обновления", "settings",
                                   on_click=lambda: admin.run("start ms-settings:windowsupdate")))
        btns.addStretch(1)
        card.v.addLayout(btns)
        card.v.addStretch(1)
        body.addWidget(card, 1)

        status = Card("Состояние")
        self.lbl = QLabel("—")
        self.lbl.setWordWrap(True)
        self.lbl.setTextFormat(Qt.RichText)
        status.v.addWidget(self.lbl)
        status.v.addStretch(1)
        body.addWidget(status, 1)
        v.addLayout(body)

        note = Card("Как это работает")
        note.v.addWidget(muted(
            "Kryostat раз в сутки переставляет дату окончания паузы обновлений вперёд, "
            "поэтому Windows не скачивает обновления и не перезагружает компьютер "
            "в неудобный момент.\n\n"
            "Windows разрешает приостановку максимум на 35 дней. Когда лимит исчерпан, "
            "нужно один раз установить накопившиеся обновления — после этого отсчёт "
            "начнётся заново, и автопродление продолжит работать.\n\n"
            "Обновления безопасности лучше не откладывать бесконечно: именно они "
            "закрывают уязвимости, через которые работают шифровальщики."))
        v.addWidget(note)
        v.addStretch(1)
        self.reload()

    def _schedule_save(self, *_):
        if not self._loading:
            self._debounce.start()

    def _save_now(self):
        if self._loading:
            return
        on, days = self.enabled.isChecked(), self.days.value()

        def _mut(cfg):
            cfg["update_defer"]["enabled"] = on
            cfg["update_defer"]["days_ahead"] = days
        config.update(_mut)
        if on:
            if not admin.is_admin():
                self.state["toast"]("Нужны права администратора — настройка сохранена, "
                                    "но пауза не применена", "error")
            else:
                self._extend_async(days)
        self.reload()

    def _extend_async(self, days):
        def done(res):
            if isinstance(res, Exception):
                self.state["toast"](f"Ошибка: {res}", "error")
            else:
                ok, msg = res
                self.state["toast"](msg[:160], "info" if ok else "error")
            self.reload()
        run_async(lambda: winupdate.extend(days), done)

    def extend(self):
        if not admin.is_admin():
            self.state["toast"]("Изменение политик обновления требует прав администратора",
                                "error")
            return
        self._extend_async(self.days.value())

    def reset(self):
        if not admin.is_admin():
            self.state["toast"]("Нужны права администратора", "error")
            return
        self._debounce.stop()

        def done(res):
            self._loading = True
            self.enabled.setChecked(False)
            self._loading = False
            self.state["toast"](res[1] if not isinstance(res, Exception) else f"Ошибка: {res}")
            self.reload()
        run_async(winupdate.reset, done)

    @staticmethod
    def _fmt_until(raw):
        if not raw:
            return "не установлена"
        try:
            dt = datetime.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
            return dt.replace(tzinfo=datetime.timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return raw

    def reload(self):
        """reg query выполняется в фоне — переход на страницу не подвисает."""
        if getattr(self, "_loading", False):
            return
        self._loading = True

        def done(res):
            self._loading = False
            if not isinstance(res, Exception):
                self._render(res)
        run_async(winupdate.status, done)

    def _render(self, s):
        on = s["enabled"]
        self.pill_state.setText("автопродление включено" if on else "выключено")
        self.pill_state.setStyleSheet(pill_style(OK if on else MUTED))
        self.lbl.setText(
            f'<table cellpadding="5">'
            f'<tr><td style="color:{MUTED}">Автопродление</td>'
            f'<td><b style="color:{OK if on else MUTED}">'
            f'{"включено" if on else "выключено"}</b></td></tr>'
            f'<tr><td style="color:{MUTED}">Шаг продления</td>'
            f'<td>{s["days_ahead"]} дн.</td></tr>'
            f'<tr><td style="color:{MUTED}">Последний запуск</td>'
            f'<td>{s["last_run"] or "ещё не выполнялось"}</td></tr>'
            f'<tr><td style="color:{MUTED}">Пауза действует до</td>'
            f'<td>{self._fmt_until(s["paused_until"])}</td></tr>'
            f'<tr><td style="color:{MUTED}">Права</td>'
            f'<td>{"администратор" if admin.is_admin() else f"<span style=color:{RED}>обычные</span>"}'
            f'</td></tr></table>')
