"""Runs a LibreOffice Impress slide show for CinemaViewer (animations, transitions and video included).

Started by presentation.SlideShow with LibreOffice's own Python (Linux: /usr/bin/python3 with python3-uno,
Windows: LibreOffice\\program\\python.exe), because the 'uno' module only exists there. Stand-alone: no
CinemaViewer imports. Commands come as JSON lines on stdin, every command gets one JSON line back:

  {"cmd": "open", "path": ..., "screen": [x, y, w, h], "slide": 0}
  {"cmd": "next"} | {"cmd": "prev"} | {"cmd": "goto", "slide": n} | {"cmd": "black", "on": true}
  {"cmd": "status"} | {"cmd": "end"}
  -> {"ok": true, "running": bool, "slide": n, "count": n, "black": bool} or {"ok": false, "error": "..."}

usage: impress_show.py <soffice> <profile-url> <pipe-name>
"""

import json
import subprocess
import sys
import time

import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.connection import NoConnectException


def note(text):
    """Progress for diavoorstelling.log (stderr)."""
    sys.stderr.write(time.strftime("%H:%M:%S ") + text + "\n")
    sys.stderr.flush()


def _monitors():
    """[(x, y, w, h)] in the system's order."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        found = []
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                  wintypes.LPARAM)

        def add(_mon, _dc, rect, _data):
            r = rect.contents
            found.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(add), 0)
        return found
    try:
        out = subprocess.run(["xrandr", "--listmonitors"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in out.splitlines()[1:]:  # " 0: +*HDMI-1 1920/530x1080/300+0+0  HDMI-1"
        try:
            geo = line.split()[2]
            size, x, y = geo.split("+")
            w, h = (int(v.split("/")[0]) for v in size.split("x"))
            found.append((int(x), int(y), w, h))
        except (IndexError, ValueError):
            continue
    return found


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


class Show:
    def __init__(self, soffice, profile, pipe):
        self.office = subprocess.Popen([soffice, f"-env:UserInstallation={profile}", "--invisible", "--norestore",
                                        "--nologo", "--nodefault", "--nolockcheck",
                                        f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext"],
                                       stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr)  # into the log
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        for _ in range(240):  # the first start of a fresh profile can take a while
            try:
                self.ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except NoConnectException:
                if self.office.poll() is not None:
                    raise RuntimeError("LibreOffice stopte direct na het starten")
                time.sleep(0.25)
        else:
            raise RuntimeError("LibreOffice reageert niet")
        note("verbonden met LibreOffice")
        self.smgr = self.ctx.ServiceManager
        self.desktop = self.smgr.createInstanceWithContext("com.sun.star.frame.Desktop", self.ctx)
        self.doc = None
        self.visible = [0]    # document indices of the slides that are shown (not hidden)
        self.wanted = False   # the show should be running (it ends by itself after the last slide)
        self.last = 0
        self.black = False
        # CinemaViewer has its own presenter view on the operator screen
        self._config("/org.openoffice.Office.Impress/Misc/Start", EnablePresenterScreen=False)

    def _config(self, path, **values):
        provider = self.smgr.createInstanceWithContext("com.sun.star.configuration.ConfigurationProvider", self.ctx)
        access = provider.createInstanceWithArguments("com.sun.star.configuration.ConfigurationUpdateAccess",
                                                      (prop("nodepath", path),))
        for key, value in values.items():
            access.setPropertyValue(key, value)
        access.commitChanges()

    def _display(self, screen):
        """LibreOffice's number for the screen with this geometry: 1 = its first screen, 0 = its own choice.
        LibreOffice counts screens in the system's monitor order: RandR monitors on Linux, EnumDisplayMonitors
        on Windows."""
        monitors = _monitors()
        note("schermen: " + ", ".join(f"{x},{y} {w}x{h}" for x, y, w, h in monitors))
        for match in (lambda m: list(m) == list(screen), lambda m: m[:2] == tuple(screen[:2])):
            for i, m in enumerate(monitors):
                if match(m):
                    return i + 1
        return 0

    def open(self, path, screen, slide=0):
        url = uno.systemPathToFileUrl(path)
        # Loaded visibly: a slide show of a hidden document crashes LibreOffice, and minimizing or hiding the
        # editing window takes the show with it. The editing window stays behind CinemaViewer's operator window.
        self.doc = self.desktop.loadComponentFromURL(url, "_blank", 0, (prop("ReadOnly", True),))
        if self.doc is None or not hasattr(self.doc, "getPresentation"):
            raise RuntimeError("LibreOffice kon de presentatie niet openen")
        note(f"geopend: {path}")
        pages = self.doc.getDrawPages()
        # the show skips hidden slides; CinemaViewer counts only the slides that are shown
        self.visible = [i for i in range(pages.getCount()) if pages.getByIndex(i).Visible] or [0]
        pres = self.doc.getPresentation()
        pres.Display = self._display(screen)
        note(f"scherm {screen} -> Display {pres.Display}")
        pres.IsFullScreen = True
        pres.IsAlwaysOnTop = True
        pres.IsMouseVisible = False
        pres.IsEndless = False
        pres.StartWithNavigator = False
        pres.AllowAnimations = True
        self.last = self.visible[min(max(0, slide), len(self.visible) - 1)]
        self._start()

    def _start(self):
        pres = self.doc.getPresentation()
        self.wanted = True
        pres.start()
        for _ in range(100):
            if pres.getController() is not None:
                break
            time.sleep(0.1)
        controller = pres.getController()
        if controller is None:
            raise RuntimeError("de diavoorstelling startte niet")
        note(f"diavoorstelling loopt: {controller.getSlideCount()} dia's")
        if self.last:
            controller.gotoSlideIndex(self.last)

    def controller(self):
        return self.doc.getPresentation().getController() if self.doc is not None else None

    def status(self):
        c = self.controller()
        running = bool(c is not None and c.isRunning())
        if self.wanted and not running and self.doc is not None:
            self._start()  # past the last slide the show ends: stay on the last slide instead
            c = self.controller()
            running = c is not None
        slide = c.getCurrentSlideIndex() if running else -1
        if slide >= 0:
            self.last = slide
        shown = max([n for n, i in enumerate(self.visible) if i <= self.last] or [0])
        return {"ok": True, "running": running, "slide": shown, "count": len(self.visible) if running else 0,
                "black": self.black}

    def handle(self, msg):
        cmd = msg.get("cmd")
        if cmd == "open":
            self.open(msg["path"], msg.get("screen") or [0, 0, 0, 0], int(msg.get("slide", 0)))
        elif cmd in ("next", "prev", "goto", "black"):
            c = self.controller()
            if c is not None:
                if self.black and cmd != "black":
                    c.resume()
                    self.black = False
                if cmd == "next":
                    c.gotoNextEffect()  # the next animation, or the next slide when there is none left
                elif cmd == "prev":
                    c.gotoPreviousEffect()
                elif cmd == "goto":
                    c.gotoSlideIndex(self.visible[min(max(0, int(msg["slide"])), len(self.visible) - 1)])
                elif msg.get("on") and not self.black:
                    c.blankScreen(0)  # black
                    self.black = True
                elif not msg.get("on") and self.black:
                    c.resume()
                    self.black = False
        elif cmd == "end":
            self.end()
            return {"ok": True, "running": False, "slide": 0, "count": 0, "black": False}
        return self.status()

    def end(self):
        self.wanted = False
        try:
            if self.doc is not None:
                self.doc.getPresentation().end()
                self.doc.close(True)
        except Exception:  # noqa: BLE001 -- closing anyway
            pass
        self.doc = None
        if self.office.poll() is not None:
            return
        try:
            self.desktop.terminate()
        except Exception:  # noqa: BLE001 -- the bridge drops when LibreOffice quits
            pass
        try:
            self.office.wait(10)
        except subprocess.TimeoutExpired:
            self.office.kill()


def reply(data):
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


def main():
    soffice, profile, pipe = sys.argv[1:4]
    try:
        show = Show(soffice, profile, pipe)
    except Exception as e:  # noqa: BLE001 -- reported to CinemaViewer
        reply({"ok": False, "error": str(e), "fatal": True})
        return 1
    reply({"ok": True, "running": False, "slide": 0, "count": 0, "black": False})
    try:
        for line in sys.stdin:
            msg = {}
            try:
                msg = json.loads(line)
                reply(show.handle(msg))
            except Exception as e:  # noqa: BLE001 -- one failed command does not end the show
                note(f"fout bij {msg.get('cmd')}: {e!r}")
                reply({"ok": False, "error": str(e)})
            if msg.get("cmd") == "end":
                return 0
    finally:
        show.end()
    return 0


if __name__ == "__main__":
    sys.exit(main())
