"""Local folders, USB drives and optical (DVD / Blu-ray / audio CD) drives."""

import ctypes
import os
import re
import string
import xml.etree.ElementTree as ET
from pathlib import Path

from ..models import MediaItem

if os.name == "nt":
    from ctypes import wintypes

VIDEO_EXT = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".mts", ".mpg", ".mpeg",
    ".webm", ".flv", ".vob", ".iso", ".ogv", ".3gp", ".divx",
}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma", ".alac", ".aiff", ".ape"}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
COVER_NAMES = ["cover", "folder", "front", "album", "albumart", "Cover", "Folder", "Front"]
_TRACK_NO = re.compile(r"^\s*(?:\d{1,2}[-.]\d{2,3}\s+|\d{1,3}\s*[-._)]+\s*)")  # "01 - ", "07. ", "1-03 "

DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_CDROM = 2, 3, 5
BUS_TYPE_USB = 7
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400

_kernel32 = None
if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                          wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                          wintypes.LPVOID]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
INVALID_HANDLE = ctypes.c_void_p(-1).value


# -- Windows drive helpers ------------------------------------------------------
def _volume_label(root):
    name = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial, maxlen, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    old_mode = _kernel32.SetErrorMode(0x0001)  # no "insert a disk" popups
    try:
        ok = _kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), name, 261, ctypes.byref(serial), ctypes.byref(maxlen),
            ctypes.byref(flags), fs, 261)
    finally:
        _kernel32.SetErrorMode(old_mode)
    return (name.value, fs.value) if ok else None


