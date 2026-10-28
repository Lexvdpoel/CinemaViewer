"""Application controller: ties player, sources, audience screens and the control window together."""

import atexit
import ctypes
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from collections import Counter, deque
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QDialog

from . import display, shares, updater, wallpaper
from .config import Config
from .dialogs import ask, message
from .control import ControlWindow
from .cursor_lock import CursorLock
from .devices import Devices
from .dmx import DmxEngine
from .models import MediaItem, fmt_size
from .music import MusicAnalyzer
from .images import ImageCache
from .mirror import ScreenMirror
from .planning import plan_intermission
from .player import Player
from .presentation import ConvertError, Presentation, SlideShow, prepare, wait_for_closing
from .screens import CinemaScreen
from .settings_dialog import ExitDialog, PasswordDialog, SettingsDialog
from .shell import EXIT_RESTART, EXIT_TO_WINDOWS, RESTART_ARG
from .cast.manager import CastManager
from .sources import CacheSource, CastSource, JellyfinSource, LocalSource, PlexSource, RadioSource, scan_drives
from .sources import cache as cache_source
from .system import (LAUNCHER, KeyboardBlocker, apply_kiosk_policies, autostart_enabled, lockscreen_enabled,
                     python_gui_exe, remove_kiosk_policies, run_elevated, set_autostart, set_shell,
                     set_taskbar_autohide, shell_enabled, show_taskbars, shutdown_computer, taskbar_autohide,
                     taskbars_visible)
from .workers import run_async

SCREEN_KEYS = ("control_screen", "video_screen")
SOURCE_KEYS = ("jellyfin", "plex", "local_folders", "network_shares", "show_usb_drives", "show_disc_drives",
               "radio")
AUDIO_KINDS = ("track", "audiocd", "radio")   # no picture: the projection shows the music screen
SHUFFLE_WINDOW = 3600.0      # random play: a title may come by at most SHUFFLE_MAX times per hour
SHUFFLE_MAX = 2


_inhibit_proc = None


