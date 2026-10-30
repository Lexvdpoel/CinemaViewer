"""Linux: force video outputs with xrandr (X11) and HDMI audio profiles with pactl.

Extenders such as HDBaseT often pass no (or a broken) EDID/ELD: X then reports the output as
disconnected or without modes, and PipeWire/PulseAudio hides the HDMI sound output. Both can be
switched on by hand with a known resolution and audio profile.

Several video cards (e.g. a GT 520 plus the Intel graphics on the board): X shows each as a RandR
'provider'. The outputs of a second card only work once it is linked to the first one as output sink
(xrandr --setprovideroutputsource); link_graphics_cards() does that.
"""

import ctypes
import ctypes.util
import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

POSITIONS = {"right": "Rechts naast de andere schermen", "left": "Links naast de andere schermen",
             "same": "Spiegelen (kopie hoofdscherm)"}

_OUTPUT_RE = re.compile(r"^(\S+) (connected|disconnected)( primary)?(?: (\d+)x(\d+)\+(-?\d+)\+(-?\d+))?")
_MODE_RE = re.compile(r"^\s+(\d+)x(\d+)(i?)\S*\s+(.*)$")  # group 3: interlaced
_C_ENV = dict(os.environ, LC_ALL="C")


def _run(args, timeout=15):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=_C_ENV)
    except (OSError, subprocess.SubprocessError) as e:
        raise OSError(f"{args[0]}: {e}") from e
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout).strip() or f"{args[0]} gaf code {result.returncode}")
    return result.stdout


def _check_x11():
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
        raise OSError("uitgangen forceren werkt alleen in een X11-sessie (niet onder Wayland)")
    if not shutil.which("xrandr"):
        raise OSError("xrandr ontbreekt (sudo apt install x11-xserver-utils)")


class _ProviderResources(ctypes.Structure):
    _fields_ = [("timestamp", ctypes.c_ulong), ("nproviders", ctypes.c_int),
                ("providers", ctypes.POINTER(ctypes.c_ulong))]


class _ProviderInfo(ctypes.Structure):
    _fields_ = [("capabilities", ctypes.c_uint), ("ncrtcs", ctypes.c_int), ("crtcs", ctypes.POINTER(ctypes.c_ulong)),
                ("noutputs", ctypes.c_int), ("outputs", ctypes.POINTER(ctypes.c_ulong)), ("name", ctypes.c_char_p),
                ("nassociatedproviders", ctypes.c_int), ("associated_providers", ctypes.POINTER(ctypes.c_ulong)),
                ("associated_capability", ctypes.POINTER(ctypes.c_uint)), ("nameLen", ctypes.c_int)]


class _OutputInfoHead(ctypes.Structure):  # only the start of XRROutputInfo
    _fields_ = [("timestamp", ctypes.c_ulong), ("crtc", ctypes.c_ulong), ("name", ctypes.c_char_p)]


_CAP_SOURCE_OUTPUT, _CAP_SINK_OUTPUT = 1, 2
_VENDORS = {"0x10de": "NVIDIA", "0x8086": "Intel", "0x1002": "AMD"}
_DRIVER_VENDORS = {"nouveau": "NVIDIA", "nvidia": "NVIDIA", "intel": "Intel", "amdgpu": "AMD", "radeon": "AMD"}


def _xrandr_lib():
    x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    xrr = ctypes.CDLL(ctypes.util.find_library("Xrandr") or "libXrandr.so.2")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xrr.XRRGetScreenResourcesCurrent.restype = ctypes.c_void_p
    xrr.XRRGetScreenResourcesCurrent.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    xrr.XRRFreeScreenResources.argtypes = [ctypes.c_void_p]
    xrr.XRRGetProviderResources.restype = ctypes.POINTER(_ProviderResources)
    xrr.XRRGetProviderResources.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    xrr.XRRFreeProviderResources.argtypes = [ctypes.POINTER(_ProviderResources)]
    xrr.XRRGetProviderInfo.restype = ctypes.POINTER(_ProviderInfo)
    xrr.XRRGetProviderInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    xrr.XRRFreeProviderInfo.argtypes = [ctypes.POINTER(_ProviderInfo)]
    xrr.XRRGetOutputInfo.restype = ctypes.POINTER(_OutputInfoHead)
    xrr.XRRGetOutputInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    xrr.XRRFreeOutputInfo.argtypes = [ctypes.POINTER(_OutputInfoHead)]
    return x11, xrr


