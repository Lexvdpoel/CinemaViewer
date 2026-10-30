"""Windows: force a resolution/refresh rate on a display output and attach it to the desktop.

Windows only drives outputs where it sees a monitor (EDID); an output without a monitor cannot
be switched on by a program. For an extender that passes the EDID late, forcing the mode again
after it appears is enough.
"""

import ctypes
import subprocess
from ctypes import wintypes

POSITIONS = {"right": "Rechts van hoofdscherm", "left": "Links van hoofdscherm"}

_user32 = ctypes.windll.user32

DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4
DISPLAY_DEVICE_MIRRORING_DRIVER = 0x8
ENUM_CURRENT_SETTINGS = -1
DM_POSITION = 0x20
DM_PELSWIDTH = 0x80000
DM_PELSHEIGHT = 0x100000
DM_DISPLAYFREQUENCY = 0x400000
CDS_UPDATEREGISTRY = 0x1
CDS_NORESET = 0x10000000
_ERRORS = {1: "herstart van de computer nodig", -1: "de videokaart weigert deze instelling",
           -2: "deze resolutie/verversing wordt niet ondersteund", -3: "kon de instelling niet opslaan",
           -4: "ongeldige opties", -5: "ongeldige parameter", -6: "niet mogelijk in deze schermopstelling"}


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD), ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


_user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DISPLAY_DEVICEW),
                                        wintypes.DWORD]
_user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
_user32.ChangeDisplaySettingsExW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(DEVMODEW), wintypes.HWND,
                                             wintypes.DWORD, wintypes.LPVOID]
_user32.ChangeDisplaySettingsExW.restype = wintypes.LONG


def _devmode():
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    return dm


def _current(name):
    dm = _devmode()
    if _user32.EnumDisplaySettingsW(name, wintypes.DWORD(ENUM_CURRENT_SETTINGS & 0xFFFFFFFF), ctypes.byref(dm)):
        return dm
    return None


def display_outputs():
    """All outputs of the video cards: [{name, label, connected, primary, active, width, height, x, y,
    modes: [(w, h, [rates])]}]. name is what Qt calls the screen (e.g. \\\\.\\DISPLAY2)."""
    outputs = []
    i = 0
    while True:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not _user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if dd.StateFlags & DISPLAY_DEVICE_MIRRORING_DRIVER:
            continue
        mon = DISPLAY_DEVICEW()
        mon.cb = ctypes.sizeof(mon)
        has_monitor = bool(_user32.EnumDisplayDevicesW(dd.DeviceName, 0, ctypes.byref(mon), 0))
        active = bool(dd.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP)
        cur = _current(dd.DeviceName) if active else None
        modes = {}
        j = 0
        while True:
            dm = _devmode()
            if not _user32.EnumDisplaySettingsW(dd.DeviceName, j, ctypes.byref(dm)):
                break
            j += 1
            rates = modes.setdefault((dm.dmPelsWidth, dm.dmPelsHeight), [])
            if dm.dmDisplayFrequency > 1 and dm.dmDisplayFrequency not in rates:
                rates.append(dm.dmDisplayFrequency)
        outputs.append({
            "name": dd.DeviceName,
            "label": " · ".join(x for x in (mon.DeviceString if has_monitor else "geen scherm",
                                            dd.DeviceString) if x),
            "card": dd.DeviceString,
            "connected": has_monitor, "primary": bool(dd.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE),
            "active": active, "width": cur.dmPelsWidth if cur else 0, "height": cur.dmPelsHeight if cur else 0,
            "x": cur.dmPositionX if cur else 0, "y": cur.dmPositionY if cur else 0,
            "modes": sorted(((w, h, sorted(r, reverse=True)) for (w, h), r in modes.items()), reverse=True),
        })
    return outputs


def force_output(name, width, height, rate=60, position="right"):
    """Attach the output to the desktop with the given mode. Returns True when something changed."""
    outputs = display_outputs()
    out = next((o for o in outputs if o["name"].lower() == name.lower()), None)
    if out is None:
        raise OSError(f"uitgang {name} bestaat niet op deze computer")
    if out["active"] and (out["width"], out["height"]) == (width, height):
        return False
    others = [o for o in outputs if o["active"] and o["name"] != out["name"]]
    if position == "left":
        x = min((o["x"] for o in others), default=0) - width
    else:
        x = max((o["x"] + o["width"] for o in others), default=0)
    dm = _devmode()
    dm.dmPelsWidth, dm.dmPelsHeight = width, height
    dm.dmPositionX, dm.dmPositionY = x, 0
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_POSITION
    if rate:
        dm.dmDisplayFrequency = int(round(rate))
        dm.dmFields |= DM_DISPLAYFREQUENCY
    code = _user32.ChangeDisplaySettingsExW(out["name"], ctypes.byref(dm), None,
                                            CDS_UPDATEREGISTRY | CDS_NORESET, None)
    if code == -2 and rate:  # the rate is not offered: let Windows pick one for this resolution
        dm.dmFields &= ~DM_DISPLAYFREQUENCY
        code = _user32.ChangeDisplaySettingsExW(out["name"], ctypes.byref(dm), None,
                                                CDS_UPDATEREGISTRY | CDS_NORESET, None)
    if code != 0:
        if not out["connected"]:
            raise OSError(f"Windows ziet geen scherm op {name} (geen EDID); controleer kabel en extender")
        raise OSError(f"{name}: {_ERRORS.get(code, f'code {code}')}")
    _user32.ChangeDisplaySettingsExW(None, None, None, 0, None)  # apply all pending changes at once
    return True


def link_graphics_cards():
    pass  # Windows uses every video card by itself


def restore_desktop_layout(force=False):
    return ""  # Windows keeps its own layout


def available_hdmi_audio():
    return {}  # Windows switches to a new HDMI audio device itself when it is the default


def route_all_audio(sink):
    pass


def follow_preferred_modes(skip=()):
    return []  # Windows picks the new preferred mode itself


def set_audio_upmix(on):
    return False  # Windows: speaker fill is a setting of the sound card


def diagnosis():
    return "Diagnose is er alleen onder Linux."


def play_test_tone(device="", card="", profile=""):
    import winsound
    winsound.Beep(440, 1500)  # the default output; VLC's choice is only used by films
    return "standaarduitgang"


def output_states():
    return {}


def watch_outputs(callback):
    return False  # Windows lays out new screens itself


def refresh_output(name):
    pass


def card_capacity():
    return {}  # Windows lists only the outputs it can drive


def open_display_settings():
    subprocess.Popen(["explorer.exe", "ms-settings:display"])


def hdmi_audio_profiles():
    return []  # Windows shows every HDMI sound output as its own device


def force_audio_profile(card, profile):
    raise OSError("geluidsprofielen forceren kan alleen onder Linux")
