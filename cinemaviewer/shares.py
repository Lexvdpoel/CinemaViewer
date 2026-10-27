"""Network shares (SMB / NFS) as media folders, set up under Instellingen → Lokaal, USB & disc.

Windows: an SMB share is used directly as \\\\server\\share; its user name and password go into the
Windows Credential Manager (cmdkey), never into config.json. NFS works the same way once the Windows
'Client for NFS' feature is installed.

Linux: a permanent mount in /etc/fstab (block between '# BEGIN CinemaViewer' and '# END CinemaViewer')
with systemd automount, so the share comes back by itself after a restart or a network hiccup. An SMB
password lives in /etc/cinemaviewer/shares/<name>.cred, readable by root only. Changing the shares asks
for the administrator password once (pkexec)."""

import getpass
import os
import re
import shlex
import subprocess

MOUNT_ROOT = "/mnt/cinemaviewer"
CRED_DIR = "/etc/cinemaviewer/shares"
TYPES = {"smb": "SMB / Windows-share", "nfs": "NFS"}


def slug(name):
    """Folder and file name for a share on Linux."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "share"


def describe(share):
    return f"{share['type']}://{share['server']}/{share['share'].strip('/')}"


def mount_point(share):
    """Linux: where the share is mounted."""
    return f"{MOUNT_ROOT}/{slug(share['name'])}"


def path(share):
    """Where the share's files are, as used for the media folder."""
    if os.name == "nt":
        return "\\\\" + share["server"] + "\\" + share["share"].strip("/\\").replace("/", "\\")
    return mount_point(share)


# -- Windows ----------------------------------------------------------------------------
def _run(args):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, capture_output=True, text=True, timeout=30, creationflags=flags)


def _apply_windows(old, new, passwords):
    for share in old:
        if not any(s["server"].lower() == share["server"].lower() for s in new):
            _run(["cmdkey", f"/delete:{share['server']}"])
    for share in new:
        password = passwords.get(share["name"])
        if share["type"] == "smb" and share.get("username") and password is not None:
            result = _run(["cmdkey", f"/add:{share['server']}", f"/user:{share['username']}", f"/pass:{password}"])
            if result.returncode != 0:
                return False, f"Kon de inloggegevens voor {share['server']} niet opslaan: {result.stdout.strip()}"
    return True, ""


# -- Linux ------------------------------------------------------------------------------
def _fstab_line(share, user):
    mount = mount_point(share)
    common = "nofail,_netdev,x-systemd.automount,x-systemd.mount-timeout=20,x-systemd.idle-timeout=0"
    if share["type"] == "nfs":
        remote = f"{share['server']}:/{share['share'].strip('/')}"
        return f"{_fstab_escape(remote)} {_fstab_escape(mount)} nfs {common} 0 0"
    remote = f"//{share['server']}/{share['share'].strip('/')}"
    auth = (f"credentials={CRED_DIR}/{slug(share['name'])}.cred" if share.get("username") else "guest")
    ids = f"uid={user},gid={user}" if user else ""
    return f"{_fstab_escape(remote)} {_fstab_escape(mount)} cifs {auth},{ids},iocharset=utf8,{common} 0 0"


def _fstab_escape(text):
    return text.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")


def linux_script(old, new, passwords, user=None):
    """Root shell script that brings fstab, credential files and mounts in line with `new`."""
    user = user or getpass.getuser()
    lines = ["set -e", f"mkdir -p {MOUNT_ROOT} {CRED_DIR}", f"chmod 700 {CRED_DIR}"]
    for share in old:  # unmount everything we set up before; mounts that stay come back below
        mp = shlex.quote(mount_point(share))
        lines.append(f"systemctl stop \"$(systemd-escape -p --suffix=automount {mp})\" 2>/dev/null || true")
        lines.append(f"umount -l {mp} 2>/dev/null || true")
        if not any(slug(s["name"]) == slug(share["name"]) for s in new):
            lines.append(f"rm -f {CRED_DIR}/{shlex.quote(slug(share['name']))}.cred")
            lines.append(f"rmdir {mp} 2>/dev/null || true")
    for share in new:
        lines.append(f"mkdir -p {shlex.quote(mount_point(share))}")
        password = passwords.get(share["name"])
        if share["type"] == "smb" and share.get("username") and password is not None:
            cred = f"{CRED_DIR}/{slug(share['name'])}.cred"
            content = f"username={share['username']}\npassword={password}\n"
            if share.get("domain"):
                content += f"domain={share['domain']}\n"
            lines.append(f"umask 077; printf %s {shlex.quote(content)} > {shlex.quote(cred)}")
    block = ["# BEGIN CinemaViewer (netwerkshares; beheerd door CinemaViewer)"]
    block += [_fstab_line(s, user) for s in new]
    block.append("# END CinemaViewer")
    lines += [
        "sed -i '/^# BEGIN CinemaViewer/,/^# END CinemaViewer/d' /etc/fstab",
        f"printf '%s\\n' {' '.join(shlex.quote(b) for b in block)} >> /etc/fstab",
        "systemctl daemon-reload",
        "systemctl restart remote-fs.target || true",
    ]
    lines += [f"systemctl start \"$(systemd-escape -p --suffix=automount {shlex.quote(mount_point(s))})\" || true"
              for s in new]
    lines.append("exit 0")
    return "\n".join(lines)


def apply(old, new, passwords):
    """Set up the shares in `new` (and remove those only in `old`). passwords: {name: password} for shares
    whose password was (re)entered. Returns (ok, message)."""
    if os.name == "nt":
        return _apply_windows(old, new, passwords)
    from .system import run_elevated
    code = run_elevated(linux_script(old, new, passwords))
    if code == 1223:
        return False, "Netwerkshares niet ingesteld: de beheerdersvraag is geannuleerd."
    if code != 0:
        return False, f"Netwerkshares instellen is mislukt (code {code})."
    return True, ""


def check(share):
    """Is the share reachable? Returns (ok, text). Can take a while: run it off the UI thread."""
    folder = path(share)
    try:
        entries = os.listdir(folder)
    except OSError as e:
        reason = e.strerror or str(e)
        if os.name == "nt" and share["type"] == "nfs":
            reason += " (is de Windows-functie ‘Client voor NFS’ geïnstalleerd?)"
        return False, f"{describe(share)} niet bereikbaar: {reason}"
    if os.name != "nt" and not entries and not os.path.ismount(folder):
        return False, f"{describe(share)} is (nog) niet gekoppeld."
    return True, f"{describe(share)}: verbonden, {len(entries)} items."


def mount_names(shares):
    """{path: name} so the sidebar shows the share's own name."""
    return {path(s): s["name"] for s in shares}

