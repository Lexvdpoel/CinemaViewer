"""Internet radio: the operator's own list of stations, found through Radio Browser.

Radio Browser (radio-browser.info) is an open, community-maintained directory of 50,000+ stations
with their working stream addresses per country. CinemaViewer only uses it to search and add
stations; the chosen stations are stored in the settings, so playing works without the directory.
A ready-made list of Dutch stations (radio_nl.json, checked when it was made) is the default.
"""

import json
import random
import re
import socket
import urllib.parse
from pathlib import Path

from .. import net
from ..models import MediaItem

DEFAULTS_FILE = Path(__file__).with_name("radio_nl.json")
FALLBACK_SERVERS = ("de1.api.radio-browser.info", "de2.api.radio-browser.info", "fi1.api.radio-browser.info")
_servers = []


def clean_logo(url):
    """Wikimedia only serves thumbnails in fixed widths (others give HTTP 400); 'null' means none."""
    if not url or url == "null":
        return ""
    return re.sub(r"(upload\.wikimedia\.org/.+/thumb/.+/)\d+px-", r"\g<1>500px-", url)


def clean_tags(tags):
    return ",".join(t.strip() for t in (tags or "").split(",") if t.strip() and not t.startswith("#"))[:80]


def default_stations():
    try:
        return json.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


# -- Radio Browser directory ----------------------------------------------------------
def _api_servers():
    """Radio Browser asks clients to pick one of its servers from the DNS name all.api…"""
    if not _servers:
        try:
            infos = socket.getaddrinfo("all.api.radio-browser.info", 443, proto=socket.IPPROTO_TCP)
            names = {socket.gethostbyaddr(i[4][0])[0] for i in infos}
            _servers.extend(sorted(n for n in names if n.endswith("radio-browser.info")))
        except OSError:
            pass
        if not _servers:
            _servers.extend(FALLBACK_SERVERS)
        random.shuffle(_servers)
    return list(_servers)


def _api(path, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    last = None
    for server in _api_servers():
        try:
            return net.request("GET", f"https://{server}/json/{path}" + (f"?{query}" if query else ""),
                               timeout=10) or []
        except net.HttpError as e:
            last = e
    raise net.HttpError(f"Radio Browser niet bereikbaar ({last})")


def countries():
    """[(code, name, number of stations)], most stations first."""
    data = _api("countries", order="stationcount", reverse="true", hidebroken="true")
    return [(c["iso_3166_1"], c["name"], c["stationcount"]) for c in data if c.get("iso_3166_1")]


def search(country="", name="", limit=150):
    """Stations of a country and/or with a name, most listened first; duplicates (same stream) once."""
    data = _api("stations/search", countrycode=country, name=name, order="clickcount", reverse="true",
                hidebroken="true", limit=limit)
    seen, out = set(), []
    for s in data:
        url = s.get("url_resolved") or s.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"name": s.get("name", "").strip() or url, "url": url, "logo": clean_logo(s.get("favicon", "")),
                    "uuid": s.get("stationuuid", ""), "tags": ",".join(clean_tags(s.get("tags")).split(",")[:4]),
                    "codec": s.get("codec", ""), "bitrate": s.get("bitrate", 0),
                    "country": s.get("countrycode", "")})
    return out


def describe(station):
    """Short text for lists: 'pop, dance · MP3 128 kbps'."""
    tech = " ".join(x for x in (station.get("codec", ""),
                                f"{station['bitrate']} kbps" if station.get("bitrate") else "") if x)
    tags = ", ".join(t for t in clean_tags(station.get("tags", "")).split(",") if t)
    return " · ".join(x for x in (tags, tech) if x)


# -- source ---------------------------------------------------------------------------
class RadioSource:
    key = "radio"
    label = "Radio"

    def __init__(self, stations):
        self.stations = stations if stations is not None else default_stations()

    def libraries(self):
        return [MediaItem(source=self.key, id="stations", title="Radiozenders", kind="folder", is_folder=True,
                          group="Radio", subtitle=f"{len(self.stations)} zenders")]

    def children(self, item):
        return [MediaItem(source=self.key, id=s.get("uuid") or s["url"], title=s["name"], kind="radio",
                          image_url=clean_logo(s.get("logo", "")), subtitle=describe(s), extra={"station": s})
                for s in self.stations]

    def play_url(self, item):
        station = item.extra.get("station", {})
        if station.get("uuid"):
            # Radio Browser's 'click': counts the listen and returns the current stream address.
            try:
                data = _api(f"url/{station['uuid']}")
                if isinstance(data, dict) and data.get("url"):
                    return data["url"]
            except net.HttpError:
                pass
        return station["url"]

    def markers(self, item):
        return {"credits": None, "chapters": []}

    def report_start(self, item, pos_ms):
        pass

    def report_progress(self, item, pos_ms, paused):
        pass

    def report_stop(self, item, pos_ms):
        pass
