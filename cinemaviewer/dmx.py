"""Ruimtelicht via DMX: ArtNet (netwerk) of USB-DMX, met vloeiende overgangen tussen staten.

Staten: "film" (film speelt), "pauze" (ook bij inactief/afgespeeld) en "muziek" (basis voor
audio-reactief). Bij afsluiten van CinemaViewer gaan de lampen uit. Eén universe van 512
kanalen; elke lamp heeft een startadres (1–512) en een type dat het aantal kanalen bepaalt.
"""

import math
import socket
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

ARTNET_PORT = 6454
FRAME_LEN = 512
TICK_MS = 40

CHANNELS = {"dimmer": 1, "rgb": 3, "rgbw": 4, "cct": 2}
LAMP_TYPES = ("dimmer", "rgb", "rgbw", "cct")
STATE_NAMES = {"film": "Film", "pauze": "Pauze", "muziek": "Muziek"}


def lamp_channels(lamp, color, level):
    """Kanaalwaarden (0–255) van één lamp: kleur per type, vermenigvuldigd met het niveau (0–100)."""
    lv = max(0.0, min(100.0, float(level))) / 100.0
    kind = lamp.get("type", "rgbw")
    if kind == "dimmer":
        return [int(round(255 * lv))]
    c = list(color or [])
    want = CHANNELS.get(kind, 4)
    if kind == "cct":
        c = (c + [255, 0])[:2]          # warm, koud
    else:
        c = (c + [0, 0, 0, 0])[:want]
    return [int(round(max(0, min(255, int(v))) * lv)) for v in c[:want]]


class _Output:
    """Verstuurt het laatste frame vanuit een eigen thread, ook als er niets verandert: een Open DMX
    adapter heeft geen eigen geheugen en lampen vallen terug als het signaal wegvalt. Een fout
    (netwerk weg, adapter eruit) komt in self.failed; de engine meldt die."""

    REFRESH = 1.0  # seconden: frame opnieuw sturen ook zonder wijziging

    def __init__(self):
        self.failed = None
        self._frame = bytes(FRAME_LEN)
        self._dirty = True
        self._wake = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="dmx-output", daemon=True)

    def start(self):
        self._thread.start()

    def update(self, data):
        self._frame = bytes(data)
        self._dirty = True
        self._wake.set()

    def _run(self):
        last = 0.0
        while not self._stop:
            self._wake.wait(self.REFRESH)
            self._wake.clear()
            if not (self._dirty or time.monotonic() - last >= self.REFRESH):
                continue
            self._dirty = False
            try:
                self.send(self._frame)
            except Exception as e:  # noqa: BLE001 - doorgegeven aan de engine
                self.failed = str(e) or e.__class__.__name__
                return
            last = time.monotonic()

    def close(self):
        """Stuurt het laatste frame nog uit (lampen uit bij afsluiten) en sluit dan."""
        if self._thread.is_alive():
            self._stop = True
            self._wake.set()
            self._thread.join(2.0)
            if self.failed is None:
                try:
                    self.send(self._frame)
                except Exception:  # noqa: BLE001 - afsluiten gaat door
                    pass
        self._release()

    def send(self, data):
        raise NotImplementedError

    def _release(self):
        pass


class ArtNetOutput(_Output):
    """ArtDmx-pakketten naar een ArtNet-node of broadcast, universe 0–511 (net/sub)."""

    def __init__(self, cfg):
        super().__init__()
        mode, ip = cfg["artnet_mode"], cfg["artnet_ip"].strip()
        if mode != "ip" or not ip:
            ip = "255.255.255.255"
        self.dest = (ip, ARTNET_PORT)
        universe = max(0, min(511, int(cfg["universe"])))
        self.subuni, self.net = universe & 0xFF, (universe >> 8) & 0x7F
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.settimeout(2.0)
        self.seq = 0
        self.start()

    def send(self, data):
        self.seq = self.seq % 255 + 1  # 1–255; 0 betekent 'geen volgorde'
        pkt = (b"Art-Net\x00" + bytes([0x00, 0x50, 0x00, 0x0E, self.seq, 0x00,
                                       self.subuni, self.net, 0x02, 0x00]) + bytes(data))
        self.sock.sendto(pkt, self.dest)

    def _release(self):
        try:
            self.sock.close()
        except OSError:
            pass


