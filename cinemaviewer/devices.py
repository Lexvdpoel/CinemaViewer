"""Control of the projector (PJLink, or InFocus over the network/RS-232) and the Denon receiver
(Denon telnet protocol).

Every device has its own worker thread, so a device that is slow or unreachable never blocks the UI.
"""

import hashlib
import queue
import re
import select
import socket
import threading
import time

from PySide6.QtCore import QObject, Signal

PJLINK_PORT = 4352
DENON_PORT = 23
DENON_MAX_MV = 98                 # Denon master volume steps: 0 = '---' (−80 dB), 80 = 0 dB, 98 = +18 dB
DENON_INPUTS = ("BD", "DVD", "SAT/CBL", "TV", "GAME", "DVR", "V.AUX", "DOCK", "CD", "NET/USB")
RETRY_PAUSE = 2.0                 # seconds between attempts to switch a device off
COOLDOWN_WAIT = 120.0             # a projector that is still cooling down refuses 'on' for a while

_STOP = object()
_VOLUME = object()


class DeviceError(Exception):
    pass


def mv_to_db(mv):
    return mv - 80


def db_to_mv(db):
    return max(0, min(DENON_MAX_MV, int(round(db)) + 80))


class _Worker:
    """Runs queued jobs one after another on a daemon thread."""

    def __init__(self, name, on_error):
        self.name = name
        self.on_error = on_error
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, fn, *args):
        self._q.put((fn, args))

    def submit_confirmed(self, fn, attempts, deadline, done):
        """Runs fn up to `attempts` times (not past `deadline`) and sets the event `done` once it worked."""
        self.submit(self._retry, fn, attempts, deadline, done)

    def _retry(self, fn, attempts, deadline, done):
        for attempt in range(attempts):
            try:
                fn()
            except (OSError, DeviceError):
                if attempt == attempts - 1 or time.monotonic() + RETRY_PAUSE >= deadline:
                    return
                time.sleep(RETRY_PAUSE)
            else:
                done.set()
                return

    def close(self, timeout):
        """Finish the queued jobs (at most `timeout` seconds) and stop the thread."""
        self._q.put(_STOP)
        self._thread.join(timeout)

    def _idle(self):
        pass

    def _run(self):
        # An unexpected error must never end this thread: the device would silently stop
        # responding until CinemaViewer is restarted.
        while True:
            try:
                job = self._q.get(timeout=0.5)
            except queue.Empty:
                try:
                    self._idle()
                except Exception as e:  # noqa: BLE001
                    self.on_error(f"{self.name}: onverwachte fout ({e})")
                continue
            if job is _STOP:
                self._cleanup()
                return
            self._do(job)

    def _do(self, job):
        fn, args = job
        try:
            fn(*args)
        except (OSError, DeviceError) as e:
            self.on_error(f"{self.name}: {e}")
        except Exception as e:  # noqa: BLE001 - see _run
            self.on_error(f"{self.name}: onverwachte fout ({e})")

    def _cleanup(self):
        pass


# -- projector: power state ------------------------------------------------------
class _ProjectorWorker(_Worker):
    """Keeps track of the projector's power state ('off', 'warming', 'on', 'cooling' or
    'unreachable') and reports changes through on_state; also changes made with its own remote."""

    STATE_POLL = 15.0          # seconds between state checks
    FAST_POLL = 3.0            # ... right after a power command, while it warms up / cools down

    def __init__(self, on_error, on_state=None):
        self.on_state = on_state
        self.state = None
        self._state_at = 0.0
        self._fast_until = 0.0
        super().__init__("Projector", on_error)

    def power(self, on, retry_until=0.0):
        """retry_until: keep retrying while the lamp is still cooling down."""
        self._report_state("warming" if on else "cooling")
        self._fast_until = time.monotonic() + 180
        self.submit(self._power, on, retry_until)

    def _report_state(self, state):
        if state != self.state:
            self.state = state
            if self.on_state:
                self.on_state(state)

    def _idle(self):
        now = time.monotonic()
        if now - self._state_at < (self.FAST_POLL if now < self._fast_until else self.STATE_POLL):
            return
        self._state_at = now
        try:
            state = self._read_state()
        except (OSError, DeviceError):
            state = "unreachable"
        self._report_state(state)

    def _read_state(self):
        raise NotImplementedError