def _drm_vendors():
    """Vendors of the video cards the kernel drives, in card order (card0, card1, …)."""
    found = []
    for path in sorted(glob.glob("/sys/class/drm/card[0-9]*/device/vendor")):
        if "-" in path.split("/")[4]:
            continue  # card0-HDMI-A-1: a connector, not a card
        try:
            with open(path) as f:
                found.append(_VENDORS.get(f.read().strip(), "Videokaart"))
        except OSError:
            pass
    return found


def providers():
    """The video cards X knows: [{id, name, card, crtcs, source, sink, linked, outputs: [names]}], or []."""
    try:
        _check_x11()
        x11, xrr = _xrandr_lib()
    except OSError:
        return []
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return []
    found = []
    try:
        root = x11.XDefaultRootWindow(dpy)
        res = xrr.XRRGetScreenResourcesCurrent(dpy, root)
        pres = xrr.XRRGetProviderResources(dpy, root)
        if not res or not pres:
            return []
        try:
            for i in range(pres.contents.nproviders):
                pid = pres.contents.providers[i]
                info = xrr.XRRGetProviderInfo(dpy, res, pid)
                if not info:
                    continue
                p = info.contents
                names = []
                for j in range(p.noutputs):
                    out = xrr.XRRGetOutputInfo(dpy, res, p.outputs[j])
                    if out:
                        names.append((out.contents.name or b"").decode(errors="replace"))
                        xrr.XRRFreeOutputInfo(out)
                found.append({"id": pid, "name": (p.name or b"").decode(errors="replace"), "crtcs": p.ncrtcs,
                              "source": bool(p.capabilities & _CAP_SOURCE_OUTPUT),
                              "sink": bool(p.capabilities & _CAP_SINK_OUTPUT),
                              "linked": p.nassociatedproviders > 0, "outputs": names})
                xrr.XRRFreeProviderInfo(info)
        finally:
            xrr.XRRFreeProviderResources(pres)
            xrr.XRRFreeScreenResources(res)
    finally:
        x11.XCloseDisplay(dpy)
    # a name for each card: from the X driver (nouveau, intel), else from the kernel's card list
    vendors = _drm_vendors()
    named = {_DRIVER_VENDORS.get(p["name"].lower()) for p in found} - {None}
    spare = [v for v in vendors if v not in named]
    for p in found:
        p["card"] = _DRIVER_VENDORS.get(p["name"].lower()) or (spare.pop(0) if spare else p["name"] or "Videokaart")
    if len({p["card"] for p in found}) < len(found):  # two of the same kind
        for n, p in enumerate(found, 1):
            p["card"] = f"{p['card']} ({n})"
    return found


def link_graphics_cards():
    """Link every further video card to the first one as output sink, so that X can show a picture on its
    outputs. Returns True when something changed (Qt then sees the new screens as they are switched on)."""
    found = providers()
    main = next((p for p in found if p["source"]), None)
    changed = False
    for p in found:
        if p is main or main is None or not p["sink"] or p["linked"]:
            continue
        _run(["xrandr", "--setprovideroutputsource", hex(p["id"]), hex(main["id"])])
        changed = True
    return changed


def card_capacity():
    """{card name: how many screens it can drive at once (CRTCs)}. A GT 520 has three connectors but
    two CRTCs: only two of them can show a picture at the same time."""
    return {p["card"]: p["crtcs"] for p in providers() if p["crtcs"]}


