"""Windows integration: start at sign-in and shutting down the computer."""

import base64
import ctypes
import os
import subprocess
import sys
import threading
import time
import winreg
from ctypes import wintypes
from pathlib import Path

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.CallNextHookEx.restype = wintypes.LPARAM
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "CinemaViewer"
AUTOSTART_ARG = "--autostart"  # marks a start by Windows at sign-in
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / "CinemaViewer.pyw"


def python_gui_exe():
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    return pythonw if pythonw.exists() else exe


def _startup_command():
    return f'"{python_gui_exe()}" "{LAUNCHER}" {AUTOSTART_ARG}'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def set_autostart(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


# -- CinemaViewer as the Windows shell (instead of Explorer) ------------------------
# Windows starts the per-user 'Shell' value at sign-in instead of explorer.exe, so there is no desktop,
# taskbar or Start menu at all. The shell process (cinemaviewer.shell) starts and guards CinemaViewer.
WINLOGON_USER_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
SHELL_ARG = "--shell"


def _shell_command():
    return f'"{python_gui_exe()}" "{LAUNCHER}" {SHELL_ARG}'


def _user_shell():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINLOGON_USER_KEY) as key:
            return winreg.QueryValueEx(key, "Shell")[0]
    except OSError:
        return None


def _set_user_shell(command):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINLOGON_USER_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if command:
            winreg.SetValueEx(key, "Shell", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "Shell")
            except FileNotFoundError:
                pass


def shell_enabled():
    value = _user_shell() or ""
    return SHELL_ARG in value and "CinemaViewer" in value


def set_shell(enabled):
    """Per user, so no admin rights; takes effect at the next sign-in."""
    _set_user_shell(_shell_command() if enabled else None)


def explorer_running():
    return bool(_user32.FindWindowW("Shell_TrayWnd", None))