class UsbDmxOutput(_Output):
    """DMX-512 via een USB-DMX adapter met een FTDI-chip (seriële poort, bv. COM3 of /dev/ttyUSB0).

    'open': Enttec Open DMX en de meeste losse FTDI-kabels. De pc maakt zelf het DMX-signaal:
    break, startcode 0 en 512 kanalen op 250 kbaud 8N2, continu (±30 keer per seconde).
    'pro': Enttec DMX USB Pro en compatibele: één pakket per frame (7E 06 lengte … E7), de
    adapter herhaalt het zelf. 'auto' vraagt het serienummer op: alleen een Pro antwoordt."""

    def __init__(self, cfg):
        import serial  # pyserial
        super().__init__()
        port = cfg["usb_port"].strip()
        if not port:
            raise ValueError("geen seriële poort ingesteld voor USB-DMX")
        self.ser = serial.Serial(port, 250000, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                                 stopbits=serial.STOPBITS_TWO, timeout=0.2, write_timeout=1.0)
        protocol = cfg.get("usb_protocol", "auto")
        if protocol == "auto":
            protocol = "pro" if self._is_pro() else "open"
        self.protocol = protocol
        if protocol == "open":
            self.REFRESH = 0  # continu
        self.start()

    def _is_pro(self):
        try:
            self.ser.reset_input_buffer()
            self.ser.write(b"\x7e\x0a\x00\x00\xe7")  # label 10: serienummer opvragen
            self.ser.flush()
            reply = self.ser.read(9)
        except Exception:  # noqa: BLE001 - geen antwoord = Open DMX
            return False
        return reply[:2] == b"\x7e\x0a"

    def send(self, data):
        body = b"\x00" + bytes(data)  # startcode 0 + kanalen
        if self.protocol == "pro":
            n = len(body)
            self.ser.write(b"\x7e\x06" + bytes([n & 0xFF, n >> 8]) + body + b"\xe7")
            return
        began = time.perf_counter()
        self.ser.break_condition = True
        time.sleep(0.0002)  # break ≥ 88 µs
        self.ser.break_condition = False
        time.sleep(0.00002)  # mark after break ≥ 8 µs
        self.ser.write(body)
        self.ser.flush()
        # de FTDI-chip buffert nog: wacht tot het frame (513 × 11 bits ≈ 23 ms) echt de deur uit is,
        # anders breekt de volgende break het af
        rest = 0.025 - (time.perf_counter() - began)
        if rest > 0:
            time.sleep(rest)
        time.sleep(0.005)

    def _release(self):
        try:
            self.ser.close()
        except Exception:  # noqa: BLE001
            pass


