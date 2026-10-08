"""Терминал с вкладками.

Первая вкладка «Kryostat» — журнал самого приложения: какие команды он выполняет,
что они ответили, что изменено в реестре. Остальные вкладки — консоли пользователя
(PowerShell, PowerShell 7, cmd, Git Bash), каждая со своей папкой, историей и правами:
как у Kryostat, от администратора или от обычного пользователя."""
import codecs
import os
import secrets
import struct
import time

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QFont, QKeySequence, QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtNetwork import QHostAddress, QTcpServer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMenu, QPlainTextEdit, QProgressBar, QSizePolicy, QTabBar, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)
from core import admin, cmdlog, config, shellproc
from .icons import icon
from .theme import ACCENT_LIGHT, AMBER, BLUE, MUTED, OK, PURPLE, RED, TEXT, TEXT2
from .widgets import muted, pill, pill_style, title, tool_button
from .workers import run_async

MAX_LINES = 8000
HISTORY_MAX = 300
RIGHTS = [("same", "Как у Kryostat"), ("admin", "Администратор"), ("user", "Обычный пользователь")]


_MONO = None


def _dur(sec):
    """0.4 с · 12 с · 3:05 · 1:02:03"""
    if sec < 10:
        return f"{sec:.1f} с".replace(".", ",")
    sec = int(sec)
    if sec < 60:
        return f"{sec} с"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _short_cwd(cwd, keep=2):
    """C:\\Users\\me\\Projects\\site → …\\Projects\\site (диск и короткие пути — как есть)"""
    sep = "\\" if "\\" in cwd else "/"
    parts = [x for x in cwd.split(sep) if x]
    if len(parts) <= keep + 1:
        return cwd
    return "…" + sep + sep.join(parts[-keep:])


def _mono():
    """Один общий моноширинный шрифт на все консоли (раньше создавался в каждой вкладке)."""
    global _MONO
    if _MONO is None:
        f = QFont("Cascadia Mono")
        f.setStyleHint(QFont.Monospace)
        f.setFamilies(["Cascadia Mono", "Consolas", "Courier New", "monospace"])
        f.setPointSize(10)
        _MONO = f
    return _MONO


_GRAVE = set()


def _bury(p: QProcess):
    """Завершить процесс, не блокируя интерфейс.
    Если удалить QProcess, пока процесс жив, Qt синхронно ждёт его завершения
    (до 30 с) — отсюда были «подвисания» при закрытии и перезапуске вкладок."""
    if p.state() == QProcess.NotRunning:
        p.deleteLater()
        return
    app = QCoreApplication.instance()
    p.setParent(app)
    _GRAVE.add(p)

    def gone(*_):
        if p in _GRAVE:                       # finished и errorOccurred могут прийти оба
            _GRAVE.discard(p)
            try:
                p.deleteLater()
            except RuntimeError:              # уже удалён при выходе из приложения
                pass
    p.finished.connect(gone)
    p.errorOccurred.connect(gone)
    p.kill()


# ======================================================== транспорты процесса
class _Transport(QObject):
    data = Signal(bytes, bool)        # данные, это stderr
    exited = Signal(int)
    failed = Signal(str)
    started = Signal()

    def write(self, b: bytes): ...
    def kill(self): ...
    def pid(self): return 0


class QProcTransport(_Transport):
    """Обычный дочерний процесс — права как у Kryostat."""

    def __init__(self, prog, args, cwd, parent=None):
        super().__init__(parent)
        self.p = QProcess(self)
        self.p.setWorkingDirectory(cwd)
        self.p.readyReadStandardOutput.connect(
            lambda: self.data.emit(bytes(self.p.readAllStandardOutput()), False))
        self.p.readyReadStandardError.connect(
            lambda: self.data.emit(bytes(self.p.readAllStandardError()), True))
        self.p.finished.connect(lambda code, _s=None: self.exited.emit(int(code)))
        self.p.errorOccurred.connect(
            lambda e: self.failed.emit("Не удалось запустить оболочку")
            if e == QProcess.FailedToStart else None)
        self.p.started.connect(self.started.emit)
        self.p.start(prog, args)

    def kill(self):
        if self.p is None:
            return
        if self.pid() and admin.IS_WINDOWS:     # дерево процессов: дочерние программы тоже
            run_async(lambda pid=self.pid(): admin.run(["taskkill", "/PID", str(pid), "/T", "/F"]))
        for sig in (self.p.readyReadStandardOutput, self.p.readyReadStandardError,
                    self.p.finished, self.p.errorOccurred, self.p.started):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        p, self.p = self.p, None
        _bury(p)

    def write(self, b):
        if self.p is not None:
            self.p.write(b)

    def pid(self):
        return int(self.p.processId() or 0) if self.p is not None else 0


class UnelevatedTransport(_Transport):
    """Kryostat — администратор, а оболочка нужна с обычными правами."""

    def __init__(self, prog, args, cwd, parent=None):
        super().__init__(parent)
        import subprocess
        import threading
        self.proc = None
        try:
            self.proc = shellproc.UnelevatedProcess(subprocess.list2cmdline([prog] + args), cwd)
        except Exception as e:              # e удаляется после except — сохраняем текст
            msg = f"Запуск без прав не удался: {e}"
            QTimer.singleShot(0, lambda m=msg: self.failed.emit(m))
            return

        def pump(stream, err):
            try:
                while True:
                    b = stream.read(65536)
                    if not b:
                        break
                    self.data.emit(b, err)
            except (OSError, ValueError):
                pass

        for st, err in ((self.proc.stdout, False), (self.proc.stderr, True)):
            threading.Thread(target=pump, args=(st, err), daemon=True).start()
        threading.Thread(target=lambda: self.exited.emit(self.proc.wait()), daemon=True).start()
        QTimer.singleShot(0, self.started.emit)

    def write(self, b):
        try:
            self.proc.stdin.write(b)
        except (OSError, ValueError, AttributeError):
            pass

    def pid(self):
        return self.proc.pid if self.proc else 0

    def kill(self):
        if self.proc:
            p = self.proc
            run_async(p.kill)


