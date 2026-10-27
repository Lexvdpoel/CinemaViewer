"""Live, downscaled copy of the projection window for the preview on the extra screens.

libVLC renders into a single window, so instead of decoding the film twice we copy the
projection window's pixels from the desktop in a background thread: GDI StretchBlt on
Windows, XGetImage on X11.
"""

import sys

if sys.platform == "win32":
    from .win_mirror import ScreenMirror  # noqa: F401
else:
    from .x11_mirror import ScreenMirror  # noqa: F401
