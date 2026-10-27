"""Audio-analyse voor de muziekstand.

Dezelfde mrl wordt in een tweede, stille VLC-instantie geladen die alleen naar een tijdelijk
bestand met ruwe PCM transcodeert; dit bestand wordt meegelezen en per frame worden met het
Goertzel-algoritme de bandenergieën (bas, midden, hoog + 24 spectrumbanden) gemeten."""

import array
import math
import os
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

RATE = 22050
WINDOW = 2048
BASS = (55.0, 110.0)
MID = (330.0, 1000.0)
TREBLE = (3000.0, 8000.0)
SPECTRUM = tuple(50.0 * (9000.0 / 50.0) ** (i / 23.0) for i in range(24))
TILT = tuple(1.0 + 1.5 * i / 23.0 for i in range(24))  # hoog heeft van nature minder energie


def goertzel(samples, rate, freq):
    """Energie rond één frequentie (simpel, zonder FFT). Het venster krimpt met de frequentie, zodat
    de meetband meegroeit (ongeveer de afstand tussen twee banden) en tonen tussen de meetpunten
    niet wegvallen; dat scheelt ook veel rekenwerk bij de hoge banden."""
    n = int(min(len(samples), max(32, 2 * rate / (0.3 * freq))))
    samples = samples[-n:]
    coeff = 2.0 * math.cos(2.0 * math.pi * (freq * n / rate) / n)
    s1 = s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return math.sqrt(power) / n if power > 0 else 0.0


class MusicAnalyzer(QObject):
    """Start/stop een stille analyse-afspeler; zendt ~20× per seconde levels uit."""

    levels = Signal(list, list)  # [bas, midden, hoog] en 24 spectrumbanden, elk 0,0–1,0

    def __init__(self):
        super().__init__()
        self._thread = None
        self._stop = None

    @property
    def running(self):
        return self._thread is not None

    def start(self, mrl, position=None):
        """position: callable met de afspeelpositie (ms) van de hoorbare speler. De analysekopie
        decodeert sneller dan realtime, dus het gemeten venster volgt die positie (ook na spoelen);
        zonder position wordt het nieuwste gedecodeerde geluid gebruikt."""
        self.stop()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(mrl, self._stop, position), daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread is not None:
            self._stop.set()
            self._thread.join(3)
            self._thread = None

    def _run(self, mrl, stop, position):
        try:
            import vlc
        except Exception:  # noqa: BLE001 - VLC niet beschikbaar: geen muziekstand
            return
        path = Path(tempfile.gettempdir()) / f"cinemaviewer-audio-{time.time_ns()}.pcm"
        sout = (":sout=#transcode{vcodec=none,acodec=s16l,channels=1,samplerate=" + str(RATE)
                + "}:std{access=file,mux=raw,dst=\"" + path.as_posix() + "\"}")
        inst = vlc.Instance("--intf", "dummy", "--quiet", "--no-video")
        player = inst.media_player_new()
        media = inst.media_new(mrl)
        media.add_options(":no-video", sout)
        player.set_media(media)
        player.play()
        peaks = [1e-9] * 4  # bas, midden, hoog en één gedeelde piek voor het spectrum
        handle = None
        last_pos, changed_at = None, 0.0
        try:
            deadline = time.monotonic() + 15
            while handle is None and not stop.is_set() and time.monotonic() < deadline:
                if path.exists() and path.stat().st_size:
                    handle = open(path, "rb")  # noqa: SIM115 - wordt in finally gesloten
                else:
                    time.sleep(0.1)
            while handle is not None and not stop.wait(0.045):  # ~20 metingen per seconde
                available = os.fstat(handle.fileno()).st_size // 2
                newest = available - WINDOW
                if newest < 0:
                    continue
                start = newest
                if position is not None:
                    # VLC werkt de positie maar een paar keer per seconde bij: tussendoor doortellen.
                    # Staat hij langer dan een halve seconde stil, dan is er gepauzeerd.
                    pos, now = position(), time.monotonic()
                    if pos != last_pos:
                        last_pos, changed_at = pos, now
                    elif now - changed_at > 0.5:
                        continue
                    est = pos + (now - changed_at) * 1000
                    start = min(newest, max(0, int(est * RATE / 1000) - WINDOW))
                handle.seek(start * 2)
                samples = array.array("h")
                samples.frombytes(handle.read(WINDOW * 2))
                if len(samples) < WINDOW:
                    continue
                vals = self._measure(samples, peaks)
                self.levels.emit(vals[:3], vals[3:])
        except OSError:
            pass
        finally:
            try:
                player.stop()
                player.release()
                inst.release()
            except Exception:  # noqa: BLE001 - afsluiten heeft geen haast met VLC
                pass
            if handle is not None:
                handle.close()
            try:
                path.unlink()
            except OSError:
                pass

    @staticmethod
    def _measure(samples, peaks):
        """Bandenergieën, genormaliseerd met een langzaam dalend piekgeheugen (automatische regeling).
        De spectrumbanden delen één piek, zodat hun onderlinge verhouding (de vorm) behouden blijft."""
        levels = [max(goertzel(samples, RATE, f) for f in group) for group in (BASS, MID, TREBLE)]
        bands = [goertzel(samples, RATE, f) * t for f, t in zip(SPECTRUM, TILT)]
        out = []
        for i, v in enumerate(levels):
            peaks[i] = max(v, peaks[i] * 0.997, 1e-9)
            out.append(min(1.0, v / peaks[i]))
        peaks[3] = max(max(bands), peaks[3] * 0.997, 1e-9)
        return out + [min(1.0, v / peaks[3]) for v in bands]
