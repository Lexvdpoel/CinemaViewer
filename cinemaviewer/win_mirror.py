"""Windows GDI implementation of ScreenMirror (see mirror.py for the dispatcher)."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

SRCCOPY = 0x00CC0020
HALFTONE = 4

_user32 = ctypes.WinDLL("user32")
_gdi32 = ctypes.WinDLL("gdi32")
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
_gdi32.SetBrushOrgEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
_gdi32.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


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
        screen_dc = _user32.GetDC(None)
        mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
        bitmap, bits, size = None, ctypes.c_void_p(), (0, 0)
        try:
            while self._running:
                rect = wintypes.RECT()
                if _user32.IsWindowVisible(self.hwnd) and _user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
                    sw, sh = rect.right - rect.left, rect.bottom - rect.top
                    if sw > 0 and sh > 0:
                        w = self.width
                        h = max(2, round(w * sh / sw))
                        if (w, h) != size:
                            if bitmap:
                                _gdi32.DeleteObject(bitmap)
                            bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
                            bitmap = _gdi32.CreateDIBSection(mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
                            _gdi32.SelectObject(mem_dc, bitmap)
                            _gdi32.SetStretchBltMode(mem_dc, HALFTONE)
                            _gdi32.SetBrushOrgEx(mem_dc, 0, 0, None)
                            size = (w, h)
                        _gdi32.StretchBlt(mem_dc, 0, 0, w, h, screen_dc, rect.left, rect.top, sw, sh, SRCCOPY)
                        _gdi32.GdiFlush()
                        data = ctypes.string_at(bits, w * h * 4)
                        self.frame.emit(QImage(data, w, h, w * 4, QImage.Format_RGB32).copy())
                self.msleep(self.interval)
        finally:
            if bitmap:
                _gdi32.DeleteObject(bitmap)
            _gdi32.DeleteDC(mem_dc)
            _user32.ReleaseDC(None, screen_dc)