# -- projector (PJLink class 1) ------------------------------------------------
_PJLINK_ERRORS = {
    "ERR1": "opdracht niet ondersteund",
    "ERR2": "ongeldige parameter",
    "ERR3": "projector is nog aan het opwarmen of afkoelen",
    "ERR4": "projectorfout (lamp/temperatuur?)",
}


def _read_cr(sock):
    """Reads one line ending in a carriage return (PJLink sends no line feeds)."""
    line = b""
    while not line.endswith(b"\r"):
        chunk = sock.recv(1)
        if not chunk:
            break
        line += chunk
    return line.decode("ascii", "replace").strip()


def pjlink(host, password, command, timeout=4.0):
    """Sends one PJLink command (e.g. 'POWR 1') and returns the answer after '='."""
    with socket.create_connection((host, PJLINK_PORT), timeout=timeout) as s:
        hello = _read_cr(s)
        if not hello.startswith("PJLINK"):
            raise DeviceError(f"geen PJLink-antwoord ({hello or 'leeg'})")
        prefix = ""
        if hello.startswith("PJLINK 1"):
            if not password:
                raise DeviceError("de projector vraagt een PJLink-wachtwoord")
            prefix = hashlib.md5((hello.split()[2] + password).encode("ascii")).hexdigest()
        s.sendall(f"{prefix}%1{command}\r".encode("ascii"))
        answer = _read_cr(s)
    if answer == "PJLINK ERRA":
        raise DeviceError("PJLink-wachtwoord onjuist")
    value = answer.partition("=")[2]
    if value in _PJLINK_ERRORS:
        raise DeviceError(_PJLINK_ERRORS[value])
    return value


class Projector(_ProjectorWorker):
    def __init__(self, host, password, on_error, on_state=None):
        self.host = host
        self.password = password
        super().__init__(on_error, on_state)

    def _read_state(self):
        return {"0": "off", "1": "on", "2": "cooling", "3": "warming"}.get(
            pjlink(self.host, self.password, "POWR ?", timeout=3.0), "unreachable")

    def _power(self, on, retry_until):
        while True:
            try:
                pjlink(self.host, self.password, f"POWR {1 if on else 0}")
                return
            except DeviceError as e:
                if "opwarmen" not in str(e) or time.monotonic() > retry_until:
                    raise
            time.sleep(2)

    def _off(self):
        pjlink(self.host, self.password, "POWR 0", timeout=3.0)

    @staticmethod
    def test(host, password):
        name = pjlink(host, password, "NAME ?") or "projector"
        state = {"0": "uit", "1": "aan", "2": "koelt af", "3": "warmt op"}.get(
            pjlink(host, password, "POWR ?"), "onbekend")
        return f"{name} ({state})"


# -- projector (InFocus protocol, network port 23 or RS-232) ------------------------
# Commands are three letters in parentheses: (PWR?) reads, (PWR1) writes. A read answers with the
# range and the value, e.g. (0-1,1) or (0-32766,1234); '?' means 'not understood'. The projector
# needs a pause after a write (the manual asks 3 s between commands). Dual-lamp models (IN5535)
# report both lamps: LMP/LDH hours, DLS/DLD status, DLI single/dual mode.
INFOCUS_PORT = 23
INFOCUS_BAUDS = (19200, 38400, 9600, 115200)   # tried in this order when the speed is 'automatic'
WRITE_PAUSE = 3.0
COMMAND_GAP = 0.25                           # between reads; a projector misses commands sent too fast
WARMING = ("2", "3", "4", "5", "8", "9", "10", "15", "16")   # SYS values after an accepted 'on'
COOLING = ("0", "1", "11", "12")                             # ... after an accepted 'off'
INFOCUS_STATUS = {
    "0": "stand-by (start)", "1": "stand-by", "2": "warmt op", "3": "warmt op", "4": "lamp ontsteekt",
    "5": "lamp ontsteekt", "7": "aan", "8": "beeld start", "9": "beeld start", "10": "aan", "11": "koelt af",
    "12": "koelt af", "13": "storing", "14": "storing", "15": "warmt op (koud)", "16": "beeld start",
}
INFOCUS_ERRORS = {
    "1": "lamp gaat niet aan (5 pogingen)", "3": "lamp onverwacht uitgegaan", "4": "ventilator defect",
    "5": "te warm", "6": "te lage spanning",
}
LAMP_MODES = {"": "Niet wijzigen", "dual": "Beide lampen", "single": "Eén lamp"}
LAMP_POWERS = {"": "Niet wijzigen", "normal": "Normaal", "eco": "Eco"}


