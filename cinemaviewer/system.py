"""Platform dispatch: Windows uses win_system, everything else uses linux_system."""
import sys

if sys.platform == "win32":
    from .win_system import *  # noqa: F401,F403
else:
    from .linux_system import *  # noqa: F401,F403
