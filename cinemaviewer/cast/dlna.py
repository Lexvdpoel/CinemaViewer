"""DLNA / UPnP MediaRenderer: apps on phones and tablets (gallery apps, BubbleUPnP, VLC, Windows
'Cast to device', …) send a photo, video or song, which CinemaViewer plays with its own player.

Implements the three standard services: AVTransport (what to play, play/pause/seek), RenderingControl
(volume, mute) and ConnectionManager (supported formats), with event notifications (GENA) so the
sending app follows the state.
"""

import html
import queue
import re
import threading
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET

from .server import Device, Response

AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RC = "urn:schemas-upnp-org:service:RenderingControl:1"
CM = "urn:schemas-upnp-org:service:ConnectionManager:1"
DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaRenderer:1"

SINK_PROTOCOLS = ",".join(f"http-get:*:{m}:*" for m in (
    "video/mp4", "video/x-matroska", "video/mpeg", "video/quicktime", "video/x-msvideo", "video/avi",
    "video/webm", "video/x-flv", "video/3gpp", "video/mp2t", "video/vnd.dlna.mpeg-tts", "video/x-ms-wmv",
    "audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/flac", "audio/x-flac",
    "audio/wav", "audio/x-wav", "audio/L16", "audio/ogg", "audio/x-ms-wma", "audio/vnd.dlna.adts",
    "image/jpeg", "image/png", "image/gif", "image/bmp", "image/webp", "application/x-mpegURL",
    "application/vnd.apple.mpegurl", "*"))

_NS = {"dc": "http://purl.org/dc/elements/1.1/", "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
       "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"}