class InFocusLink:
    """One session with an InFocus projector over telnet or a serial port (use as context manager)."""

    def __init__(self, host="", serial_port="", baud=0, timeout=3.0):
        self.host, self.serial_port, self.baud, self.timeout = host, serial_port, baud, timeout
        self._sock = self._ser = None
        self._buf = b""
        self._last_write = 0.0
        self._last_cmd = 0.0

    def __enter__(self):
        if self.serial_port:
            self._open_serial()
        else:
            try:
                self._sock = socket.create_connection((self.host, INFOCUS_PORT), timeout=self.timeout)
            except OSError as e:
                raise DeviceError(f"niet bereikbaar op {self.host}:{INFOCUS_PORT} ({e.strerror or e}); staat "
                                  "‘Command and Control Pass-Through’ aan en Always-On → Network op Yes?") from e
            time.sleep(0.3)
            self._drain()  # telnet negotiation or a welcome text
        return self

    def __exit__(self, *exc):
        for conn in (self._sock, self._ser):
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass

    def _open_serial(self):
        try:
            import serial  # pyserial
        except ImportError as e:
            raise DeviceError("pyserial ontbreekt voor RS-232") from e
        last = None
        for baud in ([self.baud] if self.baud else INFOCUS_BAUDS):
            try:
                self._ser = serial.Serial(self.serial_port, baud, timeout=0.2)
            except (OSError, serial.SerialException) as e:
                raise DeviceError(f"seriële poort {self.serial_port} niet te openen: {e}") from e
            if self.baud:
                return
            try:  # automatic speed: the speed at which the projector answers sensibly
                self.read("PWR")
                self.baud = baud
                return
            except DeviceError as e:
                last = e
                self._ser.close()
                self._ser = None
        raise DeviceError(f"geen antwoord op {self.serial_port} (19200/38400/9600/115200 geprobeerd): {last}")

    # -- raw I/O ----------------------------------------------------------------------
    def _send(self, data):
        if self._ser is not None:
            self._ser.write(data)
        else:
            self._sock.sendall(data)

    def _recv(self):
        if self._ser is not None:
            return self._ser.read(256)
        if not select.select([self._sock], [], [], 0.2)[0]:
            return b""
        data = self._sock.recv(256)
        if not data:
            raise DeviceError("verbinding verbroken door de projector")
        return data

    def _drain(self):
        try:
            while self._recv():
                pass
        except (OSError, DeviceError):
            pass
        self._buf = b""

    def _answer(self, sent):
        """The next '(...)' (or a bare '?') that is not the echo of what we sent."""
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            self._buf += self._recv()
            self._buf = re.sub(rb"\xff[\xfb-\xfe].|\xff[\xf0-\xfa]", b"", self._buf)  # telnet commands
            m = re.search(rb"\(([^()]*)\)|\?", self._buf)
            if m:
                self._buf = self._buf[m.end():]
                if m.group(0) == b"?":
                    raise DeviceError(f"{sent} wordt niet ondersteund door deze projector")
                text = m.group(1).decode("ascii", "replace").strip()
                if f"({text})" != sent:
                    return text
        raise DeviceError(f"geen antwoord op {sent}")

    # -- commands ---------------------------------------------------------------------
    def _pace(self):
        wait = max(self._last_write + WRITE_PAUSE, self._last_cmd + COMMAND_GAP) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_cmd = time.monotonic()

    def read(self, code):
        """Value of a read command: '1' from (0-1,1); text values (model, hours) as they come."""
        self._pace()
        sent = f"({code}?)"
        self._send(sent.encode("ascii"))
        text = self._answer(sent)
        return (text.rpartition(",")[2] if "," in text else text).strip().strip('"')

    def write(self, code, value):
        self._pace()
        self._send(f"({code}{int(value)})".encode("ascii"))
        self._last_write = time.monotonic()
        time.sleep(0.3)
        self._drain()  # some firmware echoes or acknowledges writes

    def try_read(self, code):
        try:
            return self.read(code)
        except DeviceError:
            return None