class ElevatedTransport(_Transport):
    """Kryostat без прав, оболочка — от администратора: UAC → --shell-host → сокет."""

    def __init__(self, key, cwd, parent=None):
        super().__init__(parent)
        self.sock = None
        self._buf = bytearray()
        self._pending = b""
        self._pid = 0
        self.token = secrets.token_hex(16)
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._accept)
        if not self.server.listen(QHostAddress.LocalHost, 0):
            QTimer.singleShot(0, lambda: self.failed.emit("Не удалось открыть локальный порт"))
            return
        if not shellproc.launch_elevated_host(self.server.serverPort(), self.token, key, cwd):
            QTimer.singleShot(0, lambda: self.failed.emit("Повышение прав отменено (UAC)"))
            return
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self.sock is None and self.failed.emit(
            "Оболочка администратора не подключилась за 60 с"))
        self._timeout.start(60_000)

    def _accept(self):
        s = self.server.nextPendingConnection()
        if self.sock is not None or s is None:
            if s:
                s.close()
            return
        s.readyRead.connect(lambda: self._read(s))
        s.disconnected.connect(lambda: self.sock is s and self.exited.emit(-1))
        self._auth = s

    def _read(self, s):
        self._buf += bytes(s.readAll())
        if self.sock is None:
            nl = self._buf.find(b"\n")
            if nl < 0:
                return
            line = bytes(self._buf[:nl])
            del self._buf[:nl + 1]
            if line.decode(errors="ignore").strip() != self.token:
                s.close()
                self._buf.clear()
                return
            self.sock = s
            self.server.close()
            self.started.emit()
            if self._pending:
                self.write(self._pending)
                self._pending = b""
        buf, off = self._buf, 0           # разбор без копирования хвоста на каждом кадре
        while len(buf) - off >= 5:
            ch, n = struct.unpack_from("<BI", buf, off)
            if len(buf) - off < 5 + n:
                break
            data = bytes(buf[off + 5:off + 5 + n])
            off += 5 + n
            if ch == shellproc.CH_EXIT:
                self.exited.emit(struct.unpack("<i", data)[0] if len(data) == 4 else 0)
            else:
                self.data.emit(data, ch == shellproc.CH_ERR)
        if off:
            del buf[:off]

    def write(self, b):
        if self.sock is None:
            self._pending += b
        else:
            self.sock.write(shellproc.frame(shellproc.CH_IN, b))

    def kill(self):
        if self.sock is not None:
            self.sock.write(shellproc.frame(shellproc.CH_KILL, b""))
            self.sock.flush()
            QTimer.singleShot(300, self.sock.close)
        self.server.close()


# ============================================================ оболочка
class Shell(QObject):
    """Постоянная оболочка. После каждой команды печатается маркер с кодом
    возврата и текущей папкой — по нему видно, что команда завершилась."""
    output = Signal(str, bool)
    finished_cmd = Signal(int, str)
    ready = Signal(str)
    died = Signal(int)

    def __init__(self, key, rights="same", cwd=None, parent=None):
        super().__init__(parent)
        self.key, self.label, self.prog, self.args, self.kind = shellproc.shell_by_key(key)
        self.rights = rights
        self.token = f"__KRYO_{secrets.token_hex(4)}__"
        self.cwd = cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~")
        self.busy = False
        self._started = False
        self._tails = {False: "", True: ""}
        self._dec = {False: self._decoder(), True: self._decoder()}
        self.t = None
        self.elevated = admin.is_admin()

    @staticmethod
    def _decoder():
        return codecs.getincrementaldecoder("utf-8")(errors="strict")

    def start(self):
        is_adm = admin.is_admin()
        if self.rights == "admin" and not is_adm and admin.IS_WINDOWS:
            self.t = ElevatedTransport(self.key, self.cwd, self)
            self.elevated = True
        elif self.rights == "user" and is_adm and admin.IS_WINDOWS:
            self.t = UnelevatedTransport(self.prog, self.args, self.cwd, self)
            self.elevated = False
        else:
            self.t = QProcTransport(self.prog, self.args, self.cwd, self)
            self.elevated = is_adm
        self.t.data.connect(self._read)
        self.t.exited.connect(self._on_exit)
        self.t.failed.connect(self._on_fail)
        if self.kind == "ps":
            init = ("try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}; "
                    "$OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'")
        elif self.kind == "cmd":
            init = "chcp 65001 >nul"
        else:
            init = "true"
        self.busy = True
        self._write_line(self._wrap(init))

    def _wrap(self, command: str) -> str:
        t = self.token
        if self.kind == "ps":
            return (f". {{ {command} }}; [Console]::Out.WriteLine('{t}' + "
                    f"$(if ($?) {{ if ($LASTEXITCODE) {{ $LASTEXITCODE }} else {{ 0 }} }} "
                    f"else {{ 1 }}) + '?' + (Get-Location).Path); $global:LASTEXITCODE = 0")
        if self.kind == "cmd":
            return f"{command} & call echo {t}%^errorlevel%?%^cd%"
        return f'{command}; echo "{t}$??$(pwd -W 2>/dev/null || pwd)"'

    def _write_line(self, text):
        self.t.write((text + ("\r\n" if admin.IS_WINDOWS else "\n")).encode("utf-8"))

    def run(self, command):
        self.busy = True
        self._write_line(self._wrap(command))

    def send_input(self, text):
        self._write_line(text)

    def kill(self):
        if self.t:
            for sig in (self.t.data, self.t.exited, self.t.failed):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self.t.kill()

    def _decode(self, data, err):
        try:
            return self._dec[err].decode(data)
        except UnicodeDecodeError:
            self._dec[err] = self._decoder()
            return admin.decode(data)

    def _read(self, data, err):
        if not data:
            return
        text = self._tails[err] + self._decode(data, err).replace("\r\n", "\n").replace("\r", "")
        self._tails[err] = ""
        lines = text.split("\n")
        tail = lines.pop()
        out = []
        for line in lines:
            pos = line.find(self.token)
            if pos >= 0:
                if pos:
                    out.append(line[:pos] + "\n")
                self._flush(out, err)
                out = []
                self._marker(line[pos + len(self.token):])
            else:
                out.append(line + "\n")
        if tail:
            if any(tail.endswith(self.token[:i]) for i in range(1, len(self.token) + 1)) \
                    or self.token in tail:
                self._tails[err] = tail
            else:
                out.append(tail)
        self._flush(out, err)

    def _flush(self, parts, err):
        if parts:
            self.output.emit("".join(parts), err)

    def _marker(self, rest):
        code, _, cwd = rest.partition("?")
        try:
            rc = int(code.strip() or 0)
        except ValueError:
            rc = 0
        if cwd.strip():
            self.cwd = cwd.strip()

        def finish():
            self.busy = False
            if not self._started:
                self._started = True
                self.ready.emit(self.cwd)
            else:
                self.finished_cmd.emit(rc, self.cwd)
        QTimer.singleShot(80, finish)        # stderr приходит отдельным каналом и чуть позже

    def _on_exit(self, code):
        self.busy = False
        self.died.emit(code)

    def _on_fail(self, msg):
        self.output.emit(msg + "\n", True)
        self.busy = False
        self.died.emit(-1)


