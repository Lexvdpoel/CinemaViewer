import socket
import urllib.parse

from .. import __version__, net
from ..models import MediaItem

KIND_MAP = {"movie": "movie", "episode": "episode", "show": "series", "season": "season", "clip": "video",
            "artist": "artist", "album": "album", "track": "track", "playlist": "playlist"}


class PlexSource:
    key = "plex"
    label = "Plex"

    def __init__(self, url, token, device_id):
        self.url = url.rstrip("/")
        self.token = token
        self.device_id = device_id

    def _headers(self):
        return {
            "X-Plex-Token": self.token,
            "X-Plex-Client-Identifier": self.device_id,
            "X-Plex-Product": "CinemaViewer",
            "X-Plex-Version": __version__,
            "X-Plex-Platform": "Windows",
            "X-Plex-Device-Name": socket.gethostname(),
        }

    def _get(self, path, **params):
        query = urllib.parse.urlencode(params)
        data = net.request("GET", f"{self.url}{path}" + (f"?{query}" if query else ""), headers=self._headers())
        return (data or {}).get("MediaContainer", {})

    def test(self):
        return self._get("/").get("friendlyName", "Plex Media Server")

    # -- browsing -------------------------------------------------------------
    def libraries(self):
        out = []
        for d in self._get("/library/sections").get("Directory", []):
            if d.get("type") not in ("movie", "show", "artist"):
                continue
            out.append(MediaItem(
                source=self.key, id=str(d["key"]), title=d.get("title", ""), kind="library",
                is_folder=True, group="Plex", extra={"section_type": d.get("type")},
            ))
        out.append(MediaItem(source=self.key, id="playlists", title="Afspeellijsten", kind="playlists",
                             is_folder=True, group="Plex"))
        return out

    def children(self, item):
        if item.kind == "playlists":
            mc = self._get("/playlists")
        elif item.kind == "playlist":
            mc = self._get(f"/playlists/{item.id}/children")
        elif item.kind == "library":
            mc = self._get(f"/library/sections/{item.id}/all", sort="titleSort")
        else:
            mc = self._get(f"/library/metadata/{item.id}/children")
        return [self._to_item(d) for d in mc.get("Metadata", [])]

    def _img(self, path, w=400, h=600):
        if not path:
            return ""
        return f"{self.url}/photo/:/transcode?" + urllib.parse.urlencode(
            {"width": w, "height": h, "minSize": 1, "upscale": 1, "url": path, "X-Plex-Token": self.token})

    def _to_item(self, d):
        t = d.get("type", "")
        subtitle = ""
        if t == "episode":
            s, e = d.get("parentIndex"), d.get("index")
            code = f"S{s:02d}E{e:02d}" if s is not None and e is not None else ""
            subtitle = " · ".join(x for x in (d.get("grandparentTitle"), code) if x)
        elif t == "season":
            subtitle = d.get("parentTitle", "")
        elif t == "track":
            artist = d.get("grandparentTitle") or d.get("parentTitle") or ""
            album = d.get("parentTitle") if d.get("grandparentTitle") else ""
            subtitle = " · ".join(x for x in (artist, album) if x)
        elif t == "playlist":
            subtitle = f"{d.get('leafCount', 0)} items"
        part_key = ""
        media = d.get("Media") or []
        if media and media[0].get("Part"):
            part_key = media[0]["Part"][0].get("key", "")
        thumb = d.get("thumb") or d.get("parentThumb") or d.get("grandparentThumb")
        art = d.get("art") or d.get("grandparentArt")
        extra = {"part_key": part_key}
        if t == "episode":  # for the bibliotheek: Series/<serie>/Seizoen <n>/
            extra.update(series=d.get("grandparentTitle", ""), season=d.get("parentIndex"), episode=d.get("index"),
                         series_image=self._img(d.get("grandparentThumb")))
        elif t == "track":
            extra.update(artist=d.get("grandparentTitle") or d.get("parentTitle") or "",
                         album=d.get("parentTitle") if d.get("grandparentTitle") else "")
        return MediaItem(
            source=self.key,
            id=str(d.get("ratingKey")),
            title=d.get("title", ""),
            kind=KIND_MAP.get(t, "video"),
            is_folder=t in ("show", "season", "artist", "album", "playlist"),
            year=d.get("year"),
            overview=d.get("summary", "") or "",
            genres=[g.get("tag") for g in d.get("Genre", []) if g.get("tag")],
            rating=d.get("contentRating", "") or "",
            community_rating=d.get("audienceRating") or d.get("rating"),
            runtime_ms=d.get("duration") or 0,
            resume_ms=d.get("viewOffset") or 0,
            played=(d.get("viewCount") or 0) > 0,
            image_url=self._img(thumb),
            backdrop_url=self._img(art, 1920, 1080),
            subtitle=subtitle,
            extra=extra,
        )

    # -- playback -------------------------------------------------------------
    def markers(self, item):
        """{'credits': ms or None, 'chapters': [(ms, name)]} from Plex' credit markers and chapters."""
        try:
            meta = self._get(f"/library/metadata/{item.id}", includeMarkers=1,
                             includeChapters=1).get("Metadata", [{}])[0]
        except net.HttpError:
            return {"credits": None, "chapters": []}
        credits = [m for m in meta.get("Marker", []) if m.get("type") == "credits"]
        final = [m for m in credits if m.get("final")] or credits[-1:]
        chapters = [(c.get("startTimeOffset", 0), c.get("tag", "")) for c in meta.get("Chapter", [])]
        return {"credits": final[0].get("startTimeOffset") if final else None, "chapters": chapters}

    def play_url(self, item):
        part_key = item.extra.get("part_key")
        if not part_key:
            meta = self._get(f"/library/metadata/{item.id}").get("Metadata", [{}])[0]
            part_key = meta["Media"][0]["Part"][0]["key"]
        return f"{self.url}{part_key}?X-Plex-Token={self.token}"

    def _timeline(self, item, state, pos_ms):
        params = {
            "ratingKey": item.id, "key": f"/library/metadata/{item.id}", "state": state,
            "time": int(pos_ms), "duration": int(item.runtime_ms or 0),
        }
        net.request("GET", f"{self.url}/:/timeline?" + urllib.parse.urlencode(params),
                    headers=self._headers(), timeout=8)

    def report_start(self, item, pos_ms):
        self._timeline(item, "playing", pos_ms)

    def report_progress(self, item, pos_ms, paused):
        self._timeline(item, "paused" if paused else "playing", pos_ms)

    def report_stop(self, item, pos_ms):
        self._timeline(item, "stopped", pos_ms)
