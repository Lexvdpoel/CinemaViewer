"""Songs at an even volume: the loudness of every song (EBU R128, in LUFS) is measured once and the
player turns it up or down to the same level.

Jellyfin already knows the loudness of songs its 'Audio normalization' task has analysed; other songs
are measured with ffmpeg (about 1–2 s per song, the whole file is read). Results are kept in
loudness.json, so a song is measured only once.
"""

import json
import re
import shutil
import subprocess
import threading
from urllib.parse import unquote, urlparse

from .config import DATA_DIR
from .net import USER_AGENT

TARGET_LUFS = -16.0
MAX_BOOST = 6.0          # dB; quieter songs are not raised further (no clipping)
MAX_CUT = 15.0
JELLYFIN_TARGET = -18.0  # Jellyfin's NormalizationGain brings a song to −18 LUFS
MEASURE_TIMEOUT = 60
_I_RE = re.compile(r"I:\s+(-?\d+(?:\.\d+)?) LUFS")


def gain_db(lufs):
    """How much a song of this loudness is turned up (+) or down (−)."""
    return max(-MAX_CUT, min(MAX_BOOST, TARGET_LUFS - lufs))


class Loudness:
    def __init__(self):
        self._path = DATA_DIR / "loudness.json"
        self._lock = threading.Lock()
        try:
            self._cache = json.loads(self._path.read_text())
        except (OSError, ValueError):
            self._cache = {}
        self._running = {}  # key -> Event, set when that measurement is done

    @staticmethod
    def applies(item):
        return item is not None and item.kind == "track"

    @staticmethod
    def _key(item):
        return f"{item.source}:{item.id}"

    def known(self, item):
        """The song's loudness in LUFS when it is known already, else None."""
        with self._lock:
            lufs = self._cache.get(self._key(item))
        if lufs is None and item.extra.get("normalization_gain") is not None:
            lufs = JELLYFIN_TARGET - float(item.extra["normalization_gain"])
        return lufs

    @staticmethod
    def can_measure(mrl):
        return bool(shutil.which("ffmpeg")) and mrl.split(":", 1)[0].lower() in ("http", "https", "file")

    def measure(self, item, mrl, wait=None):
        """Loudness in LUFS (blocks ~1–2 s; call it off the GUI thread), or None when it cannot be measured.
        With `wait` it returns after at most that many seconds; the measurement then goes on and is kept."""
        lufs = self.known(item)
        if lufs is not None or not self.can_measure(mrl):
            return lufs
        key = self._key(item)
        with self._lock:
            done = self._running.get(key)
            mine = done is None
            if mine:
                done = self._running[key] = threading.Event()
        if mine:
            threading.Thread(target=self._measure, args=(item, mrl, done), daemon=True).start()
        done.wait(wait if wait is not None else MEASURE_TIMEOUT + 5)
        return self.known(item)

    def _measure(self, item, mrl, done):
        try:
            self._run_ffmpeg(item, mrl)
        finally:
            with self._lock:
                self._running.pop(self._key(item), None)
            done.set()

    def _run_ffmpeg(self, item, mrl):
        args = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats"]
        if mrl.lower().startswith("file:"):
            args += ["-i", unquote(urlparse(mrl).path)]
        else:
            args += ["-user_agent", USER_AGENT, "-i", mrl]
        args += ["-vn", "-sn", "-dn", "-af", "ebur128=framelog=quiet", "-f", "null", "-"]
        try:
            result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=MEASURE_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return
        found = _I_RE.findall(result.stderr)
        if result.returncode != 0 or not found or float(found[-1]) < -60:  # silence or a broken file
            return
        lufs = float(found[-1])
        with self._lock:
            self._cache[self._key(item)] = lufs
            text = json.dumps(self._cache)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(text)
            tmp.replace(self._path)
        except OSError:
            pass