# ============================================================ вывод
class OutputView(QPlainTextEdit):
    """Быстрый вывод: куски копятся и вставляются пачкой раз в 30 мс.
    Стиль задаётся глобально (#TermOut), а не setStyleSheet на каждом экземпляре —
    это и было главной причиной медленного открытия вкладок."""
    MAX_PENDING = 400_000            # символов за один кадр; больше всё равно не влезет в 8000 строк

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TermOut")
        self.setReadOnly(True)
        self.setMaximumBlockCount(MAX_LINES)
        self.setUndoRedoEnabled(False)
        self.setFont(_mono())
        self.setFrameShape(QPlainTextEdit.NoFrame)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._pending = []
        self._size = 0
        self._fmts = {}
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(30)
        self._t.timeout.connect(self._flush)

    def put(self, text, color=TEXT2):
        if not text:
            return
        self._pending.append((text, color))
        self._size += len(text)
        if self._size > self.MAX_PENDING * 2:          # лавина вывода — оставляем хвост
            keep, total = [], 0
            for t, c in reversed(self._pending):
                keep.append((t, c))
                total += len(t)
                if total >= self.MAX_PENDING:
                    break
            self._pending = keep[::-1]
            self._size = total
        if not self._t.isActive():
            self._t.start()

    def _fmt(self, c):
        f = self._fmts.get(c)
        if f is None:
            f = QTextCharFormat()
            f.setForeground(QColor(c))
            self._fmts[c] = f
        return f

    def _flush(self):
        if not self._pending:
            return
        sb = self.verticalScrollBar()
        bottom = sb.value() >= sb.maximum() - 4
        cur = QTextCursor(self.document())
        cur.movePosition(QTextCursor.End)
        cur.beginEditBlock()
        merged = []
        for t, c in self._pending:
            if merged and merged[-1][1] == c:
                merged[-1][0].append(t)
            else:
                merged.append(([t], c))
        for parts, c in merged:
            cur.insertText("".join(parts), self._fmt(c))
        cur.endEditBlock()
        self._pending.clear()
        self._size = 0
        if bottom:
            sb.setValue(sb.maximum())

    def clear_all(self):
        self._pending.clear()
        self._size = 0
        self.clear()


class _Bridge(QObject):
    event = Signal(dict)


def _box(widget, margins=(0, 0, 0, 0)):
    v = QVBoxLayout(widget)
    v.setContentsMargins(*margins)
    v.setSpacing(0)
    return v


