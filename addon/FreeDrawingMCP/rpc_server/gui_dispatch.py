"""Run callables on FreeCAD's GUI thread from the XML-RPC thread.

Adapted from neka-nat/freecad-mcp (MIT): per-call response queues, an
immediate Qt-signal wake-up with a 500 ms heartbeat fallback, a re-entrancy
guard, deferral while the user drags in the 3D view or a modal dialog is
open, and separate queue/run budgets.
"""

import queue
import threading
import time
import traceback

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets

_queue = queue.Queue()
_SHUTDOWN = object()
_processing = False
_waker = None
MOUSE_DEFER_MAX_S = 5.0
_mouse_since = None
_last_defer = None


class _Wake(QtCore.QObject):
    sig = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.sig.connect(lambda: process(reschedule=False), QtCore.Qt.QueuedConnection)


def init_waker():
    global _waker
    _waker = _Wake()


def cleanup_waker():
    global _waker
    _waker = None


def request_shutdown():
    _queue.put(_SHUTDOWN)


def _defer_reason():
    global _mouse_since
    now = time.monotonic()
    if QtWidgets.QApplication.mouseButtons() != QtCore.Qt.NoButton:
        if _mouse_since is None:
            _mouse_since = now
        if now - _mouse_since < MOUSE_DEFER_MAX_S:
            return "mouse buttons held (3D navigation)"
    else:
        _mouse_since = None
    if QtWidgets.QApplication.activePopupWidget() is not None:
        return "a popup menu is open in FreeCAD"
    if QtWidgets.QApplication.activeModalWidget() is not None:
        return "a modal dialog is open in FreeCAD"
    return None


def process(reschedule=True):
    """Drain queued tasks on the GUI thread."""
    global _processing, _last_defer
    if _processing:
        return
    stop = False
    try:
        if _queue.empty():
            return
        reason = _defer_reason()
        if reason:
            _last_defer = (reason, time.monotonic())
            return
        _last_defer = None
        _processing = True
        while not _queue.empty():
            task = _queue.get()
            if task is _SHUTDOWN:
                stop = True
                return
            try:
                task()
            except BaseException as e:  # never let a task kill the loop or FreeCAD
                FreeCAD.Console.PrintError(f"FreeDrawingMCP task error: {e}\n{traceback.format_exc()}")
    finally:
        _processing = False
        if reschedule and not stop:
            QtCore.QTimer.singleShot(500, process)


def dispatch(task, timeout=300.0, queue_timeout=None, name="task"):
    """Run ``task`` on the GUI thread; return its value or raise RuntimeError."""
    resp = queue.Queue(maxsize=1)
    lock = threading.Lock()
    started = threading.Event()
    state = {"cancelled": False}

    def wrapped():
        with lock:
            if state["cancelled"]:
                return
            started.set()
        try:
            resp.put(("ok", task()))
        except BaseException as e:
            resp.put(("err", f"{type(e).__name__}: {e}", traceback.format_exc()))

    queued = time.monotonic()
    _queue.put(wrapped)
    if _waker is not None:
        _waker.sig.emit()
    if not started.wait(queue_timeout or timeout):
        with lock:
            if not started.is_set():
                state["cancelled"] = True
                hint = f" ({_last_defer[0]})" if _last_defer and _last_defer[1] >= queued else ""
                raise RuntimeError(f"FreeCAD GUI did not start '{name}' in time{hint}")
    try:
        res = resp.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError(f"'{name}' did not finish within {timeout:g}s on the GUI thread")
    if res[0] == "ok":
        return res[1]
    raise RuntimeError(res[1] + "\n" + res[2])
