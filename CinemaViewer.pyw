"""Starts CinemaViewer without a console window (used by 'start with Windows')."""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# Started by Windows as the shell (instead of Explorer): guard CinemaViewer from a separate process.
if "--shell" in sys.argv:
    from cinemaviewer.shell import supervise

    sys.exit(supervise())

# Started by Windows at sign-in: keep the mouse blocked until CinemaViewer is up (before Qt loads).
from cinemaviewer.system import block_mouse_during_autostart  # noqa: E402

block_mouse_during_autostart(sys.argv)

from cinemaviewer.main import main  # noqa: E402

sys.exit(main())
