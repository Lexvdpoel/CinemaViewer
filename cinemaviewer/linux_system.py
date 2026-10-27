"""Linux (X11 / Cinnamon) implementation of the kiosk system integration.

Same public API as win_system.py. Uses gsettings, xdotool, xinput, pkexec,
LightDM and libX11 through ctypes, so no extra Python packages are needed.
"""
import atexit
import base64
import ctypes
import getpass
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

AUTOSTART_ARG = "--autostart"  # marks a start by the desktop at sign-in
SHELL_ARG = "--shell"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / "CinemaViewer.pyw"

AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "cinemaviewer.desktop"
SESSION_FILE = Path("/usr/share/xsessions/cinemaviewer.desktop")
LIGHTDM_CONF = Path("/etc/lightdm/lightdm.conf")


def python_gui_exe():
    return sys.executable


def _startup_command():
    return f"{shlex.quote(python_gui_exe())} {shlex.quote(str(LAUNCHER))} {AUTOSTART_ARG}"


# -- autostart ------------------------------------------------------------------
def _desktop_entry(exec_line, name, comment):
    return ("[Desktop Entry]\nType=Application\n"
            f"Name={name}\nComment={comment}\n"
            f"Exec={exec_line}\nX-GNOME-Autostart-enabled=true\n")


def autostart_enabled():
    try:
        return AUTOSTART_FILE.exists() and AUTOSTART_ARG in AUTOSTART_FILE.read_text()
    except OSError:
        return False


def set_autostart(enabled):
    if enabled:
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        AUTOSTART_FILE.write_text(_desktop_entry(_startup_command(), "CinemaViewer",
                                                 "CinemaViewer starten bij aanmelden"))
    elif AUTOSTART_FILE.exists():
        AUTOSTART_FILE.unlink()


# -- shell replacement (custom X session) ----------------------------------------
def _shell_command():
    return (f"{shlex.quote(python_gui_exe())} {shlex.quote(str(LAUNCHER))} "
            f"{SHELL_ARG} {AUTOSTART_ARG}")


def shell_enabled():
    try:
        return SESSION_FILE.exists() and SHELL_ARG in SESSION_FILE.read_text()
    except OSError:
        return False


def set_shell(enabled):
    """Install/remove a LightDM session that runs CinemaViewer instead of the desktop.
    Needs admin rights once (pkexec asks)."""
    if enabled:
        entry = _desktop_entry(_shell_command(), "CinemaViewer", "CinemaViewer kiosk").replace(
            "X-GNOME-Autostart-enabled=true\n", "")
        try:
            if SESSION_FILE.read_text() == entry:
                return True  # already up to date: no administrator question at every start
        except OSError:
            pass
        script = ("set -e\n"
                  "mkdir -p /usr/share/xsessions\n"
                  "cat > /usr/share/xsessions/cinemaviewer.desktop <<'EOF'\n"
                  f"{entry}EOF\n")
    else:
        script = "rm -f /usr/share/xsessions/cinemaviewer.desktop\n"
    return run_elevated(script) == 0


def explorer_running():
    return subprocess.run(["pgrep", "-x", "cinnamon"], capture_output=True).returncode == 0


def start_desktop(timeout=20):
    """Start the normal Cinnamon desktop inside the current X session (for 'Naar Windows').
    In the kiosk session the shell process is the session itself: it becomes the full Cinnamon
    session (settings daemon, panels, desktop icons), so the session does not end with us."""
    if explorer_running():
        return True
    session = shutil.which("cinnamon-session-cinnamon")
    if session and os.environ.get("DESKTOP_SESSION") == SESSION_FILE.stem:
        os.environ.update(XDG_CURRENT_DESKTOP="X-Cinnamon", XDG_SESSION_DESKTOP="cinnamon",
                          DESKTOP_SESSION="cinnamon")
        subprocess.run(["dbus-update-activation-environment", "--systemd", "XDG_CURRENT_DESKTOP",
                        "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"], capture_output=True)
        os.execv(session, [session])
    with open(os.devnull, "wb") as devnull:
        subprocess.Popen(["cinnamon"], stdout=devnull, stderr=devnull, start_new_session=True)
    deadline = timeout
    while deadline > 0 and not explorer_running():
        threading.Event().wait(0.5)
        deadline -= 0.5
    return explorer_running()


