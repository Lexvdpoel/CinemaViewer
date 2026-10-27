"""X11 implementation of ScreenMirror (see mirror.py for the dispatcher).

Copies the projection window's pixels from the root window with XGetImage in a background
thread; the X server composites whatever is visible there, like the Windows GDI version.
"""
import ctypes

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QImage

ALL_PLANES = 0xFFFFFFFF
ZPIXMAP = 2


class XImage(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong), ("blue_mask", ctypes.c_ulong),
        ("visual", ctypes.c_void_p),
    ] + [(f"fn{i}", ctypes.c_void_p) for i in range(9)]


class ScreenMirror(QThread):
    frame = Signal(QImage)

    def __init__(self, hwnd, width=640, fps=12):
        super().__init__()
        self.hwnd = int(hwnd)
        self.width = width
        self.interval = int(1000 / fps)
        self._running = True

    def stop(self):
        self._running = False
        self.wait(2000)

    def run(self):
        try:
            x11 = ctypes.CDLL("libX11.so.6")
        except OSError:
            return
        # Every Display* must be declared as a pointer: undeclared, ctypes passes it as a 32-bit int.
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        p_int, p_uint, p_ulong = (ctypes.POINTER(t) for t in (ctypes.c_int, ctypes.c_uint, ctypes.c_ulong))
        x11.XGetGeometry.argtypes = [ctypes.c_void_p, ctypes.c_ulong, p_ulong, p_int, p_int, p_uint, p_uint,
                                     p_uint, p_uint]
        x11.XGetGeometry.restype = ctypes.c_int
        x11.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                              ctypes.c_int, p_int, p_int, p_ulong]
        x11.XTranslateCoordinates.restype = ctypes.c_int
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        # XGetImage of an area partly off-screen gives BadMatch; Xlib's default handler would end
        # the whole process for that (we just skip the frame).
        self._error_handler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(
            lambda _display, _event: 0)
        x11.XSetErrorHandler(self._error_handler)
        x11.XGetImage.restype = ctypes.POINTER(XImage)
        x11.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_int]
        x11.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]
        display = x11.XOpenDisplay(None)
        if not display:
            return
        root = x11.XDefaultRootWindow(display)
        try:
            while self._running:
                frame = self._grab(x11, display, root)
                if frame is not None:
                    self.frame.emit(frame)
                self.msleep(self.interval)
        finally:
            x11.XCloseDisplay(display)

    def _grab(self, x11, display, root):
        x, y = ctypes.c_int(), ctypes.c_int()
        child = ctypes.c_ulong()
        if not x11.XTranslateCoordinates(display, self.hwnd, root, 0, 0, ctypes.byref(x),
                                         ctypes.byref(y), ctypes.byref(child)):
            return None
        w, h = ctypes.c_uint(), ctypes.c_uint()
        root_out, bx, by, border, depth = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int(), \
            ctypes.c_uint(), ctypes.c_uint()
        if not x11.XGetGeometry(display, self.hwnd, ctypes.byref(root_out), ctypes.byref(bx),
                                ctypes.byref(by), ctypes.byref(w), ctypes.byref(h),
                                ctypes.byref(border), ctypes.byref(depth)):
            return None
        if w.value <= 0 or h.value <= 0:
            return None
        image = x11.XGetImage(display, root, x.value, y.value, w.value, h.value, ALL_PLANES, ZPIXMAP)
        if not image or not image.contents.data:
            return None
        try:
            img = image.contents
            if img.bits_per_pixel != 32 or img.byte_order != 0:  # little-endian BGRA, like RGB32
                return None
            data = ctypes.string_at(img.data, img.bytes_per_line * img.height)
            frame = QImage(data, img.width, img.height, img.bytes_per_line, QImage.Format_RGB32)
            if frame.width() > self.width:
                frame = frame.scaled(self.width, -1, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return frame.copy()
        finally:
            x11.XDestroyImage(image)
