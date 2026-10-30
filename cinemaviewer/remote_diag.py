"""Diagnose op afstand: een webpagina met schermen, videokaarten, HDMI-geluid en logregels.

Aan te zetten in Instellingen → Systeem; gaat na HOURS uur vanzelf uit. Alleen wie de code kent krijgt de
pagina (GET http://<ip>:PORT/<code>); na te veel foute codes stopt de poort. Wachtwoorden en tokens staan
er nooit in. Alleen met het aparte vinkje 'commando's toestaan' voert POST http://<ip>:PORT/<code>/run een
shellcommando uit (als de gebruiker van CinemaViewer, niet als root) en geeft de uitvoer terug; elk
commando wordt gelogd.
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
COMMAND_TIMEOUT = 120
OUTPUT_LIMIT = 400_000

_C_ENV = dict(os.environ, LC_ALL="C")

# Read-only commands whose output helps with screens and (HDMI) sound; output is capped.
_LINUX_COMMANDS = [
    ("xrandr --query", ["xrandr", "--query"]),
    ("xrandr --listproviders", ["xrandr", "--listproviders"]),
    ("xrandr --props (EDID en eigenschappen per uitgang)", ["xrandr", "--current", "--props"]),
    ("kernel: HDMI/geluid", ["sh", "-c", "journalctl -k -b --no-pager | grep -iE 'nouveau|hdmi|hda|snd|eld|audio' | tail -n 80"]),
    ("connectoren (kernel)", ["sh", "-c", "for c in /sys/class/drm/card*-*; do echo \"$c $(cat $c/status 2>/dev/null) "
                                          "$(cat $c/enabled 2>/dev/null)\"; done"]),
    ("pactl info", ["pactl", "info"]),
    ("pactl list cards", ["pactl", "list", "cards"]),
    ("pactl list short sinks", ["pactl", "list", "short", "sinks"]),
    ("pactl list short sink-inputs", ["pactl", "list", "short", "sink-inputs"]),
    ("aplay -l", ["aplay", "-l"]),
    ("lspci -nnk", ["lspci", "-nnk"]),
    ("pipewire status", ["systemctl", "--user", "--no-pager", "status", "pipewire", "pipewire-pulse", "wireplumber"]),
    ("journal (deze sessie)", ["journalctl", "--user", "-b", "-n", "150", "--no-pager"]),
    ("ip -4 addr", ["ip", "-4", "addr"]),
    ("kernel", ["uname", "-a"]),
    ("modules (geluid/video)", ["sh", "-c", "lsmod | grep -E 'snd_hda|nouveau|i915|snd_sof' | sort"]),
    ("hda-modulen opties", ["sh", "-c", "for m in snd_hda_intel snd_hda_codec_hdmi snd_hda_codec_nvhdmi; do "
                                       "for p in /sys/module/$m/parameters/*; do [ -r \"$p\" ] && "
                                       "echo \"$m.$(basename $p)=$(cat $p 2>/dev/null)\"; done; done"]),
    ("NVIDIA-geluidschip (codec, pinnen)", ["sh", "-c", "for f in /proc/asound/NVidia/codec#*; do echo \"--- $f\"; "
                                                       "cat \"$f\"; done"]),
]


PRIVATE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


def firewall_active():
    """Linux: is ufw switched on (Linux Mint blocks incoming connections with it)? Readable without root."""
    if sys.platform == "win32":
        return False
    try:  # (the ufw service also reports 'active' when ufw itself is switched off)
        with open("/etc/ufw/ufw.conf") as f:
            return any(line.strip().lower() == "enabled=yes" for line in f)
    except OSError:
        return False


def firewall_script():
    """Root script that lets the local network (not the internet) reach the diagnosis port."""
    return "\n".join(f"ufw allow proto tcp from {net} to any port {PORT} comment 'CinemaViewer diagnose'"
                      for net in PRIVATE_NETS)


def new_code():
    return secrets.token_hex(6)


def run_command(command):
    """A shell command from the support side; returns its output and exit code as text."""
    shell = ["bash", "-c", command] if sys.platform != "win32" else ["powershell", "-NoProfile", "-Command", command]
    started = time.monotonic()
    try:
        result = subprocess.run(shell, capture_output=True, timeout=COMMAND_TIMEOUT, env=_C_ENV,
                                stdin=subprocess.DEVNULL)
        out = (result.stdout + result.stderr).decode("utf-8", "replace")
        code = result.returncode
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace") + f"\n(afgebroken na {COMMAND_TIMEOUT} s)"
        code = -1
    except OSError as e:
        out, code = str(e), -1
    if len(out) > OUTPUT_LIMIT:
        out = out[:OUTPUT_LIMIT] + f"\n… ({len(out) - OUTPUT_LIMIT} tekens weggelaten)"
    return f"{out.rstrip()}\n[exit {code}, {time.monotonic() - started:.1f} s]\n"


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
    def __init__(self, code, app_report, allow_commands=lambda: False, on_command=None):
        """app_report(): text about the app, prepared in the GUI thread (Qt is not thread-safe).
        allow_commands(): whether /run may execute; on_command(text) is told about every command."""
        self.code = code
        self.app_report = app_report
        self.allow_commands = allow_commands
        self.on_command = on_command
        self.failures = 0
        self._server = None

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _authorized(self, suffix=""):
                given = self.path.strip("/").split("?", 1)[0]
                if hmac.compare_digest(given.encode(), (owner.code + suffix).encode()):
                    return True
                owner.failures += 1
                time.sleep(1.0)
                self._send(403, "Onjuiste code.\n")
                if owner.failures >= MAX_FAILURES:  # someone is guessing: close the port
                    threading.Thread(target=owner.stop, daemon=True).start()
                return False

            def do_POST(self):
                if not self._authorized("/run"):
                    return
                if not owner.allow_commands():
                    self._send(403, "Commando's zijn niet toegestaan (Instellingen → Systeem).\n")
                    return
                length = min(int(self.headers.get("Content-Length") or 0), 100_000)
                command = self.rfile.read(length).decode("utf-8", "replace").strip()
                if not command:
                    self._send(400, "Geen commando.\n")
                    return
                if owner.on_command:
                    owner.on_command(command)
                self._send(200, run_command(command))

            def do_GET(self):
                if not self._authorized():
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