def infocus_link(cfg, timeout=3.0):
    serial_port = cfg.get("serial_port", "") if cfg.get("protocol") == "infocus_serial" else ""
    return InFocusLink(cfg.get("host", ""), serial_port, int(cfg.get("baud") or 0), timeout)


def _hours(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class InFocusProjector(_ProjectorWorker):
    """Switches an InFocus projector on/off and keeps an eye on its (two) lamps.

    While CinemaViewer runs it asks the status every minute and reports a failing lamp or a
    projector error once (through on_error)."""

    POLL = 60.0

    def __init__(self, cfg, on_error, on_state=None):
        self.cfg = dict(cfg)
        self._poll_at = time.monotonic() + 20
        self._reported = set()
        self.status = {}
        super().__init__(on_error, on_state)

    def _read_state(self):
        with infocus_link(self.cfg) as link:
            power, system = link.read("PWR"), link.try_read("SYS")
        if system in ("11", "12"):
            return "cooling"
        if system in ("7", "10"):
            return "on"
        if system in WARMING:
            return "warming"
        return "on" if power == "1" else "off"

    def _power(self, on, retry_until):
        """Switching on is refused while the lamps cool down: keep trying until retry_until."""
        while True:
            with infocus_link(self.cfg) as link:
                if on and link.try_read("PWR") == "1":
                    self._apply_lamp_settings(link)
                    return
                link.write("PWR", 1 if on else 0)
                if _confirmed(link, on):
                    if on:
                        self._apply_lamp_settings(link)
                    return
                cooling = link.try_read("SYS") in ("11", "12")
            if not on or not cooling or time.monotonic() > retry_until:
                raise DeviceError("reageert niet op " + ("aanzetten" if on else "uitzetten"))
            time.sleep(5)

    def _apply_lamp_settings(self, link):
        mode = {"dual": 1, "single": 0}.get(self.cfg.get("lamp_mode", ""))
        if mode is not None and _dual_mode(link.try_read("DLI")) != str(mode):
            link.write("DLI", mode)
        power = {"normal": 0, "eco": 1}.get(self.cfg.get("lamp_power", ""))
        if power is not None and link.try_read("IPM") != str(power):
            link.write("IPM", power)

    def _off(self):
        with infocus_link(self.cfg) as link:
            link.write("PWR", 0)
            if not _confirmed(link, False):
                raise DeviceError("bevestigt uitzetten niet")

    def _idle(self):
        super()._idle()  # power state
        if time.monotonic() < self._poll_at or self.state not in ("on", "warming"):
            return  # lamps are only worth checking while they burn
        self._poll_at = time.monotonic() + self.POLL
        try:
            self.status = read_infocus_status(infocus_link(self.cfg))
        except (OSError, DeviceError):
            return  # unreachable for a moment: not worth a message every minute
        for problem in infocus_problems(self.status, self.cfg):
            if problem not in self._reported:
                self._reported.add(problem)
                self.on_error(f"Projector: {problem}")

    @staticmethod
    def test(cfg):
        return format_infocus_status(read_infocus_status(infocus_link(cfg)), cfg)


def _confirmed(link, on):
    """Did the projector take the power command? Right after 'on' it may still report 0 while it
    warms up, so the system status counts too. No answer at all (older firmware) counts as yes."""
    state = link.try_read("PWR")
    if state is None or state == ("1" if on else "0"):
        return True
    return link.try_read("SYS") in (WARMING if on else COOLING)


def read_infocus_status(link):
    """Everything worth knowing in one session; unsupported values stay None."""
    with link:
        s = {"power": link.read("PWR")}
        for key, code in (("system", "SYS"), ("error", "ERR"), ("model", "MDL"), ("lamp1_hours", "LMP"),
                          ("lamp2_hours", "LDH"), ("lamp1_on", "DLS"), ("lamp2_on", "DLD"),
                          ("dual", "DLI"), ("lamp_power", "IPM"), ("total_hours", "LMT")):
            s[key] = link.try_read(code)
    s["dual"] = _dual_mode(s["dual"])
    return s


def _dual_mode(value):
    """DLI: '1'/'0', or a word such as BOTH on newer firmware."""
    if value is None or value in ("0", "1"):
        return value
    return "1" if value.upper() in ("BOTH", "DUAL") else "0"


def infocus_problems(s, cfg):
    """Texts for a projector error, a lamp that does not burn (in dual mode) and worn lamps."""
    out = []
    if s.get("error") in INFOCUS_ERRORS:
        out.append(INFOCUS_ERRORS[s["error"]])
    if s.get("power") == "1" and s.get("system") == "10":
        known = s.get("lamp1_on") in ("0", "1") and s.get("lamp2_on") in ("0", "1")
        lamps = ("1", "2") if s.get("dual") == "1" and known else ()
        for n in lamps:
            if s.get(f"lamp{n}_on") == "0":
                out.append(f"lamp {n} brandt niet (dubbele-lampmodus staat aan)")
    limit = int(cfg.get("lamp_warn_hours") or 0)
    for n in ("1", "2"):
        hours = _hours(s.get(f"lamp{n}_hours"))
        if limit and hours is not None and hours >= limit:
            out.append(f"lamp {n} heeft {hours} uur gebrand (vervangen na ±{limit} uur)")
    return out


def format_infocus_status(s, cfg):
    state = INFOCUS_STATUS.get(s.get("system") or "", {"0": "uit", "1": "aan"}.get(s.get("power"), "onbekend"))
    lines = [f"{s.get('model') or 'InFocus-projector'} – {state}"]
    for n in ("1", "2"):
        hours = _hours(s.get(f"lamp{n}_hours"))
        both = s.get("lamp1_on") in ("0", "1") and s.get("lamp2_on") in ("0", "1")
        burning = {"1": "brandt", "0": "uit"}.get(s.get(f"lamp{n}_on") or "", "") if both else ""
        if hours is not None or burning:
            lines.append(f"Lamp {n}: " + ", ".join(x for x in (f"{hours} uur" if hours is not None else "", burning)
                                                    if x))
    extra = []
    if s.get("dual") in ("0", "1"):
        extra.append("modus: " + ("beide lampen" if s["dual"] == "1" else "één lamp"))
    if s.get("lamp_power") in ("0", "1", "2"):
        extra.append("vermogen: " + {"0": "normaal", "1": "eco", "2": "dimmen"}[s["lamp_power"]])
    if _hours(s.get("total_hours")) is not None:
        extra.append(f"projector totaal {_hours(s['total_hours'])} uur")
    if extra:
        lines.append(", ".join(extra).capitalize())
    problems = infocus_problems(s, cfg)
    if problems:
        lines.append("Let op: " + "; ".join(problems))
    return "\n".join(lines)


# -- Denon receiver --------------------------------------------------------------
class Receiver(_Worker):
    """Keeps one telnet connection open (the receiver accepts only one) and reports volume/mute
    changes made with the remote through on_volume(mv, muted).

    Following Denon's control protocol: commands end in CR, the receiver answers a request (and
    echoes a change as an event) within 200 ms, the next command may follow 1 s after PWON, and the
    lowest master volume is reported as '99' (or '00'). A connection the receiver dropped without
    closing it (network module restarted, other controller connected) is noticed by a heartbeat
    and by the missing echo after a volume change, and is then opened again."""

    VOLUME_GAP = 0.2       # at most 5 volume steps per second while the slider is dragged
    HEARTBEAT = 30.0       # idle seconds before asking 'PW?' to check the connection
    ANSWER_WAIT = 1.0      # the protocol promises an answer within 200 ms; allow for a busy receiver
    POWER_ON_WAIT = 90.0   # keep trying to switch on this long (network still starting after boot)

    def __init__(self, host, on_error, on_volume):
        self.host = host
        self.on_volume = on_volume
        self._sock = None
        self._buf = b""
        self._volume = None       # latest requested master volume (coalesced while dragging)
        self._lock = threading.Lock()
        self._sent_at = 0.0
        self._volume_at = 0.0
        self._heard = {}          # two-letter command -> time of the last line received for it
        self._rx_at = 0.0         # time anything was last received
        self._mv = None
        self._muted = None
        self._power = None        # 'PWON' / 'PWSTANDBY' as last reported by the receiver
        self._abort = threading.Event()
        super().__init__("Receiver", on_error)

    # public, called from the UI thread
    def power_on(self, source):
        self.submit(self._power_on, source)

    def set_volume(self, mv):
        with self._lock:
            first = self._volume is None
            self._volume = mv
        if first:
            self._q.put(_VOLUME)

    def set_muted(self, muted):
        self.submit(self._command, "MUON" if muted else "MUOFF", "MU" if self._power == "PWON" else None)

    def query_volume(self):
        self.submit(self._query_volume)

    def ask_status(self, timeout=5.0):
        """Status text through the open connection (a second connection would push this one off)."""
        done, result = threading.Event(), {}

        def job():
            try:
                self._command("PW?", "PW")
                result["text"] = _power_text(self._power)
            except (OSError, DeviceError) as e:
                result["error"] = e
            finally:
                done.set()
        self.submit(job)
        if not done.wait(timeout):
            raise DeviceError("de verbinding met de receiver is bezet; probeer het zo nog eens")
        if "error" in result:
            raise result["error"]
        return result["text"]

    # worker thread
    def _do(self, job):
        if job is _VOLUME:
            wait = self._volume_at + self.VOLUME_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)  # meanwhile newer slider values replace the pending one
            with self._lock:
                mv, self._volume = self._volume, None
            job = (self._send_volume, (mv,))
        super()._do(job)

    def _send_volume(self, mv):
        self._volume_at = time.monotonic()
        self._sent_at = self._volume_at
        # A switched-on receiver echoes MV; in standby it may stay silent, so only then check it.
        self._command(f"MV{mv:02d}", "MV" if self._power == "PWON" else None)

    def stop_switching_on(self):
        """Ends a power-on that is still being retried (switching off or closing comes next)."""
        self._abort.set()

    def close(self, timeout):
        self.stop_switching_on()
        super().close(timeout)

    def _power_on(self, source):
        """Switches on and waits until the receiver confirms it. Right after the PC starts the network may
        not be up yet, and a receiver in standby sometimes misses the first command: keep trying."""
        deadline = time.monotonic() + self.POWER_ON_WAIT
        while True:
            try:
                self._command("PWON", "PW")
                end = time.monotonic() + 1.5
                while self._power != "PWON" and time.monotonic() < end:
                    self._command("PW?", "PW")
                    self._read(0.3)
                if self._power == "PWON":
                    break
                error = DeviceError("reageert niet op het aan-commando (PWON)")
            except (OSError, DeviceError) as e:
                error = e
            if self._abort.is_set():
                return
            if time.monotonic() + 3.0 > deadline:
                raise DeviceError(f"gaat niet aan: {error}. Staat ‘Network Control’ (Netwerkbesturing) in "
                                  "het netwerkmenu van de Denon op ‘Always On’ (Altijd aan)? Anders is de receiver "
                                  "in stand-by niet via het netwerk te bereiken.")
            self._abort.wait(3.0)
        time.sleep(2.0)  # protocol: wait at least 1 s after PWON before the next command
        if source:
            self._command(f"SI{source}")
        self._query_volume()

    def _off(self):
        """Switches to standby and waits until the receiver confirms it."""
        self._power = None
        self._command("PWSTANDBY")
        end = time.monotonic() + 2.5
        asked = False
        while time.monotonic() < end:
            self._read(0.2)
            if self._power == "PWSTANDBY":
                return
            if not asked and time.monotonic() > end - 1.5:
                self._command("PW?")
                asked = True
        raise DeviceError("bevestigt stand-by niet")

    def _query_volume(self):
        self._mv = self._muted = None  # report the answer even when nothing changed
        self._command("MU?", "MU")
        self._command("MV?", "MV")

    def _connect(self):
        if self._sock is None:
            try:
                self._sock = socket.create_connection((self.host, DENON_PORT), timeout=3.0)
            except OSError as e:
                raise DeviceError(f"niet bereikbaar op {self.host} ({e.strerror or e})") from e
            _keepalive(self._sock)
            self._buf = b""
            self._rx_at = time.monotonic()
        return self._sock

    def _command(self, cmd, expect=None):
        """Sends one command. With `expect` (two letters) it waits for the answer/echo; when that
        does not come or the connection turns out to be broken, it connects again and sends once more."""
        for attempt in (1, 2):
            sock = self._connect()
            try:
                self._read()
                sent = time.monotonic()
                sock.sendall(cmd.encode("ascii") + b"\r")
                if expect is None:
                    time.sleep(0.06)  # Denon: leave at least 50 ms between commands
                    return
                end = sent + self.ANSWER_WAIT
                while time.monotonic() < end and self._sock is not None:
                    self._read(0.05)
                    if self._heard.get(expect, 0) >= sent:
                        return
            except OSError:
                pass
            self._disconnect()  # no answer or a broken connection: start afresh
            if attempt == 2:
                raise DeviceError(f"geen antwoord op {cmd} (verbinding opnieuw opgebouwd)")

    def _read(self, wait=0.0):
        """Handles whatever the receiver has sent (status events, answers), waiting at most `wait`."""
        if self._sock is None:
            return
        while select.select([self._sock], [], [], wait)[0]:
            data = self._sock.recv(4096)
            if not data:
                self._disconnect()
                return
            self._buf += data
            wait = 0.0
        *lines, self._buf = self._buf.split(b"\r")
        changed = False
        now = time.monotonic()
        for raw in lines:
            line = raw.decode("ascii", "replace").strip()
            if not line:
                continue
            self._rx_at = now
            self._heard[line[:2]] = now
            m = re.fullmatch(r"MV(\d\d)(\d?)", line)
            if m:
                mv = int(m.group(1)) + (0.5 if m.group(2) else 0.0)
                if mv >= 99:
                    mv = 0.0  # '99' is how the protocol reports the minimum ('---')
                if now - self._sent_at < 1.0:
                    self._mv = mv  # echo of our own change (maybe an older one while dragging)
                    continue
                changed = changed or mv != self._mv
                self._mv = mv
            elif line in ("PWON", "PWSTANDBY"):
                self._power = line
            elif line in ("MUON", "MUOFF"):
                changed = changed or (line == "MUON") != self._muted
                self._muted = line == "MUON"
        if changed and self._mv is not None:
            self.on_volume(self._mv, bool(self._muted))

    def _idle(self):
        try:
            self._read()
            if self._sock is not None and time.monotonic() - self._rx_at > self.HEARTBEAT:
                self._command("PW?", "PW")  # quiet for a while: is the connection still alive?
        except (OSError, DeviceError):
            self._disconnect()  # the next command connects again

    def _disconnect(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buf = b""

    def _cleanup(self):
        self._disconnect()

    @staticmethod
    def test(host):
        with socket.create_connection((host, DENON_PORT), timeout=4.0) as s:
            s.sendall(b"PW?\r")
            s.settimeout(2.0)
            data = b""
            try:
                while b"PW" not in data:
                    chunk = s.recv(1024)
                    if not chunk:
                        break
                    data += chunk
            except TimeoutError:
                pass
        for state in ("PWON", "PWSTANDBY"):
            if state.encode() in data:
                return _power_text(state)
        raise DeviceError("verbonden, maar geen antwoord van de receiver (is er al een ander programma "
                          "of de Denon-app verbonden? De receiver staat maar één verbinding toe)")


def _power_text(state):
    return {"PWON": "receiver staat aan", "PWSTANDBY": "receiver staat op stand-by"}.get(state, "status onbekend")


def _keepalive(sock):
    """TCP keepalive, so a connection the receiver silently dropped is also noticed by the system."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):  # Windows: idle 20 s, then every 5 s
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 20000, 5000))
        elif hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 20)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except OSError:
        pass


def projector_configured(p):
    """Network protocols need an address, RS-232 a serial port."""
    return bool(p.get("serial_port") if p.get("protocol") == "infocus_serial" else p.get("host"))


class Devices(QObject):
    """Turns the projector and receiver on when CinemaViewer starts and off when it closes."""

    error = Signal(str)
    receiver_volume = Signal(float, bool)   # master volume step (0–98) and mute, changed on the receiver
    projector_state = Signal(str)           # '' (no projector), off, warming, on, cooling, unreachable

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.projector = None
        self.receiver = None
        self._on = False

    @property
    def enabled(self):
        """True while a projector or receiver is switched on by CinemaViewer."""
        return self._on and (self.projector is not None or self.receiver is not None)

    @property
    def controls_volume(self):
        r = self.cfg["receiver"]
        return bool(self.receiver is not None and r["volume_control"])

    def power_on(self):
        """At start: the receiver switches on; the projector only when 'auto_on' is set. Otherwise it is
        watched, and switched on with the button in the top bar (no lamp hours when only listening)."""
        p, r = self.cfg["projector"], self.cfg["receiver"]
        if p["enabled"] and projector_configured(p):
            if self.projector is None:
                if p.get("protocol", "pjlink") == "pjlink":
                    self.projector = Projector(p["host"], p["password"], self.error.emit, self.projector_state.emit)
                else:
                    self.projector = InFocusProjector(p, self.error.emit, self.projector_state.emit)
            if p.get("auto_on"):
                self.projector.power(True, retry_until=time.monotonic() + COOLDOWN_WAIT)
        else:
            self.projector_state.emit("")
        if r["enabled"] and r["host"]:
            if self.receiver is None:
                self.receiver = Receiver(r["host"], self.error.emit, self.receiver_volume.emit)
            self.receiver.power_on(r["input"])
        self._on = True

    def projector_power(self, on):
        if self.projector is not None:
            self.projector.power(on, retry_until=time.monotonic() + COOLDOWN_WAIT if on else 0.0)

    @property
    def projector_status(self):
        return self.projector.state if self.projector is not None else ""

    def power_off(self, timeout=15.0, attempts=3):
        """Switches both devices off, trying each a few times, and blocks until that is done (at most
        `timeout` seconds). Returns the names ('beamer', 'receiver') of the devices that did not respond."""
        if not self._on:
            return []
        self._on = False
        deadline = time.monotonic() + timeout
        jobs = []
        for name, dev in (("beamer", self.projector), ("receiver", self.receiver)):
            if dev is self.projector and dev is not None and dev.state == "off":
                dev.close(0.5)  # never switched on (only music): nothing to switch off
                continue
            if dev is not None:
                if dev is self.receiver:
                    dev.stop_switching_on()
                done = threading.Event()
                dev.submit_confirmed(dev._off, attempts, deadline, done)
                jobs.append((name, dev, done))
        failed = []
        for name, dev, done in jobs:
            dev.close(max(0.1, deadline - time.monotonic()))
            if not done.is_set():
                failed.append(name)
        self.projector = self.receiver = None
        return failed

    def reconfigure(self, old):
        """After the settings changed: restart the devices whose connection settings changed."""
        def connection(key, data):
            return {k: v for k, v in data[key].items() if k not in ("volume_control", "max_volume_db")}
        changed = [k for k in ("projector", "receiver") if connection(k, old) != connection(k, self.cfg)]
        if not changed:
            return
        for key in changed:
            dev = getattr(self, key)
            if dev is not None:
                dev.close(0.5)
                setattr(self, key, None)
        if "projector" in changed:
            self.projector_state.emit("")
        self.power_on()  # newly enabled devices switch on; already running ones get 'on' again (harmless)

    # -- volume (only when the receiver controls it) -------------------------
    def volume_percent_to_mv(self, percent):
        top = db_to_mv(self.cfg["receiver"]["max_volume_db"])
        return int(round(max(0, min(100, percent)) * top / 100))

    def mv_to_volume_percent(self, mv):
        top = db_to_mv(self.cfg["receiver"]["max_volume_db"])
        return max(0, min(100, int(round(mv * 100 / top)))) if top else 0

    def set_volume(self, percent):
        if self.controls_volume:
            self.receiver.set_volume(self.volume_percent_to_mv(percent))

    def set_muted(self, muted):
        if self.controls_volume:
            self.receiver.set_muted(muted)
