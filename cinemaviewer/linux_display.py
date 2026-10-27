"""Linux: force video outputs with xrandr (X11) and HDMI audio profiles with pactl.

Extenders such as HDBaseT often pass no (or a broken) EDID/ELD: X then reports the output as
disconnected or without modes, and PipeWire/PulseAudio hides the HDMI sound output. Both can be
switched on by hand with a known resolution and audio profile.
"""

import os
import re
import shutil
import subprocess

POSITIONS = {"right": "Rechts van hoofdscherm", "left": "Links van hoofdscherm", "same": "Spiegelen (kopie hoofdscherm)"}

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


def display_outputs():
    """All video outputs, also disconnected ones: [{name, connected, primary, active, width, height,
    modes: [(w, h, [rates])]}]."""
    try:
        _check_x11()
        text = _run(["xrandr", "--query"])
    except OSError:
        return []
    outputs = []
    for line in text.splitlines():
        m = _OUTPUT_RE.match(line)
        if m:
            outputs.append({"name": m.group(1), "connected": m.group(2) == "connected", "primary": bool(m.group(3)),
                            "active": m.group(4) is not None, "width": int(m.group(4) or 0),
                            "height": int(m.group(5) or 0), "modes": []})
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
    primary = next((o for o in outputs if o["primary"] and o["active"] and o["name"] != name), None) or \
        next((o for o in outputs if o["active"] and o["name"] != name), None)

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
    if primary is not None:
        args += [{"left": "--left-of", "same": "--same-as"}.get(position, "--right-of"), primary["name"]]
    _run(args)
    return True


def open_display_settings():
    """Open the display settings of the desktop that is running (or arandr as fallback)."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    candidates = [
        ("cinnamon-settings", ["cinnamon-settings", "display"]),
        ("gnome-control-center", ["gnome-control-center", "display"]),
        ("systemsettings", ["systemsettings", "kcm_kscreen"]),
        ("xfce4-display-settings", ["xfce4-display-settings"]),
        ("mate-display-properties", ["mate-display-properties"]),
        ("lxqt-config-monitor", ["lxqt-config-monitor"]),
        ("arandr", ["arandr"]),
    ]
    # the tool of the running desktop first
    candidates.sort(key=lambda c: 0 if any(part in c[0] for part in desktop.split(":") if part) else 1)
    for exe, cmd in candidates:
        if shutil.which(exe):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
    raise OSError("geen programma voor beeldscherminstellingen gevonden (installeer bv. arandr)")


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
