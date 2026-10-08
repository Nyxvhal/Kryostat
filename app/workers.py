"""Фоновые задачи: тяжёлые операции (reg/schtasks/PowerShell) не должны блокировать интерфейс."""
import itertools
from PySide6.QtCore import QObject, QThread, Signal

_ids = itertools.count(1)
_jobs: dict[int, "Job"] = {}
_callbacks: dict[int, object] = {}


class Job(QThread):
    finished_with = Signal(int, object)

    def __init__(self, job_id, fn):
        super().__init__()
        self.job_id, self.fn = job_id, fn

    def run(self):
        try:
            res = self.fn()
        except Exception as e:           # исключение возвращаем получателю, а не теряем
            res = e
        self.finished_with.emit(self.job_id, res)


class _Hub(QObject):
    """Живёт в GUI-потоке — callbacks гарантированно вызываются именно там."""

    def deliver(self, job_id, res):
        job = _jobs.pop(job_id, None)
        cb = _callbacks.pop(job_id, None)
        if job is not None:
            job.wait(2000)
            job.deleteLater()
        if cb:
            try:
                cb(res)
            except Exception as e:
                from core import config
                config.log(f"callback error: {e!r}")


_hub = None


def run_async(fn, callback=None):
    """Выполнить fn() в отдельном потоке, затем вызвать callback(результат) в GUI-потоке."""
    global _hub
    if _hub is None:
        _hub = _Hub()
    jid = next(_ids)
    job = Job(jid, fn)
    _jobs[jid] = job
    _callbacks[jid] = callback
    job.finished_with.connect(_hub.deliver)
    job.start()
    return job


def wait_all(timeout_ms=2500):
    """Дождаться активных задач при выходе (но не вечно)."""
    for job in list(_jobs.values()):
        job.wait(timeout_ms)