def display_outputs():
    """All video outputs, also disconnected ones: [{name, card, connected, primary, active, width, height,
    modes: [(w, h, [rates])]}]."""
    try:
        _check_x11()
        text = _run(["xrandr", "--query"])
    except OSError:
        return []
    card_of = {name: p["card"] for p in providers() for name in p["outputs"]}
    outputs = []
    for line in text.splitlines():
        m = _OUTPUT_RE.match(line)
        if m:
            outputs.append({"name": m.group(1), "card": card_of.get(m.group(1), ""),
                            "connected": m.group(2) == "connected", "primary": bool(m.group(3)),
                            "active": m.group(4) is not None, "width": int(m.group(4) or 0),
                            "height": int(m.group(5) or 0), "x": int(m.group(6) or 0), "y": int(m.group(7) or 0),
                            "modes": []})
            continue
        m = _MODE_RE.match(line)
        if m and outputs and not m.group(3):
            rates = []
            for token in m.group(4).split():
                try:
                    rates.append(float(token.rstrip("*+")))
                except ValueError:
                    pass
            outputs[-1]["modes"].append((int(m.group(1)), int(m.group(2)), rates))
    return outputs


# -- the desktop's screen layout --------------------------------------------------------
_MONITOR_FILES = ("~/.config/cinnamon-monitors.xml", "~/.config/monitors.xml")


def saved_layout(connected):
    """The layout saved in the display settings of Cinnamon (or GNOME) for exactly these connected
    outputs: [{name, width, height, rate, x, y, primary}] plus [names to switch off], or None."""
    import xml.etree.ElementTree as ET
    want = set(connected)
    for path in _MONITOR_FILES:
        try:
            root = ET.parse(os.path.expanduser(path)).getroot()
        except (OSError, ET.ParseError):
            continue
        for conf in root.iter("configuration"):
            on, off = [], []
            for logical in conf.findall("logicalmonitor"):
                for mon in logical.findall("monitor"):
                    mode = mon.find("mode")
                    try:
                        on.append({"name": mon.findtext("monitorspec/connector", "").strip(),
                                   "width": int(mode.findtext("width")), "height": int(mode.findtext("height")),
                                   "rate": float(mode.findtext("rate") or 0),
                                   "x": int(logical.findtext("x") or 0), "y": int(logical.findtext("y") or 0),
                                   "primary": (logical.findtext("primary") or "").strip() == "yes"})
                    except (AttributeError, TypeError, ValueError):
                        break
            for disabled in conf.findall("disabled"):
                off += [c.text.strip() for c in disabled.iter("connector") if c.text]
            if on and {o["name"] for o in on} | set(off) == want:
                return on, off
    return None


def _cloned(active):
    corners = [(o["x"], o["y"]) for o in active]
    return len(corners) != len(set(corners))


def restore_desktop_layout(force=False):
    """At the start of the kiosk session: X itself shows all screens as copies of each other (in a
    resolution they all share, often 720x576), and only the desktop restores its layout. So: the layout
    saved in Cinnamon's display settings, or else every connected screen beside the previous one at its
    own best resolution. Returns a short description of what was done, or '' when nothing was needed."""
    _check_x11()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if not force and any(d in desktop for d in ("cinnamon", "gnome")):
        return ""  # that desktop restores its own layout
    outputs = display_outputs()
    connected = [o for o in outputs if o["connected"]]
    if len(connected) < 2:
        return ""
    by_name = {o["name"]: o for o in outputs}
    saved = saved_layout([o["name"] for o in connected])
    if saved is not None:
        on, off = saved
        args = ["xrandr"]
        for o in on:
            args += ["--output", o["name"], "--mode", f"{o['width']}x{o['height']}", "--pos", f"{o['x']}x{o['y']}"]
            rates = next((m[2] for m in by_name[o["name"]]["modes"] if (m[0], m[1]) == (o["width"], o["height"])), [])
            if o["rate"] and rates:
                args += ["--rate", f"{min(rates, key=lambda r: abs(r - o['rate'])):.2f}"]
            if o["primary"]:
                args.append("--primary")
        for name in off:
            args += ["--output", name, "--off"]
        _run(args)
        return "schermindeling van het bureaublad hersteld"
    active = [o for o in connected if o["active"]]
    if len(active) == len(connected) and not _cloned(active):
        return ""  # already side by side
    # every screen beside the previous one, the primary first, as many as each card can drive
    order = sorted(connected, key=lambda o: (not o["primary"], not o["active"], o["x"]))
    capacity = card_capacity()
    used = {}
    args, x = ["xrandr"], 0
    for o in order:
        limit = capacity.get(o["card"])
        if limit and used.get(o["card"], 0) >= limit:
            args += ["--output", o["name"], "--off"]
            continue
        used[o["card"]] = used.get(o["card"], 0) + 1
        width = o["modes"][0][0] if o["modes"] else 0  # xrandr lists the preferred/best mode first
        args += ["--output", o["name"], "--auto", "--pos", f"{x}x0"]
        x += width
    _run(args)
    return "schermen naast elkaar gezet"