def _is_usb(letter):
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = _kernel32.CreateFileW(f"\\\\.\\{letter}:", 0, 0x3, None, 3, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return False
    try:
        query = (ctypes.c_ubyte * 12)()          # PropertyId=0, QueryType=0
        out = (ctypes.c_ubyte * 1024)()
        returned = wintypes.DWORD()
        ok = _kernel32.DeviceIoControl(handle, IOCTL_STORAGE_QUERY_PROPERTY, query, 12, out, 1024,
                                       ctypes.byref(returned), None)
        if not ok or returned.value < 32:
            return False
        bus_type = int.from_bytes(bytes(out[28:32]), "little")
        return bus_type == BUS_TYPE_USB
    finally:
        _kernel32.CloseHandle(handle)


def _pretty_label(label):
    if not label:
        return ""
    text = label.replace("_", " ").strip()
    return text.title() if text.isupper() else text


def scan_drives(show_usb=True, show_disc=True):
    """Return MediaItems for USB drives and optical drives (with disc detection)."""
    if os.name == "nt":
        return _scan_drives_windows(show_usb, show_disc)
    return _scan_drives_linux(show_usb, show_disc)


def _scan_drives_windows(show_usb, show_disc):
    if _kernel32 is None:
        return []
    items = []
    system_drive = os.environ.get("SystemDrive", "C:")[0].upper()
    mask = _kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        dtype = _kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        if dtype == DRIVE_CDROM and show_disc:
            items.append(_disc_item(letter, root))
        elif show_usb and letter != system_drive and (
                dtype == DRIVE_REMOVABLE or (dtype == DRIVE_FIXED and _is_usb(letter))):
            info = _volume_label(root)
            if info is None:
                continue
            label = _pretty_label(info[0]) or "USB-schijf"
            items.append(MediaItem(source="local", id=root, title=f"{label} ({letter}:)", kind="usb",
                                   is_folder=True, group="USB", extra={"path": root}))
    return items


def _disc_item(letter, root):
    info = _volume_label(root)
    base = dict(source="local", id=root, group="Disc", extra={"path": root, "drive": letter})
    if info is None:
        return MediaItem(title=f"Geen disc ({letter}:)", kind="empty", **base)
    label, fs = info
    nice = _pretty_label(label)
    if os.path.isdir(os.path.join(root, "BDMV")):
        base["extra"]["mrl"] = f"bluray:///{letter}:/"
        return MediaItem(title=nice or "Blu-ray", kind="bluray", subtitle=f"Blu-ray · {letter}:", **base)
    if os.path.isdir(os.path.join(root, "VIDEO_TS")):
        base["extra"]["mrl"] = f"dvd:///{letter}:/"
        return MediaItem(title=nice or "DVD", kind="dvd", subtitle=f"DVD · {letter}:", **base)
    if fs.upper() == "CDFS" and any(n.lower().endswith(".cda") for n in _safe_listdir(root)):
        base["extra"]["mrl"] = f"cdda:///{letter}:/"
        return MediaItem(title=nice or "Audio-cd", kind="audiocd", subtitle=f"Audio-cd · {letter}:", **base)
    return MediaItem(title=f"{nice or 'Data-disc'} ({letter}:)", kind="folder", is_folder=True, **base)


def _safe_listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


# -- Linux media helpers ----------------------------------------------------------
def _proc_mounts():
    out = []
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2:
                    mount = parts[1].replace("\\040", " ").replace("\\011", "\t")
                    out.append((parts[0], mount))
    except OSError:
        pass
    return out


def _media_mounts():
    mounts = []
    for base in (Path("/run/media"), Path("/media")):
        try:
            top = sorted(base.iterdir())
        except OSError:
            continue
        for entry in top:
            if not entry.is_dir():
                continue
            mounts.append(entry)
            try:
                mounts.extend(sorted(p for p in entry.iterdir() if p.is_dir()))
            except OSError:
                pass
    return mounts


def _block_name(path):
    try:
        st = os.stat(path)
        return Path(f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}").resolve().name
    except OSError:
        return ""


def _is_usb_device(name):
    disk = re.sub(r"\d+$", "", name)
    try:
        if (Path("/sys/block") / disk / "removable").read_text().strip() == "1":
            return True
        return "usb" in str((Path("/sys/block") / disk).resolve()).lower()
    except OSError:
        return False


def _scan_drives_linux(show_usb, show_disc):
    items, seen = [], set()
    mounted = {dev for dev, _ in _proc_mounts()}
    if show_usb:
        for p in _media_mounts():
            if str(p) not in mounted or p.name == "cdrom":
                continue
            dev = _block_name(p)
            if not dev or dev in seen or not _is_usb_device(dev):
                continue
            seen.add(dev)
            label = _pretty_label(p.name) or "USB-schijf"
            items.append(MediaItem(source="local", id=str(p), title=f"{label} ({dev})", kind="usb",
                                   is_folder=True, group="USB", extra={"path": str(p)}))
    if show_disc:
        items.extend(_disc_items_linux())
    return items


def _disc_items_linux():
    items = []
    mounts = _proc_mounts()
    for devdir in sorted(Path("/sys/class/cdrom").glob("sr*")):
        dev = f"/dev/{devdir.name}"
        base = dict(source="local", id=dev, group="Disc")
        mount = next((m for d, m in mounts if d == dev), "")
        if mount and os.path.isdir(mount):
            m = Path(mount)
            nice = _pretty_label(m.name)
            extra = {"path": str(m), "drive": devdir.name}
            if (m / "BDMV").is_dir():
                extra["mrl"] = f"bluray://{dev}"
                items.append(MediaItem(title=nice or "Blu-ray", kind="bluray", subtitle=f"Blu-ray · {dev}",
                                       extra=extra, **base))
            elif (m / "VIDEO_TS").is_dir():
                extra["mrl"] = f"dvd://{dev}"
                items.append(MediaItem(title=nice or "DVD", kind="dvd", subtitle=f"DVD · {dev}",
                                       extra=extra, **base))
            else:
                items.append(MediaItem(title=f"{nice or 'Data-disc'} ({dev})", kind="folder", is_folder=True,
                                       extra=extra, **base))
            continue
        media = ""
        try:
            media = (Path("/sys/class/block") / devdir.name / "device" / "media").read_text().strip()
        except OSError:
            pass
        if "cd-rom" in media or "cd-r" in media:
            items.append(MediaItem(title="Audio-cd", kind="audiocd", subtitle=f"Audio-cd · {dev}",
                                   extra={"path": dev, "drive": devdir.name, "mrl": f"cdda://{dev}"}, **base))
        else:
            items.append(MediaItem(title=f"Geen disc ({dev})", kind="empty",
                                   extra={"path": dev, "drive": devdir.name}, **base))
    return items


# -- Folder browsing & metadata -------------------------------------------------
_QUALITY = re.compile(
    r"\b(2160p|1080p|720p|480p|4k|uhd|hdr|hdr10|dv|bluray|blu-ray|bdrip|brrip|web-?dl|webrip|web|hdtv|dvdrip|"
    r"remux|x264|x265|h264|h265|hevc|avc|aac|ac3|dts|truehd|atmos|10bit|proper|repack|extended|unrated)\b",
    re.I)
_YEAR = re.compile(r"[\(\[ ]((?:19|20)\d\d)[\)\] ]")


def clean_title(name):
    """'The.Matrix.1999.1080p.BluRay.x264' -> ('The Matrix', 1999)."""
    text = re.sub(r"[._]+", " ", name).strip()
    year = None
    m = _YEAR.search(f" {text} ")
    if m:
        year = int(m.group(1))
        text = f" {text} "[:m.start()].strip() or text
    q = _QUALITY.search(text)
    if q and q.start() > 0:
        text = text[:q.start()]
    text = re.sub(r"[\s\-\(\[]+$", "", text).strip()
    return text or name, year


def _find_image(folder, names):
    for n in names:
        for ext in IMAGE_EXT:
            p = folder / f"{n}{ext}"
            if p.is_file():
                return str(p)
    return ""


def _read_nfo(path):
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}
    def text(tag):
        el = root.find(tag)
        return (el.text or "").strip() if el is not None and el.text else ""
    out = {"title": text("title"), "overview": text("plot") or text("outline"), "rating": text("mpaa")}
    year = text("year") or text("premiered")[:4]
    if year.isdigit():
        out["year"] = int(year)
    out["genres"] = [g.text.strip() for g in root.findall("genre") if g.text]
    runtime = text("runtime")
    if runtime.isdigit():
        out["runtime_ms"] = int(runtime) * 60000
    return out


