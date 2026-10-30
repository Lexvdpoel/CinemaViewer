import socket
import urllib.parse

from .. import __version__, net
from ..models import MediaItem

TICKS_PER_MS = 10_000
FIELDS = "Overview,Genres,ProductionYear,OfficialRating,CommunityRating,RunTimeTicks,ChildCount"
SKIP_COLLECTIONS = {"books", "photos", "livetv"}

KIND_MAP = {
    "Movie": "movie",
    "Episode": "episode",
    "Series": "series",
    "Season": "season",
    "BoxSet": "collection",
    "Folder": "folder",
    "CollectionFolder": "library",
    "Video": "video",
    "Trailer": "video",
    "MusicAlbum": "album",
    "MusicArtist": "artist",
    "Audio": "track",
    "Playlist": "playlist",
}


class JellyfinSource:
    key = "jellyfin"
    label = "Jellyfin"

    def __init__(self, url, token, user_id, device_id):
        self.url = url.rstrip("/")
        self.token = token
        self.user_id = user_id
        self.device_id = device_id

    # -- auth -----------------------------------------------------------------
    @staticmethod
    def auth_headers(device_id, token=None):
        value = (
            f'MediaBrowser Client="CinemaViewer", Device="{socket.gethostname()}", '
            f'DeviceId="{device_id}", Version="{__version__}"'
        )
        if token:
            value += f', Token="{token}"'
        return {"Authorization": value, "X-Emby-Authorization": value}

    @classmethod
    def login(cls, url, username, password, device_id):
        data = net.request(
            "POST",
            url.rstrip("/") + "/Users/AuthenticateByName",
            headers=cls.auth_headers(device_id),
            data={"Username": username, "Pw": password},
        )
        if not data or "AccessToken" not in data:
            raise net.HttpError("onverwacht antwoord van Jellyfin")
        return data["AccessToken"], data["User"]["Id"]

    def _get(self, path, **params):
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.url}{path}" + (f"?{query}" if query else "")
        return net.request("GET", url, headers=self.auth_headers(self.device_id, self.token)) or {}

    def _post(self, path, data):
        net.request("POST", self.url + path, headers=self.auth_headers(self.device_id, self.token),
                    data=data, timeout=8)

    # -- browsing -------------------------------------------------------------
    def libraries(self):
        data = self._get(f"/Users/{self.user_id}/Views")
        out = []
        for d in data.get("Items", []):
            collection = d.get("CollectionType") or ""
            if collection in SKIP_COLLECTIONS:
                continue
            item = self._to_item(d)
            item.kind = "library"
            item.is_folder = True
            item.group = "Jellyfin"
            item.extra["collection"] = collection
            out.append(item)
        return out

    def children(self, item):
        if item.kind == "series":
            data = self._get(f"/Shows/{item.id}/Seasons", userId=self.user_id, Fields=FIELDS)
        elif item.kind == "season":
            data = self._get(f"/Shows/{item.extra.get('series_id')}/Episodes",
                             seasonId=item.id, userId=self.user_id, Fields=FIELDS)
        elif item.kind == "playlist":
            data = self._get(f"/Playlists/{item.id}/Items", userId=self.user_id, Fields=FIELDS)
        else:
            params = {"ParentId": item.id, "Fields": FIELDS, "SortBy": "SortName", "SortOrder": "Ascending"}
            collection = item.extra.get("collection")
            if item.kind == "library" and collection == "movies":
                params.update(Recursive="true", IncludeItemTypes="Movie")
            elif item.kind == "library" and collection == "tvshows":
                params.update(Recursive="true", IncludeItemTypes="Series")
            elif item.kind == "library" and collection == "music":
                params.update(Recursive="true", IncludeItemTypes="MusicAlbum")
            elif item.kind == "library" and collection == "musicvideos":
                params.update(Recursive="true", IncludeItemTypes="MusicVideo")
            elif item.kind == "collection":
                params["SortBy"] = "ProductionYear,SortName"
            data = self._get(f"/Users/{self.user_id}/Items", **params)
        return [self._to_item(d) for d in data.get("Items", [])]

    def _img(self, item_id, kind, tag, max_h=600):
        return f"{self.url}/Items/{item_id}/Images/{kind}?maxHeight={max_h}&quality=90&tag={tag}"

    def _to_item(self, d):
        t = d.get("Type", "")
        iid = d["Id"]
        tags = d.get("ImageTags") or {}
        if "Primary" in tags:
            image = self._img(iid, "Primary", tags["Primary"])
        elif d.get("SeriesPrimaryImageTag") and d.get("SeriesId"):
            image = self._img(d["SeriesId"], "Primary", d["SeriesPrimaryImageTag"])
        elif d.get("AlbumPrimaryImageTag") and d.get("AlbumId"):  # music track without its own image
            image = self._img(d["AlbumId"], "Primary", d["AlbumPrimaryImageTag"])
        else:
            image = ""
        backdrop = ""
        if d.get("BackdropImageTags"):
            backdrop = self._img(iid, "Backdrop/0", d["BackdropImageTags"][0], 1080)
        elif d.get("ParentBackdropItemId") and d.get("ParentBackdropImageTags"):
            backdrop = self._img(d["ParentBackdropItemId"], "Backdrop/0", d["ParentBackdropImageTags"][0], 1080)

        subtitle = ""
        if t == "Episode":
            s, e = d.get("ParentIndexNumber"), d.get("IndexNumber")
            code = f"S{s:02d}E{e:02d}" if s is not None and e is not None else ""
            subtitle = " · ".join(x for x in (d.get("SeriesName"), code) if x)
        elif t == "Season":
            subtitle = d.get("SeriesName", "")
        elif t == "Audio":
            subtitle = " · ".join(x for x in (d.get("AlbumArtist"), d.get("Album")) if x)

        user = d.get("UserData") or {}
        item = MediaItem(
            source=self.key,
            id=iid,
            title=d.get("Name", ""),
            kind=KIND_MAP.get(t, "folder" if d.get("IsFolder") else "video"),
            is_folder=bool(d.get("IsFolder")),
            year=d.get("ProductionYear"),
            overview=d.get("Overview", "") or "",
            genres=d.get("Genres") or [],
            rating=d.get("OfficialRating", "") or "",
            community_rating=d.get("CommunityRating"),
            runtime_ms=(d.get("RunTimeTicks") or 0) // TICKS_PER_MS,
            resume_ms=(user.get("PlaybackPositionTicks") or 0) // TICKS_PER_MS,
            played=bool(user.get("Played")),
            image_url=image,
            backdrop_url=backdrop,
            subtitle=subtitle,
        )
        if t == "Season":
            item.extra["series_id"] = d.get("SeriesId")
        elif t == "Episode":  # for the bibliotheek: Series/<serie>/Seizoen <n>/
            item.extra.update(series=d.get("SeriesName", ""), season=d.get("ParentIndexNumber"),
                              episode=d.get("IndexNumber"))
            if d.get("SeriesPrimaryImageTag") and d.get("SeriesId"):
                item.extra["series_image"] = self._img(d["SeriesId"], "Primary", d["SeriesPrimaryImageTag"])
        elif t == "Audio":
            item.extra.update(artist=d.get("AlbumArtist", ""), album=d.get("Album", ""))
            if d.get("NormalizationGain") is not None:  # from Jellyfin's 'Audio normalization' task
                item.extra["normalization_gain"] = d["NormalizationGain"]
        return item

    # -- playback -------------------------------------------------------------
    def markers(self, item):
        """{'credits': ms or None, 'chapters': [(ms, name)]} for planning the intermission.

        Credits come from media segments (type Outro, Jellyfin 10.10+, filled by e.g. the Intro Skipper
        plugin); chapters from the item itself."""
        credits = None
        try:
            data = self._get(f"/MediaSegments/{item.id}", includeSegmentTypes="Outro")
            starts = [s.get("StartTicks", 0) for s in data.get("Items", []) if s.get("Type") == "Outro"]
            credits = min(starts) // TICKS_PER_MS if starts else None
        except net.HttpError:
            pass
        chapters = []
        try:
            d = self._get(f"/Users/{self.user_id}/Items/{item.id}", Fields="Chapters")
            chapters = [(c.get("StartPositionTicks", 0) // TICKS_PER_MS, c.get("Name", ""))
                        for c in d.get("Chapters") or []]
        except net.HttpError:
            pass
        return {"credits": credits, "chapters": chapters}

    def play_url(self, item):
        endpoint = "Audio" if item.kind == "track" else "Videos"
        return f"{self.url}/{endpoint}/{item.id}/stream?static=true&api_key={self.token}"

    def report_start(self, item, pos_ms):
        self._post("/Sessions/Playing", {
            "ItemId": item.id, "PositionTicks": int(pos_ms * TICKS_PER_MS),
            "CanSeek": True, "PlayMethod": "DirectPlay", "IsPaused": False,
        })

    def report_progress(self, item, pos_ms, paused):
        self._post("/Sessions/Playing/Progress", {
            "ItemId": item.id, "PositionTicks": int(pos_ms * TICKS_PER_MS),
            "IsPaused": paused, "PlayMethod": "DirectPlay", "EventName": "TimeUpdate",
        })

    def report_stop(self, item, pos_ms):
        self._post("/Sessions/Playing/Stopped", {"ItemId": item.id, "PositionTicks": int(pos_ms * TICKS_PER_MS)})
