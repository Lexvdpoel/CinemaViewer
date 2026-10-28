"""Placeholder images: wallpaper files and the Windows desktop background."""

import os
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QImage

from .config import CONFIG_DIR
from .screens import render_placeholder
from .system import BACKGROUND_TYPES, current_wallpaper, open_background_settings, set_wallpaper

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WALLPAPER_DIR = PROJECT_ROOT / "wallpapers"
DESKTOP_IMAGE = CONFIG_DIR / "bureaubladachtergrond.png"
STANDARD_SIZES = [(1920, 1080), (1920, 1200)]


def lockscreen_image_path():
    if os.name != "nt":  # Cinnamon's lock screen reads it as the logged-in user
        return CONFIG_DIR / "vergrendelscherm.png"
    # ProgramData: readable for the Windows sign-in screen, writable for the user who created it
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "CinemaViewer" / "vergrendelscherm.png"


def make_lockscreen_image():
    """The stage without text, in the main screen's resolution (only rewritten when that changes)."""
    path = lockscreen_image_path()
    w, h = primary_screen_pixels()
    if path.exists():
        current = QImage(str(path))
        if (current.width(), current.height()) == (w, h):
            return path
    path.parent.mkdir(parents=True, exist_ok=True)
    render_placeholder(w, h, "", "", with_text=False).save(str(path))
    return path


def primary_screen_pixels():
    screen = QGuiApplication.primaryScreen()
    g, ratio = screen.geometry(), screen.devicePixelRatio()
    return round(g.width() * ratio), round(g.height() * ratio)


def save_wallpapers(title, subtitle, folder=WALLPAPER_DIR):
    """Write the placeholder in the standard sizes (plus this PC's main screen) as PNG files."""
    folder.mkdir(parents=True, exist_ok=True)
    sizes = list(STANDARD_SIZES)
    if primary_screen_pixels() not in sizes:
        sizes.append(primary_screen_pixels())
    paths = []
    for w, h in sizes:
        path = folder / f"CinemaViewer-{w}x{h}.png"
        render_placeholder(w, h, title, subtitle).save(str(path))
        paths.append(path)
    return paths


def apply_desktop(cfg):
    """Make the placeholder the desktop background; returns True when something changed."""
    w, h = primary_screen_pixels()
    key = f"{w}x{h}|{cfg['placeholder_title']}|{cfg['placeholder_subtitle']}"
    if key == cfg["wallpaper_key"] and DESKTOP_IMAGE.exists():
        return False
    if not cfg["wallpaper_previous"]:
        cfg["wallpaper_previous"] = current_wallpaper()
    DESKTOP_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    render_placeholder(w, h, cfg["placeholder_title"], cfg["placeholder_subtitle"]).save(str(DESKTOP_IMAGE))
    set_wallpaper(DESKTOP_IMAGE)
    cfg["wallpaper_key"] = key
    cfg.save()
    return True


def restore_desktop(cfg):
    """Put the previous background back. Returns a notice for the user when Windows has to do it."""
    prev = cfg["wallpaper_previous"]
    cfg["wallpaper_previous"] = None
    cfg["wallpaper_key"] = ""
    cfg.save()
    if not prev:
        return ""
    kind = int(prev.get("type") or 0)
    if kind == 0 and prev.get("path") and Path(prev["path"]).exists():
        set_wallpaper(prev["path"], prev.get("style", "10"), prev.get("tile", "0"))
        return ""
    # Spotlight, slideshow or solid colour can only be switched back on in Windows' own settings.
    open_background_settings()
    return (f"Je vorige bureaubladachtergrond was ‘{BACKGROUND_TYPES.get(kind, 'onbekend')}’. Die kan alleen "
            "via de Windows-instellingen weer worden aangezet; dat venster is nu geopend.")
