"""External subtitles from Jellyfin/Plex as files, and the choice of subtitle when a film starts.

VLC takes the language of a subtitle file from its name (the part before the extension), so the files are named
media.Nederlands.srt, media.Engels (geforceerd).srt, ...: readable in the subtitle menu and recognised by
choose()."""

import re
import shutil
import tempfile
import urllib.request
from pathlib import Path

from .net import SSL_CONTEXT, USER_AGENT

LANGUAGES = {
    "Nederlands": ("dut", "nld", "nl"), "Engels": ("eng", "en"), "Duits": ("ger", "deu", "de"),
    "Frans": ("fre", "fra", "fr"), "Spaans": ("spa", "es"), "Italiaans": ("ita", "it"),
    "Portugees": ("por", "pt"), "Zweeds": ("swe", "sv"), "Deens": ("dan", "da"), "Noors": ("nor", "nob", "no"),
    "Fins": ("fin", "fi"), "Pools": ("pol", "pl"), "Turks": ("tur", "tr"), "Arabisch": ("ara", "ar"),
    "Chinees": ("chi", "zho", "zh"), "Japans": ("jpn", "ja"), "Koreaans": ("kor", "ko"),
    "Russisch": ("rus", "ru"), "Grieks": ("gre", "ell", "el"),
}
_NAMES = {code: name for name, codes in LANGUAGES.items() for code in codes}

# Words in VLC's track names ("Track 1 - [Dutch]", or a file's label above), in order of preference.
PREFERRED = (
    {"dutch", "nederlands", "flemish", "vlaams", "nl", "nld", "dut"},
    {"english", "engels", "en", "eng"},
)
FORCED = {"forced", "geforceerd"}


def label(lang, forced=False):
    lang = (lang or "").strip().lower()
    name = _NAMES.get(lang) or _NAMES.get(lang.split("-")[0]) or re.sub(r"[^\w-]+", "", lang) or "Ondertitel"
    return f"{name} (geforceerd)" if forced else name


def save(subs, folder, stem="media"):
    """Fetch subtitles ([{'url', 'lang', 'forced', 'ext'}], see the sources' subtitle_files) into folder as
    <stem>.<label>.<ext>, next to the media file <stem>.*; returns the paths that worked."""
    out, used = [], set()
    for sub in subs:
        name = label(sub.get("lang"), sub.get("forced"))
        n = 2
        while name in used:  # two in the same language: the label stays last, VLC reads it from there
            name = f"{n}.{label(sub.get('lang'), sub.get('forced'))}"
            n += 1
        used.add(name)
        dest = Path(folder) / f"{stem}.{name}.{sub['ext']}"
        try:
            req = urllib.request.Request(sub["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp, dest.open("wb") as fh:
                shutil.copyfileobj(resp, fh)
            out.append(dest)
        except Exception:  # noqa: BLE001 - subtitles are a bonus
            dest.unlink(missing_ok=True)
    return out


def subtitle_files(src, item):
    try:
        return src.subtitle_files(item) if hasattr(src, "subtitle_files") else []
    except Exception:  # noqa: BLE001 - the film plays without them
        return []


_STREAM_DIR = Path(tempfile.gettempdir()) / "CinemaViewer-ondertitels"
_counter = 0


def for_streaming(src, item):
    """External subtitles of a streamed item as local files (so VLC knows their language): file URIs."""
    global _counter
    subs = subtitle_files(src, item)
    if not subs:
        return []
    _counter += 1
    if _STREAM_DIR.is_dir():
        for old in _STREAM_DIR.iterdir():  # those of earlier films (the playing one may still be open)
            shutil.rmtree(old, ignore_errors=True)
    folder = _STREAM_DIR / str(_counter)
    folder.mkdir(parents=True, exist_ok=True)
    return [p.as_uri() for p in save(subs, folder)]


def choose(tracks):
    """Subtitle track to start with: Dutch, else English (a complete one before a forced one), else off (-1).
    tracks: [(id, name)] as VLC describes them."""
    for words in PREFERRED:
        hits = []
        for track_id, name in tracks:
            tokens = set(re.findall(r"\w+", name.lower()))
            if tokens & words:
                hits.append((bool(tokens & FORCED), track_id))
        if hits:
            return next((i for forced, i in hits if not forced), hits[0][1])
    return -1
