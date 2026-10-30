"""Thin Qt wrapper around libVLC: one player that renders into the projection window."""

import ctypes
import glob
import os
import sys
import time
from pathlib import Path

# Prefer the VLC that setup.ps1 unpacked next to the app (portable install); python-vlc reads
# these variables at import time and otherwise falls back to an installed VLC. On Linux we always
# use the system VLC (libvlc.so from the vlc package).
_BUNDLED_VLC = Path(__file__).resolve().parent.parent / "runtime" / "vlc"
_BUNDLED_VLC_CACHE_MISSING = False
if sys.platform == "win32" and (_BUNDLED_VLC / "libvlc.dll").exists():
    _BUNDLED_VLC_CACHE_MISSING = not (_BUNDLED_VLC / "plugins" / "plugins.dat").exists()
    os.environ["PYTHON_VLC_LIB_PATH"] = str(_BUNDLED_VLC / "libvlc.dll")
    os.environ["PYTHON_VLC_MODULE_PATH"] = str(_BUNDLED_VLC / "plugins")
    os.add_dll_directory(str(_BUNDLED_VLC))

import vlc  # noqa: E402
from PySide6.QtCore import QObject, QTimer, Signal

from .net import USER_AGENT

_STATES = {
    vlc.State.NothingSpecial: "idle",
    vlc.State.Opening: "opening",
    vlc.State.Buffering: "opening",
    vlc.State.Playing: "playing",
    vlc.State.Paused: "paused",
    vlc.State.Stopped: "stopped",
    vlc.State.Ended: "ended",
    vlc.State.Error: "error",
}


def hardware_decoding():
    """VLC's hardware decoding, except on older AMD cards that use the 'radeon' driver (e.g. HD 5450): their
    video decoder (UVD) can lock up the card at the start of a film; the card is then reset (all screens
    flicker) and the film plays with sound but without picture. The CPU decodes full HD easily."""
    if sys.platform != "win32":
        for path in glob.glob("/sys/class/drm/card*/device/driver"):
            if os.path.basename(os.path.realpath(path)) == "radeon":
                return "none"
    return "any"


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


