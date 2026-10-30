"""Live, downscaled copy of the projection window for the preview on the extra screens.

libVLC renders into a single window, so instead of decoding the film twice we copy the
projection window's pixels from the desktop in a background thread: GDI StretchBlt on
Windows, XGetImage on X11. On Linux a playing film is copied from the player itself instead
(PlayerMirror), see there.
"""

import os
import sys
import tempfile
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

if sys.platform == "win32":
    from .win_mirror import ScreenMirror  # noqa: F401
else:
    from .x11_mirror import ScreenMirror  # noqa: F401


class PlayerMirror(QThread):
    """Preview from the player's own decoded pictures (a VLC snapshot, scaled down by VLC). Reading the
    screen back with XGetImage takes 100–200 ms per frame on an older Radeon and holds up the X server
    meanwhile, so the film on the projector stutters; a snapshot only costs the CPU a few ms."""

    frame = Signal(QImage)

    def __init__(self, player, width=640, fps=6):
        super().__init__()
        self.player = player
        self.width = width
        self.interval = 1.0 / fps
        self._running = True
        folder = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
        self._path = os.path.join(folder, f"cinemaviewer-preview-{os.getpid()}.png")

    def stop(self):
        self._running = False
        self.wait(2000)

    def run(self):
        try:
            while self._running:
                started = time.monotonic()
                if self.player.state == "playing" and \
                        self.player.mp.video_take_snapshot(0, self._path, self.width, 0) == 0:
                    image = QImage(self._path)
                    if not image.isNull():
                        self.frame.emit(image)
                time.sleep(max(0.05, self.interval - (time.monotonic() - started)))
        finally:
            try:
                os.unlink(self._path)
            except OSError:
                pass
