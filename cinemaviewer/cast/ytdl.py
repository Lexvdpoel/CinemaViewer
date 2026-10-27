"""Stream addresses for YouTube videos via yt-dlp.

yt-dlp runs in a separate Python process: a newer version downloaded while CinemaViewer runs is
used right away, and a problem inside yt-dlp can never take CinemaViewer down. YouTube changes
often; when resolving fails, the newest yt-dlp is fetched from PyPI (into the data folder) and
the video is tried once more.
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from ..config import DATA_DIR
from ..net import SSL_CONTEXT, USER_AGENT

UPDATE_DIR = DATA_DIR / "ytdlp"
PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
# H.264 up to 1080p plays smoothly everywhere (hardware decoding); separate audio is joined by VLC.
FORMAT = "bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b"

_SCRIPT = r"""
import json, sys
wheel, mode = sys.argv[1], sys.argv[2]
if wheel:
    sys.path.insert(0, wheel)
import yt_dlp
if mode == "version":
    print(json.dumps({"version": yt_dlp.version.__version__}))
    sys.exit(0)
video_id, fmt = sys.argv[3], sys.argv[4]
opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "format": fmt}
with yt_dlp.YoutubeDL(opts) as ydl:
    info = ydl.extract_info("https://www.youtube.com/watch?v=" + video_id, download=False)
parts = info.get("requested_formats") or [info]
video = next((f for f in parts if f.get("vcodec") not in (None, "none")), parts[0])
audio = next((f for f in parts if f is not video and f.get("acodec") not in (None, "none")), None)
print(json.dumps({
    "video": video["url"], "audio": audio["url"] if audio else "",
    "headers": video.get("http_headers") or {}, "title": info.get("title") or "",
    "channel": info.get("channel") or info.get("uploader") or "", "duration": info.get("duration") or 0,
    "thumbnail": info.get("thumbnail") or "", "description": (info.get("description") or "")[:600],
    "height": video.get("height") or 0, "is_live": bool(info.get("is_live")),
}))
"""


class ResolveError(Exception):
    pass


def _version_key(name):
    m = re.search(r"yt_dlp-([\d.]+)", name)
    return tuple(int(x) for x in m.group(1).strip(".").split(".")) if m else ()


def _newest_wheel():
    """The newest downloaded yt-dlp wheel, or '' to use the one installed with CinemaViewer."""
    try:
        wheels = sorted(UPDATE_DIR.glob("yt_dlp-*.whl"), key=lambda p: _version_key(p.name))
    except OSError:
        return ""
    return str(wheels[-1]) if wheels else ""


def _python():
    exe = Path(sys.executable)
    # pythonw.exe (no console) is fine for a child whose output is captured
    return str(exe)


def _run(args, timeout):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run([_python(), "-c", _SCRIPT, _newest_wheel()] + args, capture_output=True,
                                text=True, timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired as e:
        raise ResolveError("yt-dlp reageert niet (time-out)") from e
    except OSError as e:
        raise ResolveError(f"yt-dlp kon niet starten: {e}") from e
    if result.returncode != 0:
        lines = [x for x in (result.stderr or "").strip().splitlines() if x.strip()]
        message = lines[-1] if lines else f"code {result.returncode}"
        if "No module named 'yt_dlp'" in message:
            message = "yt-dlp is niet geïnstalleerd"
        raise ResolveError(message.replace("ERROR: ", "")[:300])
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        raise ResolveError("onverwacht antwoord van yt-dlp") from e


def version():
    try:
        return _run(["version"], 60)["version"]
    except ResolveError:
        return ""


def resolve(video_id, max_height=1080, update_on_error=True):
    """Stream addresses and details of a video; on failure updates yt-dlp once and tries again."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id or ""):
        raise ResolveError(f"ongeldige video-id: {video_id!r}")
    try:
        return _run(["resolve", video_id, FORMAT.format(h=int(max_height))], 90)
    except ResolveError:
        if not update_on_error or not update():
            raise
    return _run(["resolve", video_id, FORMAT.format(h=int(max_height))], 90)


def update():
    """Downloads the newest yt-dlp when it is newer than the one in use. Returns the new version or ''."""
    req = urllib.request.Request(PYPI_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
            data = json.load(resp)
        latest = data["info"]["version"]
        wheel = next(u for u in data["urls"] if u["packagetype"] == "bdist_wheel")
    except (OSError, ValueError, KeyError, StopIteration):
        return ""
    current = version()
    if current and _version_key(f"yt_dlp-{current}") >= _version_key(f"yt_dlp-{latest}"):
        return ""
    target = UPDATE_DIR / wheel["filename"]
    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        req = urllib.request.Request(wheel["url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp, open(tmp, "wb") as fh:
            fh.write(resp.read())
        tmp.replace(target)
        for old in UPDATE_DIR.glob("yt_dlp-*.whl"):  # keep only the newest
            if old != target:
                old.unlink(missing_ok=True)
    except OSError:
        return ""
    return latest