# -- hotplug ---------------------------------------------------------------------------
_RR_SCREEN_CHANGE, _RR_CRTC_CHANGE, _RR_OUTPUT_CHANGE, _RR_OUTPUT_PROPERTY = 1, 2, 4, 8


def output_states():
    """{output: (connected, active)} as X knows it now, without probing the outputs again (no flicker,
    and no new change events of its own)."""
    try:
        _check_x11()
        text = _run(["xrandr", "--current"])
    except OSError:
        return {}
    states = {}
    for line in text.splitlines():
        m = _OUTPUT_RE.match(line)
        if m:
            states[m.group(1)] = (m.group(2) == "connected", m.group(4) is not None)
    return states


def watch_outputs(callback):
    """Calls callback() (from a background thread) whenever X reports a change of the outputs: a screen
    or receiver that is switched on and passes its EDID, a cable that is plugged in. Returns False
    when that cannot be watched."""
    import threading
    try:
        _check_x11()
        x11, xrr = _xrandr_lib()
    except OSError:
        return False
    x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    xrr.XRRSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
    dpy = x11.XOpenDisplay(None)  # a connection of its own, only used by the thread below
    if not dpy:
        return False
    # (a property change: a new EDID on an output that stays connected, e.g. the projector behind the receiver)
    xrr.XRRSelectInput(dpy, x11.XDefaultRootWindow(dpy),
                       _RR_SCREEN_CHANGE | _RR_CRTC_CHANGE | _RR_OUTPUT_CHANGE | _RR_OUTPUT_PROPERTY)
    event = ctypes.create_string_buffer(256)  # XEvent is 24 longs

    def run():
        while True:
            x11.XNextEvent(dpy, event)  # blocks until X reports something
            try:
                callback()
            except Exception:  # noqa: BLE001 - keep watching
                pass

    threading.Thread(target=run, name="output-watch", daemon=True).start()
    return True


def follow_preferred_modes(skip=()):
    """An output that runs in another mode than its device now prefers is switched to the preferred one.
    A receiver that starts before the projector first passes its own EDID (often with 1080i preferred); when
    the projector comes up, the EDID changes, but X keeps the old mode, which the projector may not show.
    Outputs in `skip` (forced modes) and a layout saved in the desktop settings are left alone.
    Returns ['HDMI-1: 1920x1080i -> 1920x1200', ...]."""
    _check_x11()
    text = _run(["xrandr", "--current"])
    outputs, name = {}, None
    for line in text.splitlines():
        m = _OUTPUT_RE.match(line)
        if m:
            name = m.group(1) if m.group(2) == "connected" and m.group(4) is not None else None
            if name:
                outputs[name] = {"current": None, "preferred": None}
            continue
        if name and line.startswith(" ") and line.split():
            mode, *tokens = line.split()
            rates = []  # [rate, flags]; xrandr writes '59.95*+', or '59.95 +' with the flag apart
            for token in tokens:
                if token.strip("*+") or not rates:
                    rates.append([token.strip("*+"), token])
                else:
                    rates[-1][1] += token
            for rate, flags in rates:
                if "*" in flags:
                    outputs[name]["current"] = (mode, rate)
                if "+" in flags and outputs[name]["preferred"] is None:
                    outputs[name]["preferred"] = (mode, rate)
    if saved_layout(list(outputs)) is not None:
        return []
    done = []
    for name, o in outputs.items():
        cur, pref = o["current"], o["preferred"]
        if name in skip or not cur or not pref or cur == pref:
            continue
        if cur[0] == pref[0] and abs(float(cur[1]) - float(pref[1])) < 0.6:
            continue  # same resolution, refresh rate within rounding
        _run(["xrandr", "--output", name, "--mode", pref[0], "--rate", pref[1]])
        done.append(f"{name}: {cur[0]}@{cur[1]} -> {pref[0]}@{pref[1]}")
    return done


