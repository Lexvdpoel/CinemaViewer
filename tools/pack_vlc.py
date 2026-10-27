"""Builds vendor/vlc-<version>-win64.zip from an installed 64-bit VLC (developer tool).

Only what libVLC needs is packed: libvlc.dll, libvlccore.dll, the plugins folder and the licence files.
Usage: python tools/pack_vlc.py [VLC folder]
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["libvlc.dll", "libvlccore.dll", "COPYING.txt", "AUTHORS.txt", "README.txt"]


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Program Files\VideoLAN\VLC")
    version = "unknown"
    for line in (src / "NEWS.txt").read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("Changes between"):
            version = line.split()[-1].rstrip(":")
            break
    out = ROOT / "vendor" / f"vlc-{version}-win64.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in FILES:
            if (src / name).exists():
                zf.write(src / name, name)
        for path in sorted((src / "plugins").rglob("*")):
            if path.is_file() and path.name != "plugins.dat":  # CinemaViewer writes the cache at its first start
                zf.write(path, path.relative_to(src).as_posix())
    print(f"{out.name}: {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
