"""Keeps the mouse pointer on the operator screen.

The cinema screens hide the pointer, so a pointer that wanders onto one is simply gone. On Windows
the pointer is clipped by the system (ClipCursor, no flicker); Windows drops that clip on some
events (another window in front, UAC, display changes), so it is checked and set again regularly.
Elsewhere the pointer is pulled back as soon as it leaves the screen (X11; Wayland does not allow
an application to move the pointer).
The lock only holds while CinemaViewer is the active application, so other programs (e.g. the
display settings) can still be used normally.
"""

import sys

from PySide6.QtCore import QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    _user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO))
    _user32.ClipCursor.argtypes = (ctypes.POINTER(wintypes.RECT),)
    _user32.GetClipCursor.argtypes = (ctypes.POINTER(wintypes.RECT),)
    MONITOR_DEFAULTTONEAREST = 2


def _rect_tuple(r):
    return (r.left, r.top, r.right, r.bottom)


class CursorLock(QObject):
    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.window = None
        self.enabled = False
        self._clipped = None  # the Windows clip rect we set
        self._timer = QTimer(self)
        self._timer.setInterval(250 if sys.platform == "win32" else 15)
        self._timer.timeout.connect(self._enforce)
        qapp.applicationStateChanged.connect(lambda _s: self._update())

    def set_window(self, window, enabled):
        """Lock the pointer to the screen `window` is on (enabled False or window None: free)."""
        self.window = window
        self.enabled = bool(enabled and window is not None)
        self._update()

    def release(self):
        self.enabled = False
        self._update()

    def _active(self):
        return self.enabled and self.qapp.applicationState() == Qt.ApplicationActive

    def _update(self):
        if self._active():
            self._enforce()
            self._timer.start()
        else:
            self._timer.stop()
            self._unclip()

    def _enforce(self):
        if not self._active():
            self._update()
            return
        if sys.platform == "win32":
            self._clip_windows()
        else:
            self._pull_back()

    # -- Windows ----------------------------------------------------------------------
    def _clip_windows(self):
        hwnd = int(self.window.winId())
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return
        want = _rect_tuple(info.rcMonitor)  # physical pixels, like ClipCursor
        current = wintypes.RECT()
        if self._clipped == want and _user32.GetClipCursor(ctypes.byref(current)) \
                and _rect_tuple(current) == want:
            return
        if _user32.ClipCursor(ctypes.byref(info.rcMonitor)):
            self._clipped = want

    def _unclip(self):
        if sys.platform == "win32" and self._clipped is not None:
            _user32.ClipCursor(None)
            self._clipped = None

    # -- elsewhere --------------------------------------------------------------------
    def _pull_back(self):
        screen = self.window.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.geometry()
        pos = QCursor.pos()
        if area.contains(pos):
            return
        QCursor.setPos(QPoint(min(max(pos.x(), area.left()), area.right()),
                              min(max(pos.y(), area.top()), area.bottom())))
