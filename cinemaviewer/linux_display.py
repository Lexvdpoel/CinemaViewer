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
                out.append((card, profile, f"{desc or card} – {name}"))
        elif in_profiles and not line.startswith("\t\t"):
            in_profiles = False
    return out


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