def start_desktop(timeout=20):
    """Start Explorer as the real desktop (taskbar, Start menu). Explorer only becomes the shell when
    the Shell value names it, otherwise it just opens a folder window; so point the value at Explorer
    while it starts and put CinemaViewer back afterwards (for the next sign-in)."""
    if explorer_running():
        return True
    own = _user_shell()
    _set_user_shell("explorer.exe")
    try:
        subprocess.Popen([str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe")])
        deadline = time.monotonic() + timeout
        while not explorer_running() and time.monotonic() < deadline:
            time.sleep(0.25)
        time.sleep(2)  # Explorer reads the value while it initialises, not only before the taskbar shows
    finally:
        _set_user_shell(own)
    return explorer_running()


# Ctrl+Alt+Del itself cannot be intercepted by any program (Windows' secure attention sequence).
# What we can do is hide the options on that screen through the per-user policies that
# Group Policy would set ("Ctrl+Alt+Del Options"). Ctrl+Shift+Esc is blocked by DisableTaskMgr too.
_POLICY_SYSTEM = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
_POLICY_EXPLORER = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
KIOSK_POLICIES = [
    (_POLICY_SYSTEM, "DisableTaskMgr"),          # Taakbeheer
    (_POLICY_SYSTEM, "DisableLockWorkstation"),  # Vergrendelen
    (_POLICY_SYSTEM, "DisableChangePassword"),   # Wachtwoord wijzigen
    (_POLICY_EXPLORER, "NoLogoff"),              # Afmelden
]


def kiosk_writable():
    """True when this (non-elevated) user may change the policy values."""
    for path in {p for p, _n in KIOSK_POLICIES}:
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE).Close()
        except OSError:
            return False
    return True


def _current_user_sid():
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         timeout=10, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    return out.strip().split(",")[-1].strip('"')


def grant_rights_script(hive_root):
    """PowerShell that creates the policy keys under `hive_root` and lets the user's SID change them."""
    lines = ["$ErrorActionPreference = 'Stop'"]
    for path in sorted({p for p, _n in KIOSK_POLICIES}):
        key = f"Registry::{hive_root}\\{path}"
        lines += [
            f"if (-not (Test-Path '{key}')) {{ New-Item -Path '{key}' -Force | Out-Null }}",
            f"$acl = Get-Acl '{key}'",
            "$rule = New-Object System.Security.AccessControl.RegistryAccessRule($sid, 'ReadKey,SetValue', 'Allow')",
            "$acl.AddAccessRule($rule)",
            f"Set-Acl -Path '{key}' -AclObject $acl",
        ]
    return "\n".join(lines)


def run_elevated(script):
    """Run a PowerShell script with administrator rights (one UAC prompt). Returns the exit code;
    1223 means the user declined the prompt."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    launcher = ("try { $p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
                f"-ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','{encoded}'; exit $p.ExitCode }} "
                "catch { exit 1223 }")
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", launcher],
                            capture_output=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode


def grant_kiosk_rights():
    """One-time elevated step (UAC prompt). Returns True on success."""
    sid = _current_user_sid()
    script = f"$sid = New-Object System.Security.Principal.SecurityIdentifier('{sid}')\n" + \
        grant_rights_script(f"HKEY_USERS\\{sid}")
    return run_elevated(script) == 0 and kiosk_writable()


# -- Windows sign-in: automatic logon and lock screen image ------------------------
_WINLOGON = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
_CSP = r"SOFTWARE\Microsoft\Windows\CurrentVersion\PersonalizationCSP"


def _hklm_get(path, name, default=None):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def logon_account():
    """(user name, domain, label) as Winlogon needs them for the signed-in user.
    Microsoft accounts sign in with their e-mail address under the domain 'MicrosoftAccount'."""
    import os
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\IdentityCRL\UserExtendedProperties") as key:
            email = winreg.EnumKey(key, 0)
        if "@" in email:
            return email, "MicrosoftAccount", f"{os.environ.get('USERNAME', '')} ({email})"
    except OSError:
        pass
    user, domain = os.environ.get("USERNAME", ""), os.environ.get("COMPUTERNAME", ".")
    return user, domain, user


def autologon_enabled():
    return str(_hklm_get(_WINLOGON, "AutoAdminLogon", "0")) == "1"


def autologon_user():
    return _hklm_get(_WINLOGON, "DefaultUserName", "")


def lockscreen_enabled(image_path):
    return (_hklm_get(_CSP, "LockScreenImageStatus", 0) == 1
            and str(_hklm_get(_CSP, "LockScreenImagePath", "")).lower() == str(image_path).lower())


def check_windows_password(user, domain, password):
    """True when Windows accepts the password (so autologon won't get stuck at boot)."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.LogonUserW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                  wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    import os
    attempts = [(user, domain), (user, None), (os.environ.get("USERNAME", ""), ".")]
    for name, dom in attempts:
        token = wintypes.HANDLE()
        if advapi.LogonUserW(name, dom, password, 2, 0, ctypes.byref(token)):  # interactive, default provider
            _kernel32.CloseHandle(token)
            return True
    return False


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def protect_for_user(text):
    """Encrypt with DPAPI for this Windows user: only this user on this PC can decrypt it.
    Used to hand the password to the elevated script without putting it in plain text anywhere."""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    raw = text.encode("utf-16-le")
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "versleutelen mislukt")
    try:
        return base64.b64encode(ctypes.string_at(blob_out.pbData, blob_out.cbData)).decode("ascii")
    finally:
        _kernel32.LocalFree(blob_out.pbData)


# Stores/clears the 'DefaultPassword' LSA secret, exactly like Sysinternals Autologon does.
_LSA_CSHARP = r'''
using System; using System.Runtime.InteropServices;
public static class CvLsa {
  [StructLayout(LayoutKind.Sequential)] struct US { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
  [StructLayout(LayoutKind.Sequential)] struct OA { public int Length; public IntPtr Root; public IntPtr Name; public uint Attr; public IntPtr Sd; public IntPtr Qos; }
  [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr sys, ref OA oa, uint access, out IntPtr h);
  [DllImport("advapi32.dll")] static extern uint LsaStorePrivateData(IntPtr h, ref US key, IntPtr data);
  [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr h);
  [DllImport("advapi32.dll")] static extern int LsaNtStatusToWinError(uint s);
  static US Str(string s) { US u = new US(); u.Buffer = Marshal.StringToHGlobalUni(s); u.Length = (ushort)(s.Length * 2); u.MaximumLength = (ushort)(s.Length * 2 + 2); return u; }
  public static void Store(string name, string value) {
    OA oa = new OA(); oa.Length = Marshal.SizeOf(typeof(OA)); IntPtr pol;
    uint st = LsaOpenPolicy(IntPtr.Zero, ref oa, 0x000F0FFF, out pol);
    if (st != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(st));
    try {
      US key = Str(name); IntPtr data = IntPtr.Zero;
      if (value != null) { US v = Str(value); data = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(US))); Marshal.StructureToPtr(v, data, false); }
      st = LsaStorePrivateData(pol, ref key, data);
      if (st != 0 && value != null) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(st));
    } finally { LsaClose(pol); }
  }
}
'''


def sign_in_script(autologon=None, user="", domain="", password_blob="", lockscreen=None, image_path="",
                   hklm="HKLM:"):
    """PowerShell (run elevated) that switches autologon and/or the lock screen image.
    None = leave unchanged. `hklm` can point elsewhere for testing."""
    def q(text):  # PowerShell single-quoted string content
        return str(text).replace("'", "''")

    user, domain, image_path, password_blob = q(user), q(domain), q(image_path), q(password_blob)
    winlogon = f"{hklm}\\{_WINLOGON}"
    csp = f"{hklm}\\{_CSP}"
    lines = ["$ErrorActionPreference = 'Stop'"]
    if autologon is not None:
        lines.append("Add-Type -TypeDefinition @'\n" + _LSA_CSHARP + "\n'@")
        lines.append(f"if (-not (Test-Path '{winlogon}')) {{ New-Item -Path '{winlogon}' -Force | Out-Null }}")
        lines.append(f"Remove-ItemProperty -Path '{winlogon}' -Name DefaultPassword,AutoLogonCount "
                     "-ErrorAction SilentlyContinue")  # never keep a plain-text password
    if autologon:
        lines += [
            "Add-Type -AssemblyName System.Security",
            "$pw = [Text.Encoding]::Unicode.GetString([Security.Cryptography.ProtectedData]::Unprotect("
            f"[Convert]::FromBase64String('{password_blob}'), $null, 'CurrentUser'))",
            "[CvLsa]::Store('DefaultPassword', $pw)",
            "Remove-Variable pw",
            f"Set-ItemProperty -Path '{winlogon}' -Name DefaultUserName -Value '{user}'",
            f"Set-ItemProperty -Path '{winlogon}' -Name DefaultDomainName -Value '{domain}'",
            f"Set-ItemProperty -Path '{winlogon}' -Name AutoAdminLogon -Value '1'",
        ]
    elif autologon is False:
        lines += [
            f"Set-ItemProperty -Path '{winlogon}' -Name AutoAdminLogon -Value '0'",
            "[CvLsa]::Store('DefaultPassword', $null)",
        ]
    if lockscreen is not None:
        lines.append(f"if (-not (Test-Path '{csp}')) {{ New-Item -Path '{csp}' -Force | Out-Null }}")
    if lockscreen:
        lines += [
            f"Set-ItemProperty -Path '{csp}' -Name LockScreenImagePath -Value '{image_path}'",
            f"Set-ItemProperty -Path '{csp}' -Name LockScreenImageUrl -Value '{image_path}'",
            f"New-ItemProperty -Path '{csp}' -Name LockScreenImageStatus -Value 1 -PropertyType DWord -Force | Out-Null",
        ]
    elif lockscreen is False:
        lines.append(f"Remove-ItemProperty -Path '{csp}' -Name LockScreenImagePath,LockScreenImageUrl,"
                     "LockScreenImageStatus -ErrorAction SilentlyContinue")
    lines.append("exit 0")
    return "\n".join(lines)


def _policy_set(path, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)[0] == 1
    except OSError:
        return False


def apply_kiosk_policies():
    """Turn the restrictions on; returns the names we set (so we only remove our own later)."""
    applied = []
    for path, name in KIOSK_POLICIES:
        if _policy_set(path, name):
            continue  # already set by an administrator: leave it alone
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
        applied.append(name)
    return applied


def remove_kiosk_policies(names):
    for path, name in KIOSK_POLICIES:
        if name not in names:
            continue
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


class _HookThread(threading.Thread):
    """Runs a session-wide low-level input hook on its own thread with a message loop."""

    HOOK_ID = None
    WM_QUIT = 0x0012

    def __init__(self, name):
        super().__init__(daemon=True, name=name)
        self._thread_id = None
        self._ready = threading.Event()
        self.installed = False
        self.blocked = 0
        self._proc = _HOOKPROC(self._callback)  # keep a reference for the lifetime of the hook

    def _callback(self, n_code, w_param, l_param):
        raise NotImplementedError

    def run(self):
        self._thread_id = _kernel32.GetCurrentThreadId()
        hook = _user32.SetWindowsHookExW(self.HOOK_ID, self._proc, _kernel32.GetModuleHandleW(None), 0)
        self.installed = bool(hook)
        self._ready.set()
        if not hook:
            return
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:  # hooks need a message loop
            pass
        _user32.UnhookWindowsHookEx(hook)

    def start(self):
        super().start()
        self._ready.wait(2)
        return self.installed

    def stop(self):
        if self._thread_id and self.is_alive():
            _user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
            self.join(2)


class KeyboardBlocker(_HookThread):
    """Low-level keyboard hook that swallows the Windows key, Alt+Tab, Alt+Esc, Ctrl+Esc and
    Ctrl+Shift+Esc for the whole session while CinemaViewer runs. (Ctrl+Alt+Del and Win+L are
    handled by Windows itself and cannot be hooked.)"""

    HOOK_ID = 13  # WH_KEYBOARD_LL
    LLKHF_ALTDOWN = 0x20
    VK_TAB, VK_ESCAPE, VK_CONTROL, VK_LWIN, VK_RWIN, VK_APPS = 0x09, 0x1B, 0x11, 0x5B, 0x5C, 0x5D

    def __init__(self):
        super().__init__("CinemaViewer-keyboard")

    @classmethod
    def should_block(cls, vk, alt_down, ctrl_down):
        if vk in (cls.VK_LWIN, cls.VK_RWIN):
            return True
        if alt_down and vk in (cls.VK_TAB, cls.VK_ESCAPE):
            return True
        return ctrl_down and vk == cls.VK_ESCAPE    # Ctrl+Esc (Start) and Ctrl+Shift+Esc (Taakbeheer)

    def _callback(self, n_code, w_param, l_param):
        if n_code == 0:
            kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            ctrl = bool(_user32.GetAsyncKeyState(self.VK_CONTROL) & 0x8000)
            if self.should_block(kb.vkCode, bool(kb.flags & self.LLKHF_ALTDOWN), ctrl):
                self.blocked += 1
                return 1
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)


