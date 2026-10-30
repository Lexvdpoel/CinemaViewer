"""Diagnose op afstand: een webpagina met schermen, videokaarten, HDMI-geluid en logregels.

Aan te zetten in Instellingen → Systeem; gaat na HOURS uur vanzelf uit. Alleen wie de code kent krijgt de
pagina (GET http://<ip>:PORT/<code>); na te veel foute codes stopt de poort. Wachtwoorden en tokens staan
er nooit in. Alleen met het aparte vinkje 'commando's toestaan' voert POST http://<ip>:PORT/<code>/run een
shellcommando uit (als de gebruiker van CinemaViewer, niet als root) en geeft de uitvoer terug; elk
commando wordt gelogd.
"""

import faulthandler
import glob
import hmac
import socket
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
WATCH_INTERVAL = 20         # seconds between self-checks of the port
OUTPUT_LIMIT = 400_000

_C_ENV = dict(os.environ, LC_ALL="C")

# Read-only commands whose output helps with screens and (HDMI) sound; output is capped.
_LINUX_COMMANDS = [
    ("xrandr --current", ["xrandr", "--current"]),  # (--query probes every output again: screens may blink)
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


def _log_path():
    from .config import DATA_DIR
    return DATA_DIR / "remote_diag.log"


def log(message):
    """One line in remote_diag.log (kept short: the newest ~2000 lines)."""
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(errors="replace").splitlines()[-2000:] if path.exists() else []
        lines.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{threading.current_thread().name}] {message}")
        path.write_text("\n".join(lines) + "\n")
    except OSError:
        pass


def _dump_threads(reason):
    """Where every thread is right now, for a port that stopped answering."""
    try:
        with open(_log_path(), "a") as f:
            f.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {reason}: threads\n")
            f.flush()
            faulthandler.dump_traceback(f, all_threads=True)
    except OSError:
        pass


class _Server(ThreadingHTTPServer):
    request_queue_size = 64
    daemon_threads = True
    allow_reuse_address = True


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
        self._handler = None
        self._stopping = False

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 30  # a connection that sends nothing is closed

            def handle_one_request(self):
                started = time.monotonic()
                try:
                    super().handle_one_request()
                except Exception as e:  # noqa: BLE001 - logged, the server goes on
                    log(f"fout bij verzoek van {self.client_address[0]}: {e!r}")
                kind = (getattr(self, "command", "") or "?") + (" run" if "/run" in getattr(self, "path", "") else "")
                if not getattr(self, "path", "").startswith("/_ping"):
                    log(f"{self.client_address[0]} {kind} klaar in {time.monotonic() - started:.1f} s")

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
                if self.path == "/_ping":  # self-check of the port, says nothing
                    self._send(200, "ok\n")
                    return
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

        self._handler = Handler
        self._serve()
        threading.Thread(target=self._watch, name="remote-diag-watch", daemon=True).start()
        log(f"poort {PORT} open")

    def _serve(self):
        self._server = _Server(("0.0.0.0", PORT), self._handler)
        threading.Thread(target=self._server.serve_forever, name="remote-diag", daemon=True).start()

    def _ping(self):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=8) as s:
                s.settimeout(8)
                s.sendall(b"GET /_ping HTTP/1.0\r\n\r\n")
                return s.recv(64).startswith(b"HTTP/")
        except OSError:
            return False

    def _watch(self):
        """Every WATCH_INTERVAL s: does the port still answer? If not, write down where the threads are and
        open the port anew (the old listening socket is closed without waiting for its loop)."""
        while not self._stopping:
            time.sleep(WATCH_INTERVAL)
            if self._stopping or self._server is None or self._ping():
                continue
            _dump_threads("poort antwoordt niet meer")
            old, self._server = self._server, None
            try:
                old.socket.close()
            except OSError:
                pass
            try:
                self._serve()
                log("poort opnieuw geopend")
            except OSError as e:
                log(f"poort opnieuw openen mislukt: {e}")

    def stop(self):
        self._stopping = True
        server, self._server = self._server, None
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()  # never wait on a stuck loop
            try:
                server.server_close()
            except OSError:
                pass
            log("poort gesloten")

    @property
    def running(self):
        return self._server is not None
