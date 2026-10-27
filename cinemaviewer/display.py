"""Platform dispatch for forced display outputs and audio profiles (see win_display / linux_display)."""
import sys

if sys.platform == "win32":
    from .win_display import *  # noqa: F401,F403
else:
    from .linux_display import *  # noqa: F401,F403
