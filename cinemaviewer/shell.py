"""Shell process: Windows starts this at sign-in instead of Explorer (Instellingen → Systeem).

It starts CinemaViewer and watches it. 'Naar Windows' starts the normal desktop; a crash restarts
CinemaViewer, and when it keeps crashing the desktop comes back so the PC never stays black.
Holding Shift right after signing in skips CinemaViewer and starts the desktop (emergency route).
Deliberately free of Qt, so it starts fast and keeps running when CinemaViewer itself fails."""

import ctypes
import os
import subprocess
import sys
import time

from .system import AUTOSTART_ARG, LAUNCHER, python_gui_exe, start_desktop

EXIT_TO_WINDOWS = 10   # CinemaViewer's exit code for 'Naar Windows'
EXIT_RESTART = 11      # CinemaViewer's exit code for 'start me again' (after an update)
RESTART_ARG = "--restart"  # a CinemaViewer started again by itself (waits for the old one)
MAX_CRASHES = 3        # this many crashes within CRASH_WINDOW seconds → back to the desktop
CRASH_WINDOW = 120
VK_SHIFT = 0x10


def _shift_held():
    if sys.platform == "win32":
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
    return _shift_held_x11()


def _shift_held_x11():
    """Is a Shift key physically down? XQueryKeymap gives 32 bytes of keycodes; Shift_L=50,
    Shift_R=62. Without X11 (no display) we assume not held."""
    try:
        x11 = ctypes.CDLL("libX11.so.6")
    except OSError:
        return False
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)
    if not display:
        return False
    try:
        keys = (ctypes.c_ubyte * 32)()
        x11.XQueryKeymap(display, keys)
        return bool(keys[50 // 8] & (1 << (50 % 8))) or bool(keys[62 // 8] & (1 << (62 % 8)))
    finally:
        x11.XCloseDisplay(display)


def _release_kiosk_policies():
    """CinemaViewer normally lifts these itself; after crashes it could not."""
    try:
        from .config import Config
        from .system import remove_kiosk_policies
        cfg = Config.load()
        remove_kiosk_policies(cfg["kiosk_applied"])
        cfg["kiosk_applied"] = []
        cfg.save()
    except Exception:  # noqa: BLE001 - the desktop must come back regardless
        pass


def supervise():
    if _shift_held():
        start_desktop()
        return 0
    crashes = []
    env = dict(os.environ, CINEMAVIEWER_SHELL="1")  # CinemaViewer restarts itself through us
    while True:
        code = subprocess.call([str(python_gui_exe()), str(LAUNCHER), AUTOSTART_ARG], env=env)
        if code == 0:            # 'Computer uitschakelen', or Windows ended the session
            return 0
        if code == EXIT_RESTART:
            continue
        if code == EXIT_TO_WINDOWS:
            start_desktop()
            return 0
        now = time.monotonic()
        crashes = [t for t in crashes if now - t < CRASH_WINDOW] + [now]
        if len(crashes) >= MAX_CRASHES:
            _release_kiosk_policies()
            start_desktop()
            return code
        time.sleep(2)
