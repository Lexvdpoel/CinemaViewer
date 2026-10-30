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
RECONNECT_TRIES = 5          # schrijffouten binnen 5 minuten voordat DMX echt stopt
RECONNECT_DELAY_MS = 2000

CHANNELS = {"dimmer": 1, "rgb": 3, "rgbw": 4, "cct": 2}
LAMP_TYPES = ("dimmer", "rgb", "rgbw", "cct")
STATE_NAMES = {"film": "Film", "pauze": "Pauze", "muziek": "Muziek"}
MUSIC_MODES = {  # wat een lamp doet in de muziekstand
    "bass": "Mee met de bas",
    "mid": "Mee met het midden (zang)",
    "treble": "Mee met het hoog",
    "flash": "Flitsen op de beat",
    "cycle": "Kleurencyclus",
    "color": "Kleur op de muziek (bas rood, midden groen, hoog blauw)",
    "spectrum": "Spectrum over de lampen",
    "static": "Vast (stand Muziek)",
    "off": "Uit",
}
MUSIC_SPEEDS = {"fast": ("Snel", 0.06), "normal": ("Normaal", 0.15), "calm": ("Rustig", 0.4)}


def lamp_max(lamp):
    """Het maximale niveau van een lamp (1–100%): 100% in een staat of op het lampenpaneel wordt dit."""
    try:
        return max(1.0, min(100.0, float(lamp.get("max", 100))))
    except (TypeError, ValueError):
        return 100.0