class MouseBlocker(_HookThread):
    """Low-level mouse hook that swallows all mouse input (movement, clicks, wheel). Used while
    CinemaViewer starts with Windows; it lets go by itself after `timeout` seconds so a hanging
    start can never leave the computer without a mouse."""

    HOOK_ID = 14  # WH_MOUSE_LL

    def __init__(self, timeout=90):
        super().__init__("CinemaViewer-mouse")
        self._failsafe = threading.Timer(timeout, self.stop)
        self._failsafe.daemon = True

    def _callback(self, n_code, w_param, l_param):
        if n_code == 0:
            self.blocked += 1
            return 1
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def start(self):
        if super().start():
            self._failsafe.start()
        return self.installed

    def stop(self):
        self._failsafe.cancel()
        super().stop()


_startup_mouse_blocker = None


def block_mouse_during_autostart(argv):
    """Block the mouse when Windows started CinemaViewer (sign-in), until release_startup_mouse().
    Called before the heavy imports so the block begins as early as possible."""
    global _startup_mouse_blocker
    if AUTOSTART_ARG not in argv or not (autostart_enabled() or shell_enabled()):
        return
    _startup_mouse_blocker = MouseBlocker()
    _startup_mouse_blocker.start()


def release_startup_mouse():
    global _startup_mouse_blocker
    if _startup_mouse_blocker is not None:
        _startup_mouse_blocker.stop()
        _startup_mouse_blocker = None