# -- kiosk policies (gsettings) ----------------------------------------------------
KIOSK_POLICIES = [
    ("org.cinnamon.desktop.screensaver", "lock-enabled", "false"),
    ("org.cinnamon.settings-daemon.plugins.power", "sleep-inactive-ac-type", "'nothing'"),
    ("org.cinnamon.settings-daemon.plugins.power", "sleep-inactive-battery-type", "'nothing'"),
]


def _policy_name(schema, key):
    return f"{schema}/{key}"


def _gsettings_get(schema, key):
    try:
        result = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _gsettings_set(schema, key, value):
    result = subprocess.run(["gsettings", "set", schema, key, str(value)], capture_output=True, text=True,
                            timeout=10)
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout).strip() or f"gsettings code {result.returncode}")


def kiosk_writable():
    schema, key, _ = KIOSK_POLICIES[0]
    return _gsettings_get(schema, key) is not None


def grant_kiosk_rights():
    return kiosk_writable()  # gsettings needs no elevation on Linux


def run_elevated(script):
    """Run a POSIX shell script as root via pkexec. Lines starting with '#CV-USER ' run as the
    current user instead (gsettings must happen in the user's own session). Returns an exit code;
    1223 means the administrator question was refused (same code as Windows UAC cancel)."""
    user_lines = [line[len("#CV-USER "):] for line in script.splitlines() if line.startswith("#CV-USER ")]
    for line in user_lines:
        try:
            subprocess.run(shlex.split(line), capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
    script = "\n".join(line for line in script.splitlines() if not line.startswith("#CV-USER "))
    if not script.strip():
        return 0
    handle, path = tempfile.mkstemp(suffix=".sh", prefix="cinemaviewer-")
    try:
        with os.fdopen(handle, "w") as f:
            f.write(script + "\n")
        os.chmod(path, 0o600)
        result = subprocess.run(["pkexec", "sh", path], capture_output=True, text=True, timeout=300)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if result.returncode in (126, 127):  # pkexec: authentication failure / dialog dismissed
        return 1223
    return result.returncode


# -- sign-in: autologon (LightDM) and lock screen -----------------------------------
def logon_account():
    user = getpass.getuser()
    return user, "", f"{user} op {socket.gethostname()}"


def _lightdm_autologin_user():
    try:
        text = LIGHTDM_CONF.read_text()
    except OSError:
        return ""
    in_seat = False
    found = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_seat = stripped in ("[Seat:*]", "[Seat Defaults]")
        elif in_seat and stripped.startswith("autologin-user=") and not stripped.startswith(("#", ";")):
            found = stripped.split("=", 1)[1].strip()
    return found


def autologon_enabled():
    return _lightdm_autologin_user() != ""


def autologon_user():
    return _lightdm_autologin_user()


def lockscreen_enabled(image_path):
    if (_gsettings_get("org.cinnamon.desktop.screensaver", "lock-enabled") or "").strip() != "true":
        return False
    uri = (_gsettings_get("org.cinnamon.desktop.screensaver", "picture-uri") or "").strip("'\"")
    return uri == Path(image_path).as_uri()


def check_windows_password(user, domain, password):
    """True when the password is accepted. Uses pamtester when installed; without it we cannot
    verify and let the setup continue (LightDM autologin itself needs no stored password)."""
    pamtester = shutil.which("pamtester")
    if not pamtester:
        return True
    try:
        result = subprocess.run([pamtester, "-q", "login", user], input=password + "\n",
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return True
    return result.returncode == 0


def protect_for_user(text):
    """Obfuscate for passing through the elevated script (LightDM autologin needs no password,
    so this is not real encryption on Linux)."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def sign_in_script(autologon=None, user="", domain="", password_blob="", lockscreen=None, image_path="",
                   hklm="HKLM:"):
    """Shell script (run elevated) that switches LightDM autologon and/or the lock screen.
    None = leave unchanged."""
    lines = ["set -e"]
    if autologon is not None:
        lines += [
            "CONF=/etc/lightdm/lightdm.conf",
            "[ -f \"$CONF\" ] || : > \"$CONF\"",
            "sed -i -E 's/^[#;]?[[:space:]]*autologin-user=.*/#autologin-user=/' \"$CONF\"",
            "sed -i -E 's/^[#;]?[[:space:]]*autologin-user-timeout=.*/#autologin-user-timeout=/' \"$CONF\"",
        ]
        if autologon:
            lines.append(f"sed -i -E '/^\\[Seat:\\*\\]/a autologin-user={shlex.quote(user)}\\n"
                         "autologin-user-timeout=0' \"$CONF\"")
    if lockscreen is not None:
        if lockscreen:
            lines.append("#CV-USER gsettings set org.cinnamon.desktop.screensaver lock-enabled true")
            lines.append("#CV-USER gsettings set org.cinnamon.desktop.screensaver picture-uri "
                         + shlex.quote(Path(image_path).as_uri()))
        else:
            lines.append("#CV-USER gsettings set org.cinnamon.desktop.screensaver lock-enabled false")
    lines.append("exit 0")
    return "\n".join(lines)


def apply_kiosk_policies():
    """Turn the restrictions on; returns the names we set (so we only remove our own later)."""
    applied = []
    for schema, key, value in KIOSK_POLICIES:
        if _gsettings_get(schema, key) == value:
            continue  # already set by the user: leave it alone
        _gsettings_set(schema, key, value)
        applied.append(_policy_name(schema, key))
    return applied


def remove_kiosk_policies(names):
    for schema, key, _ in KIOSK_POLICIES:
        if _policy_name(schema, key) not in names:
            continue
        subprocess.run(["gsettings", "reset", schema, key], capture_output=True, timeout=10)


# -- input blocking (X11 grabs + xinput) ---------------------------------------------
_X_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


def _load_x11():
    for name in ("libX11.so.6", "libX11.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


SHIFT, LOCK, CONTROL, MOD1, MOD2 = 1, 2, 4, 8, 16  # X modifier masks (module level: the
# comprehensions in KeyboardBlocker.GRABS cannot see class attributes)


class KeyboardBlocker(threading.Thread):
    """Passive X11 grabs for the keys Windows would use to escape: Super, Alt+Tab, Alt+Esc,
    Ctrl+Esc and Ctrl+Shift+Esc. Grabbed keys are reported to our own input-only window and
    released with AsyncKeyboard, so they never reach an application."""

    KEYSYMS = {"Super_L": 0xFFEB, "Super_R": 0xFFEC, "Tab": 0xFF09, "Escape": 0xFF1B}
    SHIFT, LOCK, CONTROL, MOD1, MOD2 = SHIFT, LOCK, CONTROL, MOD1, MOD2
    GRABS = (
        [("Super_L", m) for m in (0, LOCK, MOD2, LOCK | MOD2)] +
        [("Super_R", m) for m in (0, LOCK, MOD2, LOCK | MOD2)] +
        [("Tab", MOD1 | m) for m in (0, LOCK, MOD2, LOCK | MOD2)] +
        [("Escape", MOD1 | m) for m in (0, LOCK, MOD2, LOCK | MOD2)] +
        [("Escape", CONTROL | m) for m in (0, LOCK, MOD2, LOCK | MOD2, SHIFT, SHIFT | LOCK,
                                           SHIFT | MOD2, SHIFT | LOCK | MOD2)]
    )
    KEY_PRESS_MASK = 1
    ASYNC = 0

    def __init__(self):
        super().__init__(daemon=True, name="CinemaViewer-keyboard")
        self._ready = threading.Event()
        self._stopped = False
        self._stop_r, self._stop_w = os.pipe()
        self.installed = False
        self.blocked = 0

    def run(self):
        import select
        x11 = _load_x11()
        if x11 is None:
            self._ready.set()
            return
        # Every Display* must be declared as a pointer: undeclared, ctypes passes it as a 32-bit int.
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong,
                                            ctypes.c_ulong]
        x11.XCreateSimpleWindow.restype = ctypes.c_ulong
        x11.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_uint
        x11.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x11.XConnectionNumber.restype = ctypes.c_int
        x11.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        x11.XAllowEvents.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        # A key another program already grabbed (e.g. Super in Cinnamon) gives BadAccess; Xlib's
        # default handler would end the whole process for that.
        self._error_handler = _X_ERROR_HANDLER(lambda _display, _event: 0)
        x11.XSetErrorHandler(self._error_handler)
        try:
            display = x11.XOpenDisplay(None)
        except Exception:  # noqa: BLE001
            display = None
        if not display:
            self._ready.set()
            return
        root = x11.XDefaultRootWindow(display)
        win = x11.XCreateSimpleWindow(display, root, 0, 0, 1, 1, 0, 0, 0)
        x11.XSelectInput(display, win, self.KEY_PRESS_MASK)
        grabbed = 0
        for keysym_name, mods in self.GRABS:
            code = x11.XKeysymToKeycode(display, self.KEYSYMS[keysym_name])
            if not code:
                continue
            for extra in (0, self.LOCK):  # CapsLock variants (NumLock is Mod2, already covered)
                if x11.XGrabKey(display, code, mods | extra, win, False, self.ASYNC, self.ASYNC):
                    grabbed += 1  # Xlib returns 1; a refused grab only shows up as an (ignored) error
        x11.XSync(display, False)
        self.installed = grabbed > 0
        self._ready.set()
        fd = x11.XConnectionNumber(display)
        event = ctypes.create_string_buffer(192)
        while not self._stopped:
            readable, _, _ = select.select([self._stop_r, fd], [], [], 0.2)
            if self._stop_r in readable:
                break
            if fd in readable:
                x11.XNextEvent(display, event)
                self.blocked += 1
                x11.XAllowEvents(display, self.ASYNC, 0)  # AsyncKeyboard: swallow and let go
        x11.XCloseDisplay(display)

    def start(self):
        super().start()
        self._ready.wait(2)
        return self.installed

    def stop(self):
        self._stopped = True
        try:
            os.write(self._stop_w, b"x")
        except OSError:
            pass
        if self.is_alive():
            self.join(2)


class MouseBlocker:
    """Disables all pointer devices with xinput while CinemaViewer starts at sign-in; lets go
    by itself after `timeout` seconds so a hanging start can never leave the PC without a mouse."""

    def __init__(self, timeout=90):
        self._ids = []
        self.installed = False
        self.blocked = 0
        self._failsafe = threading.Timer(timeout, self.stop)
        self._failsafe.daemon = True

    def start(self):
        xinput = shutil.which("xinput")
        if not xinput:
            return False
        try:
            listed = subprocess.run([xinput, "list", "--id-only"], capture_output=True, text=True, timeout=10)
            for line in listed.stdout.split():
                kind = subprocess.run([xinput, "list", "--class", line], capture_output=True, text=True,
                                      timeout=10)
                if kind.stdout.strip().lower() in ("pointer", "touchpad"):
                    result = subprocess.run([xinput, "disable", line], capture_output=True, timeout=10)
                    if result.returncode == 0:
                        self._ids.append(line)
                        self.blocked += 1
        except (OSError, subprocess.SubprocessError):
            pass
        self.installed = bool(self._ids)
        if self.installed:
            self._failsafe.start()
        return self.installed

    def stop(self):
        self._failsafe.cancel()
        xinput = shutil.which("xinput")
        while self._ids and xinput:
            try:
                subprocess.run([xinput, "enable", self._ids.pop()], capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                break


_startup_mouse_blocker = None


def block_mouse_during_autostart(argv):
    """Block the mouse when the desktop started CinemaViewer (sign-in), until release_startup_mouse().
    Called before the heavy imports so the block begins as early as possible."""
    global _startup_mouse_blocker
    if AUTOSTART_ARG not in argv or not (autostart_enabled() or shell_enabled()):
        return
    _startup_mouse_blocker = MouseBlocker()
    _startup_mouse_blocker.start()


def release_startup_mouse():
    global _startup_mouse_blocker
    if _startup_mouse_blocker is not None:
        _startup_mouse_blocker.stop()
        _startup_mouse_blocker = None


# -- desktop wallpaper -----------------------------------------------------------
BACKGROUND_TYPES = {0: "afbeelding", 1: "effen kleur", 2: "diavoorstelling", 3: "diavoorstelling"}

_BG_SCHEMA = "org.cinnamon.desktop.background"
_STYLE_TO_OPTION = {"10": "zoom", "6": "spanned", "2": "stretched", "0": "centered"}
_OPTION_TO_STYLE = {"zoom": ("10", "0"), "spanned": ("6", "0"), "stretched": ("2", "0"),
                    "centered": ("0", "0"), "wallpaper": ("0", "1")}


def _file_uri(path):
    return Path(path).as_uri()


def current_wallpaper():
    """What the desktop shows now, so it can be put back later."""
    uri = (_gsettings_get(_BG_SCHEMA, "picture-uri") or "").strip("'\"")
    option = (_gsettings_get(_BG_SCHEMA, "picture-options") or "zoom").strip("'\"")
    style, tile = _OPTION_TO_STYLE.get(option, ("10", "0"))
    path = uri
    if path.startswith("file://"):
        from urllib.parse import unquote, urlparse
        path = unquote(urlparse(path).path)
    return {"path": path, "style": style, "tile": tile, "type": 0}


def set_wallpaper(path, style="10", tile="0"):
    """Set a picture as desktop background. No admin needed."""
    option = "wallpaper" if str(tile) == "1" else _STYLE_TO_OPTION.get(str(style), "zoom")
    _gsettings_set(_BG_SCHEMA, "picture-options", f"'{option}'")
    _gsettings_set(_BG_SCHEMA, "picture-uri", f"'{_file_uri(path)}'")


# -- taskbar (Cinnamon panels) ------------------------------------------------------
def _xdotool(args):
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return ""
    try:
        result = subprocess.run([xdotool] + args, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _dock_windows(visible_only=False):
    args = (["--onlyvisible"] if visible_only else []) + ["search", "--type", "_NET_WM_WINDOW_TYPE_DOCK"]
    return [int(word) for word in _xdotool(args).split() if word.lstrip("-").isdigit()]


def taskbar_autohide():
    out = _cinnamon_eval("try{''+Main.panelManager.getAllPanels().every("
                         "function(p){return p.get_mode()===3})}catch(e){'false'}")
    return "true" in out


def set_taskbar_autohide(enabled):
    """Hide the panels like Cinnamon's own 'auto-hide' (mode 3 = DROP). Best effort: on other
    desktops nothing happens."""
    _cinnamon_eval("try{Main.panelManager.getAllPanels().forEach(function(p){p.set_mode(%d)})}"
                   "catch(e){}" % (3 if enabled else 2))


def _cinnamon_eval(js):
    try:
        result = subprocess.run(["gdbus", "call", "--session", "--dest", "org.Cinnamon",
                                 "--object-path", "/org/Cinnamon", "--method", "org.Cinnamon.Eval", js],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def show_taskbars(visible):
    """Hide/show the panels on every monitor (only while CinemaViewer runs; Cinnamon shows them
    again after a restart of the session)."""
    if visible:
        for win in _dock_windows():
            _xdotool(["windowmap", str(win)])
    else:
        for win in _dock_windows(visible_only=True):
            _xdotool(["windowunmap", str(win)])


def taskbars_visible():
    visible = set(_dock_windows(visible_only=True))
    return [win in visible for win in _dock_windows()]


def open_background_settings():
    if shutil.which("cinnamon-settings"):
        subprocess.Popen(["cinnamon-settings", "background"])
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", "cinnamon-settings://background"])


def shutdown_computer():
    """Ask the system to power off; raises RuntimeError when it refuses."""
    result = subprocess.run(["systemctl", "poweroff"], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"code {result.returncode}")


atexit.register(release_startup_mouse)