def _apply_nfo(item, nfo):
    if not nfo:
        return
    item.title = nfo.get("title") or item.title
    item.year = nfo.get("year") or item.year
    item.overview = nfo.get("overview", "")
    item.genres = nfo.get("genres", [])
    item.rating = nfo.get("rating", "")
    item.runtime_ms = nfo.get("runtime_ms", 0)


def _file_item(path: Path, single_video_in_folder):
    title, year = clean_title(path.stem)
    folder = path.parent
    names = [f"{path.stem}-poster", path.stem]
    if single_video_in_folder:
        names += ["poster", "folder", "cover"]
    item = MediaItem(
        source="local", id=str(path), title=title, year=year, kind="video",
        image_url=_find_image(folder, names),
        backdrop_url=_find_image(folder, [f"{path.stem}-fanart"] + (["fanart", "backdrop"] if single_video_in_folder else [])),
        subtitle=path.suffix[1:].upper(),
        extra={"path": str(path), "mrl": path.as_uri()},
    )
    nfo = folder / f"{path.stem}.nfo"
    if not nfo.is_file() and single_video_in_folder:
        nfo = folder / "movie.nfo"
    if nfo.is_file():
        _apply_nfo(item, _read_nfo(nfo))
    if path.suffix.lower() == ".iso":
        item.subtitle = "Disc-image"
    return item


def _audio_item(path: Path):
    """A music file: cover from the album folder, backdrop from the album or artist folder.
    The tags (and an embedded cover) are read by VLC while it plays."""
    folder = path.parent
    title = _TRACK_NO.sub("", path.stem, count=1).strip() or path.stem
    return MediaItem(
        source="local", id=str(path), title=title, kind="track",
        image_url=_find_image(folder, [path.stem] + COVER_NAMES),
        backdrop_url=_find_image(folder, ["fanart", "backdrop"]) or _find_image(folder.parent, ["fanart", "backdrop",
                                                                                               "artist"]),
        subtitle=folder.name,
        extra={"path": str(path), "mrl": path.as_uri()},
    )


def _folder_item(path: Path):
    title, year = clean_title(path.name)
    item = MediaItem(source="local", id=str(path), title=title, year=year, kind="folder", is_folder=True,
                     image_url=_find_image(path, ["poster", "folder", "cover"]),
                     backdrop_url=_find_image(path, ["fanart", "backdrop"]),
                     extra={"path": str(path)})
    location = path.as_posix().rstrip("/") + "/"
    if (path / "BDMV").is_dir():
        item.kind, item.is_folder, item.subtitle = "bluray", False, "Blu-ray map"
        item.extra["mrl"] = "bluray:///" + location
    elif (path / "VIDEO_TS").is_dir():
        item.kind, item.is_folder, item.subtitle = "dvd", False, "DVD map"
        item.extra["mrl"] = "dvd:///" + location
    if (path / "movie.nfo").is_file() and not item.is_folder:
        _apply_nfo(item, _read_nfo(path / "movie.nfo"))
    return item


class LocalSource:
    key = "local"
    label = "Lokaal"

    def __init__(self, folders, show_usb=True, show_disc=True, names=None):
        self.folders = folders
        self.show_usb = show_usb
        self.show_disc = show_disc
        self.names = names or {}  # own names, e.g. of network shares

    def libraries(self):
        out = []
        for f in self.folders:
            p = Path(f)
            out.append(MediaItem(source=self.key, id=str(p), title=self.names.get(f) or p.name or str(p),
                                 kind="folder", is_folder=True, group="Mappen", extra={"path": str(p)}))
        return out

    def drives(self):
        return scan_drives(self.show_usb, self.show_disc)

    def children(self, item):
        path = Path(item.extra.get("path") or item.id)
        try:
            entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            raise RuntimeError(f"map niet leesbaar: {e.strerror or e}") from e
        dirs, files, tracks = [], [], []
        for p in entries:
            if p.name.startswith((".", "$")) or p.name in ("System Volume Information",):
                continue
            try:
                if p.is_dir():
                    dirs.append(p)
                elif p.suffix.lower() in VIDEO_EXT:
                    files.append(p)
                elif p.suffix.lower() in AUDIO_EXT:
                    tracks.append(p)
            except OSError:
                continue
        single = len(files) == 1
        return ([_folder_item(d) for d in dirs] + [_file_item(f, single) for f in files]
                + [_audio_item(t) for t in tracks])

    def play_url(self, item):
        return item.extra["mrl"]

    def markers(self, item):
        return {"credits": None, "chapters": []}  # local files: VLC reads the chapters itself

    def report_start(self, item, pos_ms):
        pass

    def report_progress(self, item, pos_ms, paused):
        pass

    def report_stop(self, item, pos_ms):
        pass
