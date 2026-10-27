from dataclasses import dataclass, field


@dataclass
class MediaItem:
    source: str                 # "jellyfin" | "plex" | "local"
    id: str
    title: str
    kind: str = "video"         # library, folder, series, season, collection, movie, episode, video, dvd, bluray,
                                # audiocd, artist, album, track, playlist, empty
    is_folder: bool = False
    year: int | None = None
    overview: str = ""
    genres: list = field(default_factory=list)
    rating: str = ""            # age rating, e.g. "PG-13"
    community_rating: float | None = None
    runtime_ms: int = 0
    resume_ms: int = 0
    played: bool = False
    image_url: str = ""         # http(s) URL or local file path
    backdrop_url: str = ""
    subtitle: str = ""          # e.g. "Series · S01E02"
    group: str = ""             # sidebar group for libraries
    extra: dict = field(default_factory=dict)

    @property
    def playable(self):
        return not self.is_folder and self.kind not in ("library", "empty", "displaycast")

    @property
    def is_disc(self):
        return self.kind in ("dvd", "bluray", "audiocd")


SOURCE_LABELS = {"jellyfin": "Jellyfin", "plex": "Plex", "local": "Lokaal", "cache": "Bibliotheek"}


def fmt_time(ms, force_hours=False):
    if ms is None or ms < 0:
        ms = 0
    s = int(ms // 1000)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h or force_hours:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_runtime(ms):
    if not ms:
        return ""
    minutes = round(ms / 60000)
    if not minutes:
        return "< 1 min"
    h, m = divmod(minutes, 60)
    return f"{h}u {m:02d}m" if h else f"{m} min"


def fmt_size(size):
    """Bytes as a Dutch size: '850 MB', '12,4 GB', '1,8 TB'."""
    for unit, factor in (("TB", 1024 ** 4), ("GB", 1024 ** 3)):
        if size >= factor:
            value = size / factor
            text = f"{value:.0f}" if value >= 100 else f"{value:.1f}".replace(".", ",")
            return f"{text} {unit}"
    return f"{size / 1024 ** 2:.0f} MB"