class Player(QObject):
    state_changed = Signal(str)
    tick = Signal(int, int)  # position ms, length ms

    def __init__(self):
        super().__init__()
        self.hw_decoding = hardware_decoding()
        args = ["--no-video-title-show", "--no-osd", "--quiet", "--no-snapshot-preview",
                "--no-stats"]
        if _BUNDLED_VLC_CACHE_MISSING:
            # Without plugins.dat libVLC loads all ~360 plugin DLLs at every start, which on a cold PC
            # (virus scanner) can take minutes. This writes the cache once; later starts take a moment.
            args.append("--reset-plugins-cache")
        self.instance = vlc.Instance(args)
        self.mp = self.instance.media_player_new()
        self.mp.video_set_mouse_input(False)
        self.mp.video_set_key_input(False)
        self.state = "idle"
        self._volume = 80
        self._muted = False
        self.gain_db = 0.0         # this song turned up/down to an even level (see loudness.py)
        self.fixed_output = False  # True: VLC at full level, the receiver does the volume
        self.audio_device = ""     # VLC audio output device id; "" = system default
        self._grace_until = 0.0
        self._track_cache = (None, {})
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    # -- control --------------------------------------------------------------
    def set_window(self, hwnd):
        if sys.platform == "win32":
            self.mp.set_hwnd(int(hwnd))
        else:
            self.mp.set_xwindow(int(hwnd))

    def play(self, mrl, start_ms=0, options=()):
        """options: extra VLC media options (later ones win, e.g. a source's own user agent)."""
        media = self.instance.media_new(mrl)
        if start_ms > 0:
            media.add_option(f":start-time={start_ms / 1000:.1f}")
        media.add_option(":network-caching=1500")
        # per media: libVLC ignores this option when it is given to the instance
        media.add_option(f":avcodec-hw={self.hw_decoding}")
        media.add_option(":no-snapshot-preview")  # the preview takes snapshots: never show them on the film
        media.add_option(f":http-user-agent={USER_AGENT}")
        for option in options:
            media.add_option(option)
        self.mp.set_media(media)
        if self.audio_device:
            self.mp.audio_output_device_set(None, self.audio_device)
        self._grace_until = time.monotonic() + 2.0
        self._set_state("opening")
        self.mp.play()

    def stop(self):
        self._grace_until = 0
        self.mp.stop()

    def pause(self):
        self.mp.set_pause(1)

    def resume(self):
        self.mp.set_pause(0)

    def toggle_pause(self):
        if self.state == "playing":
            self.mp.set_pause(1)
        elif self.state == "paused":
            self.mp.set_pause(0)

    def seek(self, ms):
        length = self.mp.get_length()
        if length > 0:
            ms = max(0, min(ms, length - 1000))
        self.mp.set_time(int(ms))

    def seek_relative(self, delta_ms):
        self.seek(self.mp.get_time() + delta_ms)

    @property
    def position(self):
        return max(0, self.mp.get_time())

    @property
    def length(self):
        return max(0, self.mp.get_length())

    @property
    def volume(self):
        return self._volume

    @property
    def muted(self):
        return self._muted

    def set_volume(self, value):
        self._volume = max(0, min(100, int(value)))
        self._apply_audio()

    def set_muted(self, muted):
        self._muted = bool(muted)
        self._apply_audio()

    def set_fixed_output(self, fixed):
        self.fixed_output = bool(fixed)
        self._apply_audio()

    def set_gain_db(self, db):
        self.gain_db = float(db)
        self._apply_audio()

    def _apply_audio(self):
        base = 100 if self.fixed_output else self._volume
        # VLC's volume works on a cubic scale (PulseAudio/PipeWire): +6 dB is 126%, −6 dB 79%
        self.mp.audio_set_volume(min(200, int(round(base * 10 ** (self.gain_db / 60)))))
        self.mp.audio_set_mute(False if self.fixed_output else self._muted)

    # -- audio output device --------------------------------------------------
    def audio_devices(self):
        """[(device id, description)] of the current audio output (e.g. PulseAudio sinks, WASAPI)."""
        out = []
        devices = self.mp.audio_output_device_enum()
        if not devices:
            return out
        try:
            dev = devices
            while dev:
                d = dev.contents
                if d.device:
                    out.append((_text(d.device), _text(d.description) or _text(d.device)))
                dev = d.next
        finally:
            vlc.libvlc_audio_output_device_list_release(devices)
        return out

    def set_audio_device(self, device_id):
        """Takes effect immediately (also during playback); "" goes back to the system default."""
        self.audio_device = device_id or ""
        self.mp.audio_output_device_set(None, self.audio_device or None)

    # -- tracks ---------------------------------------------------------------
    def audio_tracks(self):
        return [(i, _text(n)) for i, n in (self.mp.audio_get_track_description() or []) if i >= 0]

    def audio_track(self):
        return self.mp.audio_get_track()

    def set_audio_track(self, track_id):
        self.mp.audio_set_track(track_id)

    def subtitle_tracks(self):
        return [(i, _text(n)) for i, n in (self.mp.video_get_spu_description() or []) if i >= 0]

    def subtitle_track(self):
        return self.mp.video_get_spu()

    def set_subtitle_track(self, track_id):
        self.mp.video_set_spu(track_id)

    # -- disc navigation ------------------------------------------------------
    def navigate(self, action):
        codes = {"activate": 0, "up": 1, "down": 2, "left": 3, "right": 4, "popup": 5}
        self.mp.navigate(codes[action])

    def disc_menu(self):
        # DVD root menu (title 0); for Blu-ray this opens the popup menu.
        try:
            self.mp.set_title(0)
        except Exception:  # noqa: BLE001
            self.navigate("popup")

    def chapter(self, delta):
        if delta > 0:
            self.mp.next_chapter()
        else:
            self.mp.previous_chapter()

    def chapters(self):
        """[(start_ms, name)] of the current title (mkv chapters, DVD/Blu-ray chapters)."""
        try:
            return [(int(c.time_offset), _text(c.name)) for c in self.mp.get_full_chapter_descriptions(-1) or []]
        except Exception:  # noqa: BLE001 - chapter info is best effort
            return []

    def chapter_info(self):
        return self.mp.get_chapter(), self.mp.get_chapter_count()

    # -- media information ----------------------------------------------------
    def has_video(self):
        """False when the playing media has no picture (music); only reliable shortly after start."""
        return self.mp.has_vout() > 0

    def audio_meta(self):
        """Tags of the playing media: title, artist, album and the (embedded) cover as a local path."""
        media = self.mp.get_media()
        if media is None:
            return {}
        out = {key: _text(media.get_meta(meta)) for key, meta in (
            ("title", vlc.Meta.Title), ("artist", vlc.Meta.Artist), ("album", vlc.Meta.Album),
            ("now_playing", vlc.Meta.NowPlaying))}  # now_playing: the song a radio stream announces
        art = _text(media.get_meta(vlc.Meta.ArtworkURL))
        if art.startswith("file:"):  # VLC extracts embedded covers to its art cache
            from urllib.parse import unquote, urlparse
            path = unquote(urlparse(art).path)
            if sys.platform == "win32" and path.startswith("/") and path[2:3] == ":":
                path = path[1:]
            out["artwork"] = path
        return out

    def media_info(self):
        info = {}
        media = self.mp.get_media()
        if media is None:
            return info
        info["meta_title"] = _text(media.get_meta(vlc.Meta.Title))
        # tracks_get() leaks its array in python-vlc, so read codec details only once per media.
        if self._track_cache[0] is not media or not self._track_cache[1]:
            self._track_cache = (media, self._codec_info(media))
        info.update(self._track_cache[1])
        if "video" not in info:
            try:
                w, h = self.mp.video_get_size(0)
                if w:
                    info["video"] = f"{w}×{h}"
            except Exception:  # noqa: BLE001
                pass
        audio_id = self.audio_track()
        for i, name in self.audio_tracks():
            if i == audio_id:
                info["audio_track"] = name
        spu_id = self.subtitle_track()
        info["subtitle_track"] = "Uit"
        for i, name in self.subtitle_tracks():
            if i == spu_id:
                info["subtitle_track"] = name
        chapter, count = self.chapter_info()
        if count and count > 1:
            info["chapter"] = f"{chapter + 1} / {count}"
        return info

    @staticmethod
    def _codec_info(media):
        info = {}
        try:
            for t in media.tracks_get() or []:
                codec = _text(vlc.libvlc_media_get_codec_description(t.type, t.codec))
                # In C the audio/video/subtitle pointers share one union slot at the 'audio' offset;
                # python-vlc declares them as separate fields, so always read that slot and cast.
                if not t.audio:
                    continue
                if t.type == vlc.TrackType.video and "video" not in info:
                    v = ctypes.cast(t.audio, ctypes.POINTER(vlc.VideoTrack)).contents
                    fps = v.frame_rate_num / v.frame_rate_den if v.frame_rate_den else 0
                    info["video"] = " · ".join(x for x in (f"{v.width}×{v.height}", f"{fps:.3g} fps" if fps else "",
                                                           codec) if x)
                elif t.type == vlc.TrackType.audio and "audio" not in info:
                    a = t.audio.contents
                    ch = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(a.channels, f"{a.channels} kanalen")
                    info["audio"] = " · ".join(x for x in (codec, ch, f"{a.rate / 1000:g} kHz" if a.rate else "") if x)
        except Exception:  # noqa: BLE001 - track info is best effort
            pass
        return info

    # -- polling --------------------------------------------------------------
    def _set_state(self, state):
        if state != self.state:
            self.state = state
            if state == "playing":
                # libVLC resets audio settings when a new output starts
                self._apply_audio()
            self.state_changed.emit(state)

    def _poll(self):
        state = _STATES.get(self.mp.get_state(), "idle")
        if state in ("idle", "stopped") and time.monotonic() < self._grace_until:
            return
        self._set_state(state)
        if state in ("playing", "paused"):
            self.tick.emit(self.position, self.length)

    def shutdown(self):
        self._timer.stop()
        self.mp.stop()
        self.mp.release()
        self.instance.release()
