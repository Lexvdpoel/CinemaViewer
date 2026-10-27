"""Updates from GitHub: the newest release of the (private) repository replaces the program files.

A release is a tag 'v1.2.3' with release notes. Only the program itself is replaced (the cinemaviewer
package, CinemaViewer.pyw, scripts, README); settings, the bibliotheek, runtime/.venv and vendor stay.
When requirements.txt changes, the Python packages are brought up to date as well (Linux: pip in
.venv-linux; Windows: runtime\\ready.txt is removed, so run.bat installs them on the next start)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import __version__
from .net import SSL_CONTEXT, USER_AGENT

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
# What an update replaces (relative to the project folder).
PROGRAM_FILES = ("cinemaviewer", "CinemaViewer.pyw", "README.md", "requirements.txt", "run.bat", "run.sh", "setup.sh",
                 "tools")


class UpdateError(Exception):
    pass


def version_tuple(text):
    parts = []
    for piece in text.lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts + [0] * (3 - len(parts)))


def _request(url, token, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                               "X-GitHub-Api-Version": "2022-11-28"})
    if token:
        req.add_unredirected_header("Authorization", f"Bearer {token}")  # not sent on to the download host
    return req


def latest(repo, token):
    """Newest release: {'version', 'tag', 'notes', 'newer'}; raises UpdateError."""
    if "/" not in (repo or ""):
        raise UpdateError("vul de GitHub-repository in als eigenaar/naam")
    try:
        with urllib.request.urlopen(_request(f"{API}/repos/{repo}/releases/latest", token), timeout=15,
                                    context=SSL_CONTEXT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("repository of release niet gevonden (klopt de naam, en is het token geldig "
                              "voor deze privé-repository?)") from e
        if e.code in (401, 403):
            raise UpdateError(f"geen toegang tot {repo} (HTTP {e.code}); controleer het token") from e
        raise UpdateError(f"GitHub gaf HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise UpdateError(f"GitHub niet bereikbaar ({getattr(e, 'reason', e)})") from e
    tag = data.get("tag_name", "")
    return {"version": tag.lstrip("vV"), "tag": tag, "notes": (data.get("body") or "").strip(),
            "newer": version_tuple(tag) > version_tuple(__version__)}


def install(repo, token, tag, on_progress=None):
    """Download the release and replace the program files. Returns (report, restart): restart is False
    when CinemaViewer has to be started with run.bat to install new parts (Windows)."""
    work = Path(tempfile.mkdtemp(prefix="cinemaviewer-update-"))
    try:
        archive = work / "release.zip"
        try:
            with urllib.request.urlopen(_request(f"{API}/repos/{repo}/zipball/{tag}", token), timeout=60,
                                        context=SSL_CONTEXT) as resp, archive.open("wb") as fh:
                done = 0
                while chunk := resp.read(256 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done)
        except (urllib.error.URLError, TimeoutError) as e:
            raise UpdateError(f"downloaden mislukt ({getattr(e, 'reason', e)})") from e
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(work / "src")
        tops = [p for p in (work / "src").iterdir() if p.is_dir()]
        if len(tops) != 1 or not (tops[0] / "cinemaviewer" / "__init__.py").is_file():
            raise UpdateError("de release bevat geen CinemaViewer")
        new = tops[0]
        old_requirements = _read(PROJECT_ROOT / "requirements.txt")
        for name in PROGRAM_FILES:
            src, dst = new / name, PROJECT_ROOT / name
            if src.is_dir():
                staged = PROJECT_ROOT / f".{name}.new"
                shutil.rmtree(staged, ignore_errors=True)
                shutil.copytree(src, staged, ignore=shutil.ignore_patterns("__pycache__"))
                if dst.exists():
                    old = PROJECT_ROOT / f".{name}.old"
                    shutil.rmtree(old, ignore_errors=True)
                    dst.rename(old)
                    try:
                        staged.rename(dst)
                    except OSError:
                        old.rename(dst)  # never leave the program without its files
                        raise
                    shutil.rmtree(old, ignore_errors=True)
                else:
                    staged.rename(dst)
            elif src.is_file():
                shutil.copy2(src, dst)
        for script in ("run.sh", "setup.sh"):
            if os.name != "nt" and (PROJECT_ROOT / script).is_file():
                (PROJECT_ROOT / script).chmod(0o755)
        report = f"Bijgewerkt naar versie {tag.lstrip('vV')}."
        if _read(PROJECT_ROOT / "requirements.txt") == old_requirements:
            return report, True
        if os.name == "nt":  # the running runtime cannot be replaced while it is in use
            (PROJECT_ROOT / "runtime" / "ready.txt").unlink(missing_ok=True)
            return (report + " Deze versie heeft nieuwe onderdelen nodig: zet de nieuwe vendor-map van de release "
                    "neer, sluit CinemaViewer en start run.bat.", False)
        return report + " " + _update_packages(), True
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _update_packages():
    """Linux: bring the Python packages in .venv-linux in line with the new requirements.txt."""
    pip = Path(sys.executable).with_name("pip")
    result = subprocess.run([str(pip), "install", "-q", "-r", str(PROJECT_ROOT / "requirements.txt")],
                            capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        return "Let op: de Python-onderdelen konden niet worden bijgewerkt (" + (result.stderr.strip()[-200:]) + ")."
    return "Python-onderdelen bijgewerkt."