def refresh_output(name):
    """Switch an output off and on again (same mode), so that a device that only just came up (an AVR
    passing on its EDID) gets a fresh signal."""
    _check_x11()
    _run(["xrandr", "--output", name, "--off"])


def diagnosis():
    """What Linux sees of the screens and HDMI sound, as text for the settings (a photo of it says enough)."""
    lines = ["SCHERMEN (xrandr --current)"]
    for name, (connected, active) in sorted(output_states().items()):
        lines.append(f"  {name}: {'aangesloten' if connected else 'niets'}{', actief' if active else ''}")
    lines.append("VIDEOKAARTEN")
    for p in providers():
        lines.append(f"  {p['card']} ({p['name']}): {p['crtcs']} schermen, gekoppeld={p['linked']}, "
                     f"uitgangen {', '.join(p['outputs'])}")
    lines.append("HDMI-GELUID (pactl)")
    profiles = _hdmi_profiles()
    if not shutil.which("pactl"):
        lines.append("  pactl ontbreekt")
    for card, profile, desc, available in profiles:
        lines.append(f"  {'JA ' if available else 'nee'} {desc}  [{card.replace('alsa_card.', '')} {profile}]")
    try:
        active = re.findall(r"Name: (\S+)[\s\S]*?Active Profile: (\S+)", _run(["pactl", "list", "cards"]))
        for card, profile in active:
            lines.append(f"  actief: {card.replace('alsa_card.', '')} -> {profile}")
        lines.append("  standaard: " + _run(["pactl", "get-default-sink"]).strip())
    except OSError as e:
        lines.append(f"  pactl: {e}")
    lines.append("ELD (wat het apparaat over geluid doorgeeft)")
    for path in sorted(glob.glob("/proc/asound/card*/eld#*")):
        try:
            with open(path) as f:
                info = dict(line.split(None, 1) for line in f.read().splitlines() if " " in line or "\t" in line)
        except OSError:
            continue
        name = info.get("monitor_name", "").strip()
        lines.append(f"  {path[12:]}: aanwezig={info.get('monitor_present', '?').strip()} "
                     f"geldig={info.get('eld_valid', '?').strip()} {name}")
    if not glob.glob("/proc/asound/card*/eld#*"):
        lines.append("  geen ELD-bestanden: de HDMI-geluidskaart van de videokaart is niet gevonden")
    try:
        cards = open("/proc/asound/cards").read().strip()
        lines.append("GELUIDSKAARTEN")
        lines += ["  " + line.strip() for line in cards.splitlines() if line.strip() and not line.startswith(" " * 5)]
    except OSError:
        pass
    return "\n".join(lines)


def _modeline(width, height, rate):
    """Timings for a mode the output does not offer itself (no EDID), via cvt or gtf."""
    for tool, args in (("cvt", [str(width), str(height), str(rate)]),
                       ("gtf", [str(width), str(height), str(rate)])):
        if not shutil.which(tool):
            continue
        for line in _run([tool] + args).splitlines():
            line = line.strip()
            if line.startswith("Modeline"):
                parts = line.split()
                return parts[1].strip('"'), parts[2:]
    raise OSError("cvt/gtf ontbreekt om een eigen resolutie te maken (sudo apt install xserver-xorg-core)")