def lamp_channels(lamp, color, level):
    """Kanaalwaarden (0–255) van één lamp: kleur per type, vermenigvuldigd met het niveau (0–100),
    geschaald naar het maximum van de lamp."""
    lv = max(0.0, min(100.0, float(level))) / 100.0 * lamp_max(lamp) / 100.0
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
                    for _ in range(5):  # een paar keer, zodat ook een gemist frame geen kwaad kan
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
    changed = Signal()                 # andere staat, of handmatig aan/uit (lampenpaneel)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.out = None
        self.current = [0.0] * FRAME_LEN
        self.target = [0.0] * FRAME_LEN
        self.step = [0.0] * FRAME_LEN
        self.state = None              # "film", "pauze", "muziek" of None
        self.manual = None             # [{"color", "level"}] per lamp uit het lampenpaneel; None = automatisch
        self.music_on = False
        self.last_error = None
        self._hue = 0.0
        self._bands = [0.0] * 12
        self._bass_avg = 0.0
        self._flash = 0.0
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

    def _open(self):
        self.out = ArtNetOutput(self.data) if self.data["backend"] == "artnet" else UsbDmxOutput(self.data)

    def start(self):
        """Opent de output en gaat naar de pauze-stand (CinemaViewer start nooit lichtjes-uit)."""
        self.stop_output()
        self.last_error = None
        self._failures = []
        if not self.data["enabled"]:
            return
        try:
            self._open()
        except Exception as e:  # noqa: BLE001 - slecht adres, poort bezet, pyserial ontbreekt
            self.out = None
            self._warn(f"DMX kan niet starten: {e}")
            return
        self.current = [0.0] * FRAME_LEN
        self._timer.start()
        self.set_state("pauze")
        if self.manual is not None:
            self._set_target(self._lamps_frame(self.manual), 0.5)

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
        if name == "film" and self.manual is not None:
            self.set_auto()  # een film begint: het licht volgt weer vanzelf
            return
        if self.manual is None:
            self._set_target(self._state_frame(name), self.data["states"].get(name, {}).get("transition", 2.0))
            self.changed.emit()

    def state_lamps(self, name):
        """Kleur en niveau per lamp in een staat (aangevuld tot het aantal lampen)."""
        entries = list(self.data["states"].get(name, {}).get("lamps", []))
        entries += [{"color": [], "level": 0} for _ in range(len(self.data["lamps"]) - len(entries))]
        return [dict(e) for e in entries[:len(self.data["lamps"])]]

    def _state_frame(self, name):
        return self._lamps_frame(self.data["states"].get(name, {}).get("lamps", []))

    def _lamps_frame(self, entries):
        frame = [0.0] * FRAME_LEN
        for lamp, entry in zip(self.data["lamps"], entries):
            self._paint(frame, lamp, entry.get("color"), entry.get("level", 0))
        return frame

    # -- handmatig (lampenpaneel) -------------------------------------------------
    def set_manual(self, entries, seconds=0.3):
        """Lampen met de hand: tot set_auto() of het begin van een film negeert het licht de staten."""
        was = self.manual is not None
        self.manual = [dict(e) for e in entries]
        if self.active:
            self._set_target(self._lamps_frame(self.manual), seconds)
        if not was:
            self.changed.emit()

    def set_auto(self):
        if self.manual is None:
            return
        self.manual = None
        if self.active and not self.music_on:
            self.set_state(self.state or "pauze")
        self.changed.emit()

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
        if not (self.active and self.music_on) or self.manual is not None:
            return
        music = self.data["music"]
        lamps = self.data["lamps"]
        own = music.get("lamps") or [] if music.get("per_lamp") else []
        raw_bass, mid, treble = (list(levels) + [0.0, 0.0, 0.0])[:3]
        self._bands = list(bands) or [0.0]
        # beat: the bass jumps clearly above its running average; a flash then dies out in ~0,3 s
        beat = raw_bass > 0.25 and raw_bass > self._bass_avg * 1.35
        self._bass_avg = self._bass_avg * 0.92 + raw_bass * 0.08
        self._flash = 1.0 if beat else self._flash * 0.6
        self._hue = (self._hue + 0.006 + 0.03 * min(1.0, raw_bass * 1.4)) % 1.0
        frame = [0.0] * FRAME_LEN
        for i, (lamp, entry) in enumerate(zip(lamps, self.state_lamps("muziek"))):
            setting = own[i] if i < len(own) else music
            mode = setting.get("mode", "bass")
            sens = max(0.0, min(100.0, float(setting.get("sensitivity", 70)))) / 100.0 * 2.0
            bass, m, t = (min(1.0, v * sens) for v in (raw_bass, mid, treble))
            color, level = list(entry.get("color") or [255, 255, 255]), float(entry.get("level", 0))
            if mode == "cycle":
                color = list(hsv_to_rgb(self._hue, 0.9, 255))
                level *= 0.35 + 0.65 * bass
            elif mode == "spectrum":  # the lamps share the bands from low to high
                band = self._bands[min(len(self._bands) - 1, i * len(self._bands) // max(1, len(lamps)))]
                level *= 0.15 + 0.85 * min(1.0, band * sens)
            elif mode == "mid":
                level *= 0.2 + 0.8 * m * m
            elif mode == "treble":
                level *= 0.15 + 0.85 * t * t
            elif mode == "flash":
                level *= self._flash if sens else 0.0
            elif mode == "color":
                if lamp.get("type") in ("rgb", "rgbw"):
                    color = [int(255 * bass), int(255 * m), int(255 * t)]
                    level *= 0.3 + 0.7 * max(bass, m, t)
                else:  # a single colour: just the strength of the music
                    level *= 0.2 + 0.8 * max(bass, m, t)
            elif mode == "off":
                level = 0.0
            elif mode != "static":  # "bass": meedansen op de bas
                level *= 0.2 + 0.8 * bass * bass
            self._paint(frame, lamp, color, max(0.0, min(100.0, level)))
        self._set_target(frame, MUSIC_SPEEDS.get(music.get("speed", "normal"), MUSIC_SPEEDS["normal"])[1])

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
        if self.out.failed is not None:  # netwerk weg, adapter eruit, of het systeem hing even
            failed = self.out.failed
            self.stop_output()
            now = time.monotonic()
            self._failures = [t for t in getattr(self, "_failures", []) if now - t < 300] + [now]
            if len(self._failures) > RECONNECT_TRIES:
                self._warn(f"DMX gestopt: {failed}")
            else:
                QTimer.singleShot(RECONNECT_DELAY_MS, lambda: self._reconnect(failed))
            return
        if changed:
            self.out.update(int(round(v)) for v in self.current)

    def _reconnect(self, failed, attempt=1):
        """Na een schrijffout de output opnieuw openen; de lampen houden hun stand (geen nieuwe start)."""
        if self.out is not None or not self.data["enabled"]:
            return
        try:
            self._open()
        except Exception as e:  # noqa: BLE001 - adapter (nog) niet terug
            self.out = None
            if attempt < RECONNECT_TRIES:
                QTimer.singleShot(RECONNECT_DELAY_MS * (attempt + 1), lambda: self._reconnect(failed, attempt + 1))
            else:
                self._warn(f"DMX gestopt: {failed} (opnieuw verbinden lukt niet: {e})")
            return
        self._timer.start()
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
        # wat er ook gebeurd is: alle 512 kanalen op 0 voordat de output dichtgaat
        self.current = [0.0] * FRAME_LEN
        self.target = [0.0] * FRAME_LEN
        if self.out is not None and self.out.failed is None:
            self.out.update(bytes(FRAME_LEN))
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
