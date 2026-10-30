"""Persistent settings, stored as JSON in %APPDATA%\\CinemaViewer\\config.json."""

import copy
import json
import os
import uuid
from pathlib import Path

if os.name == "nt":
    CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "CinemaViewer"
else:
    CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "CinemaViewer"
CONFIG_PATH = CONFIG_DIR / "config.json"

if os.name == "nt":
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CinemaViewer"
else:
    DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "CinemaViewer"
CACHE_DIR = DATA_DIR / "cache"

DEFAULTS = {
    # Screens are stored by their system name (e.g. "\\\\.\\DISPLAY1"); index is a fallback.
    "control_screen": {"name": "", "index": 0},
    "video_screen": {"name": "", "index": 1},
    "info_screen": {"name": "", "index": -1},  # 'Nu te zien'; index -1: alle overige schermen
    "placeholder_title": "CinemaViewer",
    "placeholder_subtitle": "by L. van der Poel",
    "hide_taskbar": False,          # taskbar on auto-hide (also at boot) and fully hidden while running
    "taskbar_autohide_before": None,  # the user's own auto-hide setting, restored when switched off
    "desktop_wallpaper": False,     # placeholder as Windows desktop background
    "wallpaper_previous": None,     # what the desktop showed before, to put it back
    "wallpaper_key": "",            # size + texts of the current wallpaper (regenerate on change)
    # outputs switched on with a fixed mode at start (e.g. HDBaseT without EDID):
    # [{"output": "HDMI-1", "width": 1920, "height": 1080, "rate": 60, "position": "right"}]
    "forced_outputs": [],
    # VLC audio device ("" = system default); on Linux optionally a forced card profile (HDMI)
    "audio_output": {"device": "", "card": "", "profile": ""},
    "audio_follow_hdmi": True,  # all sound to the HDMI device that was connected last (if it takes sound)
    "lock_cursor": True,            # mouse pointer stays on the control screen
    "now_showing_on_other_screens": True,
    "preview_on_other_screens": True,
    "volume": 80,
    "report_progress": True,
    "device_id": "",
    "settings_password_hash": "",   # empty = default password (see security.py)
    "kiosk_mode": True,             # limit the Ctrl+Alt+Del screen while CinemaViewer runs
    "kiosk_applied": [],            # policy values CinemaViewer set itself (removed again on exit)
    "auto_intermission": False,     # plan a break halfway through the film (checkbox at the play button)
    "intermission_minutes": 15,     # 0 = break lasts until resumed by hand
    "intermission_countdown": 10,   # seconds shown before the film resumes
    "intermission_rewind": 15,      # seconds the film jumps back on resume
    "jellyfin": {"enabled": False, "url": "", "username": "", "token": "", "user_id": ""},
    "plex": {"enabled": False, "url": "", "token": ""},
    # switched on when CinemaViewer starts and off when it closes (over the network)
    # protocol: "pjlink" | "infocus_net" (telnet port 23) | "infocus_serial" (RS-232, baud 0 = automatic)
    # lamp_mode / lamp_power: "" = leave as set on the projector; lamp_warn_hours: 0 = no warning
    # auto_on: switch on when CinemaViewer starts; otherwise with the button in the top bar
    "projector": {"enabled": False, "auto_on": False, "protocol": "pjlink", "host": "", "password": "", "serial_port": "",
                  "baud": 0, "lamp_mode": "", "lamp_power": "", "lamp_warn_hours": 2000},
    "receiver": {"enabled": False, "host": "", "input": "BD",              # Denon, e.g. AVR-4311
                 "volume_control": False, "max_volume_db": -10},
    "local_folders": [],
    # SMB/NFS shares shown as media folders: {name, type: smb|nfs, server, share, username, domain}.
    # Passwords are not kept here (Windows Credential Manager / root-only file on Linux).
    "network_shares": [],
    # Updates from GitHub releases: repo "eigenaar/naam", read-only token for a private repository.
    "updates": {"repo": "Lexvdpoel/CinemaViewer", "token": "", "check_at_start": True},
    # Browser in the operator window (can be shown on the projection); home: start page
    "browser": {"enabled": True, "home": "https://www.google.com/"},
    "radio": {"enabled": True, "stations": None},   # stations None = the ready-made Dutch list
    # Displaycast (YouTube 'Link with TV code' + DLNA): ids are kept so phones stay paired
    "displaycast": {"name": "", "autostart": False, "port": 47801, "screen_id": "", "device_id": "",
                    "dlna_uuid": "", "dial_uuid": ""},
    "show_usb_drives": True,
    "show_disc_drives": True,
    # ruimtelicht over DMX (ArtNet via netwerk of USB-DMX); staten: film, pauze, muziek
    "dmx": {
        "enabled": False,
        "backend": "artnet",           # "artnet" | "usb"
        "artnet_mode": "broadcast",    # "broadcast" | "ip"
        "artnet_ip": "",
        "universe": 0,
        "usb_port": "",                # bijv. COM3 of /dev/ttyUSB0
        "usb_protocol": "auto",        # "auto" | "open" (Enttec Open) | "pro" (Enttec Pro)
        "lamps": [{"name": "Voor", "type": "rgbw", "address": 1, "max": 100}],
        "states": {
            "film":   {"transition": 3.0, "lamps": [{"color": [0, 0, 0], "level": 8}]},
            "pauze":  {"transition": 2.0, "lamps": [{"color": [255, 170, 90], "level": 60}]},
            "muziek": {"transition": 0.4, "lamps": [{"color": [255, 120, 60], "level": 85}]},
        },
        # mode: see dmx.MUSIC_MODES; per_lamp: every lamp its own mode/sensitivity in "lamps"
        "music": {"enabled": True, "mode": "bass", "sensitivity": 70, "speed": "normal", "per_lamp": False,
                  "lamps": []},
    },
}


def _merge(defaults, data):
    out = copy.deepcopy(defaults)
    for key, value in (data or {}).items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, data):
        self.data = data

    @classmethod
    def load(cls):
        data = {}
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        cfg = cls(_merge(DEFAULTS, data))
        if not cfg.data["device_id"]:
            cfg.data["device_id"] = uuid.uuid4().hex
            cfg.save()
        return cfg

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CONFIG_PATH)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    @property
    def first_run(self):
        return not CONFIG_PATH.exists() or not (
            self.data["jellyfin"]["token"] or self.data["plex"]["token"] or self.data["local_folders"] or self.data["network_shares"]
        )
