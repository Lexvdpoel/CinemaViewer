"""YouTube receiver: CinemaViewer acts like a YouTube TV app ('Link with TV code').

The phone's YouTube app and this receiver meet in a YouTube 'lounge': the receiver registers a
screen, keeps a long-polling connection open and gets commands (setPlaylist, play, pause, seekTo,
setVolume, …); it answers with its state (nowPlaying, onStateChange). On the same network the YouTube
app also finds it through DIAL, so it appears in the cast menu without entering a code.

This is YouTube's own (undocumented) protocol, as used by smart TVs; it may change. Built after the
open-source yt-cast-receiver project (github.com/patrickkfkan/yt-cast-receiver).
"""

import html
import json
import queue
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from ..net import SSL_CONTEXT
from .server import Device, Response

BASE = "https://www.youtube.com/api/lounge"
APP = "cinemaviewer"
DIAL_TYPES = ["urn:dial-multiscreen-org:service:dial:1", "urn:dial-multiscreen-org:device:dial:1", "upnp:rootdevice"]
STATE = {"idle": -1, "playing": 1, "paused": 2, "loading": 3, "stopped": 4}
CODE_REFRESH = 300


def _request(url, data=None, timeout=20):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if data is not None else "GET")
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT)


def _post_text(url, data):
    with _request(url, data) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_chunks(buffer):
    """Takes complete BrowserChannel chunks ('123\\n[[AID,["name",payload]],…]') from the start of
    `buffer`. Returns ([(AID, name, payload)], rest of buffer)."""
    messages = []
    while True:
        start = buffer.find("[")
        if start < 0:
            return messages, buffer
        depth, in_str, esc, end = 0, False, False, -1
        for i in range(start, len(buffer)):
            ch = buffer[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            return messages, buffer  # incomplete: wait for more
        try:
            chunk = json.loads(buffer[start:end])
        except ValueError:
            chunk = []
        buffer = buffer[end:]
        for entry in chunk if isinstance(chunk, list) else []:
            if isinstance(entry, list) and len(entry) == 2 and isinstance(entry[1], list) and entry[1]:
                aid, (name, *rest) = entry[0], entry[1]
                payload = rest[0] if len(rest) == 1 else rest
                messages.append((aid, name, payload))


class YouTubeReceiver:
    """`bridge` connects to CinemaViewer: play_youtube(video_id, start_s), command(name, *args) and
    snapshot(). `store` is a dict kept in the settings (screen and device id: phones stay paired)."""

    def __init__(self, name, store, dial_uuid, bridge, on_status):
        self.name = name
        self.store = store
        self.bridge = bridge
        self.on_status = on_status          # on_status(dict): code, remotes, state, error
        self.device = Device(dial_uuid, "/dial/ssdp/device-desc.xml", DIAL_TYPES)
        self.code = ""
        self.remotes = {}                   # remote device id -> name
        self.playlist = {"list_id": "", "ids": [], "index": 0, "ctt": "", "params": ""}
        self.current = ""                   # video id that is (or should be) playing
        self._pending = False               # asked CinemaViewer to start it, not playing yet
        self.cpn = ""
        self._stop = threading.Event()
        self._out = queue.Queue()
        self._lock = threading.Lock()
        self._sid = self._gsession = self._token = None
        self._rid = random.randint(41000, 49999)
        self._aid = 0
        self._ofs = 0
        self._running = False
        self._last_state_sent = 0.0
        self._error = ""

    # -- lifecycle ----------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._run, name="Displaycast YouTube", daemon=True).start()
        threading.Thread(target=self._sender, name="Displaycast YouTube send", daemon=True).start()
        threading.Thread(target=self._code_loop, name="Displaycast YouTube code", daemon=True).start()

    def stop(self):
        self._stop.set()
        if self._running:  # say goodbye to the phones, without making CinemaViewer wait for it
            def bye():
                try:
                    self._send_now([("loungeScreenDisconnected", {})])
                except OSError:
                    pass
            threading.Thread(target=bye, daemon=True).start()

    def _status(self, **kw):
        info = {"code": self.code, "remotes": list(self.remotes.values()), "running": self._running,
                "error": self._error}
        info.update(kw)
        self.on_status(info)

    # -- session ------------------------------------------------------------------
    def _ensure_screen(self):
        if not self.store.get("device_id"):
            self.store["device_id"] = str(uuid.uuid4())
        if not self.store.get("screen_id"):
            with _request(BASE + "/pairing/generate_screen_id") as resp:
                self.store["screen_id"] = resp.read().decode().strip()

    def _lounge_token(self):
        data = json.loads(_post_text(BASE + "/pairing/get_lounge_token_batch",
                                     {"screen_ids": self.store["screen_id"]}))
        return data["screens"][0]

    def _params(self, kind):
        p = {"device": "LOUNGE_SCREEN", "id": self.store["device_id"], "obfuscatedGaiaId": "", "name": self.name,
             "app": APP, "theme": "cl", "capabilities": "dsp,mic,dpa,ntb", "cst": "m", "mdxVersion": 2,
             "loungeIdToken": self._token, "VER": 8, "v": 2, "zx": uuid.uuid4().hex[:12], "t": 1}
        info = json.dumps({"brand": "CinemaViewer", "model": "Displaycast", "year": 0, "os": "Windows",
                           "osVersion": "10.0", "chipset": "", "clientName": "TVHTML5",
                           "dialAdditionalDataSupportLevel": "unsupported",
                           "mdxDialServerType": "MDX_DIAL_SERVER_TYPE_UNKNOWN"})
        if kind == "init":
            p.update(deviceInfo=info, RID=self._rid, CVER=1)
            self._rid += 1
        elif kind == "send":
            p.update(deviceInfo=info, SID=self._sid, RID=self._rid, AID=self._aid, gsessionid=self._gsession)
            self._rid += 1
        else:  # long poll
            p.update(RID="rpc", SID=self._sid, CI=0, AID=self._aid, gsessionid=self._gsession, TYPE="xmlhttp")
        return urllib.parse.urlencode(p)

    def _begin(self):
        """Screen id + lounge token + a fresh bind session (SID, gsessionid)."""
        self._ensure_screen()
        try:
            token = self._lounge_token()
        except (OSError, ValueError, KeyError, IndexError):
            self.store["screen_id"] = ""  # stored screen no longer valid: start over with a new one
            self._ensure_screen()
            token = self._lounge_token()
        self._token = token["loungeToken"]
        self._token_until = time.monotonic() + min(token.get("refreshIntervalInMillis", 0) / 1000 or 86400, 86400 * 7)
        self._sid = self._gsession = None
        self._aid = 0
        self._ofs = 0
        with self._lock:
            url = f"{BASE}/bc/bind?{self._params('init')}"
        body = _post_text(url, {"count": 0})
        messages, _rest = parse_chunks(body)
        for aid, name, payload in messages:
            if name == "c":
                self._sid = payload[0] if isinstance(payload, list) else payload
            elif name == "S":
                self._gsession = payload
        if not (self._sid and self._gsession):
            raise OSError("YouTube gaf geen sessie")
        for aid, name, payload in messages:
            if name not in ("c", "S"):
                self._handle(aid, name, payload)
        self._running = True
        self._error = ""
        self._status()

    def _run(self):
        backoff = 2
        while not self._stop.is_set():
            try:
                if not self._running or time.monotonic() > getattr(self, "_token_until", 0):
                    self._begin()
                    backoff = 2
                self._poll()
            except urllib.error.HTTPError as e:
                # 400/404/410: the session expired; start a new one
                self._running = False
                self._error = f"YouTube: HTTP {e.code}"
            except (OSError, ValueError) as e:
                self._running = False
                self._error = f"YouTube niet bereikbaar ({getattr(e, 'reason', e)})"
            if not self._running and not self._stop.is_set():
                self._status()
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)

    def _poll(self):
        """One long-polling GET; YouTube ends it every few minutes, then the next one starts."""
        with _request(f"{BASE}/bc/bind?{self._params('rpc')}", timeout=90) as resp:
            buffer = ""
            while not self._stop.is_set():
                line = resp.readline()
                if not line:
                    return
                buffer += line.decode("utf-8", "replace")
                messages, buffer = parse_chunks(buffer)
                for aid, name, payload in messages:
                    try:
                        self._handle(aid, name, payload)
                    except Exception as e:  # noqa: BLE001 - one odd message must not end the session
                        self._error = f"YouTube-bericht {name}: {e}"

    # -- sending --------------------------------------------------------------------
    def send(self, *messages):
        """Queue messages [(name, payload dict)] for the phone(s)."""
        self._out.put(list(messages))

    def _sender(self):
        while not self._stop.is_set():
            try:
                batch = self._out.get(timeout=0.5)
            except queue.Empty:
                continue
            while not self._out.empty():  # one POST for what piled up
                try:
                    batch += self._out.get_nowait()
                except queue.Empty:
                    break
            if not self._running:
                continue
            try:
                self._send_now(batch)
            except OSError:
                self._running = False  # the poll loop starts a new session

    def _send_now(self, batch):
        with self._lock:  # RID and ofs must rise one by one
            data = {"count": len(batch), "ofs": self._ofs}
            for i, (name, payload) in enumerate(batch):
                data[f"req{i}__sc"] = name
                for key, value in (payload or {}).items():
                    data[f"req{i}_{key}"] = value
            self._ofs += len(batch)
            url = f"{BASE}/bc/bind?{self._params('send')}"
        with _request(url, data, timeout=15) as resp:
            resp.read()

    # -- pairing --------------------------------------------------------------------
    def _code_loop(self):
        while not self._stop.is_set():
            if self._running and self._token:
                try:
                    code = _post_text(BASE + "/pairing/get_pairing_code?ctx=pair", {
                        "access_type": "permanent", "app": APP, "lounge_token": self._token,
                        "screen_id": self.store["screen_id"], "screen_name": self.name,
                        "device_id": self.store["device_id"]}).strip()
                    if code.isdigit():
                        self.code = " ".join(code[i:i + 3] for i in range(0, len(code), 3))
                        self._status()
                    self._stop.wait(CODE_REFRESH)
                    continue
                except OSError:
                    pass
            self._stop.wait(5)

    def register_pairing_code(self, code):
        """DIAL launch from a phone on the network: it hands over its own pairing code."""
        _post_text(BASE + "/pairing/register_pairing_code", {
            "access_type": "permanent", "app": APP, "pairing_code": code, "screen_id": self.store["screen_id"],
            "screen_name": self.name, "device_id": self.store["device_id"]})

    # -- incoming messages ----------------------------------------------------------
    def _handle(self, aid, name, payload):
        if isinstance(aid, int):
            self._aid = max(self._aid, aid)
        p = payload if isinstance(payload, dict) else {}
        if name == "loungeStatus":
            try:
                devices = json.loads(p.get("devices", "[]"))
            except ValueError:
                devices = []
            self.remotes = {d.get("id"): d.get("name", "telefoon") for d in devices if d.get("type") == "REMOTE_CONTROL"}
            self._status()
            self.send(("onHasPreviousNextChanged", self._nav()), ("onAutoplayModeChanged", {"autoplayMode": "UNSUPPORTED"}),
                      ("nowPlaying", self._now_playing()))
        elif name == "remoteConnected":
            self.remotes[p.get("id", "")] = p.get("name", "telefoon")
            self._status()
            self.send(("onHasPreviousNextChanged", self._nav()), ("nowPlaying", self._now_playing()),
                      ("onStateChange", self._state_payload()))
        elif name == "remoteDisconnected":
            self.remotes.pop(p.get("id", ""), None)
            self._status()
        elif name == "getNowPlaying":
            self.send(("nowPlaying", self._now_playing()))
        elif name in ("setPlaylist", "updatePlaylist"):
            self._playlist(name, p)
        elif name == "play":
            self.bridge.command("resume")
        elif name == "pause":
            self.bridge.command("pause")
        elif name == "stopVideo":
            self.bridge.command("stop")
        elif name == "seekTo":
            self.bridge.command("seek", int(float(p.get("newTime", 0)) * 1000))
        elif name == "next":
            self.step(1)
        elif name == "previous":
            self.step(-1)
        elif name == "getVolume":
            self.send(("onVolumeChanged", self._volume()))
        elif name == "setVolume":
            if "volume" in p:
                self.bridge.command("volume", max(0, min(100, int(float(p["volume"])))))
            if "muted" in p:
                self.bridge.command("mute", str(p["muted"]).lower() == "true")
        elif name == "setAutoplayMode":
            self.send(("onAutoplayModeChanged", {"autoplayMode": "UNSUPPORTED"}))

    def _playlist(self, name, p):
        pl = self.playlist
        if not p.get("listId"):
            pl.update(list_id="", ids=[], index=0)
            if name == "updatePlaylist":
                self.current = ""
                self.bridge.command("stop")
            return
        pl["list_id"] = p["listId"]
        pl["ids"] = [v for v in p.get("videoIds", "").split(",") if v]
        pl["ctt"], pl["params"] = p.get("ctt", ""), p.get("params", "")
        if name == "setPlaylist" and p.get("videoId"):
            index = int(p.get("currentIndex") or 0)
            if p["videoId"] != self.current or index != pl["index"] or self.bridge.snapshot().get("owner") != "youtube":
                pl["index"] = index
                self._start(p["videoId"], float(p.get("currentTime") or 0))
                return
        elif name == "updatePlaylist" and self.current:
            if self.current in pl["ids"]:
                pl["index"] = pl["ids"].index(self.current)
            else:
                self.current = ""
                self.bridge.command("stop")
        self.send(("nowPlaying", self._now_playing()), ("onHasPreviousNextChanged", self._nav()))

    def _start(self, video_id, start_s=0.0):
        self.current = video_id
        self._pending = True
        self.cpn = "".join(random.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
                           for _ in range(16))
        self.bridge.play_youtube(video_id, start_s)
        self.send(("nowPlaying", self._now_playing("loading")), ("onStateChange", self._state_payload("loading")),
                  ("onHasPreviousNextChanged", self._nav()))

    def step(self, delta):
        """Next/previous video in the phone's queue; False at the end of the queue."""
        pl = self.playlist
        i = pl["index"] + delta
        if not (0 <= i < len(pl["ids"])):
            return False
        pl["index"] = i
        self._start(pl["ids"][i])
        return True

    # -- state towards the phone -------------------------------------------------------
    def _nav(self):
        pl = self.playlist
        return {"hasPrevious": str(pl["index"] > 0).lower(), "hasNext": str(pl["index"] < len(pl["ids"]) - 1).lower()}

    def _volume(self):
        snap = self.bridge.snapshot()
        return {"volume": int(snap.get("volume", 0)), "muted": str(bool(snap.get("muted"))).lower()}

    def _state(self, override=None):
        snap = self.bridge.snapshot()
        if override:
            return STATE[override], snap
        if snap.get("owner") != "youtube" or snap.get("video_id") != self.current:
            if self._pending:
                return STATE["loading"], snap  # being started (stream address is fetched)
            return (STATE["stopped"] if self.current else STATE["idle"]), snap
        if snap.get("state") in ("playing", "paused"):
            self._pending = False
        return STATE.get(snap.get("state"), STATE["stopped"]), snap

    def _state_payload(self, override=None):
        state, snap = self._state(override)
        ours = snap.get("owner") == "youtube" and snap.get("video_id") == self.current
        pos = snap.get("position_ms", 0) / 1000 if ours else 0
        dur = snap.get("duration_ms", 0) / 1000 if ours else 0
        return {"state": state, "currentTime": f"{pos:.3f}", "duration": f"{dur:.3f}", "cpn": self.cpn,
                "loadedTime": f"{dur:.3f}" if state in (1, 2, 3) else "0", "seekableStartTime": "0",
                "seekableEndTime": f"{dur:.3f}"}

    def _now_playing(self, override=None):
        if not self.current:
            return {}
        out = self._state_payload(override)
        out["videoId"] = self.current
        pl = self.playlist
        if pl["list_id"]:
            out["listId"] = pl["list_id"]
            out["currentIndex"] = str(pl["index"])
        if pl["ctt"]:
            out["ctt"] = pl["ctt"]
        if pl["params"]:
            out["params"] = pl["params"]
        return out

    def player_changed(self, reason="state"):
        """From CinemaViewer (state change, seek, volume, or every few seconds while playing)."""
        if not self._running:
            return
        if reason == "volume":
            self.send(("onVolumeChanged", self._volume()))
            return
        if not self.current:
            return
        now = time.monotonic()
        if reason == "tick" and now - self._last_state_sent < 10:
            return
        self._last_state_sent = now
        self.send(("onStateChange", self._state_payload()))

    def play_failed(self):
        self._pending = False
        self.send(("onStateChange", self._state_payload("stopped")), ("nowPlaying", self._now_playing("stopped")))

    def ended(self):
        """The video finished: the next one in the phone's queue, or stop."""
        if not self.step(1):
            self.send(("onStateChange", self._state_payload("stopped")), ("nowPlaying", self._now_playing("stopped")))

    # -- DIAL (discovery by the YouTube app on the same network) ------------------------------
    def handle_dial(self, req):
        if req.path == "/dial/ssdp/device-desc.xml":
            xml = ('<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">'
                   "<specVersion><major>1</major><minor>0</minor></specVersion>"
                   f"<URLBase>{req.base_url}</URLBase><device>"
                   "<deviceType>urn:dial-multiscreen-org:device:dial:1</deviceType>"
                   f"<friendlyName>{html.escape(self.name)}</friendlyName><manufacturer>CinemaViewer</manufacturer>"
                   f"<modelName>Displaycast</modelName><UDN>uuid:{self.device.uuid}</UDN>"
                   "<serviceList><service><serviceType>urn:dial-multiscreen-org:service:dial:1</serviceType>"
                   "<serviceId>urn:dial-multiscreen-org:serviceId:dial</serviceId>"
                   "<controlURL>/dial/notfound</controlURL><eventSubURL>/dial/notfound</eventSubURL>"
                   "<SCPDURL>/dial/notfound</SCPDURL></service></serviceList></device></root>")
            return Response(200, xml, {"Application-URL": f"{req.base_url}/dial/apps"})
        if req.path in ("/dial/apps", "/dial/apps/"):
            return Response(204)
        if req.path == "/dial/apps/YouTube":
            if req.method == "GET":
                state = "running" if self._running else "stopped"
                xml = ('<?xml version="1.0" encoding="UTF-8"?>'
                       '<service xmlns="urn:dial-multiscreen-org:schemas:dial" dialVer="1.7">'
                       f'<name>YouTube</name><options allowStop="false"/><state>{state}</state>'
                       + ('<link rel="run" href="run"/>' if self._running else "") + "</service>")
                return Response(200, xml)
            if req.method == "POST":
                launch = urllib.parse.parse_qs(req.body.decode("utf-8", "replace"))
                code = (launch.get("pairingCode") or [""])[0]
                if not code or not self._running:
                    return Response(503)
                try:
                    self.register_pairing_code(code)
                except OSError:
                    return Response(503)
                return Response(201, b"", {"LOCATION": f"{req.base_url}/dial/apps/YouTube/run"})
        if req.path.startswith("/dial/apps/YouTube/") and req.method == "DELETE":
            return Response(200)
        return Response(404)