# -- desktop wallpaper ---------------------------------------------------------
SPI_SETDESKWALLPAPER = 0x0014
SPI_GETDESKWALLPAPER = 0x0073
SPIF_UPDATE_AND_BROADCAST = 0x01 | 0x02
_DESKTOP_KEY = r"Control Panel\Desktop"
_WALLPAPERS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Wallpapers"
BACKGROUND_TYPES = {0: "afbeelding", 1: "effen kleur", 2: "diavoorstelling", 3: "Windows Spotlight"}

_user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
_user32.SystemParametersInfoW.restype = wintypes.BOOL


def _reg_get(path, name, default=None):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def current_wallpaper():
    """What the desktop shows now, so it can be put back later."""
    buf = ctypes.create_unicode_buffer(1024)
    _user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, len(buf), buf, 0)
    return {
        "path": _reg_get(_DESKTOP_KEY, "WallPaper", buf.value) or buf.value,
        "style": _reg_get(_DESKTOP_KEY, "WallpaperStyle", "10"),
        "tile": _reg_get(_DESKTOP_KEY, "TileWallpaper", "0"),
        "type": _reg_get(_WALLPAPERS_KEY, "BackgroundType", 0),
    }


def set_wallpaper(path, style="10", tile="0"):
    """Set a picture as desktop background ('10' = fill, scaled to every monitor). No admin needed."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _DESKTOP_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, str(style))
        winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, str(tile))
    if not _user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, ctypes.c_wchar_p(str(path)),
                                         SPIF_UPDATE_AND_BROADCAST):
        raise OSError(ctypes.get_last_error(), "Windows weigerde de achtergrond")


# -- taskbar ---------------------------------------------------------------------
ABM_GETSTATE, ABM_SETSTATE = 0x04, 0x0A
ABS_AUTOHIDE = 0x01
SW_HIDE, SW_SHOW = 0, 5
_TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")  # main + taskbars on other monitors


class _APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]


_shell32 = ctypes.WinDLL("shell32")
_shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(_APPBARDATA)]
_shell32.SHAppBarMessage.restype = ctypes.c_size_t
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def _appbar(message, state=0):
    abd = _APPBARDATA(cbSize=ctypes.sizeof(_APPBARDATA), hWnd=_user32.FindWindowW("Shell_TrayWnd", None),
                      lParam=state)
    return _shell32.SHAppBarMessage(message, ctypes.byref(abd))


def taskbar_autohide():
    return bool(_appbar(ABM_GETSTATE) & ABS_AUTOHIDE)


_STUCKRECTS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StuckRects3"


def set_taskbar_autohide(enabled):
    """Windows' own 'automatically hide the taskbar'. Applied live via the shell, and also written to
    the setting Explorer reads at sign-in (it only saves it itself later), so it holds at the next boot."""
    state = _appbar(ABM_GETSTATE)
    _appbar(ABM_SETSTATE, (state | ABS_AUTOHIDE) if enabled else (state & ~ABS_AUTOHIDE))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STUCKRECTS, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            data = bytearray(winreg.QueryValueEx(key, "Settings")[0])
            if len(data) > 8:
                data[8] = (data[8] | 0x01) if enabled else (data[8] & ~0x01)  # bit 0 = auto-hide
                winreg.SetValueEx(key, "Settings", 0, winreg.REG_BINARY, bytes(data))
    except OSError:
        pass  # the live change above still works


def _taskbar_windows():
    found = []

    def collect(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, name, 64)
        if name.value in _TASKBAR_CLASSES:
            found.append(hwnd)
        return True

    _user32.EnumWindows(_WNDENUMPROC(collect), 0)
    return found


def show_taskbars(visible):
    """Hide/show the taskbar on every monitor (only while CinemaViewer runs; Explorer shows it again
    after a restart of Windows Explorer or a sign-in)."""
    for hwnd in _taskbar_windows():
        _user32.ShowWindow(hwnd, SW_SHOW if visible else SW_HIDE)


def taskbars_visible():
    return [bool(_user32.IsWindowVisible(h)) for h in _taskbar_windows()]


def open_background_settings():
    subprocess.Popen(["explorer.exe", "ms-settings:personalization-background"])


def shutdown_computer():
    """Ask Windows to power off; raises RuntimeError when Windows refuses."""
    result = subprocess.run(["shutdown", "/s", "/t", "0"], capture_output=True, text=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"code {result.returncode}")