class ActivityTab(QWidget):
    """Вкладка «Kryostat»: только действия самого приложения."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = _box(self)
        bar = QFrame()
        bar.setObjectName("TermInputBar")
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(12, 6, 12, 6)
        bh.setSpacing(14)
        self.c_cmd = QCheckBox("Команды")
        self.c_out = QCheckBox("Вывод команд")
        self.c_reg = QCheckBox("Изменения реестра")
        self.c_log = QCheckBox("Журнал")
        for c in (self.c_cmd, self.c_out, self.c_reg, self.c_log):
            c.setChecked(True)
            bh.addWidget(c)
        bh.addStretch(1)
        self.count = QLabel("команд выполнено: 0")
        self.count.setObjectName("Small")
        bh.addWidget(self.count)
        self.out = OutputView()
        v.addWidget(self.out, 1)
        v.addWidget(bar)
        self.n = 0
        self.bridge = _Bridge()
        self.bridge.event.connect(self.on_event, Qt.QueuedConnection)
        self._listener = self.bridge.event.emit
        for ev in cmdlog.subscribe(self._listener):
            self.on_event(ev)

    def on_event(self, ev):
        k, t = ev.get("kind"), ev.get("time", "")
        if k == "cmd":
            self.n += 1
            self.count.setText(f"команд выполнено: {self.n}")
        if k == "info":
            if self.c_log.isChecked():
                self.out.put(f"[{t}] • {ev['text']}\n", MUTED)
        elif k == "cmd" and self.c_cmd.isChecked():
            self.out.put(f"[{t}] ", MUTED)
            self.out.put("kryostat › ", ACCENT_LIGHT)
            self.out.put(ev["text"] + "\n", AMBER)
        elif k == "action" and self.c_reg.isChecked():
            self.out.put(f"[{t}] ", MUTED)
            self.out.put("kryostat * ", PURPLE)
            self.out.put(ev["text"] + "\n", BLUE)
        elif k == "result" and self.c_cmd.isChecked():
            text = (ev.get("text") or "").rstrip()
            if text and self.c_out.isChecked():
                self.out.put("    " + text.replace("\n", "\n    ") + "\n", TEXT2)
            rc = ev.get("rc")
            self.out.put(f"    └ код {rc}\n", OK if rc == 0 else RED)

    def shutdown(self):
        cmdlog.unsubscribe(self._listener)


class ConsoleTab(QWidget):
    """Одна консоль пользователя. Оболочка запускается при первом показе вкладки:
    восстановленные вкладки больше не стартуют все сразу при открытии терминала."""
    state_changed = Signal(QWidget)
    IDLE_HINT = "команда + Enter   ·   ↑/↓ история   ·   Ctrl+C прервать   ·   Ctrl+L очистить"
    BUSY_HINT = "команда выполняется…   Shift+Enter — отправить ввод программе (Y/N, пароль…)"

    def __init__(self, key, rights, cwd, name=None, parent=None):
        super().__init__(parent)
        self.key, self.rights, self.start_cwd = key, rights, cwd
        self.custom_name = name
        self.history, self.hpos = [], 0
        self.shell = None
        self.alive = False
        self.starting = False
        self._closing = False
        self._queued = []
        v = _box(self)
        self.out = OutputView()
        v.addWidget(self.out, 1)

        # полоса «выполняется»: что запущено, сколько идёт, как прервать
        self.busy_bar = QFrame()
        self.busy_bar.setObjectName("TermBusy")
        br = QHBoxLayout(self.busy_bar)
        br.setContentsMargins(12, 3, 6, 3)
        br.setSpacing(8)
        self.busy_prog = QProgressBar()
        self.busy_prog.setObjectName("TermBusyProg")
        self.busy_prog.setRange(0, 0)
        self.busy_prog.setTextVisible(False)
        self.busy_prog.setFixedSize(70, 4)
        br.addWidget(self.busy_prog)
        self.busy_lbl = QLabel("")
        self.busy_lbl.setObjectName("TermBusyText")
        self.busy_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        br.addWidget(self.busy_lbl, 1)
        self.busy_hint = QLabel("")
        self.busy_hint.setObjectName("TermBusyHint")
        br.addWidget(self.busy_hint)
        self.busy_bar.hide()
        v.addWidget(self.busy_bar)
        self._t0 = 0.0
        self._cur_cmd = ""
        self.unseen = ""                    # «✓»/«✖» на вкладке, пока её не открыли
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._update_busy)
        self._hint_timer = QTimer(self, singleShot=True, interval=3500)
        self._hint_timer.timeout.connect(lambda: self.busy_hint.setText(""))

        bar = QFrame()
        bar.setObjectName("TermInputBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 4, 6, 4)
        row.setSpacing(6)
        self.prompt = QLabel(">")
        self.prompt.setObjectName("TermPrompt")
        self.prompt.setFont(_mono())
        self.prompt.setMaximumWidth(420)
        row.addWidget(self.prompt)
        self.input = QLineEdit()
        self.input.setObjectName("TermInput")
        self.input.setFont(_mono())
        self.input.setPlaceholderText(self.IDLE_HINT)
        self.input.returnPressed.connect(self.submit)
        self.input.installEventFilter(self)
        row.addWidget(self.input, 1)
        self.run_btn = run = tool_button("", "play", "Primary", self.submit, "Выполнить (Enter)")
        run.setFixedSize(34, 28)
        row.addWidget(run)
        self.stop_btn = tool_button("", "stop", "Danger", self.interrupt, "Прервать (Ctrl+C)")
        self.stop_btn.setFixedSize(34, 28)
        self.stop_btn.hide()
        row.addWidget(self.stop_btn)
        v.addWidget(bar)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.out.clear_all,
                  context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+C"), self.input, activated=self._ctrl_c,
                  context=Qt.WidgetShortcut)
        self._set_prompt(self._cwd())

    # ----------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        if self.unseen:
            self.unseen = ""
            self.state_changed.emit(self)
        if self.shell is None and not self._closing:
            QTimer.singleShot(0, self._lazy_start)

    def _lazy_start(self):
        if self.shell is None and not self._closing:
            self.restart()

    def name(self):
        return self.custom_name or shellproc.short_label(self.key)

    def _cwd(self):
        return self.shell.cwd if self.shell else (self.start_cwd or os.path.expanduser("~"))

    def elevated(self):
        if self.shell:
            return self.shell.elevated
        return self.rights == "admin" or (self.rights == "same" and admin.is_admin())

    def rights_label(self):
        return "админ" if self.elevated() else ""

    def restart(self, keep_cwd=True):
        cwd = self.shell.cwd if (keep_cwd and self.shell) else self.start_cwd
        self._stop()
        self.shell = Shell(self.key, self.rights, cwd, self)
        self.shell.output.connect(self._on_output)
        self.shell.ready.connect(self._ready)
        self.shell.finished_cmd.connect(self._done)
        self.shell.died.connect(self._died)
        self.alive = True
        self.starting = True
        self.out.put(f"— запуск {self.shell.label}…\n", MUTED)
        self.shell.start()
        self.state_changed.emit(self)

    def _on_output(self, s, err):
        self.out.put(s, RED if err else TEXT)

    def _stop(self):
        if self.shell:
            sh, self.shell = self.shell, None
            for sig in (sh.output, sh.ready, sh.finished_cmd, sh.died):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            sh.kill()
            sh.deleteLater()

    def _set_busy(self, on):
        self.busy_bar.setVisible(on)
        self.run_btn.setVisible(not on)
        self.stop_btn.setVisible(on)
        self.input.setPlaceholderText(self.BUSY_HINT if on else self.IDLE_HINT)
        if on:
            self._update_busy()
            self._tick.start()
        else:
            self._tick.stop()
            self.busy_hint.setText("")

    def _update_busy(self):
        cmd = self._cur_cmd if len(self._cur_cmd) <= 70 else self._cur_cmd[:67] + "…"
        q = f"  ·  в очереди: {len(self._queued)}" if self._queued else ""
        self.busy_lbl.setText(f"Выполняется: {cmd}  ·  {_dur(time.monotonic() - self._t0)}{q}")

    def _finish_line(self, rc):
        el = _dur(time.monotonic() - self._t0)
        if rc:
            self.out.put(f"✖ Завершено с ошибкой · код {rc} · {el}\n", RED)
        else:
            self.out.put(f"✔ Выполнено · {el}\n", OK)
        if not self.isVisible():
            self.unseen = "✖" if rc else "✓"

    def _ready(self, cwd):
        self.starting = False
        r = "администратор" if self.shell.elevated else "обычный пользователь"
        self.out.put(f"— {self.shell.label} · {r} · {cwd}\n", ACCENT_LIGHT)
        self._prompt()
        self.state_changed.emit(self)
        if self._queued:
            q, self._queued = self._queued, []
            for cmd in q:
                self._run(cmd)
                break                       # остальное — после завершения первой
            self._queued = q[1:]

    def _done(self, rc, cwd):
        self._finish_line(rc)
        self._set_busy(False)
        self._prompt()
        self.state_changed.emit(self)
        if self._queued:
            self._run(self._queued.pop(0))

    def _died(self, code):
        self.alive = False
        self.starting = False
        self._queued.clear()
        self._set_busy(False)
        self.state_changed.emit(self)
        if self._closing:
            return
        self.out.put(f"— оболочка завершилась (код {code}). Enter — запустить заново\n", AMBER)

    def _set_prompt(self, cwd):
        pre = "PS " if shellproc.shell_by_key(self.key)[4] == "ps" else ""
        sign = "#" if self.elevated() else ">"
        full = f"{pre}{cwd}{sign}"
        fm = self.prompt.fontMetrics()
        self.prompt.setText(fm.elidedText(f"{pre}{_short_cwd(cwd)}{sign}", Qt.ElideMiddle, 410))
        self.prompt.setToolTip(full)
        self._prompt_full = full

    def _prompt(self):
        self._set_prompt(self._cwd())

    def busy(self):
        return bool(self.shell and self.shell.busy and self.shell._started)

    def submit(self):
        text = self.input.text()
        self.input.clear()
        if not self.shell or not self.alive:
            self.restart()
            if text.strip():
                self._queue(text.strip())
            return
        if self.shell.busy:
            if self.shell._started:
                shift = QGuiApplication.keyboardModifiers() & Qt.ShiftModifier
                if shift:                    # программа ждёт ввода (pause, [Y/N]…)
                    self.out.put("› " + text + "\n", ACCENT_LIGHT)
                    self.shell.send_input(text)
                else:                        # новую команду — только после завершения текущей
                    self.input.setText(text)
                    self.busy_hint.setText("дождитесь завершения · Shift+Enter — ввод программе")
                    self._hint_timer.start()
                return
            elif text.strip():               # оболочка ещё запускается — выполним, как будет готова
                self._queue(text.strip())
            return
        cmd = text.strip()
        if not cmd:
            return
        self._remember(cmd)
        if cmd.lower() in ("cls", "clear", "clear-host"):
            self.out.clear_all()
            return
        if cmd.lower() == "exit":
            self.out.put("Чтобы закрыть вкладку, нажмите × или Ctrl+W\n", MUTED)
            return
        self._run(cmd)

    def _remember(self, cmd):
        if not self.history or self.history[-1] != cmd:
            self.history.append(cmd)
            del self.history[:-HISTORY_MAX]
        self.hpos = len(self.history)

    def _queue(self, cmd):
        self._remember(cmd)
        self._queued.append(cmd)
        self.out.put(f"  (в очереди: {cmd})\n", MUTED)

    def _run(self, cmd):
        if self.out.document().characterCount() > 1 or self.out._pending:
            self.out.put("\n", TEXT)
        self.out.put(self._prompt_full + " ", ACCENT_LIGHT)
        self.out.put(cmd + "\n", TEXT)
        self._t0 = time.monotonic()
        self._cur_cmd = cmd
        self.shell.run(cmd)
        self._set_busy(True)
        self.state_changed.emit(self)

    def _ctrl_c(self):
        if self.input.hasSelectedText():
            self.input.copy()
        elif self.busy() or self._queued:
            self.interrupt()

    def interrupt(self):
        if not (self.busy() or self._queued):
            return
        self._queued.clear()
        self._set_busy(False)
        self.out.put(f"^C — команда прервана · {_dur(time.monotonic() - self._t0)}\n", AMBER)
        self.restart(keep_cwd=True)

    def eventFilter(self, obj, e):
        if obj is self.input and e.type() == e.Type.KeyPress and self.history:
            if e.key() == Qt.Key_Up:
                self.hpos = max(0, self.hpos - 1)
                self.input.setText(self.history[self.hpos])
                return True
            if e.key() == Qt.Key_Down:
                self.hpos = min(len(self.history), self.hpos + 1)
                self.input.setText(self.history[self.hpos] if self.hpos < len(self.history) else "")
                return True
        return super().eventFilter(obj, e)

    def close_tab(self):
        self._closing = True
        self._stop()

    def snapshot(self):
        return {"key": self.key, "rights": self.rights, "name": self.custom_name,
                "cwd": self._cwd()}


class NewTabDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новая вкладка терминала")
        self.setMinimumWidth(480)
        f = QFormLayout(self)
        f.setContentsMargins(20, 18, 20, 16)
        f.setSpacing(12)
        self.shell = QComboBox()
        for key, label, *_ in shellproc.available_shells():
            self.shell.addItem(label, key)
        self.shell.setCurrentIndex(max(0, self.shell.findData(cfg.get("default_shell"))))
        self.rights = QComboBox()
        for key, label in RIGHTS:
            self.rights.addItem(label, key)
        self.rights.setCurrentIndex(max(0, self.rights.findData(cfg.get("default_rights", "same"))))
        self.name = QLineEdit()
        self.name.setPlaceholderText("необязательно, например «Сайт» или «Сборка»")
        self.cwd = QLineEdit(cfg.get("start_dir") or os.path.expanduser("~"))
        browse = tool_button("", "folder", on_click=self._browse, tip="Выбрать папку")
        browse.setFixedWidth(40)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.cwd, 1)
        row.addWidget(browse)
        self.external = QCheckBox("Открыть в отдельном окне Windows, а не во вкладке")
        self.remember = QCheckBox("Использовать по умолчанию")
        f.addRow("Оболочка", self.shell)
        f.addRow("Права", self.rights)
        f.addRow("Название", self.name)
        f.addRow("Папка", row)
        f.addRow("", self.external)
        f.addRow("", self.remember)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = bb.button(QDialogButtonBox.Ok)
        ok.setText("Открыть")
        ok.setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Папка", self.cwd.text())
        if d:
            self.cwd.setText(os.path.normpath(d))

    def values(self):
        return {"key": self.shell.currentData(), "rights": self.rights.currentData(),
                "name": self.name.text().strip() or None, "cwd": self.cwd.text().strip(),
                "external": self.external.isChecked(), "remember": self.remember.isChecked()}


# ============================================================ страница
class TerminalPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._inited = False
        self._conf = None
        self._tab_cache = {}
        self._pill_color = None
        self._saved = None
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(title("Терминал"))
        head.addStretch(1)
        self.pill = pill("—", MUTED)
        head.addWidget(self.pill)
        v.addLayout(head)
        v.addWidget(muted(
            "Вкладка «Kryostat» показывает, что делает само приложение. Остальные вкладки — "
            "ваши консоли: у каждой своя оболочка, папка, история и права."))

        bar = QHBoxLayout()
        bar.setSpacing(6)
        cfg = self._cfg()
        for key, label, *_ in shellproc.available_shells():
            b = tool_button(shellproc.short_label(label), "terminal",
                            on_click=lambda _=False, k=key: self.new_tab(k))
            b.setToolTip(f"Новая вкладка: {label} ({self._rights_name(cfg['default_rights'])})")
            bar.addWidget(b)
        bar.addSpacing(6)
        bar.addWidget(tool_button("Админ", "shield", "Magenta",
                                  lambda: self.new_tab(self._cfg()["default_shell"], "admin"),
                                  "Новая вкладка от имени администратора"))
        bar.addWidget(tool_button("Без прав", "power", on_click=lambda: self.new_tab(
            self._cfg()["default_shell"], "user"), tip="Новая вкладка от обычного пользователя"))
        bar.addWidget(tool_button("", "settings", on_click=self.new_tab_dialog,
                                  tip="Настроить и открыть новую вкладку…"))
        bar.addStretch(1)
        self.btn_stop = tool_button("Прервать", "stop", "Danger", self._interrupt, "Ctrl+C")
        bar.addWidget(self.btn_stop)
        bar.addWidget(tool_button("", "refresh", on_click=self._restart, tip="Перезапустить оболочку"))
        bar.addWidget(tool_button("", "copy", on_click=self._copy, tip="Копировать весь вывод"))
        bar.addWidget(tool_button("", "trash", on_click=self._clear, tip="Очистить (Ctrl+L)"))
        v.addLayout(bar)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("Term")
        tb = self.tabs.tabBar()
        tb.setObjectName("TermTabs")
        self.tabs.setTabsClosable(False)         # свой крестик: ровный, фиксированного размера
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(False)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setIconSize(QSize(14, 14))
        tb.setExpanding(False)
        tb.setDrawBase(False)
        self.tabs.currentChanged.connect(self._on_current)
        self.tabs.tabBarDoubleClicked.connect(self._rename)
        tb.setContextMenuPolicy(Qt.CustomContextMenu)
        tb.customContextMenuRequested.connect(self._tab_menu)
        tb.tabMoved.connect(lambda *_: self._save_timer.start())
        plus = QToolButton()
        plus.setObjectName("TabPlus")
        plus.setIcon(icon("plus", TEXT2, 14))
        plus.setToolTip("Новая вкладка (Ctrl+T)")
        plus.setPopupMode(QToolButton.MenuButtonPopup)
        plus.clicked.connect(lambda: self.new_tab())
        m = QMenu(plus)
        for key, label, *_ in shellproc.available_shells():
            for r, rl in RIGHTS:
                m.addAction(f"{label} — {rl}", lambda k=key, rr=r: self.new_tab(k, rr))
            m.addSeparator()
        m.addAction("Настроить и открыть…", self.new_tab_dialog)
        plus.setMenu(m)
        self.tabs.setCornerWidget(plus, Qt.TopRightCorner)
        v.addWidget(self.tabs, 1)

        self.activity = ActivityTab()
        self.tabs.addTab(self.activity, icon("gauge", PURPLE, 14), "Kryostat")

        for seq, fn in (("Ctrl+T", lambda: self.new_tab()),
                        ("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())),
                        ("Ctrl+Tab", lambda: self._step(1)),
                        ("Ctrl+Shift+Tab", lambda: self._step(-1))):
            QShortcut(QKeySequence(seq), self, activated=fn, context=Qt.WidgetWithChildrenShortcut)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1500)
        self._save_timer.timeout.connect(self._save_tabs)
        self._update_state()

    # ----------------------------------------------------------------
    def _cfg(self):
        """Настройки терминала: читаются один раз и обновляются только при изменении."""
        if self._conf is None:
            c = {"default_shell": shellproc.available_shells()[0][0], "default_rights": "same",
                 "start_dir": os.path.expanduser("~"), "restore": True, "tabs": []}
            c.update(config.load().get("terminal") or {})
            self._conf = c
        return self._conf

    @staticmethod
    def _rights_name(r):
        return dict(RIGHTS).get(r, r)

    def _step(self, d):
        n = self.tabs.count()
        if n:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % n)

    def on_show(self):
        """Вкладки создаются при первом открытии страницы — не тормозим запуск Kryostat.
        Оболочка запускается только у видимой вкладки, остальные — при переключении."""
        if not self._inited:
            self._inited = True
            cfg = self._cfg()
            saved = cfg.get("tabs") if cfg.get("restore", True) else []
            self.tabs.setUpdatesEnabled(False)
            try:
                for t in saved or []:
                    if not isinstance(t, dict):
                        continue
                    if t.get("rights") == "admin" and not admin.is_admin():
                        t = dict(t, rights="same")       # не показываем UAC сразу при открытии
                    self.new_tab(t.get("key"), t.get("rights"), t.get("cwd"), t.get("name"),
                                 focus=False, save=False)
                if self.tabs.count() == 1:
                    self.new_tab(focus=False, save=False)
            finally:
                self.tabs.setUpdatesEnabled(True)
            self._saved = self._snapshot()
            self.tabs.setCurrentIndex(1)
        self.focus_input()

    def focus_input(self):
        w = self.tabs.currentWidget()
        if isinstance(w, ConsoleTab):
            w.input.setFocus()

    def _close_button(self, tab):
        b = QToolButton(self.tabs.tabBar())
        b.setObjectName("TabClose")
        b.setIcon(icon("x", MUTED, 12))
        b.setIconSize(QSize(12, 12))
        b.setFixedSize(QSize(20, 20))
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip("Закрыть вкладку (Ctrl+W)")
        b.setAutoRaise(True)
        b.clicked.connect(lambda: self.close_tab(self.tabs.indexOf(tab)))
        return b

    def new_tab(self, key=None, rights=None, cwd=None, name=None, focus=True, save=True):
        cfg = self._cfg()
        key = key or cfg["default_shell"]
        if key not in {s[0] for s in shellproc.available_shells()}:
            key = cfg["default_shell"]
        rights = rights if rights in dict(RIGHTS) else cfg["default_rights"]
        tab = ConsoleTab(key, rights, cwd or cfg.get("start_dir"), name)
        tab.state_changed.connect(self._tab_state)
        i = self.tabs.addTab(tab, tab.name())
        self.tabs.tabBar().setTabButton(i, QTabBar.RightSide, self._close_button(tab))
        self._tab_state(tab)
        if focus:
            self.tabs.setCurrentIndex(i)
            tab.input.setFocus()
        if save:
            self._save_timer.start()
        return tab

    def new_tab_dialog(self):
        d = NewTabDialog(self._cfg(), self)
        if d.exec() != QDialog.Accepted:
            return
        v = d.values()
        if v["remember"]:
            config.update(lambda c: c.setdefault("terminal", {}).update(
                {"default_shell": v["key"], "default_rights": v["rights"], "start_dir": v["cwd"]}))
            self._conf = None
        if v["external"]:
            shellproc.open_external(v["key"], v["cwd"], v["rights"] == "admin")
            return
        self.new_tab(v["key"], v["rights"], v["cwd"], v["name"])

    def close_tab(self, i):
        w = self.tabs.widget(i)
        if not isinstance(w, ConsoleTab):
            return                                   # вкладку Kryostat не закрываем
        w.close_tab()
        self._tab_cache.pop(id(w), None)
        if self.tabs.currentIndex() == i and i == self.tabs.count() - 1 and i > 1:
            self.tabs.setCurrentIndex(i - 1)         # после закрытия — соседняя консоль, а не журнал
        self.tabs.removeTab(i)
        w.deleteLater()
        self._save_timer.start()
        self._update_state()

    def _tab_state(self, tab):
        i = self.tabs.indexOf(tab)
        if i < 0:
            return
        rl = tab.rights_label()
        mark = "● " if tab.busy() else (tab.unseen + " " if tab.unseen else "")
        text = f"{mark}{tab.name()}" + (f" · {rl}" if rl else "")
        if not tab.alive and tab.shell is None and not tab._closing:
            color = MUTED                            # ещё не запускалась
        elif not tab.alive:
            color = RED
        elif tab.busy() or tab.starting:
            color = AMBER
        else:
            color = PURPLE if tab.elevated() else ACCENT_LIGHT
        tip = f"{tab.name()} · {self._rights_name(tab.rights)} · {tab._cwd()}"
        # обновляем только то, что изменилось: setTabText/setTabIcon пересчитывают всю панель
        old = self._tab_cache.get(id(tab), (None, None, None))
        if old[0] != text:
            self.tabs.setTabText(i, text)
        if old[1] != color:
            self.tabs.setTabIcon(i, icon("terminal", color, 14))
        if old[2] != tip:
            self.tabs.setTabToolTip(i, tip)
        self._tab_cache[id(tab)] = (text, color, tip)
        if tab is self.tabs.currentWidget():
            self._update_state()
        if old[2] != tip:
            self._save_timer.start()

    def _on_current(self, _i):
        self._update_state()
        self.focus_input()

    def _set_pill(self, text, color):
        self.pill.setText(text)
        if color != self._pill_color:                # setStyleSheet — дорогая операция
            self._pill_color = color
            self.pill.setStyleSheet(pill_style(color))

    def _update_state(self):
        w = self.tabs.currentWidget()
        n = self.tabs.count() - 1
        if isinstance(w, ConsoleTab):
            busy = w.busy()
            self.btn_stop.setEnabled(busy or bool(w._queued))
            if w.starting:
                st, c = "запуск…", AMBER
            elif busy:
                st, c = "выполняется…", AMBER
            elif w.alive:
                st, c = "готов", OK
            elif w.shell is None:
                st, c = "ожидает запуска", MUTED
            else:
                st, c = "остановлена", RED
            self._set_pill(f"{st} · вкладок: {n}", c)
        else:
            self.btn_stop.setEnabled(False)
            self._set_pill(f"журнал Kryostat · вкладок: {n}", PURPLE)

    def _cur(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, ConsoleTab) else None

    def _interrupt(self):
        if self._cur():
            self._cur().interrupt()

    def _restart(self):
        if self._cur():
            self._cur().restart()

    def _clear(self):
        w = self.tabs.currentWidget()
        (w.out if hasattr(w, "out") else self.activity.out).clear_all()

    def _copy(self):
        w = self.tabs.currentWidget()
        self.state["clipboard"](w.out.toPlainText())

    def _rename(self, i):
        w = self.tabs.widget(i)
        if not isinstance(w, ConsoleTab):
            return
        text, ok = QInputDialog.getText(self, "Название вкладки", "Название:", text=w.name())
        if ok:
            w.custom_name = text.strip() or None
            self._tab_state(w)
            self._save_timer.start()

    def _tab_menu(self, pos):
        i = self.tabs.tabBar().tabAt(pos)
        w = self.tabs.widget(i) if i >= 0 else None
        m = QMenu(self)
        if isinstance(w, ConsoleTab):
            m.addAction(icon("settings", MUTED, 16), "Переименовать", lambda: self._rename(
                self.tabs.indexOf(w)))
            m.addAction(icon("refresh", MUTED, 16), "Перезапустить", w.restart)
            m.addAction(icon("copy", MUTED, 16), "Дублировать",
                        lambda: self.new_tab(w.key, w.rights, w._cwd()))
            if admin.IS_WINDOWS:
                m.addAction(icon("folder", MUTED, 16), "Открыть эту папку в Проводнике",
                            lambda: os.startfile(w._cwd()))
            m.addAction(icon("monitor", MUTED, 16), "Открыть в отдельном окне",
                        lambda: shellproc.open_external(w.key, w._cwd(), w.elevated()))
            m.addSeparator()
            m.addAction(icon("x", MUTED, 16), "Закрыть", lambda: self.close_tab(self.tabs.indexOf(w)))
            m.addAction("Закрыть остальные", lambda: self._close_others(w))
            m.addSeparator()
        m.addAction(icon("plus", MUTED, 16), "Новая вкладка…", self.new_tab_dialog)
        m.exec(self.tabs.tabBar().mapToGlobal(pos))

    def _close_others(self, keep):
        for i in range(self.tabs.count() - 1, 0, -1):
            if self.tabs.widget(i) is not keep:
                self.close_tab(i)

    def _snapshot(self):
        return [self.tabs.widget(i).snapshot() for i in range(self.tabs.count())
                if isinstance(self.tabs.widget(i), ConsoleTab)]

    def _save_tabs(self):
        tabs = self._snapshot()
        if tabs == self._saved:                      # ничего не изменилось — диск не трогаем
            return
        self._saved = tabs
        config.update(lambda c: c.setdefault("terminal", {}).__setitem__("tabs", tabs))
        if self._conf is not None:
            self._conf["tabs"] = tabs

    def shutdown(self):
        if self._inited:
            self._save_timer.stop()
            self._save_tabs()
        self.activity.shutdown()
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, ConsoleTab):
                w.close_tab()