def keep_display_awake():
    global _inhibit_proc
    if os.name == "nt":
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    elif _inhibit_proc is None:
        # systemd-inhibit holds a sleep inhibitor as long as its child process lives
        try:
            _inhibit_proc = subprocess.Popen(
                ["systemd-inhibit", "--what=idle:handle-lid-switch:handle-power-key",
                 "--who", "CinemaViewer", "--why", "film", "sleep", "infinity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            atexit.register(_inhibit_proc.terminate)
        except OSError:
            pass


class CinemaApp(QObject):
    def __init__(self, qapp, windowed=False):
        super().__init__()
        self.qapp = qapp
        self.windowed = windowed
        self.cfg = Config.load()
        self.images = ImageCache()
        self.player = Player()
        self.player.set_volume(self.cfg["volume"])
        self.devices = Devices(self.cfg)
        self.dmx = DmxEngine(self.cfg)
        self.music_analyzer = MusicAnalyzer()
        self.cast = CastManager(self.cfg)
        self.cursor_lock = CursorLock(qapp)
        self.sources = {}
        self.current = None          # MediaItem being played
        self.queue = []              # MediaItems waiting after the current one
        self.shuffle = None          # random play: {"pool": [MediaItem], "plays": deque[(key, t)], "title"}
        self._history = deque(maxlen=100)  # titles started, newest last (the current one included)
        self._queue_failures = 0
        self._mrl = None
        self._pos = 0
        self._len = 0
        self._reported_start = False
        self._control_front = True
        self._source_generation = 0
        self._drive_signature = None
        self._scanning = False
        self.closing = False
        self.available_update = None  # newer GitHub release found at start
        self.browser_streaming = False  # the operator's browser is shown on the projection
        self.presentation = None     # open Presentation (slides on the projection)
        self._slide_shortcuts = []   # slide keys (PgUp/PgDn, arrows, B), only active during a presentation
        self.single_screen = False
        self.cinema_windows = []
        self.video_window = None
        self.mirror = None
        self.intermission = None     # dict while a break ('Pauze') is running
        self.keyboard_blocker = None
        self._plan = None            # planned break halfway: {"at", "reason", "length", "done"}
        self._chapters = None        # chapters as VLC sees them
        self._chapter_tries = 0
        self._server_chapters = []   # chapters from Jellyfin/Plex (fallback)
        self._credits_ms = None
        self._audio_mode = False     # current media has no picture: projection shows the music screen
        self._video_checked = False
        self._audio_meta = {}
        self._meta_tries = 0
        self._analyzing = None       # mrl the audio analysis runs for

        self.control = ControlWindow(self)
        self.control.set_volume_display(self.player.volume, self.player.muted)
        self.player.state_changed.connect(self._on_state)
        self.player.tick.connect(self._on_tick)
        self.devices.error.connect(self.control.toast)
        self.devices.receiver_volume.connect(self._on_receiver_volume)
        self.devices.projector_state.connect(self.control.set_projector_state)
        # queued: a receiver may ask for the next video while the app is still handling 'ended'
        self.cast.play_requested.connect(self._cast_play, Qt.QueuedConnection)
        self.cast.command_requested.connect(self._cast_command, Qt.QueuedConnection)
        self.cast.status_changed.connect(self.control.set_cast_status, Qt.QueuedConnection)
        self.dmx.error.connect(self.control.toast)
        self.music_analyzer.levels.connect(self.dmx.set_levels)
        self.music_analyzer.levels.connect(self._on_levels)  # queued: emitted from the analysis thread

        self._report_timer = QTimer(self)
        self._report_timer.setInterval(10000)
        self._report_timer.timeout.connect(lambda: self.current and self._report(
            "progress", self.current, self._pos, self.player.state == "paused"))
        self._drive_timer = QTimer(self)
        self._drive_timer.setInterval(4000)
        self._drive_timer.timeout.connect(self._scan_drives)
        self._screen_timer = QTimer(self)
        self._screen_timer.setSingleShot(True)
        self._screen_timer.setInterval(1000)
        self._screen_timer.timeout.connect(self.layout_screens)
        qapp.screenAdded.connect(lambda _s: self._screen_timer.start())
        qapp.screenRemoved.connect(lambda _s: self._screen_timer.start())
        self._taskbar_timer = QTimer(self)
        self._taskbar_timer.setInterval(1500)
        self._taskbar_timer.timeout.connect(self._guard_taskbar)
        self._intermission_timer = QTimer(self)
        self._intermission_timer.timeout.connect(self._tick_intermission)
        self.install_shortcuts(self.control)

    def start(self):
        keep_display_awake()
        self.devices.power_on()
        self.dmx.start()  # licht gaat naar de pauze-stand; bij afsluiten weer uit
        self.qapp.aboutToQuit.connect(self.dmx.power_off)
        self.player.set_fixed_output(self.devices.controls_volume)
        self.control.set_auto_intermission(self.cfg["auto_intermission"])
        self.apply_kiosk()
        self.qapp.aboutToQuit.connect(self.release_kiosk)  # also when Windows ends the session
        self.qapp.aboutToQuit.connect(self.devices.power_off)
        try:  # keep the paths right when the folder was moved or copied
            if autostart_enabled():
                set_autostart(True)
            if shell_enabled():
                set_shell(True)
        except OSError:
            pass
        self.apply_forced_outputs(retry=True)  # before the screens are laid out
        self.apply_audio_output()
        self.layout_screens()
        self.reload_sources()
        if self.cfg["displaycast"].get("autostart"):
            self.cast.start()
        self._drive_timer.start()
        self.sync_wallpaper()  # e.g. after a resolution change or on a new PC
        if self.cfg["updates"]["repo"] and self.cfg["updates"].get("check_at_start", True):
            QTimer.singleShot(15000, self.check_for_update)  # after the start, off the critical path
        self.sync_taskbar()
        if self.cfg.first_run:
            QTimer.singleShot(600, self.open_settings)

    # -- screens --------------------------------------------------------------
    def resolve_screen_index(self, ref, default):
        screens = QGuiApplication.screens()
        name = (ref or {}).get("name")
        idx = (ref or {}).get("index", default)
        valid_idx = isinstance(idx, int) and 0 <= idx < len(screens)
        matches = [i for i, s in enumerate(screens) if name and s.name() == name]
        # Identical monitors can share a name, so prefer the stored index when it matches too.
        if valid_idx and (idx in matches or not matches):
            return idx
        if matches:
            return matches[0]
        return min(default, len(screens) - 1)

    def layout_screens(self):
        screens = QGuiApplication.screens()
        ci = self.resolve_screen_index(self.cfg["control_screen"], 0)
        vi = self.resolve_screen_index(self.cfg["video_screen"], 1 if len(screens) > 1 else 0)

        resume = None
        if self.current is not None and self.player.state in ("playing", "paused", "opening"):
            resume = (self._mrl, self.player.position, self.current.extra.get("vlc_options", ()))
            self.player.stop()
        self._update_mirror(False)
        if self.browser_streaming:
            self.control.browser_panel.reclaim(pause_media=False)  # back on the projection after the re-layout
        for w in self.cinema_windows:
            w.release()
            w.deleteLater()
        self.cinema_windows = []
        self.video_window = None
        self.single_screen = ci == vi

        for i, screen in enumerate(screens):
            if i == vi:
                role = "video"
            elif i == ci:
                continue
            else:
                role = "aux"
            w = CinemaScreen(self, role, topmost=not self.single_screen and not self.windowed)
            self.install_shortcuts(w)
            w.place_on(screen, self.windowed)
            self.cinema_windows.append(w)
            if role == "video":
                self.video_window = w

        self.control.place_on(screens[ci], self.windowed)
        # the cinema screens hide the pointer: keep it where the operator can see it
        self.cursor_lock.set_window(self.control, self.cfg["lock_cursor"] and not self.single_screen
                                    and not self.windowed)
        self.player.set_window(self.video_window.video_handle())
        if resume:
            self.player.play(*resume)
        self._control_front = True
        self._sync_screens()
        self.control.raise_()
        self.control.activateWindow()

    def _sync_screens(self):
        if self.intermission is not None:
            self._refresh_intermission()
            if self.single_screen and self.video_window and not self._control_front:
                self.video_window.raise_()
            self._update_mirror(False)
            return
        if self.presentation is not None or self.browser_streaming:
            for w in self.cinema_windows:
                if w.role != "video":
                    w.show_placeholder()
                elif self.presentation is not None:
                    w.show_slides(self.presentation)  # also underneath LibreOffice's show, should it stop
                    if self.presentation.live:
                        w.hide()
                else:
                    w.show_browser()
                    self.control.browser_panel.show_on(w)
            if self.single_screen and self.video_window:
                front = self.control if self._control_front else self.video_window
                front.raise_()
                front.activateWindow()
            self._update_mirror(False)
            return
        state = self.player.state
        active = self.current is not None
        showing_video = active and state in ("playing", "paused")
        for w in self.cinema_windows:
            if w.role == "video":
                if showing_video and self._audio_mode:
                    w.show_music(self.current, self.queue[0] if self.queue else None)
                    w.set_music_meta(self._audio_meta)  # also for windows that were just (re)created
                    w.set_music_playing(state == "playing")
                elif showing_video:
                    w.show_video()
                elif not active:
                    w.show_placeholder()
            elif active and self.cfg["now_showing_on_other_screens"]:
                w.show_now_showing(self.current)
            else:
                w.show_placeholder()
        if self.single_screen and self.video_window:
            front = self.control if (self._control_front or not showing_video) else self.video_window
            front.raise_()
            front.activateWindow()
        self._update_mirror(showing_video)

    def _update_mirror(self, showing_video):
        """Capture the live preview only while a film is visible and an extra screen shows it."""
        aux = [w for w in self.cinema_windows if w.role == "aux"]
        want = (showing_video and bool(aux) and self.video_window is not None
                and self.cfg["now_showing_on_other_screens"] and self.cfg["preview_on_other_screens"])
        if want and self.mirror is None:
            self.mirror = ScreenMirror(self.video_window.winId())
            self.mirror.frame.connect(self._on_preview_frame)
            self.mirror.start()
        elif not want and self.mirror is not None:
            self.mirror.frame.disconnect(self._on_preview_frame)
            self.mirror.stop()
            self.mirror = None
            for w in aux:
                w.set_preview_frame(None)

    def _on_preview_frame(self, image):
        if self.mirror is None:  # a frame that was already queued when the capture stopped
            return
        for w in self.cinema_windows:
            w.set_preview_frame(image)

    def toggle_front(self):
        if not self.single_screen:
            return
        self._control_front = not self._control_front
        w = self.control if self._control_front else self.video_window
        w.raise_()
        w.activateWindow()

    def apply_forced_outputs(self, retry=False, quiet=False):
        """Switch the configured outputs on with their fixed mode. A new screen reaches Qt as
        screenAdded, which lays the screens out again. With retry (at start), failing outputs (an
        extender that is still linking up) are tried again a few times; only the last failure is shown."""
        errors = []
        for f in self.cfg["forced_outputs"]:
            try:
                display.force_output(f["output"], int(f["width"]), int(f["height"]), float(f.get("rate") or 0),
                                     f.get("position", "right"))
            except (OSError, ValueError, KeyError) as e:
                errors.append(f"{f.get('output', '?')}: {e}")
        if errors and retry:
            QTimer.singleShot(5000, lambda: self.apply_forced_outputs(quiet=True))
            QTimer.singleShot(15000, lambda: self.apply_forced_outputs(quiet=True))
            QTimer.singleShot(40000, lambda: self.apply_forced_outputs())
        elif errors and not quiet:
            self.control.toast("Uitgang forceren mislukt – " + "; ".join(errors), ms=10000)
        return errors

    def apply_audio_output(self):
        a = self.cfg["audio_output"]
        device = a.get("device", "")
        if a.get("card") and a.get("profile"):
            try:
                device = display.force_audio_profile(a["card"], a["profile"]) or device
            except OSError as e:
                self.control.toast(f"HDMI-geluid forceren mislukt: {e}", ms=10000)
        try:
            self.player.set_audio_device(device)
        except Exception as e:  # noqa: BLE001 - libVLC without device selection
            self.control.toast(f"Geluidsuitgang kiezen mislukt: {e}")

    def apply_placeholder_texts(self):
        for w in self.cinema_windows:
            w.placeholder.set_texts(self.cfg["placeholder_title"], self.cfg["placeholder_subtitle"])

    # -- sources --------------------------------------------------------------
    def reload_sources(self):
        cfg = self.cfg
        dev = cfg["device_id"]
        self._source_generation += 1
        gen = self._source_generation
        self.sources = {}
        self.control._libs = {}

        def load(key, src):
            self.control.set_libraries(key, "Verbinden…")
            run_async(src.libraries,
                      lambda libs: gen == self._source_generation and self.control.set_libraries(key, libs),
                      lambda err: gen == self._source_generation and self.control.set_libraries(
                          key, f"Niet bereikbaar: {err}"))

        j = cfg["jellyfin"]
        if j["enabled"] and j["url"]:
            if j["token"]:
                self.sources["jellyfin"] = JellyfinSource(j["url"], j["token"], j["user_id"], dev)
                load("jellyfin", self.sources["jellyfin"])
            else:
                self.control.set_libraries("jellyfin", "Niet ingelogd – zie Instellingen")
        p = cfg["plex"]
        if p["enabled"] and p["url"]:
            if p["token"]:
                self.sources["plex"] = PlexSource(p["url"], p["token"], dev)
                load("plex", self.sources["plex"])
            else:
                self.control.set_libraries("plex", "Geen token – zie Instellingen")
        cache = CacheSource()
        self.sources["cache"] = cache
        self.control.set_libraries("cache", cache.libraries())
        local = LocalSource(cfg["local_folders"] + [shares.path(s) for s in cfg["network_shares"]],
                            cfg["show_usb_drives"], cfg["show_disc_drives"], shares.mount_names(cfg["network_shares"]))
        self.sources["local"] = local
        if cfg["local_folders"] or cfg["network_shares"]:
            self.control.set_libraries("folders", local.libraries())
        self.sources["cast"] = CastSource()
        self.control.set_libraries("cast", self.sources["cast"].libraries())
        if cfg["browser"]["enabled"]:
            self.control.set_libraries("browser", [MediaItem(source="browser", id="browser", title="Browser",
                                                             kind="browser", group="Browser")])
        if self.presentation is not None:
            self.control.set_libraries("presentation", [self.presentation.item])
        if cfg["radio"]["enabled"]:
            self.sources["radio"] = RadioSource(cfg["radio"]["stations"])
            self.control.set_libraries("radio", self.sources["radio"].libraries())
        self._drive_signature = None
        self._scan_drives()

    def _scan_drives(self):
        if self._scanning:
            return
        self._scanning = True
        cfg = self.cfg
        run_async(scan_drives, self._on_drives, lambda _e: setattr(self, "_scanning", False),
                  cfg["show_usb_drives"], cfg["show_disc_drives"])

    def _on_drives(self, items):
        self._scanning = False
        signature = [(i.id, i.kind, i.title) for i in items]
        if signature == self._drive_signature:
            return
        previous = {s[0]: s[1] for s in (self._drive_signature or [])}
        first_scan = self._drive_signature is None
        self._drive_signature = signature
        self.control.set_libraries("usb", [i for i in items if i.group == "USB"])
        self.control.set_libraries("disc", [i for i in items if i.group == "Disc"])
        if not first_scan:
            for i in items:
                if i.is_disc and previous.get(i.id) != i.kind:
                    self.control.toast(f"Disc geplaatst: {i.title}. Kies hem links onder ‘Disc-stations’.",
                                       error=False)
                elif i.group == "USB" and i.id not in previous:
                    self.control.toast(f"USB-schijf aangesloten: {i.title}", error=False)

    # -- bibliotheek (cache) ------------------------------------------------------
    def is_cached(self, item):
        return item.source in ("plex", "jellyfin", "local") and cache_source.is_cached(item)

    def cache_download(self, item, on_progress, on_done, on_error, cancel=None):
        src = self.sources.get(item.source)
        if src is None:
            on_error("Deze bron is niet beschikbaar.")
            return

        def work():
            return cache_source.download(src, item, on_progress, cancel)

        def done(folder):
            self.control.set_libraries("cache", self.sources["cache"].libraries())
            on_done(folder)

        run_async(work, done, on_error)

    def cache_import(self, item, info, on_progress, on_done, on_error, cancel=None):
        """Copy a local file / unprotected disc into the bibliotheek, filed as info says."""
        def done(folder):
            self._refresh_cache()
            on_done(folder)

        run_async(cache_source.import_local, done, on_error, item, info, on_progress, cancel)

    def cache_space_problem(self):
        """Text when the bibliotheek's disk is (nearly) full, else ''."""
        try:
            cache_source.ensure_space()
        except OSError as e:
            return str(e)
        return ""

    def cache_usage(self):
        try:
            return cache_source.usage()
        except OSError:
            return None

    def _refresh_cache(self):
        self.control.set_libraries("cache", self.sources["cache"].libraries())

    def _cache_in_use(self, item):
        if self.current is None or self.current.source not in ("cache", "plex", "jellyfin", "local"):
            return False
        playing = cache_source.folder_for(self.current)
        return playing is not None and playing == cache_source.folder_for(item)

    def cache_remove(self, item):
        """Delete one item from the bibliotheek (after a question); True when it is gone."""
        if self._cache_in_use(item):
            self.control.toast(f"‘{item.title}’ speelt nu; stop het afspelen eerst.")
            return False
        if not ask(self.control, "Uit bibliotheek verwijderen",
                   f"‘{item.title}’ van deze computer verwijderen? Op Plex/Jellyfin blijft hij gewoon staan.",
                   "Verwijderen", "Annuleren"):
            return False
        try:
            freed = cache_source.remove(item)
        except OSError as e:
            self.control.toast(f"Kon ‘{item.title}’ niet verwijderen: {e}")
            return False
        self._refresh_cache()
        self.control.refresh_cache_view()
        self.control.toast(f"‘{item.title}’ verwijderd, {fmt_size(freed)} vrijgemaakt.", error=False, ms=4000)
        return True

    def cache_remove_watched(self):
        watched = [i for i in self.sources["cache"].all_items() if i.played and not self._cache_in_use(i)]
        if not watched:
            self.control.toast("Er staan geen gekeken films of afleveringen in de bibliotheek.", error=False)
            return
        names = "\n".join(f"• {i.title}" for i in watched[:8]) + ("\n• …" if len(watched) > 8 else "")
        if not ask(self.control, "Gekeken items verwijderen",
                   f"Deze {len(watched)} gekeken film(s)/aflevering(en) van deze computer verwijderen?\n\n{names}",
                   "Verwijderen", "Annuleren"):
            return
        freed, failed = 0, []
        for item in watched:
            try:
                freed += cache_source.remove(item)
            except OSError:
                failed.append(item.title)
                continue
            self.control.cache_item_removed(item)
        self._refresh_cache()
        self.control.refresh_cache_view()
        if failed:
            self.control.toast(f"Niet verwijderd: {', '.join(failed)}")
        else:
            self.control.toast(f"{len(watched)} item(s) verwijderd, {fmt_size(freed)} vrijgemaakt.",
                               error=False, ms=4000)

    # -- updates --------------------------------------------------------------
    def check_for_update(self):
        u = self.cfg["updates"]

        def found(release):
            self.available_update = release if release["newer"] else None
            if self.available_update:
                self.control.toast(f"Nieuwe versie {release['version']} beschikbaar: Instellingen → Systeem → "
                                   "Bijwerken.", error=False, ms=12000)

        run_async(updater.latest, found, lambda _err: None, u["repo"], u["token"])

    def install_update(self, repo, token, tag, on_done, on_error):
        if self.current is not None:
            on_error("Stop eerst het afspelen; daarna kan CinemaViewer worden bijgewerkt.")
            return
        run_async(updater.install, on_done, on_error, repo, token, tag)

    def restart(self):
        """Start CinemaViewer again (after an update), through the kiosk shell when that runs."""
        if not os.environ.get("CINEMAVIEWER_SHELL"):
            args = [a for a in sys.argv[1:] if a != RESTART_ARG] + [RESTART_ARG]
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen([python_gui_exe(), str(LAUNCHER)] + args, creationflags=flags, close_fds=True,
                             start_new_session=os.name != "nt")
        self.shutdown(EXIT_RESTART, keep_devices=True)

    # -- wachtrij -------------------------------------------------------------
    def gather_playable(self, item, on_done, on_error):
        src = self.sources.get(item.source)
        if src is None:
            on_error("Deze bron is niet beschikbaar.")
            return

        def work():
            out = []
            seen = {(item.source, item.id)}
            q = deque([(item, 0)])
            while q and len(out) < 300:
                node, depth = q.popleft()
                if depth >= 4:
                    continue
                try:
                    children = src.children(node)
                except Exception:
                    continue
                for child in children:
                    key = (child.source, child.id)
                    if key in seen or child.kind in ("library", "empty"):
                        continue
                    seen.add(key)
                    if child.playable:
                        out.append(child)
                        if len(out) >= 300:
                            break
                    elif child.is_folder and depth < 3:
                        q.append((child, depth + 1))
            return out

        run_async(work, on_done, on_error)

    def play_all(self, items):
        items = [i for i in items if getattr(i, "playable", False)]
        if not items:
            self.control.toast("Er is niets afspeelbaars in deze lijst.")
            return
        self._end_shuffle()
        self._queue_failures = 0
        self.queue = list(items[1:])
        self._queue_changed()
        self.play(items[0], advance_on_error=True)

    def queue_append(self, items):
        if hasattr(items, "source"):
            items = [items]
        added = [i for i in items if getattr(i, "playable", False)]
        if not added:
            self.control.toast("Er is niets afspeelbaars in deze lijst.")
            return
        self.queue.extend(added)
        self._queue_changed()
        self.control.toast(f"{len(added)} titel{'s' if len(added) != 1 else ''} toegevoegd aan de wachtrij.",
                           error=False, ms=2500)

    def clear_queue(self):
        self._end_shuffle()
        if not self.queue:
            return
        self.queue = []
        self._queue_failures = 0
        self._queue_changed()

    def remove_from_queue(self, index):
        if 0 <= index < len(self.queue):
            del self.queue[index]
            if self.shuffle is not None and not self.queue and self.current is not None:
                nxt = self._pick_shuffle()  # random play: choose another next title
                if nxt is not None:
                    self.queue.append(nxt)
            self._queue_changed()

    def play_from_queue(self, index):
        if not (0 <= index < len(self.queue)):
            return
        item = self.queue.pop(index)
        self._queue_changed()
        self._queue_failures = 0
        self.play(item, advance_on_error=True)

    def _next_from_queue(self):
        while self.queue:
            item = self.queue.pop(0)
            self._queue_changed()
            if getattr(item, "playable", False):
                self.play(item, advance_on_error=True)
                return

    # -- willekeurig afspelen --------------------------------------------------
    def play_shuffled(self, items, title=""):
        """Plays random title after random title from `items` until stopped. A title comes by at most
        SHUFFLE_MAX times per hour and never twice in a row; titles not heard in the last hour go first."""
        items = [i for i in items if getattr(i, "playable", False)]
        if not items:
            self.control.toast("Er is niets afspeelbaars in deze lijst.")
            return
        self.queue = []
        self._queue_failures = 0
        self.shuffle = {"pool": items, "plays": deque(), "title": title}
        self.control.set_shuffle(title, len(items))
        first = self._pick_shuffle()
        self._queue_changed()
        self.play(first, advance_on_error=True)

    def _end_shuffle(self):
        if self.shuffle is not None:
            self.shuffle = None
            self.control.set_shuffle(None)

    @staticmethod
    def _key(item):
        return item.source, item.id

    def _pick_shuffle(self):
        sh = self.shuffle
        plays = sh["plays"]
        now = time.monotonic()
        while plays and now - plays[0][1] > SHUFFLE_WINDOW:
            plays.popleft()
        count = Counter(k for k, _t in plays)
        last = plays[-1][0] if plays else None
        allowed = [i for i in sh["pool"] if count[self._key(i)] < SHUFFLE_MAX]
        fresh = [i for i in allowed if not count[self._key(i)]]
        others = [i for i in allowed if self._key(i) != last]
        choices = fresh or others or (allowed if len(sh["pool"]) == 1 else [])
        return random.choice(choices) if choices else None

    def _shuffle_started(self, item):
        """A title from the random pool started: remember it and pick the next one in advance (so the
        queue and 'Hierna' on the music screen show it)."""
        sh = self.shuffle
        if sh is None or self._key(item) not in {self._key(i) for i in sh["pool"]}:
            return
        sh["plays"].append((self._key(item), time.monotonic()))
        if not self.queue:
            nxt = self._pick_shuffle()
            if nxt is None:
                self.control.toast("Willekeurig afspelen stopt na dit nummer: elk nummer is het afgelopen uur al "
                                   f"{SHUFFLE_MAX} keer gespeeld.", error=False, ms=8000)
                self._end_shuffle()
            else:
                self.queue.append(nxt)
            self._queue_changed()

    # -- terug / vooruit (spoelen bij film, vorig/volgend nummer bij muziek) ---------
    def skip_back(self):
        if self.current is None:
            return
        if not self._audio_mode:
            self.player.seek_relative(-10000)
        elif self.player.position > 3000 or len(self._history) < 2:
            self.player.seek(0)  # like any music player: first back to the start of this song
        else:
            current = self._history.pop()
            previous = self._history.pop()   # started again below, which puts it back in the history
            self.queue.insert(0, current)     # 'next' returns to the song we came from
            self._queue_changed()
            self.play(previous, advance_on_error=True)

    def skip_forward(self):
        if self.current is None:
            return
        if not self._audio_mode:
            self.player.seek_relative(30000)
        elif self.queue:
            self._next_from_queue()

    def play_playlist(self, item):
        self.control.toast(f"‘{item.title}’ wordt geladen…", error=False, ms=2500)

        def failed(err):
            self.control.toast(f"‘{item.title}’ laden mislukt: {err}")

        self.gather_playable(item, self.play_all, failed)

    # -- playback -------------------------------------------------------------
    def play(self, item, resume=False, advance_on_error=False):
        if not advance_on_error:  # chosen by hand, not from the queue: random play ends
            self._end_shuffle()
        src = self.sources.get(item.source)
        if src is None:
            self.control.toast("Deze bron is niet beschikbaar.")
            if advance_on_error and self.queue:
                self._next_from_queue()
            return
        start = item.resume_ms if resume else 0
        self.control.toast(f"‘{item.title}’ wordt gestart…", error=False, ms=2500)

        def failed(err):
            self.control.toast(f"Kan ‘{item.title}’ niet starten: {err}")
            if item.source == "cast":
                self.cast.failed(item)
            if advance_on_error and self.queue:
                self._next_from_queue()

        run_async(src.play_url, lambda mrl: self._start(item, mrl, start), failed, item)

    def _start(self, item, mrl, start_ms):
        self.stop_browser_stream()
        if self.presentation is not None:
            self.close_presentation(sync=False)
        if self.current is not None:
            self._report("stop", self.current, self._pos)
        self.current = item
        self._mrl = mrl
        self._history.append(item)
        self._shuffle_started(item)
        self._pos = start_ms
        self._len = item.runtime_ms
        self._reported_start = False
        self._control_front = False
        self._plan = None
        self._chapters = None
        self._chapter_tries = 0
        self._server_chapters = []
        self._credits_ms = None
        self._audio_mode = item.kind in AUDIO_KINDS
        if not self._audio_mode and self.devices.projector_status in ("off", "cooling"):
            self.control.toast("De beamer staat uit. Zet hem aan met ‘Beamer aan’ bovenin.", ms=8000)
        self._video_checked = self._audio_mode
        self.control.set_music_mode(self._audio_mode)
        self._audio_meta = {}
        self._meta_tries = 0
        self.player.play(mrl, start_ms, item.extra.get("vlc_options", ()))
        self.control.set_now_playing(item)
        self.control.set_play_state("opening")
        self.control.set_planned_pause(None)
        self._sync_screens()
        src = self.sources.get(item.source)
        if src is not None:  # credits position and chapters from Jellyfin/Plex for the planned break
            run_async(src.markers, lambda m, it=item: self._on_markers(it, m), None, item)

    def stop(self):
        """Stop button: whatever the projection shows ends (film, browser, presentation)."""
        self.clear_queue()
        self.stop_browser_stream()
        if self.presentation is not None:
            self.close_presentation()
        if self.current is not None:
            self._end_intermission(resume=False)
            self.player.stop()

    # -- browser and presentations on the projection ------------------------------
    def start_browser_stream(self):
        self.stop()
        self.browser_streaming = True
        self._control_front = False  # one screen: the browser comes to the front (Tab switches)
        self._sync_screens()

    def stop_browser_stream(self, pause_media=True):
        if not self.browser_streaming:
            return
        self.browser_streaming = False
        self.control.browser_panel.reclaim(pause_media)
        self._control_front = True
        self._sync_screens()

    def open_presentation(self, item):
        self.control.toast(f"‘{item.title}’ wordt geopend; de eerste keer omzetten duurt even…", error=False,
                           ms=120000)

        def done(result):
            try:
                presentation = Presentation(item, *result)
            except ConvertError as e:
                failed(e)
                return
            self.stop()
            self.presentation = presentation
            self._control_front = False
            self.control.hide_toast()
            self.control.set_libraries("presentation", [item])
            self.control.show_presentation_panel()
            for sc in self._slide_shortcuts:
                sc.setEnabled(True)
            self._sync_screens()
            presentation.changed.connect(self._presentation_changed)
            path = item.extra.get("path") or item.id
            if SlideShow.supported(path) and self.video_window is not None:
                # the real show (animations, video) on the projection; the PDF slides stay underneath
                show = SlideShow(path, self.video_window.windowHandle().screen().geometry())
                show.failed.connect(lambda err, p=presentation: self._slide_show_failed(p, err))
                presentation.attach(show)
                show.start()
                QTimer.singleShot(90000, lambda p=presentation, s=show: (
                    self.presentation is p and p.show is s and not p.live
                    and self._slide_show_failed(p, "LibreOffice startte de voorstelling niet op tijd")))

        def failed(err):
            self.control.toast(f"Kan ‘{item.title}’ niet openen: {err}")

        run_async(prepare, done, failed, item.extra.get("path") or item.id)

    def _presentation_changed(self):
        """LibreOffice's show started or stopped: the projection window steps aside or comes back."""
        w, pres = self.video_window, self.presentation
        if w is None or pres is None:
            return
        if pres.live and w.isVisible():
            w.hide()
            if not self.single_screen:  # keys (and presenter remotes) back to the operator window
                QTimer.singleShot(1200, lambda: (self.control.raise_(), self.control.activateWindow()))
        elif not pres.live and not w.isVisible():
            self._show_projection()

    def _show_projection(self):
        w = self.video_window
        if w is not None and not w.isVisible() and getattr(w, "placed", None):
            w.place_on(*w.placed)

    def _slide_show_failed(self, presentation, err):
        if presentation is not self.presentation:
            return
        presentation.stop_show()
        self._show_projection()
        self.control.toast(f"De diavoorstelling met animaties lukt niet ({err}). De dia's worden zonder "
                           "animaties getoond.", ms=10000)

    def close_presentation(self, sync=True):
        if self.presentation is None:
            return
        self.presentation.changed.disconnect(self._presentation_changed)
        self.presentation.stop_show()
        self._show_projection()
        self.presentation = None
        for sc in self._slide_shortcuts:
            sc.setEnabled(False)
        for w in self.cinema_windows:
            if w.slides is not None:
                w.slides.set_presentation(None)
        self.control.set_libraries("presentation", None)
        self.control.presentation_closed()
        self._control_front = True
        if sync:
            self._sync_screens()

    def prerender_slide(self, page):
        w = self.video_window
        if self.presentation is None or w is None or w.slides is None or page >= self.presentation.count:
            return
        ratio = w.slides.devicePixelRatioF()
        self.presentation.render(page, QSize(int(w.slides.width() * ratio), int(w.slides.height() * ratio)))

    def _slide(self, action):
        if self.presentation is not None:
            action(self.presentation)

    def toggle_pause(self):
        if self.presentation is not None:  # presenter remotes and the space bar: next slide
            self.presentation.next()
            return
        if self.intermission is not None:
            if self.intermission["mode"] == "pause":
                self.resume_intermission()
            return
        if self.current is not None:
            self.player.toggle_pause()

    # -- intermission ('Pauze') ----------------------------------------------
    def toggle_intermission(self):
        if self.current is None or self._audio_mode:  # no 'Pauze'-screen for music
            return
        if self.intermission is None:
            self.start_intermission()
        elif self.intermission["mode"] == "pause":
            self.resume_intermission()

    def start_intermission(self):
        if self.player.state not in ("playing", "paused") or self._audio_mode:
            return
        plan = self._plan
        if plan and not plan["done"] and abs(self.player.position - plan["at"]) < 15 * 60_000:
            plan["done"] = True  # a break by hand near the middle replaces the planned one
            self.control.set_planned_pause(None)
        minutes = int(self.cfg["intermission_minutes"])
        self.intermission = {
            "mode": "pause",
            "pos": self.player.position,
            "resume_at": datetime.now() + timedelta(minutes=minutes) if minutes else None,
            "countdown_end": None,
        }
        self.player.pause()
        self._intermission_timer.start(1000)
        self._sync_screens()

    def resume_intermission(self):
        im = self.intermission
        if im is None or im["mode"] != "pause":
            return
        # Jump back a little so the audience picks up the story again; seeking while paused
        # lets the picture be ready when the countdown ends.
        self.player.seek(max(0, im["pos"] - int(self.cfg["intermission_rewind"]) * 1000))
        total = int(self.cfg["intermission_countdown"])
        if total <= 0:
            self._end_intermission(resume=True)
            return
        im["mode"] = "countdown"
        im["countdown_end"] = time.monotonic() + total
        self._intermission_timer.start(40)  # smooth countdown ring
        self._refresh_intermission()

    def _tick_intermission(self):
        im = self.intermission
        if im is None:
            self._intermission_timer.stop()
            return
        if im["mode"] == "pause" and im["resume_at"] and datetime.now() >= im["resume_at"]:
            self.resume_intermission()
        elif im["mode"] == "countdown" and time.monotonic() >= im["countdown_end"]:
            self._end_intermission(resume=True)
            return
        self._refresh_intermission()

    def _refresh_intermission(self):
        im = self.intermission
        total = int(self.cfg["intermission_countdown"])
        left = None if im["mode"] == "pause" else max(0.0, im["countdown_end"] - time.monotonic())
        title = self.current.title if self.current else ""
        for w in self.cinema_windows:
            w.show_intermission(title, im["resume_at"], left, total)
        self.control.set_intermission(im["mode"], math.ceil(left) if left is not None else 0)

    def _end_intermission(self, resume):
        if self.intermission is None:
            return
        self.intermission = None
        self._intermission_timer.stop()
        if resume:
            self.player.resume()
        self.control.set_intermission(None)
        self.control.set_play_state("playing" if resume else self.player.state)
        self._sync_screens()

    # -- kiosk (Ctrl+Alt+Del screen) -----------------------------------------
    def apply_kiosk(self):
        if not self.cfg["kiosk_mode"] or self.windowed:
            return
        if self.keyboard_blocker is None:  # Windows key, Alt+Tab, ... (needs no extra rights)
            self.keyboard_blocker = KeyboardBlocker()
            if not self.keyboard_blocker.start():
                self.keyboard_blocker = None
                self.control.toast("Kon de Windows-toets en Alt+Tab niet blokkeren.")
        try:
            applied = apply_kiosk_policies()
        except PermissionError:
            self.control.toast("Ctrl+Alt+Del-menu nog niet beperkt: klik eenmalig op ‘Rechten instellen’ "
                               "onder Instellingen → Systeem.", ms=10000)
            return
        except OSError as e:
            self.control.toast(f"Kon het Ctrl+Alt+Del-menu niet beperken: {e}")
            return
        self.cfg["kiosk_applied"] = sorted(set(self.cfg["kiosk_applied"]) | set(applied))
        self.cfg.save()

    def release_kiosk(self):
        if self.keyboard_blocker is not None:
            self.keyboard_blocker.stop()
            self.keyboard_blocker = None
        if not self.cfg["kiosk_applied"]:
            return
        try:
            remove_kiosk_policies(self.cfg["kiosk_applied"])
        except OSError:
            return
        self.cfg["kiosk_applied"] = []
        self.cfg.save()

    def set_volume(self, value):
        self.player.set_volume(value)
        self.devices.set_volume(self.player.volume)
        if self.player.muted and value > 0:
            self.player.set_muted(False)
            self.devices.set_muted(False)
        self.cfg["volume"] = self.player.volume
        self.control.set_volume_display(self.player.volume, self.player.muted)
        self._cast_update("volume")

    # -- Displaycast -----------------------------------------------------------------
    def _cast_play(self, item):
        """A phone casts something: it replaces what is playing (Displaycast was opened for it)."""
        self.clear_queue()
        self.control.toast(f"Displaycast: {'YouTube' if item.extra.get('cast') == 'youtube' else item.title} "
                           "wordt gestart…", error=False, ms=3000)
        self.play(item, resume=item.resume_ms > 0)

    def _cast_command(self, name, value):
        casting = self.current is not None and self.current.source == "cast"
        if name == "volume":
            self.set_volume(int(value))
        elif name == "mute":
            if bool(value) != self.player.muted:
                self.toggle_mute()
        elif not casting:
            return  # a phone may not pause or stop a film that was started here
        elif name == "pause":
            self.player.pause()
        elif name == "resume":
            if self.player.state == "paused":
                self.player.resume()
        elif name == "stop":
            self.stop()
        elif name == "seek":
            self.player.seek(int(value))
            self._cast_update("seek", position=int(value))

    def _cast_update(self, reason="state", position=None):
        pos = self.player.position if position is None else position
        self.cast.update(self.current, self.player.state if self.current is not None else "stopped", pos,
                         self.player.length or (self.current.runtime_ms if self.current else 0),
                         self.player.volume, self.player.muted, reason)

    def allow_cast_firewall(self):
        """Windows asks per program for network access; this allows Python (CinemaViewer) on private and
        domain networks for Displaycast, with one administrator question."""
        exes = {Path(sys.executable).with_name(n) for n in ("python.exe", "pythonw.exe")}
        lines = ["netsh advfirewall firewall delete rule name=\"CinemaViewer Displaycast\" | Out-Null"]
        for exe in sorted(str(e) for e in exes if e.exists()):
            lines.append(f'netsh advfirewall firewall add rule name="CinemaViewer Displaycast" dir=in action=allow '
                         f'program="{exe}" enable=yes profile=private,domain | Out-Null')
        try:
            code = run_elevated("\n".join(lines))
        except (OSError, subprocess.SubprocessError):
            code = -1
        if code == 0:
            self.control.toast("Windows Firewall staat Displaycast nu toe op privé- en domeinnetwerken.", error=False)
        else:
            self.control.toast("Firewall niet aangepast" + (" (geweigerd)." if code == 1223 else f" (code {code})."))

    def toggle_projector(self):
        state = self.devices.projector_status
        if state == "on":
            film = self.current is not None and not self._audio_mode
            if not ask(self.control, "Beamer uitzetten",
                       ("Er speelt nu een film; het beeld verdwijnt dan. " if film else "")
                       + "Na het uitzetten moet de lamp eerst afkoelen voordat de beamer weer aan kan.\n\n"
                       "Beamer uitzetten?", yes="Uitzetten", no="Annuleren"):
                return
            self.devices.projector_power(False)
        elif state in ("off", "unreachable", None):
            self.devices.projector_power(True)

    def toggle_mute(self):
        self.player.set_muted(not self.player.muted)
        self.devices.set_muted(self.player.muted)
        self.control.set_volume_display(self.player.volume, self.player.muted)
        self._cast_update("volume")

    def _on_receiver_volume(self, mv, muted):
        """Volume or mute changed on the receiver itself (remote, front panel, power on)."""
        if not self.devices.controls_volume:
            return
        self.player.set_volume(self.devices.mv_to_volume_percent(mv))
        self.player.set_muted(muted)
        self.cfg["volume"] = self.player.volume
        self.control.set_volume_display(self.player.volume, self.player.muted)

    def _on_state(self, state):
        self.control.set_play_state(state)
        for w in self.cinema_windows:
            w.set_music_playing(state == "playing")
        if state == "playing":
            self._queue_failures = 0
            if self._music_mode(self.current):
                self.dmx.music_start()
            else:
                self._dmx_stop_music()
                self.dmx.set_state("film")
            self._update_analyzer()
        elif state == "paused":
            self._dmx_stop_music()
            self.dmx.set_state("pauze")
        if self.intermission is not None and state in ("playing", "paused"):
            self.control.set_intermission(self.intermission["mode"])
        item = self.current
        if item is not None and state == "playing" and not self._reported_start:
            self._reported_start = True
            self._report("start", item, self.player.position)
            self._report_timer.start()
        if item is not None and state in ("ended", "stopped", "error", "idle"):
            self._dmx_stop_music()
            self.dmx.set_state("pauze")
            self._end_intermission(resume=False)
            pos = self._len if state == "ended" else self._pos
            self._report("stop", item, pos)
            self._report_timer.stop()
            if state == "error":
                self.control.toast(f"Afspelen van ‘{item.title}’ is mislukt. Is het bestand/de disc leesbaar "
                                   "en wordt het formaat door VLC ondersteund?")
            if state == "ended" and item.source == "cast":
                self.cast.ended(item)  # YouTube: next video of the phone's queue (queued signal)
            if item.kind == "radio" or item.source == "cast":
                pass  # live or cast: nothing to resume or mark as seen
            elif state == "ended" or (item.runtime_ms and pos > item.runtime_ms * 0.9):
                item.played, item.resume_ms = True, 0
            elif pos > 60000:
                item.resume_ms = pos
            if item.source in ("cache", "plex", "jellyfin", "local") and cache_source.save_progress(item):
                self._refresh_cache()  # watched mark and 'Gekeken verwijderen' in the sidebar
            self.current = None
            self._update_analyzer()
            self.control.set_now_playing(None)
            self.control.grid.viewport().update()
            if state == "ended":
                self._queue_failures = 0
            elif state == "error":
                self._queue_failures += 1
                if self._queue_failures > 5:
                    self.control.toast("Wachtrij gestopt na meerdere fouten.")
                    self.clear_queue()
            if self.queue and state in ("ended", "error") and self._queue_failures <= 5:
                self._next_from_queue()
        self._sync_screens()
        self._cast_update("state")

    def _music_mode(self, item):
        """True bij afspelen van een nummer met de DMX-muziekstand aan."""
        return (self.dmx.active and self.cfg["dmx"]["music"]["enabled"]
                and item is not None and getattr(item, "kind", "") == "track")

    def _dmx_stop_music(self):
        self.dmx.music_stop()

    def _update_analyzer(self):
        """The audio analysis runs while music plays (spectrum on the projection screen) or the DMX
        music mode is on; it follows the player's position, so pausing or seeking needs no restart."""
        item = self.current
        want = item is not None and (self._audio_mode or self._music_mode(item))
        if want and self._analyzing != self._mrl:
            self.music_analyzer.start(self._mrl, position=lambda: self.player.position)
            self._analyzing = self._mrl
        elif not want and self._analyzing is not None:
            self.music_analyzer.stop()
            self._analyzing = None

    def _on_levels(self, levels, bands):
        if self._audio_mode:
            for w in self.cinema_windows:
                w.set_levels(levels, bands)

    def _queue_changed(self):
        self.control.set_queue(self.queue)
        if self._audio_mode and self.video_window is not None and self.current is not None:
            self._sync_screens()  # 'Hierna' on the music screen

    def _check_audio(self, pos):
        """Files without picture (also when the source does not say so) get the music screen, and
        their tags/embedded cover are read once VLC has parsed them."""
        if not self._video_checked and self.player.state == "playing" and pos > 1500:
            self._video_checked = True
            if not self.player.has_video():
                self._audio_mode = True
                self.control.set_music_mode(True)
                self._sync_screens()
                self._update_analyzer()
        # Tags settle within seconds; a radio station keeps announcing the current song.
        radio = self.current is not None and self.current.kind == "radio"
        self._meta_tries += 1
        if self._audio_mode and (self._meta_tries <= 40 or (radio and self._meta_tries % 5 == 0)):
            meta = self.player.audio_meta()
            if meta and meta != self._audio_meta:
                self._audio_meta = meta
                for w in self.cinema_windows:
                    w.set_music_meta(meta)
                if radio:
                    self.control.set_radio_song(meta.get("now_playing", ""))

    def _on_tick(self, pos, length):
        self._pos = pos
        if length:
            self._len = length
        self.control.set_progress(pos, length)
        for w in self.cinema_windows:
            w.set_progress(pos, length)
        self._check_audio(pos)
        self._cast_update("tick")
        self._check_planned_pause(pos, length)

    # -- planned break halfway ----------------------------------------------
    def set_auto_intermission(self, enabled):
        self.cfg["auto_intermission"] = bool(enabled)
        self.cfg.save()
        self.control.set_auto_intermission(enabled)
        self._plan = None
        if not enabled:
            self.control.set_planned_pause(None)
        elif self.current is not None and self._len:
            self._check_planned_pause(self.player.position, self._len)

    def _on_markers(self, item, markers):
        if item is not self.current or not markers:
            return
        self._credits_ms = markers.get("credits")
        self._server_chapters = markers.get("chapters") or []
        if self._plan is None or not self._plan["done"]:
            self._plan = None  # re-plan with the server's information

    def _check_planned_pause(self, pos, length):
        if not self.cfg["auto_intermission"] or self.current is None or length <= 0 or self._audio_mode:
            return
        # VLC knows the chapters of the stream/disc only shortly after playback starts: keep asking
        # for a few seconds (ticks come every 200 ms) and re-plan once they appear.
        if not self._chapters and self._chapter_tries < 50 and self.player.state == "playing":
            self._chapter_tries += 1
            self._chapters = self.player.chapters()
            if self._chapters and self._plan is not None and not self._plan["done"]:
                self._plan = None
        if self._plan is None or self._plan["length"] != length:
            chapters = self._chapters or self._server_chapters
            at, reason = plan_intermission(length, self._credits_ms, chapters)
            # Started (or resumed) beyond the planned moment: no break this time.
            self._plan = {"at": at, "reason": reason, "length": length,
                          "done": at is None or pos > at + 5000}
            self.control.set_planned_pause(None if self._plan["done"] else at, reason)
        plan = self._plan
        if plan["done"] or self.intermission is not None or self.player.state != "playing":
            return
        if pos >= plan["at"]:
            plan["done"] = True
            self.control.set_planned_pause(None)
            if pos <= plan["at"] + 5000:   # not when the operator skipped past it
                self.start_intermission()

    def _report(self, kind, item, pos, paused=False):
        if not self.cfg["report_progress"]:
            return
        src = self.sources.get(item.source)
        if src is None:
            return
        args = (item, pos, paused) if kind == "progress" else (item, pos)
        run_async(getattr(src, f"report_{kind}"), None, None, *args)

    # -- settings / lifecycle -------------------------------------------------
    def open_settings(self):
        if self.single_screen:
            self._control_front = True
            self.control.raise_()
        if PasswordDialog(self.cfg["settings_password_hash"], self.control).exec() != QDialog.Accepted:
            return
        dlg = SettingsDialog(self, self.control)
        if dlg.exec() != QDialog.Accepted:
            if dlg.restart_after:  # updated, but the other settings were not saved
                self.cfg.data["updates"] = dlg.data["updates"]
                self.cfg.save()
                QTimer.singleShot(300, self.restart)
            return
        old = self.cfg.data
        self.cfg.data = dlg.data
        self.cfg.save()
        if dlg.restart_after:
            QTimer.singleShot(300, self.restart)  # new version installed
            return
        if old["forced_outputs"] != self.cfg["forced_outputs"]:
            self.apply_forced_outputs()
        if old["audio_output"] != self.cfg["audio_output"]:
            self.apply_audio_output()
        if any(old[k] != self.cfg[k] for k in SCREEN_KEYS):
            self.layout_screens()
        else:
            self.apply_placeholder_texts()
            self._sync_screens()
            self.cursor_lock.set_window(self.control, self.cfg["lock_cursor"] and not self.single_screen
                                        and not self.windowed)
        if any(old[k] != self.cfg[k] for k in SOURCE_KEYS):
            self.reload_sources()
        if old["kiosk_mode"] != self.cfg["kiosk_mode"]:
            self.apply_kiosk() if self.cfg["kiosk_mode"] else self.release_kiosk()
        self.sync_wallpaper()
        if old["hide_taskbar"] != self.cfg["hide_taskbar"]:
            self.sync_taskbar()
        self.devices.reconfigure(old)
        if old["displaycast"].get("name") != self.cfg["displaycast"].get("name") and self.cast.running:
            self.cast.stop()  # phones should see the new name
            self.cast.start()
        self.dmx.reconfigure(old)
        self.player.set_fixed_output(self.devices.controls_volume)
        if self.devices.controls_volume and not old["receiver"]["volume_control"]:
            self.devices.receiver.query_volume()  # slider shows the receiver's current volume

    def sync_taskbar(self, running=True):
        """Option on: auto-hide (so it stays hidden at the next boot) and, while CinemaViewer runs,
        fully hidden on every monitor. Option off: the user's own setting comes back."""
        if self.windowed:
            return
        try:
            if self.cfg["hide_taskbar"]:
                if self.cfg["taskbar_autohide_before"] is None:
                    self.cfg["taskbar_autohide_before"] = taskbar_autohide()
                    self.cfg.save()
                set_taskbar_autohide(True)
                show_taskbars(not running)
                if running:
                    self._taskbar_timer.start()  # Explorer re-shows it (e.g. right after switching to auto-hide)
                else:
                    self._taskbar_timer.stop()
            else:
                self._taskbar_timer.stop()
                if self.cfg["taskbar_autohide_before"] is not None:
                    set_taskbar_autohide(self.cfg["taskbar_autohide_before"])
                    self.cfg["taskbar_autohide_before"] = None
                    self.cfg.save()
                show_taskbars(True)
        except OSError as e:
            self.control.toast(f"Kon de taakbalk niet aanpassen: {e}")

    def _guard_taskbar(self):
        if any(taskbars_visible()):
            show_taskbars(False)

    def sync_wallpaper(self):
        """Desktop background follows the setting and the placeholder texts."""
        try:
            if lockscreen_enabled(wallpaper.lockscreen_image_path()):
                wallpaper.make_lockscreen_image()  # follows a changed screen resolution; no admin needed
            if self.cfg["desktop_wallpaper"]:
                wallpaper.apply_desktop(self.cfg)
            elif self.cfg["wallpaper_previous"]:
                notice = wallpaper.restore_desktop(self.cfg)
                if notice:
                    message(self.control, "Bureaubladachtergrond", notice)
        except OSError as e:
            self.control.toast(f"Kon de bureaubladachtergrond niet instellen: {e}")

    def request_quit(self):
        if self.single_screen:
            self._control_front = True
            self.control.raise_()
        dlg = ExitDialog(self.current.title if self.current else None, self.control)
        if dlg.exec() != QDialog.Accepted:
            return
        if dlg.result_action == "windows":
            if PasswordDialog(self.cfg["settings_password_hash"], self.control).exec() == QDialog.Accepted:
                if self._devices_off():
                    self.shutdown(EXIT_TO_WINDOWS)  # as the shell, the desktop is started next
        elif dlg.result_action == "shutdown":
            if not self._devices_off():  # before Windows starts closing programs
                return
            try:
                shutdown_computer()
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                self.control.toast(f"Windows kon de computer niet uitschakelen: {e}")
                self.devices.power_on()
                return
            self.shutdown()

    def _devices_off(self):
        """Switches projector and receiver off (each tried a few times). When one does not respond,
        asks whether to close anyway; False means: stay open (the devices are switched on again)."""
        if not self.devices.enabled:
            return True
        self.control.toast("Beamer en receiver worden uitgezet…", error=False, ms=20000)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()  # show the toast before waiting
        try:
            failed = self.devices.power_off()
        finally:
            QApplication.restoreOverrideCursor()
        self.control.hide_toast()
        if not failed:
            return True
        one = len(failed) == 1
        if ask(self.control, "Pas op",
               f"De {' en de '.join(failed)} {'reageert' if one else 'reageren'} niet op het uit-commando en "
               f"{'staat' if one else 'staan'} mogelijk nog aan.\n\nToch afsluiten?"):
            return True
        self.devices.power_on()
        return False

    def shutdown(self, exit_code=0, keep_devices=False):
        self.closing = True
        if self.current is not None and self.cfg["report_progress"]:
            src = self.sources.get(self.current.source)
            try:
                src.report_stop(self.current, self._pos)
            except Exception:  # noqa: BLE001 - best effort while exiting
                pass
        self.current = None
        self._intermission_timer.stop()
        self.music_analyzer.stop()
        self._analyzing = None
        self.cast.stop()
        if self.presentation is not None:
            self.presentation.stop_show()
        wait_for_closing()
        self.dmx.power_off()
        self.release_kiosk()
        self.cursor_lock.release()
        self.sync_taskbar(running=False)  # back on the desktop: taskbar appears again on mouse-over
        self.cfg.save()
        self._update_mirror(False)
        self.player.shutdown()
        if not keep_devices:  # a restart (update) keeps the projector and receiver on
            self.devices.power_off()
        for w in self.cinema_windows:
            w.release()
        self.qapp.exit(exit_code)

    # -- keyboard -------------------------------------------------------------
    def install_shortcuts(self, window):
        """Window-wide shortcuts; text fields keep their own keys (Qt shortcut override)."""
        bindings = [
            (("Space", "K", Qt.Key_MediaTogglePlayPause, Qt.Key_MediaPlay, Qt.Key_MediaPause), self.toggle_pause),
            ((Qt.Key_MediaStop, "Ctrl+S"), self.stop),
            (("Ctrl+Left", "J"), self.skip_back),
            (("Ctrl+Right", "L"), self.skip_forward),
            (("+", "=", "Ctrl+Up", Qt.Key_VolumeUp), lambda: self.set_volume(self.player.volume + 5)),
            (("-", "Ctrl+Down", Qt.Key_VolumeDown), lambda: self.set_volume(self.player.volume - 5)),
            (("M", Qt.Key_VolumeMute), self.toggle_mute),
            (("Tab",), self.toggle_front),
            (("Ctrl+Q",), self.request_quit),
            (("P",), self.toggle_intermission),
            (("Ctrl+,",), self.open_settings),
            (("Backspace",), self.control.go_back),
        ]
        for keys, fn in bindings:
            for key in keys:
                sc = QShortcut(QKeySequence(key), window)
                sc.setContext(Qt.WindowShortcut)
                sc.activated.connect(fn)
        slide_bindings = [
            (("PgDown", "Right", "Down", "N"), lambda: self._slide(Presentation.next)),
            (("PgUp", "Left", "Up"), lambda: self._slide(Presentation.previous)),
            (("Home",), lambda: self._slide(lambda p: p.go(0))),
            (("End",), lambda: self._slide(lambda p: p.go(p.count - 1))),
            (("B", "."), lambda: self._slide(Presentation.toggle_black)),
        ]
        alive = set(map(id, self.cinema_windows + [self.control, window]))

        def kept(sc):  # shortcuts of windows replaced at a screen re-layout are dropped
            try:
                return id(sc.parent()) in alive
            except RuntimeError:
                return False

        self._slide_shortcuts = [sc for sc in self._slide_shortcuts if kept(sc)]
        for keys, fn in slide_bindings:
            for key in keys:
                sc = QShortcut(QKeySequence(key), window)
                sc.setContext(Qt.WindowShortcut)
                sc.setEnabled(self.presentation is not None)
                sc.activated.connect(fn)
                self._slide_shortcuts.append(sc)
