"""Run blocking calls (network, disk) off the UI thread and deliver results back on it."""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_live = set()


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn, args):
        super().__init__()
        self.fn = fn
        self.args = args
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI
            self.signals.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.signals.done.emit(result)


def run_async(fn, on_done=None, on_error=None, *args):
    task = _Task(fn, args)
    task.setAutoDelete(False)
    _live.add(task)

    def finish(cb, value):
        _live.discard(task)
        if cb:
            cb(value)

    task.signals.done.connect(lambda r: finish(on_done, r))
    task.signals.failed.connect(lambda e: finish(on_error, e))
    QThreadPool.globalInstance().start(task)
