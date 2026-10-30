"""Diagnose op afstand: een webpagina die alleen leest (schermen, videokaarten, HDMI-geluid, logregels).

Aan te zetten in Instellingen → Systeem; gaat na HOURS uur vanzelf uit. Alleen wie de code kent krijgt de
pagina (http://<ip>:PORT/<code>); na te veel foute codes stopt de poort. Er wordt niets uitgevoerd of
gewijzigd op verzoek van buiten, en wachtwoorden en tokens staan er nooit in.
"""

import glob
import hmac
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
HOURS = 4
MAX_FAILURES = 20

_C_ENV = dict(os.environ, LC_ALL="C")

# Read-only commands whose output helps with screens and (HDMI) sound; output is capped.
_LINUX_COMMANDS = [
    ("xrandr --query", ["xrandr", "--query"]),
    ("xrandr --listproviders", ["xrandr", "--listproviders"]),
    ("pactl info", ["pactl", "info"]),
    ("pactl list cards", ["pactl", "list", "cards"]),
    ("pactl list short sinks", ["pactl", "list", "short", "sinks"]),
    ("pactl list short sink-inputs", ["pactl", "list", "short", "sink-inputs"]),
    ("aplay -l", ["aplay", "-l"]),
    ("lspci -nnk", ["lspci", "-nnk"]),
    ("pipewire status", ["systemctl", "--user", "--no-pager", "status", "pipewire", "pipewire-pulse", "wireplumber"]),
    ("journal (deze sessie)", ["journalctl", "--user", "-b", "-n", "150", "--no-pager"]),
    ("ip -4 addr", ["ip", "-4", "addr"]),
]


def new_code():
    return secrets.token_hex(4)


def _command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=6, env=_C_ENV)
        text = (result.stdout + result.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        text = f"({e})"
    return text[-20000:]


def _tail(path, lines=150):
    try:
        with open(os.path.expanduser(path), errors="replace") as f:
            return "".join(f.readlines()[-lines:]).strip()
    except OSError as e:
        return f"({e.strerror})"


def system_report():
    """Everything outside the app itself (runs in the server thread)."""
    from . import display
    parts = [("diagnose", display.diagnosis())]
    if sys.platform != "win32":
        for title, args in _LINUX_COMMANDS:
            parts.append((title, _command(args)))
        eld = []
        for path in sorted(glob.glob("/proc/asound/card*/eld#*")):
            eld.append(f"--- {path}\n" + _tail(path, 40))
        parts.append(("ELD-bestanden", "\n".join(eld) or "(geen)"))
        parts.append(("~/.xsession-errors", _tail("~/.xsession-errors")))
    return "\n\n".join(f"===== {title}\n{text}" for title, text in parts)


class DiagServer:
    def __init__(self, code, app_report):
        """app_report(): text about the app, prepared in the GUI thread (Qt is not thread-safe)."""
        self.code = code
        self.app_report = app_report
        self.failures = 0
        self._server = None

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                given = self.path.strip("/").split("?", 1)[0]
                if not hmac.compare_digest(given.encode(), owner.code.encode()):
                    owner.failures += 1
                    time.sleep(1.0)
                    self._send(403, "Onjuiste code.\n")
                    if owner.failures >= MAX_FAILURES:  # someone is guessing: close the port
                        threading.Thread(target=owner.stop, daemon=True).start()
                    return
                try:
                    text = owner.app_report() + "\n\n" + system_report()
                except Exception as e:  # noqa: BLE001 - always answer
                    text = f"Fout bij verzamelen: {e}"
                self._send(200, text)

            def _send(self, status, text):
                body = text.encode("utf-8", "replace")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        threading.Thread(target=self._server.serve_forever, name="remote-diag", daemon=True).start()

    def stop(self):
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    @property
    def running(self):
        return self._server is not None