def force_output(name, width, height, rate=60, position="right"):
    """Switch the output on with the given mode, also when no screen is detected on it.
    Does nothing when it already runs at that resolution. Returns True when something changed."""
    _check_x11()
    outputs = display_outputs()
    out = next((o for o in outputs if o["name"] == name), None)
    if out is None:
        raise OSError(f"uitgang {name} bestaat niet op deze computer")
    if out["active"] and (out["width"], out["height"]) == (width, height):
        return False
    limit = card_capacity().get(out["card"])
    in_use = sum(1 for o in outputs if o["active"] and o["card"] == out["card"] and o["name"] != name)
    if limit and not out["active"] and in_use >= limit:
        raise OSError(f"de videokaart ({out['card']}) kan maar {limit} schermen tegelijk aansturen en die zijn al in "
                      "gebruik; zet eerst een andere uitgang van die kaart uit, of gebruik een uitgang van een "
                      "andere videokaart")
    others = [o for o in outputs if o["active"] and o["name"] != name]
    if position == "same":
        anchor = next((o for o in others if o["primary"]), None) or next(iter(others), None)
    elif position == "left":  # beside the outermost screen, so that it never lands on top of another one
        anchor = min(others, key=lambda o: o["x"], default=None)
    else:
        anchor = max(others, key=lambda o: o["x"] + o["width"], default=None)

    mode = next((m for m in out["modes"] if (m[0], m[1]) == (width, height)), None)
    args = ["xrandr", "--output", name]
    if mode is not None:
        args += ["--mode", f"{width}x{height}"]
        if rate and any(abs(r - rate) < 0.6 for r in mode[2]):
            args += ["--rate", str(rate)]
    else:
        mode_name, timings = _modeline(width, height, rate or 60)
        try:
            _run(["xrandr", "--newmode", mode_name] + timings)
        except OSError:
            pass  # already created earlier in this session
        _run(["xrandr", "--addmode", name, mode_name])
        args += ["--mode", mode_name]
    if anchor is not None:
        args += [{"left": "--left-of", "same": "--same-as"}.get(position, "--right-of"), anchor["name"]]
    _run(args)
    return True


def open_display_settings():
    """Open the display settings of the desktop that is running, or arandr.
    Only the running desktop's own tool: Cinnamon's and GNOME's panels get the screens from their window
    manager, so in the CinemaViewer kiosk session (Openbox) they open empty."""
    desktop = [part for part in os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":") if part]
    candidates = [
        ("cinnamon", ["cinnamon-settings", "display"]),
        ("gnome", ["gnome-control-center", "display"]),
        ("kde", ["systemsettings", "kcm_kscreen"]),
        ("xfce", ["xfce4-display-settings"]),
        ("mate", ["mate-display-properties"]),
        ("lxqt", ["lxqt-config-monitor"]),
    ]
    commands = [cmd for key, cmd in candidates if key in desktop] + [["arandr"]]
    for cmd in commands:
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
    raise OSError("in de kiosk is er geen bureaublad met scherminstellingen. Zet uitgangen aan onder "
                  "Instellingen → Uitgangen, of kies ‘Naar admin omgeving’ en open daar de scherminstellingen")


# -- audio: HDMI profiles --------------------------------------------------------------
def hdmi_audio_profiles():
    """HDMI/DisplayPort profiles of the sound cards, also those marked unavailable (no ELD from
    the extender): [(card, profile, description)]."""
    return [(card, profile, desc) for card, profile, desc, _available in _hdmi_profiles()]


def available_hdmi_audio():
    """{(card, profile): description} of the HDMI/DisplayPort outputs where a device that takes sound is
    connected right now (it passed its ELD: a receiver, TV or projector with audio)."""
    return {(card, profile): desc for card, profile, desc, available in _hdmi_profiles() if available}


