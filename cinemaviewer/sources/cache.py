"""Bibliotheek van gedownloade Plex/Jellyfin-items, die lokaal (offline) af te spelen zijn.

Op schijf ingedeeld zoals een mediabibliotheek:
    Films/<Titel (jaar)>/
    Series/<Serie>/Seizoen <n>/<S01E02 Titel>/
    Muziek/<Artiest>/<Album>/<Titel>/
    Overig/<Titel>/
Elke itemmap bevat het mediabestand, item.json (gegevens, gekeken, hervatpositie) en de afbeeldingen.
Een item wordt teruggevonden via bron en id in item.json, niet via de mapnaam."""

import json
import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from ..config import CACHE_DIR
from ..models import SOURCE_LABELS, MediaItem
from ..net import SSL_CONTEXT, USER_AGENT

META = "item.json"
EXTENSIONS = {".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".ts", ".mpg", ".mpeg", ".m2ts", ".iso",
              ".vob", ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma"}
CATEGORIES = ("Films", "Series", "Muziek", "Overig")
# Folder kinds by depth below a category (for icons and the grid): Series/<serie>/<seizoen>
FOLDER_KINDS = {"Series": ("series", "season"), "Muziek": ("artist", "album")}
MAX_NAME = 50  # keeps the full path well within Windows' 260 characters
_LEGACY = re.compile(r"^(jellyfin|plex)-.+")            # before the indeling: cache/<bron>-<id>/
_EPISODE_CODE = re.compile(r"^(?P<series>.*?)\s*·\s*S(?P<season>\d+)E(?P<episode>\d+)$")


# -- locating -------------------------------------------------------------------
def _read_meta(folder):
    try:
        return json.loads((folder / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _media_files(folder):
    """What plays in an item folder: a copied DVD/Blu-ray (its VIDEO_TS / BDMV folder) or media files."""
    for disc in ("VIDEO_TS", "BDMV"):
        if (folder / disc).is_dir():
            return [folder / disc]
    try:
        return [p for p in sorted(folder.iterdir()) if p.suffix.lower() in EXTENSIONS]
    except OSError:
        return []


def _mrl(media):
    """VLC address: dvd:/// or bluray:/// for a copied disc (menus work), file:// otherwise."""
    if media.name in ("VIDEO_TS", "BDMV"):
        scheme = "dvd" if media.name == "VIDEO_TS" else "bluray"
        return f"{scheme}:///" + media.parent.as_posix().rstrip("/") + "/"
    return media.as_uri()


def _item_folders(base=None):
    """All downloaded items below base (default: the whole bibliotheek), skipping half downloads."""
    base = base or CACHE_DIR
    try:
        metas = sorted(base.rglob(META))
    except OSError:
        return []
    return [m.parent for m in metas if not any(p.startswith(".tmp-") for p in m.relative_to(CACHE_DIR).parts)
            and _media_files(m.parent)]


def _find(source, source_id):
    for folder in _item_folders():
        data = _read_meta(folder) or {}
        if data.get("source") == source and str(data.get("source_id")) == str(source_id):
            return folder
    return None


def _source_id(item):
    """How a copied item is recognised. A disc station always has the same id (its drive), so a disc
    is recognised by its name instead."""
    if item.source == "local" and item.group == "Disc":
        return f"disc:{item.kind}:{item.title}"
    return item.id


def folder_for(item):
    """Cache folder of an item: a bibliotheek item itself, or the Plex/Jellyfin/local item it came from
    (None when that is not in the bibliotheek)."""
    if item.source == CacheSource.key:
        return CACHE_DIR / item.id if item.id else None
    return _find(item.source, _source_id(item))


def is_cached(item):
    folder = folder_for(item)
    return folder is not None and (folder / META).is_file() and bool(_media_files(folder))


# -- layout -----------------------------------------------------------------------
def _safe(name, fallback):
    """A folder name that works on Windows and Linux."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(name or ""))
    name = re.sub(r"\s+", " ", name).strip()[:MAX_NAME].strip().rstrip(".")
    return name or fallback


def _with_episode_info(meta):
    """Older downloads only have 'Serie · S01E02' in the subtitle."""
    if meta.get("kind") == "episode" and not meta.get("series"):
        m = _EPISODE_CODE.match(meta.get("subtitle", ""))
        if m:
            meta.update(series=m["series"], season=int(m["season"]), episode=int(m["episode"]))
    if meta.get("kind") == "track" and not meta.get("artist"):
        parts = [p.strip() for p in meta.get("subtitle", "").split("·")]
        meta.update(artist=parts[0] if parts else "", album=parts[1] if len(parts) > 1 else "")
    return meta


def _relative_folder(meta):
    title = meta.get("title") or meta.get("source_id") or "Onbekend"
    kind = meta.get("category") or meta.get("kind")  # category: what a copied file/disc was filed as
    if kind == "episode":
        season, episode = meta.get("season"), meta.get("episode")
        code = f"S{season:02d}E{episode:02d} " if isinstance(season, int) and isinstance(episode, int) else ""
        season_dir = f"Seizoen {season}" if isinstance(season, int) else "Overige afleveringen"
        return Path("Series", _safe(meta.get("series"), "Onbekende serie"), season_dir, _safe(code + title, "Aflevering"))
    if kind in ("movie", "video", "musicvideo", "film", "dvd", "bluray"):
        year = f" ({meta['year']})" if meta.get("year") else ""
        return Path("Films", _safe(title[:MAX_NAME - len(year)] + year, "Film"))
    if kind == "track":
        return Path("Muziek", _safe(meta.get("artist"), "Onbekende artiest"), _safe(meta.get("album"), "Onbekend album"),
                    _safe(title, "Nummer"))
    return Path("Overig", _safe(title, "Item"))


def _free_target(path):
    """path, or 'path (2)', 'path (3)', ... when two titles give the same folder name."""
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name} ({n})")
        n += 1
    return candidate


def _prune(folder):
    """Remove folders above a deleted item that hold no items any more (a season, then the serie)."""
    parent = folder.parent
    while parent != CACHE_DIR and CACHE_DIR in parent.parents:
        if _item_folders(parent):
            return
        shutil.rmtree(parent, ignore_errors=True)
        parent = parent.parent


def migrate():
    """Move downloads from before the indeling (cache/<bron>-<id>/) into Films/Series/... ."""
    try:
        legacy = [d for d in CACHE_DIR.iterdir() if d.is_dir() and _LEGACY.match(d.name) and (d / META).is_file()]
    except OSError:
        return
    for folder in legacy:
        meta = _read_meta(folder)
        if meta is None:
            continue
        target = _free_target(CACHE_DIR / _relative_folder(_with_episode_info(meta)))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(target)
            (target / META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            continue  # e.g. playing right now: next time


# -- watched, removing, space -----------------------------------------------------------
def _size(folder):
    total = 0
    for p in folder.rglob("*"):
        try:
            total += p.stat().st_size if p.is_file() else 0
        except OSError:
            pass
    return total


def save_progress(item):
    """Remember watched / resume position of a cached item (also when its Plex/Jellyfin original was played)."""
    folder = folder_for(item)
    data = _read_meta(folder) if folder else None
    if data is None:
        return False
    if data.get("played") == item.played and data.get("resume_ms") == item.resume_ms:
        return False
    data["played"], data["resume_ms"] = item.played, item.resume_ms
    try:
        (folder / META).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def remove(item):
    """Delete a cached item from disk (and a season/serie that becomes empty); returns the bytes freed."""
    folder = folder_for(item)
    if folder is None or not (folder / META).is_file():
        return 0
    size = _size(folder)
    shutil.rmtree(folder)
    _prune(folder)
    return size


def usage():
    """Free space on the cache disk, and what the bibliotheek (and its watched part) takes."""
    base = CACHE_DIR
    while not base.exists() and base != base.parent:
        base = base.parent
    disk = shutil.disk_usage(base)
    size = watched_size = watched = count = 0
    for folder in _item_folders():
        folder_size = _size(folder)
        count += 1
        size += folder_size
        if (_read_meta(folder) or {}).get("played"):
            watched += 1
            watched_size += folder_size
    return {"free": disk.free, "total": disk.total, "size": size, "count": count,
            "watched": watched, "watched_size": watched_size}


# -- downloading -----------------------------------------------------------------------
def _open(url):
    # Same agent and certificate check as the other connections (net.py): Cloudflare blocks others.
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT)


def _fetch(url, dest):
    if not url:
        return
    try:
        with _open(url) as resp, dest.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except Exception:  # noqa: BLE001 - images are nice-to-have
        dest.unlink(missing_ok=True)


def _meta(item):
    extra = item.extra
    return {
        "source": item.source, "source_id": item.id, "title": item.title, "kind": item.kind,
        "year": item.year, "overview": item.overview, "genres": item.genres, "rating": item.rating,
        "community_rating": item.community_rating, "runtime_ms": item.runtime_ms,
        "subtitle": item.subtitle, "played": False, "resume_ms": 0,
        "series": extra.get("series", ""), "season": extra.get("season"), "episode": extra.get("episode"),
        "artist": extra.get("artist", ""), "album": extra.get("album", ""),
    }


def download(src, item, on_progress=None, cancel=None):
    """Stream the media for item (served by src) into the bibliotheek; returns its folder.
    cancel: a threading.Event; when it is set the download stops and its files are removed."""
    existing = folder_for(item)
    if existing is not None and is_cached(item):
        return existing
    meta = _with_episode_info(_meta(item))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f".tmp-{item.source}-{item.id}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        url = src.play_url(item)
        with _open(url) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
            if suffix not in EXTENSIONS:
                suffix = ".mp4"
            done = 0
            with (tmp / f"media{suffix}").open("wb") as fh:
                while True:
                    _check(cancel)
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
            if total and done != total:
                raise OSError(f"verbinding afgebroken na {done // 1048576} van {total // 1048576} MB")
        _fetch(item.image_url, tmp / "poster.jpg")
        _fetch(item.backdrop_url, tmp / "backdrop.jpg")
        return _store(tmp, meta, item.extra.get("series_image"))
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _store(tmp, meta, series_image=""):
    """Finish a download/copy: write item.json and move it to its place in the bibliotheek."""
    (tmp / META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    folder = _free_target(CACHE_DIR / _relative_folder(meta))
    folder.parent.mkdir(parents=True, exist_ok=True)
    if "episode" in (meta.get("kind"), meta.get("category")) and not (folder.parent.parent / "poster.jpg").exists():
        if series_image and Path(series_image).is_file():
            shutil.copyfile(series_image, folder.parent.parent / "poster.jpg")
        else:
            _fetch(series_image, folder.parent.parent / "poster.jpg")  # poster of the serie
    tmp.replace(folder)
    return folder


# -- copying from folders, USB, network shares and discs ----------------------------------
_EPISODE_NAME = re.compile(r"[Ss](?P<season>\d{1,2})[ ._-]?[Ee](?P<episode>\d{1,3})|\b(?P<season2>\d{1,2})x(?P<episode2>\d{2,3})\b")
_SEASON_DIR = re.compile(r"^(season|seizoen|staffel|saison|s)[ ._-]*\d+$", re.I)


class CopyProtected(Exception):
    """A disc with copy protection: playing it works, copying it does not."""


class Cancelled(Exception):
    """The download or copy was cancelled; nothing is left behind."""


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("geannuleerd")


def guess(item):
    """Film or episode, with title/year or serie/season/episode, guessed from the file or folder name."""
    from .local import clean_title
    path = Path(item.extra.get("path") or item.id)
    name = path.stem if path.is_file() else path.name
    m = _EPISODE_NAME.search(name)
    if m and item.group != "Disc":
        season = int(m["season"] or m["season2"])
        episode = int(m["episode"] or m["episode2"])
        series = clean_title(name[:m.start()])[0].strip(" -") if m.start() > 0 else ""
        rest = clean_title(name[m.end():])[0].strip(" -.")
        series_dir = path.parent.parent if _SEASON_DIR.match(path.parent.name) else path.parent
        if not series:
            series = clean_title(series_dir.name)[0]
        poster = next((p for p in (series_dir / n for n in ("poster.jpg", "folder.jpg", "cover.jpg")) if p.is_file()),
                      None)
        return {"category": "episode", "title": rest or f"Aflevering {episode}", "year": None,
                "series": series, "season": season, "episode": episode, "series_image": str(poster or "")}
    return {"category": "film", "title": item.title, "year": item.year, "series": "", "season": None,
            "episode": None}


def _disc_parts(src, kind):
    names = ("VIDEO_TS", "AUDIO_TS") if kind == "dvd" else ("BDMV", "CERTIFICATE")
    return [src / n for n in names if (src / n).is_dir()]


def import_local(item, info, on_progress=None, cancel=None):
    """Copy a file, DVD/Blu-ray folder or unprotected disc into the bibliotheek, filed as info says
    (see guess()); returns its folder. Copy protection is never bypassed: a protected disc is refused."""
    existing = folder_for(item)
    if existing is not None and is_cached(item):
        return existing
    src = Path(item.extra.get("path") or item.id)
    if item.kind in ("dvd", "bluray"):
        if item.kind == "bluray" and (src / "AACS").is_dir():
            raise CopyProtected("Deze Blu-ray is beveiligd tegen kopiëren (AACS). Afspelen vanaf de disc kan wel, "
                                "naar de bibliotheek kopiëren niet.")
        files = [(p, p.relative_to(src)) for part in _disc_parts(src, item.kind)
                 for p in sorted(part.rglob("*")) if p.is_file()]
        if not files:
            raise OSError("geen VIDEO_TS- of BDMV-map gevonden")
    else:
        files = [(src, Path("media" + src.suffix.lower()))]
    total = sum(p.stat().st_size for p, _ in files)
    if total > shutil.disk_usage(CACHE_DIR.parent if not CACHE_DIR.exists() else CACHE_DIR).free:
        raise OSError("niet genoeg vrije ruimte op de schijf van de bibliotheek")
    meta = {
        "source": "local", "source_id": _source_id(item), "kind": item.kind, "title": info["title"] or item.title,
        "year": info.get("year"), "overview": item.overview, "genres": item.genres, "rating": item.rating,
        "community_rating": item.community_rating, "runtime_ms": item.runtime_ms, "played": False, "resume_ms": 0,
        "category": info["category"], "series": info.get("series", ""), "season": info.get("season"),
        "episode": info.get("episode"), "subtitle": "", "original": str(src),
    }
    if info["category"] == "episode" and isinstance(meta["season"], int) and isinstance(meta["episode"], int):
        meta["subtitle"] = f"{meta['series']} · S{meta['season']:02d}E{meta['episode']:02d}"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f".tmp-local-{abs(hash(meta['source_id']))}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        done = 0
        for path, rel in files:
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as fin, (tmp / rel).open("wb") as fout:
                while True:
                    _check(cancel)
                    try:
                        chunk = fin.read(4 * 1024 * 1024)
                    except OSError as e:
                        if item.kind == "dvd":  # scrambled (CSS) sectors cannot be read without decrypting
                            raise CopyProtected("Deze DVD kon niet gelezen worden; waarschijnlijk is hij beveiligd "
                                                "tegen kopiëren. Afspelen vanaf de disc kan wel.") from e
                        raise
                    if not chunk:
                        break
                    fout.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
        for name, url in (("poster.jpg", item.image_url), ("backdrop.jpg", item.backdrop_url)):
            if url and Path(url).is_file():
                shutil.copyfile(url, tmp / name)
        return _store(tmp, meta, info.get("series_image") or item.image_url if info["category"] == "episode" else "")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


# -- source ------------------------------------------------------------------------------
class CacheSource:
    key = "cache"
    label = "Bibliotheek"

    def __init__(self):
        migrate()

    def libraries(self):
        """Sidebar: one entry per category that holds something (Films, Series, ...)."""
        return [self._folder_item(CACHE_DIR / cat) for cat in CATEGORIES if _item_folders(CACHE_DIR / cat)]

    def all_items(self):
        return [self._leaf_item(f) for f in _item_folders()]

    def children(self, item):
        """The whole bibliotheek (item without id: films, series, artists, ...) or one folder in it."""
        if not item.id:
            out = []
            for cat in CATEGORIES:
                out += self.children(self._folder_item(CACHE_DIR / cat)) if _item_folders(CACHE_DIR / cat) else []
            return sorted(out, key=lambda i: i.title.lower())
        folder = CACHE_DIR / item.id
        out = []
        try:
            subdirs = sorted((d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".tmp-")),
                             key=lambda d: d.name.lower())
        except OSError:
            return out
        for d in subdirs:
            if (d / META).is_file():
                if _media_files(d):
                    out.append(self._leaf_item(d))
            elif _item_folders(d):
                out.append(self._folder_item(d))
        return out

    def _folder_item(self, folder):
        rel = folder.relative_to(CACHE_DIR)
        items = _item_folders(folder)
        depth = len(rel.parts) - 1
        kinds = FOLDER_KINDS.get(rel.parts[0], ())
        kind = "library" if depth == 0 else kinds[depth - 1] if depth - 1 < len(kinds) else "folder"
        poster = next((p for p in [folder / "poster.jpg"] + [f / "poster.jpg" for f in items] if p.is_file()), None)
        noun = "aflevering" if rel.parts[0] == "Series" else "item"
        return MediaItem(
            source=self.key, id=rel.as_posix(), title=folder.name, kind=kind, is_folder=True,
            subtitle=f"{len(items)} {noun}{'en' if len(items) != 1 else ''}",
            played=bool(items) and all((_read_meta(f) or {}).get("played") for f in items),
            image_url=str(poster) if poster else "",
        )

    def _leaf_item(self, folder):
        data = _read_meta(folder) or {}
        media = _media_files(folder)
        origin = SOURCE_LABELS.get(data.get("source", ""), "")
        subtitle = " · ".join(x for x in (data.get("subtitle", ""), f"uit {origin}" if origin else "") if x)
        return MediaItem(
            source=self.key,
            id=folder.relative_to(CACHE_DIR).as_posix(),
            title=data.get("title", folder.name),
            kind=data.get("kind", "video"),
            year=data.get("year"),
            overview=data.get("overview", "") or "",
            genres=data.get("genres") or [],
            rating=data.get("rating", "") or "",
            community_rating=data.get("community_rating"),
            runtime_ms=data.get("runtime_ms", 0),
            resume_ms=data.get("resume_ms", 0) or 0,
            played=bool(data.get("played")),
            image_url=str(folder / "poster.jpg") if (folder / "poster.jpg").is_file() else "",
            backdrop_url=str(folder / "backdrop.jpg") if (folder / "backdrop.jpg").is_file() else "",
            subtitle=subtitle,
            extra={"mrl": _mrl(media[0]), "path": str(media[0]), "cached": True},
        )

    def play_url(self, item):
        return item.extra["mrl"]

    def markers(self, item):
        return {"credits": None, "chapters": []}

    def report_start(self, item, pos_ms):
        pass

    def report_progress(self, item, pos_ms, paused):
        pass

    def report_stop(self, item, pos_ms):
        pass