def fmt_hms(ms):
    s = max(0, int(ms or 0) // 1000)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def parse_hms(text):
    """'1:02:03.500' or '0:00:12' -> milliseconds."""
    m = re.fullmatch(r"\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?\s*", text or "")
    if not m:
        return 0
    h, mi, s, frac = m.groups()
    return ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + (int((frac + "000")[:3]) if frac else 0)


def parse_didl(xml_text):
    """Title, artist, album, cover and kind ('video', 'track', 'photo') from DIDL-Lite metadata."""
    out = {"title": "", "artist": "", "album": "", "art": "", "kind": "", "duration_ms": 0}
    if not xml_text or "<" not in xml_text:
        return out
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    item = root.find("didl:item", _NS)
    if item is None:
        return out

    def text(path):
        el = item.find(path, _NS)
        return (el.text or "").strip() if el is not None and el.text else ""

    out["title"] = text("dc:title")
    out["artist"] = text("upnp:artist") or text("dc:creator")
    out["album"] = text("upnp:album")
    out["art"] = text("upnp:albumArtURI")
    cls = text("upnp:class")
    out["kind"] = ("track" if "audioItem" in cls else "photo" if "imageItem" in cls
                   else "video" if "videoItem" in cls else "")
    res = item.find("didl:res", _NS)
    if res is not None:
        out["duration_ms"] = parse_hms(res.get("duration", ""))
    return out


def _scpd(actions, variables):
    """Service description XML from {action: [(arg, direction, related variable)]} and
    {variable: (type, events, allowed values)}."""
    acts = "".join(
        f"<action><name>{name}</name><argumentList>" + "".join(
            f"<argument><name>{a}</name><direction>{d}</direction><relatedStateVariable>{v}</relatedStateVariable>"
            "</argument>" for a, d, v in args) + "</argumentList></action>" for name, args in actions.items())
    vars_ = "".join(
        f'<stateVariable sendEvents="{"yes" if ev else "no"}"><name>{n}</name><dataType>{t}</dataType>'
        + ("<allowedValueList>" + "".join(f"<allowedValue>{x}</allowedValue>" for x in allowed)
           + "</allowedValueList>" if allowed else "")
        + "</stateVariable>" for n, (t, ev, allowed) in variables.items())
    return ('<?xml version="1.0" encoding="utf-8"?><scpd xmlns="urn:schemas-upnp-org:service-1-0">'
            "<specVersion><major>1</major><minor>0</minor></specVersion>"
            f"<actionList>{acts}</actionList><serviceStateTable>{vars_}</serviceStateTable></scpd>")


I, O = "in", "out"
IID = ("InstanceID", I, "A_ARG_TYPE_InstanceID")
AVT_SCPD = _scpd({
    "SetAVTransportURI": [IID, ("CurrentURI", I, "AVTransportURI"), ("CurrentURIMetaData", I, "AVTransportURIMetaData")],
    "SetNextAVTransportURI": [IID, ("NextURI", I, "NextAVTransportURI"),
                              ("NextURIMetaData", I, "NextAVTransportURIMetaData")],
    "GetMediaInfo": [IID, ("NrTracks", O, "NumberOfTracks"), ("MediaDuration", O, "CurrentMediaDuration"),
                     ("CurrentURI", O, "AVTransportURI"), ("CurrentURIMetaData", O, "AVTransportURIMetaData"),
                     ("NextURI", O, "NextAVTransportURI"), ("NextURIMetaData", O, "NextAVTransportURIMetaData"),
                     ("PlayMedium", O, "PlaybackStorageMedium"), ("RecordMedium", O, "RecordStorageMedium"),
                     ("WriteStatus", O, "RecordMediumWriteStatus")],
    "GetTransportInfo": [IID, ("CurrentTransportState", O, "TransportState"),
                         ("CurrentTransportStatus", O, "TransportStatus"), ("CurrentSpeed", O, "TransportPlaySpeed")],
    "GetPositionInfo": [IID, ("Track", O, "CurrentTrack"), ("TrackDuration", O, "CurrentTrackDuration"),
                        ("TrackMetaData", O, "CurrentTrackMetaData"), ("TrackURI", O, "CurrentTrackURI"),
                        ("RelTime", O, "RelativeTimePosition"), ("AbsTime", O, "AbsoluteTimePosition"),
                        ("RelCount", O, "RelativeCounterPosition"), ("AbsCount", O, "AbsoluteCounterPosition")],
    "GetDeviceCapabilities": [IID, ("PlayMedia", O, "PossiblePlaybackStorageMedia"),
                              ("RecMedia", O, "PossibleRecordStorageMedia"),
                              ("RecQualityModes", O, "PossibleRecordQualityModes")],
    "GetTransportSettings": [IID, ("PlayMode", O, "CurrentPlayMode"), ("RecQualityMode", O, "CurrentRecordQualityMode")],
    "GetCurrentTransportActions": [IID, ("Actions", O, "CurrentTransportActions")],
    "Stop": [IID], "Play": [IID, ("Speed", I, "TransportPlaySpeed")], "Pause": [IID],
    "Seek": [IID, ("Unit", I, "A_ARG_TYPE_SeekMode"), ("Target", I, "A_ARG_TYPE_SeekTarget")],
    "Next": [IID], "Previous": [IID],
}, {
    "TransportState": ("string", False, ["STOPPED", "PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING", "NO_MEDIA_PRESENT"]),
    "TransportStatus": ("string", False, ["OK", "ERROR_OCCURRED"]), "TransportPlaySpeed": ("string", False, ["1"]),
    "PlaybackStorageMedium": ("string", False, ["NONE", "NETWORK"]), "RecordStorageMedium": ("string", False, ["NOT_IMPLEMENTED"]),
    "PossiblePlaybackStorageMedia": ("string", False, None), "PossibleRecordStorageMedia": ("string", False, None),
    "PossibleRecordQualityModes": ("string", False, None), "CurrentPlayMode": ("string", False, ["NORMAL"]),
    "CurrentRecordQualityMode": ("string", False, ["NOT_IMPLEMENTED"]), "RecordMediumWriteStatus": ("string", False, None),
    "NumberOfTracks": ("ui4", False, None), "CurrentTrack": ("ui4", False, None),
    "CurrentTrackDuration": ("string", False, None), "CurrentMediaDuration": ("string", False, None),
    "CurrentTrackMetaData": ("string", False, None), "CurrentTrackURI": ("string", False, None),
    "AVTransportURI": ("string", False, None), "AVTransportURIMetaData": ("string", False, None),
    "NextAVTransportURI": ("string", False, None), "NextAVTransportURIMetaData": ("string", False, None),
    "RelativeTimePosition": ("string", False, None), "AbsoluteTimePosition": ("string", False, None),
    "RelativeCounterPosition": ("i4", False, None), "AbsoluteCounterPosition": ("i4", False, None),
    "CurrentTransportActions": ("string", False, None), "LastChange": ("string", True, None),
    "A_ARG_TYPE_SeekMode": ("string", False, ["REL_TIME", "ABS_TIME", "TRACK_NR"]),
    "A_ARG_TYPE_SeekTarget": ("string", False, None), "A_ARG_TYPE_InstanceID": ("ui4", False, None),
})
RC_SCPD = _scpd({
    "GetVolume": [IID, ("Channel", I, "A_ARG_TYPE_Channel"), ("CurrentVolume", O, "Volume")],
    "SetVolume": [IID, ("Channel", I, "A_ARG_TYPE_Channel"), ("DesiredVolume", I, "Volume")],
    "GetMute": [IID, ("Channel", I, "A_ARG_TYPE_Channel"), ("CurrentMute", O, "Mute")],
    "SetMute": [IID, ("Channel", I, "A_ARG_TYPE_Channel"), ("DesiredMute", I, "Mute")],
    "ListPresets": [IID, ("CurrentPresetNameList", O, "PresetNameList")],
}, {
    "Volume": ("ui2", False, None), "Mute": ("boolean", False, None), "PresetNameList": ("string", False, None),
    "LastChange": ("string", True, None), "A_ARG_TYPE_Channel": ("string", False, ["Master"]),
    "A_ARG_TYPE_InstanceID": ("ui4", False, None),
})
CM_SCPD = _scpd({
    "GetProtocolInfo": [("Source", O, "SourceProtocolInfo"), ("Sink", O, "SinkProtocolInfo")],
    "GetCurrentConnectionIDs": [("ConnectionIDs", O, "CurrentConnectionIDs")],
    "GetCurrentConnectionInfo": [("ConnectionID", I, "A_ARG_TYPE_ConnectionID"),
                                 ("RcsID", O, "A_ARG_TYPE_RcsID"), ("AVTransportID", O, "A_ARG_TYPE_AVTransportID"),
                                 ("ProtocolInfo", O, "A_ARG_TYPE_ProtocolInfo"),
                                 ("PeerConnectionManager", O, "A_ARG_TYPE_ConnectionManager"),
                                 ("PeerConnectionID", O, "A_ARG_TYPE_ConnectionID"),
                                 ("Direction", O, "A_ARG_TYPE_Direction"), ("Status", O, "A_ARG_TYPE_ConnectionStatus")],
}, {
    "SourceProtocolInfo": ("string", True, None), "SinkProtocolInfo": ("string", True, None),
    "CurrentConnectionIDs": ("string", True, None), "A_ARG_TYPE_ConnectionID": ("i4", False, None),
    "A_ARG_TYPE_RcsID": ("i4", False, None), "A_ARG_TYPE_AVTransportID": ("i4", False, None),
    "A_ARG_TYPE_ProtocolInfo": ("string", False, None), "A_ARG_TYPE_ConnectionManager": ("string", False, None),
    "A_ARG_TYPE_Direction": ("string", False, ["Input", "Output"]),
    "A_ARG_TYPE_ConnectionStatus": ("string", False, ["OK", "Unknown"]),
})


class SoapFault(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code, self.text = code, text


class DlnaRenderer:
    """`bridge` connects to CinemaViewer: play(uri, meta), command(name, *args) and snapshot()."""

    def __init__(self, uuid_, name, bridge):
        self.uuid = uuid_
        self.name = name
        self.bridge = bridge
        self.device = Device(uuid_, "/dlna/device.xml", ["upnp:rootdevice", DEVICE_TYPE, AVT, RC, CM])
        self.uri = ""
        self.meta = ""
        self.info = parse_didl("")
        self._subs = {}               # sid -> {"service", "urls", "expires", "seq"}
        self._lock = threading.Lock()
        self._events = queue.Queue()
        self._last = {}
        threading.Thread(target=self._notifier, name="Displaycast DLNA events", daemon=True).start()

    # -- descriptions ---------------------------------------------------------
    def description(self):
        services = "".join(
            f"<service><serviceType>{t}</serviceType><serviceId>urn:upnp-org:serviceId:{sid}</serviceId>"
            f"<SCPDURL>/dlna/{sid}.xml</SCPDURL><controlURL>/dlna/{sid}/control</controlURL>"
            f"<eventSubURL>/dlna/{sid}/event</eventSubURL></service>"
            for t, sid in ((AVT, "AVTransport"), (RC, "RenderingControl"), (CM, "ConnectionManager")))
        return ('<?xml version="1.0" encoding="utf-8"?>'
                '<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0">'
                "<specVersion><major>1</major><minor>0</minor></specVersion><device>"
                f"<deviceType>{DEVICE_TYPE}</deviceType><friendlyName>{html.escape(self.name)}</friendlyName>"
                "<manufacturer>CinemaViewer</manufacturer><modelName>CinemaViewer Displaycast</modelName>"
                "<modelDescription>CinemaViewer media renderer</modelDescription><modelNumber>1</modelNumber>"
                f"<UDN>uuid:{self.uuid}</UDN><dlna:X_DLNADOC>DMR-1.50</dlna:X_DLNADOC>"
                f"<serviceList>{services}</serviceList></device></root>")

    def handle(self, req):
        path = req.path
        if path == "/dlna/device.xml":
            return Response(200, self.description())
        scpd = {"/dlna/AVTransport.xml": AVT_SCPD, "/dlna/RenderingControl.xml": RC_SCPD,
                "/dlna/ConnectionManager.xml": CM_SCPD}.get(path)
        if scpd:
            return Response(200, scpd)
        m = re.fullmatch(r"/dlna/(AVTransport|RenderingControl|ConnectionManager)/(control|event)", path)
        if not m:
            return Response(404)
        service = {"AVTransport": AVT, "RenderingControl": RC, "ConnectionManager": CM}[m.group(1)]
        if m.group(2) == "event":
            return self._event_request(req, service)
        return self._soap(req, service)

    # -- SOAP -----------------------------------------------------------------
    def _soap(self, req, service):
        action = req.headers.get("SOAPACTION", "").strip('"').rpartition("#")[2]
        args = {}
        try:
            root = ET.fromstring(req.body)
            body = next(el for el in root.iter() if el.tag.endswith("}Body") or el.tag == "Body")
            call = list(body)[0]
            action = action or call.tag.rpartition("}")[2]
            args = {child.tag.rpartition("}")[2]: (child.text or "") for child in call}
        except (ET.ParseError, StopIteration, IndexError):
            pass
        try:
            out = self._action(service, action, args)
        except SoapFault as f:
            fault = ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                     's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><s:Fault>'
                     "<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>"
                     '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
                     f"<errorCode>{f.code}</errorCode><errorDescription>{html.escape(f.text)}</errorDescription>"
                     "</UPnPError></detail></s:Fault></s:Body></s:Envelope>")
            return Response(500, fault)
        values = "".join(f"<{k}>{html.escape(str(v))}</{k}>" for k, v in out.items())
        envelope = ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                    f'<u:{action}Response xmlns:u="{service}">{values}</u:{action}Response></s:Body></s:Envelope>')
        return Response(200, envelope, {"EXT": ""})

    def _ours(self, snap):
        return snap.get("owner") == "dlna" and snap.get("uri") == self.uri

    def transport_state(self, snap=None):
        snap = snap or self.bridge.snapshot()
        if not self._ours(snap):
            return "STOPPED" if self.uri else "NO_MEDIA_PRESENT"
        return {"playing": "PLAYING", "paused": "PAUSED_PLAYBACK", "loading": "TRANSITIONING"}.get(
            snap.get("state"), "STOPPED")

    def _action(self, service, action, a):
        snap = self.bridge.snapshot()
        ours = self._ours(snap)
        if service == AVT:
            if action == "SetAVTransportURI":
                self.uri, self.meta = a.get("CurrentURI", "").strip(), a.get("CurrentURIMetaData", "")
                self.info = parse_didl(self.meta)
                if ours or snap.get("owner") == "dlna":
                    self.bridge.play(self.uri, self.info)  # already casting: switch right away
                self.changed()
                return {}
            if action == "SetNextAVTransportURI":
                return {}
            if action == "Play":
                if not self.uri:
                    raise SoapFault(701, "Transition not available")
                if ours and snap.get("state") == "paused":
                    self.bridge.command("resume")
                elif not ours or snap.get("state") not in ("playing", "loading"):
                    self.bridge.play(self.uri, self.info)
                return {}
            if action == "Pause":
                if ours:
                    self.bridge.command("pause")
                return {}
            if action == "Stop":
                if ours:
                    self.bridge.command("stop")
                return {}
            if action == "Seek":
                if a.get("Unit") in ("REL_TIME", "ABS_TIME") and ours:
                    self.bridge.command("seek", parse_hms(a.get("Target", "")))
                return {}
            if action in ("Next", "Previous"):
                return {}
            if action == "GetTransportInfo":
                return {"CurrentTransportState": self.transport_state(snap), "CurrentTransportStatus": "OK",
                        "CurrentSpeed": "1"}
            if action == "GetPositionInfo":
                pos = snap.get("position_ms", 0) if ours else 0
                dur = (snap.get("duration_ms") or self.info["duration_ms"]) if ours else self.info["duration_ms"]
                return {"Track": 1 if self.uri else 0, "TrackDuration": fmt_hms(dur), "TrackMetaData": self.meta,
                        "TrackURI": self.uri, "RelTime": fmt_hms(pos), "AbsTime": fmt_hms(pos),
                        "RelCount": 2147483647, "AbsCount": 2147483647}
            if action == "GetMediaInfo":
                dur = snap.get("duration_ms") if ours else self.info["duration_ms"]
                return {"NrTracks": 1 if self.uri else 0, "MediaDuration": fmt_hms(dur), "CurrentURI": self.uri,
                        "CurrentURIMetaData": self.meta, "NextURI": "", "NextURIMetaData": "",
                        "PlayMedium": "NETWORK" if self.uri else "NONE", "RecordMedium": "NOT_IMPLEMENTED",
                        "WriteStatus": "NOT_IMPLEMENTED"}
            if action == "GetDeviceCapabilities":
                return {"PlayMedia": "NETWORK", "RecMedia": "NOT_IMPLEMENTED", "RecQualityModes": "NOT_IMPLEMENTED"}
            if action == "GetTransportSettings":
                return {"PlayMode": "NORMAL", "RecQualityMode": "NOT_IMPLEMENTED"}
            if action == "GetCurrentTransportActions":
                return {"Actions": "Play,Pause,Stop,Seek" if self.uri else ""}
        elif service == RC:
            if action == "GetVolume":
                return {"CurrentVolume": int(snap.get("volume", 0))}
            if action == "SetVolume":
                self.bridge.command("volume", max(0, min(100, int(a.get("DesiredVolume") or 0))))
                return {}
            if action == "GetMute":
                return {"CurrentMute": "1" if snap.get("muted") else "0"}
            if action == "SetMute":
                self.bridge.command("mute", a.get("DesiredMute", "0").strip().lower() in ("1", "true", "yes"))
                return {}
            if action == "ListPresets":
                return {"CurrentPresetNameList": "FactoryDefaults"}
        elif service == CM:
            if action == "GetProtocolInfo":
                return {"Source": "", "Sink": SINK_PROTOCOLS}
            if action == "GetCurrentConnectionIDs":
                return {"ConnectionIDs": "0"}
            if action == "GetCurrentConnectionInfo":
                return {"RcsID": 0, "AVTransportID": 0, "ProtocolInfo": "", "PeerConnectionManager": "",
                        "PeerConnectionID": -1, "Direction": "Input", "Status": "OK"}
        raise SoapFault(401, "Invalid Action")

    # -- events (GENA) ----------------------------------------------------------
    def _event_request(self, req, service):
        now = time.monotonic()
        if req.method == "SUBSCRIBE":
            timeout = 1800
            m = re.search(r"Second-(\d+)", req.headers.get("TIMEOUT", ""))
            if m:
                timeout = max(60, min(7200, int(m.group(1))))
            sid = req.headers.get("SID")
            with self._lock:
                if sid:  # renewal
                    if sid not in self._subs:
                        return Response(412)
                    self._subs[sid]["expires"] = now + timeout
                else:
                    urls = re.findall(r"<([^>]+)>", req.headers.get("CALLBACK", ""))
                    if not urls:
                        return Response(412)
                    sid = f"uuid:{uuid.uuid4()}"
                    self._subs[sid] = {"service": service, "urls": urls, "expires": now + timeout, "seq": 0}
                    self._events.put(("initial", sid))
            return Response(200, b"", {"SID": sid, "TIMEOUT": f"Second-{timeout}"})
        if req.method == "UNSUBSCRIBE":
            with self._lock:
                self._subs.pop(req.headers.get("SID", ""), None)
            return Response(200)
        return Response(405)

    def changed(self):
        """Called when something may have changed (state, position jump, volume): notify subscribers."""
        self._events.put(("change", None))

    def _last_change(self, service, snap):
        if service == AVT:
            values = {"TransportState": self.transport_state(snap), "TransportStatus": "OK",
                      "CurrentTrackURI": self.uri, "AVTransportURI": self.uri,
                      "CurrentTrackMetaData": self.meta, "AVTransportURIMetaData": self.meta,
                      "CurrentTrackDuration": fmt_hms(snap.get("duration_ms") if self._ours(snap)
                                                      else self.info["duration_ms"]),
                      "CurrentTransportActions": "Play,Pause,Stop,Seek" if self.uri else "",
                      "NumberOfTracks": "1" if self.uri else "0", "CurrentTrack": "1" if self.uri else "0"}
            ns = "urn:schemas-upnp-org:metadata-1-0/AVT/"
        elif service == RC:
            values = {"Volume": str(int(snap.get("volume", 0))), "Mute": "1" if snap.get("muted") else "0"}
            ns = "urn:schemas-upnp-org:metadata-1-0/RCS/"
        else:
            return None
        master = 'channel="Master" '
        attrs = "".join(f'<{k} {master if k in ("Volume", "Mute") else ""}val="{html.escape(v, quote=True)}"/>'
                        for k, v in values.items())
        return f'<Event xmlns="{ns}"><InstanceID val="0">{attrs}</InstanceID></Event>', values

    def _notifier(self):
        while True:
            kind, sid = self._events.get()
            time.sleep(0.05)
            while not self._events.empty():  # coalesce bursts
                try:
                    k, s = self._events.get_nowait()
                    if k == "initial":
                        kind, sid = k, s
                except queue.Empty:
                    break
            snap = self.bridge.snapshot()
            now = time.monotonic()
            with self._lock:
                for key in [k for k, v in self._subs.items() if v["expires"] < now]:
                    del self._subs[key]
                subs = list(self._subs.items())
            for key, sub in subs:
                if sub["service"] == CM:
                    if kind == "initial" and key == sid:
                        body = ('<?xml version="1.0"?><e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">'
                                f"<e:property><SinkProtocolInfo>{html.escape(SINK_PROTOCOLS)}</SinkProtocolInfo></e:property>"
                                "<e:property><SourceProtocolInfo></SourceProtocolInfo></e:property>"
                                "<e:property><CurrentConnectionIDs>0</CurrentConnectionIDs></e:property>"
                                "</e:propertyset>")
                        self._send(key, sub, body)
                    continue
                made = self._last_change(sub["service"], snap)
                if made is None:
                    continue
                event, values = made
                if not (kind == "initial" and key == sid) and self._last.get((key, "v")) == values:
                    continue  # nothing new for this subscriber
                self._last[(key, "v")] = values
                body = ('<?xml version="1.0"?><e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">'
                        f"<e:property><LastChange>{html.escape(event)}</LastChange></e:property></e:propertyset>")
                self._send(key, sub, body)

    def _send(self, sid, sub, body):
        for url in sub["urls"]:
            req = urllib.request.Request(url, data=body.encode("utf-8"), method="NOTIFY", headers={
                "Content-Type": 'text/xml; charset="utf-8"', "NT": "upnp:event", "NTS": "upnp:propchange",
                "SID": sid, "SEQ": str(sub["seq"])})
            try:
                urllib.request.urlopen(req, timeout=3).close()
                break
            except OSError:
                continue
        sub["seq"] += 1