def _hdmi_profiles():
    if not shutil.which("pactl"):
        return []
    try:
        text = _run(["pactl", "list", "cards"])
    except OSError:
        return []
    out = []
    card = desc = None
    in_profiles = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Name: "):
            card, desc, in_profiles = stripped[6:], None, False
        elif stripped.startswith("device.description = ") and desc is None:
            desc = stripped.split("=", 1)[1].strip().strip('"')
        elif stripped == "Profiles:":
            in_profiles = True
        elif in_profiles and line.startswith("\t\t") and card:
            profile, _, rest = stripped.partition(": ")
            name = rest.rsplit(" (sinks:", 1)[0]
            if profile.startswith("output:") and "+input" not in profile and \
                    ("hdmi" in profile or "displayport" in profile.lower()):
                out.append((card, profile, f"{desc or card} – {name}", "available: no" not in rest))
        elif in_profiles and not line.startswith("\t\t"):
            in_profiles = False
    return out


_UPMIX_CONF = """# Written by CinemaViewer (Instellingen → Apparaten → alle luidsprekers): stereo is spread over
# every channel of a surround output (centre, surround, LFE) instead of only front left/right.
stream.properties = {
    channelmix.upmix        = true
    channelmix.upmix-method = psd
    channelmix.lfe-cutoff   = 150
    channelmix.fc-cutoff    = 12000
    channelmix.rear-delay   = 12.0
}
"""


def set_audio_upmix(on):
    """Stereo over all surround channels (PipeWire's own default only fills front left/right). PipeWire reads
    this when it starts, so it is restarted once when the setting changes. Returns True when it changed."""
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "pipewire"
    paths = [root / "pipewire-pulse.conf.d" / "90-cinemaviewer-upmix.conf",
             root / "client.conf.d" / "90-cinemaviewer-upmix.conf"]
    changed = False
    for path in paths:
        try:
            if on and (not path.exists() or path.read_text() != _UPMIX_CONF):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_UPMIX_CONF)
                changed = True
            elif not on and path.exists():
                path.unlink()
                changed = True
        except OSError:
            pass
    if changed and shutil.which("systemctl"):
        try:
            _run(["systemctl", "--user", "restart", "pipewire-pulse.service"], timeout=20)
        except OSError:
            pass
    return changed


def route_all_audio(sink):
    """Make the sink the system's default and move everything that plays now (browser, music analysis,
    other programs) to it."""
    _run(["pactl", "set-default-sink", sink])
    for line in _run(["pactl", "list", "short", "sink-inputs"]).splitlines():
        stream = line.split("\t", 1)[0].strip()
        if stream.isdigit():
            try:
                _run(["pactl", "move-sink-input", stream, sink])
            except OSError:
                pass  # a stream that just ended


def play_test_tone(device="", card="", profile=""):
    """Two seconds of a tone (left, then right) on a sound output, to hear whether it works. With card and
    profile that HDMI profile is switched on first. Returns the sink it played on."""
    import math, struct, tempfile, wave
    if not shutil.which("paplay"):
        raise OSError("paplay ontbreekt (pulseaudio-utils)")
    sink = force_audio_profile(card, profile) if card and profile else device
    handle, path = tempfile.mkstemp(suffix=".wav", prefix="cinemaviewer-tone-")
    os.close(handle)
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            frames = bytearray()
            for i in range(48000 * 2):
                v = int(9000 * math.sin(2 * math.pi * 440 * i / 48000))
                frames += struct.pack("<hh", v if i < 48000 else 0, v if i >= 48000 else 0)
            w.writeframes(bytes(frames))
        _run(["paplay"] + (["--device", sink] if sink else []) + [path], timeout=10)
    finally:
        os.unlink(path)
    return sink or "standaarduitgang"


def force_audio_profile(card, profile):
    """Activate the card profile and return the name of its sink (the VLC audio device)."""
    _run(["pactl", "set-card-profile", card, profile])
    prefix = card.replace("alsa_card.", "alsa_output.", 1) + "."
    sinks = [line.split("\t")[1] for line in _run(["pactl", "list", "short", "sinks"]).splitlines()
             if "\t" in line]
    wanted = profile.split(":", 1)[1].split("+", 1)[0]  # e.g. hdmi-stereo-extra1
    for sink in sinks:
        if sink == prefix + wanted:
            return sink
    return next((s for s in sinks if s.startswith(prefix)), "")
