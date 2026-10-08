"""Оптимизация Windows: обратимые твики + разовые действия обслуживания."""
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QHeaderView, QMessageBox,
                               QPlainTextEdit, QPushButton, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)
from core import admin, config, tweaks
from .icons import icon
from .theme import OK, AMBER, GREEN, MUTED, PURPLE, RED, TEXT, TEXT2
from .widgets import Card, RowDelegate, SearchBox, muted, pill, title, tool_button
from .workers import run_async


class RunWorker(QThread):
    progress = Signal(str)
    done = Signal(int, int, list)

    def __init__(self, ids, enable):
        super().__init__()
        self.ids, self.enable = ids, enable

    def run(self):
        log, good = [], 0
        for tid in self.ids:
            t = tweaks.BY_ID.get(tid)
            self.progress.emit(f'→ {t[1] if t else tid}')
            try:
                ok, msg = tweaks.apply(tid, self.enable)
            except Exception as e:
                ok, msg = False, str(e)
            good += int(ok)
            log.append(f'[{tid}] {"OK" if ok else "ОШИБКА"}\n{msg}')
        self.done.emit(good, len(self.ids), log)


class TweaksPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.worker = None
        self._action_busy = set()
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(title("Оптимизация Windows"))
        head.addStretch(1)
        self.pill_info = pill("—", MUTED)
        head.addWidget(self.pill_info)
        v.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._tweaks_tab(), icon("zap", MUTED, 15), "Твики")
        self.tabs.addTab(self._actions_tab(), icon("trash", MUTED, 15), "Очистка и обслуживание")
        v.addWidget(self.tabs, 1)
        self.reload()

    # ------------------------------------------------------------ твики
    def _tweaks_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(12)
        v.addWidget(muted(
            f"{len(tweaks.TWEAKS)} оптимизаций. Каждая выполняет команды (sc, reg, schtasks, "
            "powercfg) и имеет обратную команду — всё откатывается. Янтарным отмечены пункты "
            "с побочными эффектами. Перед большими изменениями создайте точку восстановления "
            "на вкладке «Очистка и обслуживание»."))
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = SearchBox("Поиск оптимизации…")
        self.search.textChanged.connect(self._filter)
        bar.addWidget(self.search, 1)
        self.btn_apply = tool_button("Применить отмеченные", "zap", "Primary",
                                     lambda: self.run(True))
        self.btn_undo = tool_button("Откатить отмеченные", "refresh", "Danger",
                                    lambda: self.run(False))
        bar.addWidget(self.btn_apply)
        bar.addWidget(self.btn_undo)
        bar.addWidget(tool_button("Рекомендуемые", "shield", "Magenta", self.select_recommended))
        bar.addWidget(tool_button("Снять отметки", on_click=lambda: self._check_all(False)))
        v.addLayout(bar)

        cfg = config.load()
        self.auto = QCheckBox("Применять применённые твики заново при каждом запуске Kryostat "
                              "(Windows иногда возвращает настройки после обновлений)")
        self.auto.setChecked(cfg.get("auto_apply_tweaks", False))
        self.auto.toggled.connect(self._save_auto)
        v.addWidget(self.auto)

        self.tree = QTreeWidget()
        self.tree.setItemDelegate(RowDelegate(10, self.tree))
        self.tree.setHeaderLabels(["Оптимизация", "Что делает", "Состояние"])
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 360)
        self.tree.setColumnWidth(2, 130)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.currentItemChanged.connect(self._show_cmds)
        self.tree.itemChanged.connect(self._sync_twins)
        v.addWidget(self.tree, 3)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(150)
        self.log.setPlaceholderText("Выберите пункт, чтобы увидеть его команды")
        v.addWidget(self.log, 1)
        return w

    def _save_auto(self, on):
        config.update(lambda cfg: cfg.__setitem__("auto_apply_tweaks", bool(on)))

    def reload(self):
        applied = set(config.load().get("tweaks_applied") or [])
        checked = {n.data(0, Qt.UserRole) for n in self._nodes() if n.checkState(0) == Qt.Checked}
        self._syncing = True
        self.tree.clear()
        bold = QFont(); bold.setBold(True)
        for cat, ids in tweaks.CATEGORIES.items():
            ids = [i for i in ids if i in tweaks.BY_ID]
            if not ids:
                continue
            n_on = sum(1 for i in ids if i in applied)
            parent = QTreeWidgetItem([cat, f"{len(ids)} пунктов", f"{n_on}/{len(ids)}"])
            parent.setFont(0, bold)
            parent.setForeground(0, QBrush(QColor(GREEN)))
            parent.setForeground(1, QBrush(QColor(MUTED)))
            parent.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(True)
            for tid in ids:
                t = tweaks.BY_ID[tid]
                risky = tweaks.RISKY.get(tid)
                note = ""
                if tid in tweaks.NEEDS_REBOOT:
                    note = " · нужна перезагрузка"
                elif tid in tweaks.NEEDS_EXPLORER:
                    note = " · перезапуск Проводника"
                node = QTreeWidgetItem([t[1], t[2] + note,
                                        "● применено" if tid in applied else "—"])
                node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                node.setCheckState(0, Qt.Checked if tid in checked else Qt.Unchecked)
                node.setData(0, Qt.UserRole, tid)
                if risky:
                    node.setForeground(0, QBrush(QColor(AMBER)))
                    node.setToolTip(0, "Побочный эффект: " + risky)
                node.setForeground(2, QBrush(QColor(OK if tid in applied else MUTED)))
                parent.addChild(node)
        self._syncing = False
        self._filter(self.search.text())
        self.pill_info.setText(f"{len(applied)} из {len(tweaks.TWEAKS)} применено")

    def _filter(self, text):
        q = (text or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            p = self.tree.topLevelItem(i)
            vis = 0
            for j in range(p.childCount()):
                c = p.child(j)
                hide = bool(q) and q not in (c.text(0) + " " + c.text(1)).lower()
                c.setHidden(hide)
                vis += not hide
            p.setHidden(vis == 0)

    def _nodes(self):
        for i in range(self.tree.topLevelItemCount()):
            p = self.tree.topLevelItem(i)
            for j in range(p.childCount()):
                yield p.child(j)

    def _sync_twins(self, node, col):
        """Пункт может быть в нескольких категориях — галочка одна на всех."""
        if getattr(self, "_syncing", False) or col != 0 or not node.data(0, Qt.UserRole):
            return
        self._syncing = True
        tid, st = node.data(0, Qt.UserRole), node.checkState(0)
        for n in self._nodes():
            if n is not node and n.data(0, Qt.UserRole) == tid:
                n.setCheckState(0, st)
        self._syncing = False

    def _check_all(self, on):
        self._syncing = True
        for n in self._nodes():
            n.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self._syncing = False

    def select_recommended(self):
        self._syncing = True
        for n in self._nodes():
            n.setCheckState(0, Qt.Checked if n.data(0, Qt.UserRole) in tweaks.RECOMMENDED
                            else Qt.Unchecked)
        self._syncing = False
        self.state["toast"]("Отмечен безопасный набор — проверьте и нажмите «Применить»")

    def _show_cmds(self, cur, _prev=None):
        if not cur:
            return
        t = tweaks.BY_ID.get(cur.data(0, Qt.UserRole))
        if not t:
            self.log.setPlainText("")
            return
        risky = tweaks.RISKY.get(t[0])
        head = f"# {t[1]}\n" + (f"# ВНИМАНИЕ: {risky}\n" if risky else "")
        self.log.setPlainText(head + "\n# Применение:\n" + "\n".join(t[3])
                              + "\n\n# Откат:\n" + "\n".join(t[4]))

    def run(self, enable: bool):
        ids = list(dict.fromkeys(n.data(0, Qt.UserRole) for n in self._nodes()
                                 if n.checkState(0) == Qt.Checked))
        if not ids:
            self.state["toast"]("Отметьте хотя бы один пункт", "warn")
            return
        if not admin.is_admin():
            self.state["toast"]("Нужны права администратора — перезапустите Kryostat "
                                "от имени администратора", "error")
            return
        if enable and any(i in tweaks.RISKY for i in ids):
            notes = "\n".join(f'• {tweaks.BY_ID[i][1]} — {tweaks.RISKY[i]}'
                              for i in ids if i in tweaks.RISKY)
            if QMessageBox.question(
                    self, "Подтверждение",
                    f"Среди выбранного есть пункты с побочными эффектами:\n\n{notes}\n\n"
                    f"Продолжить?") != QMessageBox.Yes:
                return
        self.btn_apply.setEnabled(False)
        self.btn_undo.setEnabled(False)
        self.log.setPlainText("Выполняю…\n")
        self._run_enable = enable
        self._run_ids = ids
        self.worker = RunWorker(ids, enable)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_progress(self, text):
        self.log.appendPlainText(text)

    def _on_done(self, good, n, log):
        self.btn_apply.setEnabled(True)
        self.btn_undo.setEnabled(True)
        self.log.setPlainText("\n\n".join(log))
        self.reload()
        what = "Применено" if self._run_enable else "Откатано"
        if good < n:
            self.state["toast"](f"{what}: {good} из {n}. Подробности — внизу", "warn")
        else:
            self.state["toast"](f"{what} пунктов: {n}")
        ids = set(self._run_ids)
        if ids & tweaks.NEEDS_REBOOT:
            self.state["toast"]("Часть изменений вступит в силу после перезагрузки", "warn")
        elif ids & tweaks.NEEDS_EXPLORER and QMessageBox.question(
                self, "Проводник", "Перезапустить Проводник, чтобы применить изменения "
                                   "интерфейса сейчас?") == QMessageBox.Yes:
            run_async(tweaks.restart_explorer)

    # ------------------------------------------------------------ обслуживание
    def _actions_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(12)
        v.addWidget(muted("Разовые действия. Всё, что выполняется, видно в разделе «Терминал»."))
        grid = QGridLayout()
        grid.setSpacing(10)
        self.action_btns = {}
        for i, (aid, name, desc, _cmd) in enumerate(tweaks.ACTIONS):
            b = QPushButton(f"{name}\n{desc}")
            b.setObjectName("Tile")
            b.setMinimumHeight(64)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(_cmd)
            b.clicked.connect(lambda _=False, a=aid: self.run_action(a))
            grid.addWidget(b, i // 3, i % 3)
            self.action_btns[aid] = b
        v.addLayout(grid)
        self.alog = QPlainTextEdit(readOnly=True)
        self.alog.setPlaceholderText("Результат последнего действия")
        v.addWidget(self.alog, 1)
        return w

    def run_action(self, aid):
        if aid in self._action_busy:
            return
        if not admin.is_admin() and aid not in ("dns", "temp", "recycle", "explorer"):
            self.state["toast"]("Нужны права администратора", "error")
            return
        name = tweaks.ACTIONS_BY_ID[aid][1]
        self._action_busy.add(aid)
        btn = self.action_btns[aid]
        btn.setEnabled(False)
        self.alog.appendPlainText(f"→ {name}…")

        def done(res):
            self._action_busy.discard(aid)
            btn.setEnabled(True)
            if isinstance(res, Exception):
                self.alog.appendPlainText(f"✗ {name}: {res}\n")
                self.state["toast"](f"{name}: ошибка", "error")
                return
            ok, out = res
            self.alog.appendPlainText(("✓ " if ok else "✗ ") + name + "\n" + out.strip()[-800:] + "\n")
            self.state["toast"](f"{name}: {'готово' if ok else 'с ошибками'}",
                                "info" if ok else "warn")
        run_async(lambda: tweaks.run_action(aid), done)
