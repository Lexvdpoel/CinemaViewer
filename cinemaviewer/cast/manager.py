"""Ties the Displaycast receivers (YouTube, DLNA) to CinemaViewer.

The receivers run on their own threads; their requests reach the app through Qt signals (so they
are handled on the UI thread), and the app's player state comes back as a snapshot they can read
at any time.
"""

import socket
import uuid

from PySide6.QtCore import QObject, Signal

from ..models import MediaItem
from .dlna import DlnaRenderer
from .server import HttpServer, SsdpServer, local_ips
from .youtube import YouTubeReceiver

DEFAULT_PORT = 47801


def default_name():
    return f"CinemaViewer ({socket.gethostname()})"


class CastManager(QObject):
    play_requested = Signal(object)          # MediaItem to play
    command_requested = Signal(str, object)  # pause/resume/stop/seek/volume/mute, value
    status_changed = Signal(dict)            # for the Displaycast panel

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.http = self.ssdp = self.dlna = self.youtube = None
        self._snap = {"owner": None, "state": "stopped", "position_ms": 0, "duration_ms": 0, "volume": 0,
                      "muted": False}
        self._yt_status = {}
        self.error = ""

    # -- settings -------------------------------------------------------------------
    @property
    def store(self):
        d = self.cfg["displaycast"]
        for key in ("dlna_uuid", "dial_uuid"):
            if not d.get(key):
                d[key] = str(uuid.uuid4())
        return d

    @property
    def name(self):
        return (self.cfg["displaycast"].get("name") or "").strip() or default_name()

    @property
    def running(self):
        return self.http is not None

    # -- lifecycle --------------------------------------------------------------------
    def start(self):
        if self.running:
            return
        store = self.store
        try:
            self.http = HttpServer(int(store.get("port") or DEFAULT_PORT))
            self.ssdp = SsdpServer(self.http.port, on_error=None)
        except OSError as e:
            self.http = None
            self.error = f"Displaycast kon niet starten: {e}"
            self._emit()
            return
        self.dlna = DlnaRenderer(store["dlna_uuid"], self.name, self)
        self.youtube = YouTubeReceiver(self.name, store, store["dial_uuid"], self, self._on_youtube_status)
        self.http.route("/dlna/", self.dlna.handle)
        self.http.route("/dial/", self.youtube.handle_dial)
        self.ssdp.devices = [self.dlna.device, self.youtube.device]
        self.http.start()
        try:
            self.ssdp.start_listening()
        except OSError as e:  # discovery is a nice-to-have: the TV code still works
            self.error = f"Zichtbaar maken op het netwerk lukt niet ({e}); de tv-code werkt wel."
        self.youtube.start()
        self._emit()

    def stop(self):
        if not self.running:
            return
        for part in (self.youtube, self.ssdp):
            if part is not None:
                part.stop()
        self.http.stop()
        self.http = self.ssdp = self.dlna = self.youtube = None
        self._yt_status = {}
        self.error = ""
        self._emit()

    def status(self):
        yt = self._yt_status
        return {"running": self.running, "name": self.name, "code": yt.get("code", ""),
                "remotes": yt.get("remotes", []), "youtube_ok": bool(yt.get("running")),
                "youtube_error": yt.get("error", ""), "error": self.error,
                "addresses": [f"{ip}:{self.http.port}" for ip in local_ips()] if self.running else []}

    def _emit(self):
        self.status_changed.emit(self.status())

    def _on_youtube_status(self, info):  # receiver thread
        self._yt_status = info
        self.cfg["displaycast"].update(screen_id=self.store.get("screen_id", ""),
                                       device_id=self.store.get("device_id", ""))
        self._emit()

    # -- bridge for the receivers (called from their threads) -----------------------------------
    def snapshot(self):
        return self._snap

    def play(self, uri, info):
        """DLNA: a file or stream from a phone."""
        kind = info.get("kind") or "video"
        item = MediaItem(source="cast", id=uri, title=info.get("title") or "Gecast via DLNA",
                         kind={"track": "track", "photo": "photo"}.get(kind, "video"),
                         image_url=info.get("art", ""), runtime_ms=info.get("duration_ms", 0),
                         subtitle=" · ".join(x for x in (info.get("artist", ""), info.get("album", "")) if x),
                         extra={"cast": "dlna", "mrl": uri})
        self.play_requested.emit(item)

    def play_youtube(self, video_id, start_s):
        item = MediaItem(source="cast", id=f"youtube:{video_id}", title="YouTube-video", kind="video",
                         subtitle="Displaycast · YouTube", resume_ms=int(start_s * 1000),
                         image_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                         extra={"cast": "youtube", "video_id": video_id})
        self.play_requested.emit(item)

    def command(self, name, *args):
        self.command_requested.emit(name, args[0] if args else None)

    # -- from CinemaViewer (UI thread) ---------------------------------------------------
    def update(self, item, state, position_ms, duration_ms, volume, muted, reason="state"):
        """The player's state; tells the receivers when something they report changed."""
        owner = item.extra.get("cast") if item is not None and item.source == "cast" else None
        snap = {"owner": owner, "state": state, "position_ms": position_ms, "duration_ms": duration_ms,
                "volume": volume, "muted": muted, "uri": item.extra.get("mrl", "") if owner == "dlna" else "",
                "video_id": item.extra.get("video_id", "") if owner == "youtube" else ""}
        old, self._snap = self._snap, snap
        if not self.running:
            return
        changed = any(old.get(k) != snap.get(k) for k in ("owner", "state", "uri", "video_id"))
        if changed or reason in ("seek", "volume"):
            self.dlna.changed()
        if self.youtube is not None:
            if reason == "volume":
                self.youtube.player_changed("volume")
            elif changed or reason == "seek" or (reason == "tick" and owner == "youtube"):
                self.youtube.player_changed("state" if changed or reason == "seek" else "tick")

    def failed(self, item):
        if item.extra.get("cast") == "youtube" and self.youtube is not None:
            self.youtube.play_failed()

    def ended(self, item):
        """A cast video played to the end: YouTube continues with the phone's queue."""
        if item.extra.get("cast") == "youtube" and self.youtube is not None:
            self.youtube.ended()