def serial_ports():
    """Beschikbare seriële poorten (lege lijst als pyserial ontbreekt)."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [p.device for p in list_ports.comports()]


def hsv_to_rgb(h, s, v):
    """Hue 0–1, verzadiging en waarde 0–255 → (r, g, b)."""
    h = (h % 1.0) * 6
    i, f = int(h), h - int(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6]


class DmxEngine(QObject):
    """Regelt de output: volgt de staat, laat waarden naar het doel toe lopen en speelt muziek mee."""

    error = Signal(str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.out = None
        self.current = [0.0] * FRAME_LEN
        self.target = [0.0] * FRAME_LEN
        self.step = [0.0] * FRAME_LEN
        self.state = None              # "film", "pauze", "muziek" of None
        self.music_on = False
        self.last_error = None
        self._hue = 0.0
        self._bands = [0.0] * 12
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    # -- config en verbinding -------------------------------------------------
    @property
    def data(self):
        return self.cfg["dmx"]

    @property
    def active(self):
        return self._timer.isActive()

    def start(self):
        """Opent de output en gaat naar de pauze-stand (CinemaViewer start nooit lichtjes-uit)."""
        self.stop_output()
        self.last_error = None
        if not self.data["enabled"]:
            return
        try:
            self.out = ArtNetOutput(self.data) if self.data["backend"] == "artnet" \
                else UsbDmxOutput(self.data)
        except Exception as e:  # noqa: BLE001 - slecht adres, poort bezet, pyserial ontbreekt
            self.out = None
            self._warn(f"DMX kan niet starten: {e}")
            return
        self.current = [0.0] * FRAME_LEN
        self._timer.start()
        self.set_state("pauze")

    def stop_output(self):
        self._timer.stop()
        if self.out is not None:
            self.out.close()
            self.out = None

    def reconfigure(self, old):
        """Na een wijziging in de instellingen: opnieuw verbinden en de huidige staat opnieuw zetten."""
        if self.data == old["dmx"]:
            return
        self.stop_output()
        self.start()

    # -- staten ----------------------------------------------------------------
    def set_state(self, name):
        if not self.active:
            return
        self.music_on = False
        self.state = name
        self._set_target(self._state_frame(name), self.data["states"].get(name, {}).get("transition", 2.0))

    def _state_frame(self, name):
        frame = [0.0] * FRAME_LEN
        st = self.data["states"].get(name, {})
        for lamp, entry in zip(self.data["lamps"], st.get("lamps", [])):
            self._paint(frame, lamp, entry.get("color"), entry.get("level", 0))
        return frame

    @staticmethod
    def _paint(frame, lamp, color, level):
        try:
            addr = int(lamp["address"])
        except (TypeError, ValueError):
            return
        if not 1 <= addr <= FRAME_LEN:
            return
        for i, v in enumerate(lamp_channels(lamp, color, level)):
            if addr - 1 + i < FRAME_LEN:
                frame[addr - 1 + i] = float(v)

    def _set_target(self, frame, seconds):
        self.target = frame
        ticks = max(1.0, max(0.0, float(seconds)) * 1000.0 / TICK_MS)
        self.step = [abs(t - c) / ticks for c, t in zip(self.current, frame)]

    # -- muziek ----------------------------------------------------------------
    def music_start(self):
        """Ga naar de muziek-stand; de levels komen van de MusicAnalyzer via set_levels."""
        if not self.active:
            return
        self.state = "muziek"
        self.music_on = True

    def music_stop(self):
        self.music_on = False

    def set_levels(self, levels, bands):
        """Van de audio-analyse (≈20× per seconde): bereken het doelframe voor de huidige mode."""
        if not (self.active and self.music_on):
            return
        sens = max(0.0, min(100.0, float(self.data["music"]["sensitivity"]))) / 100.0 * 2.0
        bass = min(1.0, levels[0] * sens)
        self._bands = bands
        mode = self.data["music"]["mode"]
        base = self._state_frame("muziek")
        frame = [0.0] * FRAME_LEN
        self._hue = (self._hue + 0.006 + 0.03 * bass) % 1.0
        for lamp, entry in zip(self.data["lamps"], self.data["states"]["muziek"].get("lamps", [])):
            color, level = list(entry.get("color") or [255, 255, 255]), float(entry.get("level", 0))
            if mode == "cycle":
                color = list(hsv_to_rgb(self._hue, 0.9, 255))
                level *= 0.35 + 0.65 * bass
            elif mode == "spectrum":
                idx = self._index_of(lamp)
                level *= 0.15 + 0.85 * (self._bands[idx % len(self._bands)] * sens)
            else:  # "bass": meedansen op de bas
                level *= 0.2 + 0.8 * bass * bass
            self._paint(frame, lamp, color, max(0.0, min(100.0, level)))
        self._set_target(frame, 0.15)

    def _index_of(self, lamp):
        lamps = self.data["lamps"]
        try:
            return lamps.index(lamp)
        except ValueError:
            return 0

    # -- frame-uitzenden ---------------------------------------------------------
    def _tick(self):
        changed = False
        for i, (c, t, s) in enumerate(zip(self.current, self.target, self.step)):
            if c == t:
                continue
            if s <= 0:
                self.current[i] = t
            elif c < t:
                self.current[i] = t if c + s >= t else c + s
            else:
                self.current[i] = t if c - s <= t else c - s
            changed = True
        if self.out is None:
            return
        if self.out.failed is not None:  # netwerk weg, adapter eruit
            failed = self.out.failed
            self.stop_output()
            self._warn(f"DMX gestopt: {failed}")
            return
        if changed:
            self.out.update(int(round(v)) for v in self.current)

    def power_off(self, fade=1.2):
        """Lampen uit bij afsluiten: naar 0 laten lopen en dan pas stoppen (max. 3 s)."""
        self.music_on = False
        if not self.active:
            return
        self._set_target([0.0] * FRAME_LEN, fade)
        deadline = time.monotonic() + fade + 0.4
        while time.monotonic() < deadline and any(self.current):
            self._tick()
            time.sleep(0.02)
        self.stop_output()

    def describe(self):
        """Eén regel voor de instellingen: loopt het, en waarheen."""
        if not self.data["enabled"]:
            return False, "DMX staat uit: vink ‘Lampen via DMX aansturen’ aan."
        if self.last_error:
            return False, self.last_error
        if isinstance(self.out, UsbDmxOutput):
            kind = "Enttec Pro" if self.out.protocol == "pro" else "Open DMX via FTDI"
            return True, f"DMX wordt verstuurd via {self.data['usb_port'].strip()} ({kind})."
        if isinstance(self.out, ArtNetOutput):
            return True, f"DMX wordt verstuurd via ArtNet naar {self.out.dest[0]}, universe {self.data['universe']}."
        return False, "DMX loopt niet."

    def _warn(self, message):
        self.last_error = message
        self.error.emit(message)
