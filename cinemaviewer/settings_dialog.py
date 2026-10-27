"""Settings: screens, Jellyfin, Plex and local sources."""

import copy
import os
import subprocess

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QColorDialog, QDialog, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import __version__, display, shares, updater
from .cast.manager import default_name as default_cast_name
from .devices import (DENON_INPUTS, INFOCUS_BAUDS, LAMP_MODES, LAMP_POWERS, DeviceError, InFocusProjector,
                      Projector, Receiver)
from .dmx import LAMP_TYPES, STATE_NAMES, serial_ports
from .dialogs import FramelessDialog, message, pick_folder
from .screens import IdentifyOverlay
from .security import check_password, hash_password
from .sources import JellyfinSource, PlexSource, radio
from .style import G_POWER, G_SETTINGS, TEXT, glyph_icon
from .system import (autologon_enabled, autologon_user, autostart_enabled, check_windows_password,
                     grant_kiosk_rights, kiosk_writable, lockscreen_enabled, logon_account, protect_for_user,
                     run_elevated, set_autostart, set_shell, shell_enabled, sign_in_script)
from .wallpaper import lockscreen_image_path
from .workers import run_async

MIN_PASSWORD_LENGTH = 4
COMMON_MODES = [(3840, 2160), (2560, 1440), (1920, 1200), (1920, 1080), (1680, 1050), (1600, 900), (1366, 768),
                (1280, 800), (1280, 720), (1024, 768)]


class PasswordDialog(FramelessDialog):
    """Asks for the settings password before the settings can be opened."""

    def __init__(self, stored_hash, parent=None):
        super().__init__(parent)
        self.stored_hash = stored_hash
        self.attempts = 0
        self.setWindowTitle("Instellingen vergrendeld")
        self.setFixedWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        title = QLabel("Instellingen vergrendeld")
        title.setObjectName("H1")
        lay.addWidget(title)
        lay.addWidget(_status("Voer het wachtwoord in om de instellingen te openen."))
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.Password)
        self.field.setPlaceholderText("Wachtwoord")
        self.field.returnPressed.connect(self._try)
        lay.addWidget(self.field)
        self.error = _status("", ok=False)
        self.error.hide()
        lay.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Annuleren")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Openen")
        self.ok.setObjectName("Primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._try)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        lay.addLayout(buttons)
        self.field.setFocus()

    def _try(self):
        if check_password(self.field.text(), self.stored_hash):
            self.accept()
            return
        self.attempts += 1
        self.field.clear()
        self.error.setText("Onjuist wachtwoord.")
        self.error.show()
        if self.attempts >= 3:
            # slow down guessing: lock the dialog for a few seconds after repeated failures
            wait = min(30, 5 * (self.attempts - 2))
            self.field.setEnabled(False)
            self.ok.setEnabled(False)
            self.error.setText(f"Onjuist wachtwoord. Probeer het over {wait} seconden opnieuw.")
            QTimer.singleShot(wait * 1000, self._unlock)

    def _unlock(self):
        self.field.setEnabled(True)
        self.ok.setEnabled(True)
        self.error.setText("Probeer het opnieuw.")
        self.field.setFocus()


class WindowsPasswordDialog(FramelessDialog):
    """Asks the Windows password for automatic sign-in."""

    def __init__(self, account, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Windows-wachtwoord")
        self.setFixedWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        title = QLabel("Windows-wachtwoord")
        title.setObjectName("H1")
        lay.addWidget(title)
        lay.addWidget(_status(f"Voer het wachtwoord in van {account}. Bij een Microsoft-account is dat het "
                              "wachtwoord van het account, niet je pincode."))
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.Password)
        self.field.returnPressed.connect(self.accept)
        lay.addWidget(self.field)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Annuleren")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Instellen")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def ask(self):
        self.field.setFocus()
        accepted = self.exec() == QDialog.Accepted
        text = self.field.text()
        self.field.clear()
        return text if accepted and text else None


class ExitDialog(FramelessDialog):
    """Power button menu. result_action is 'windows', 'shutdown' or None (cancelled)."""

    def __init__(self, now_playing=None, parent=None):
        super().__init__(parent)
        self.result_action = None
        self.setWindowTitle("Afsluiten")
        self.setFixedWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        title = QLabel("Afsluiten")
        title.setObjectName("H1")
        lay.addWidget(title)
        if now_playing:
            lay.addWidget(_status(f"‘{now_playing}’ wordt nu afgespeeld en wordt dan gestopt."))
        for action, glyph, head, text in (
            ("windows", G_SETTINGS, "Naar Windows", "Terug naar het bureaublad (wachtwoord vereist)."),
            ("shutdown", G_POWER, "Computer uitschakelen", "CinemaViewer sluiten en de computer uitzetten."),
        ):
            b = QPushButton(f"  {head}\n  {text}")
            b.setIcon(glyph_icon(glyph, TEXT, 22))
            b.setIconSize(QSize(22, 22))
            b.setMinimumHeight(70)
            b.setStyleSheet("QPushButton { text-align: left; padding: 10px 16px; }")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, a=action: self._choose(a))
            lay.addWidget(b)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Annuleren")
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _choose(self, action):
        self.result_action = action
        self.accept()


def screen_description(index, screen):
    geo = screen.geometry()
    model = " ".join(x for x in (screen.manufacturer(), screen.model()) if x).strip() or screen.name()
    primary = " (hoofdscherm)" if screen == QGuiApplication.primaryScreen() else ""
    return f"Scherm {index + 1}: {model} – {geo.width()}×{geo.height()} op ({geo.x()}, {geo.y()}){primary}"


def _scroll(page):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWidget(page)
    return area


def _if_alive(fn, *args):
    """Answers from the network may arrive after the settings dialog was closed: then ignore them."""
    try:
        fn(*args)
    except RuntimeError:  # 'Internal C++ object already deleted'
        pass


def _status(text, ok=None):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setObjectName("Meta")
    if ok is not None:
        lbl.setStyleSheet(f"color: {'#7BC67E' if ok else '#E07A75'};")
    return lbl


class ShareDialog(FramelessDialog):
    """Add or change a network share (SMB / NFS)."""

    def __init__(self, share, taken_names, parent=None):
        super().__init__(parent)
        self._taken = taken_names
        self._editing = share is not None
        self._original = dict(share or {})
        share = share or {"name": "", "type": "smb", "server": "", "share": "", "username": ""}
        self.setWindowTitle("Netwerkshare")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(14)
        head = QLabel("Netwerkshare")
        head.setObjectName("H1")
        lay.addWidget(head)
        form = QFormLayout()
        form.setSpacing(10)
        self.cb_type = QComboBox()
        for key, text in shares.TYPES.items():
            self.cb_type.addItem(text, key)
        self.cb_type.setCurrentIndex(max(0, self.cb_type.findData(share["type"])))
        self.ed_server = QLineEdit(share["server"])
        self.ed_server.setPlaceholderText("bv. nas.local of 192.168.1.20")
        self.ed_share = QLineEdit(share["share"])
        self.ed_user = QLineEdit(share.get("username", ""))
        self.ed_user.setPlaceholderText("leeg = gast / zonder inloggen")
        self.ed_password = QLineEdit()
        self.ed_password.setEchoMode(QLineEdit.Password)
        if self._editing and share.get("username"):
            self.ed_password.setPlaceholderText("ongewijzigd")
        self.ed_name = QLineEdit(share["name"])
        self.ed_name.setPlaceholderText("naam links in het menu, bv. NAS Films")
        form.addRow("Soort", self.cb_type)
        form.addRow("Server", self.ed_server)
        form.addRow("Share", self.ed_share)
        form.addRow("Gebruikersnaam", self.ed_user)
        form.addRow("Wachtwoord", self.ed_password)
        form.addRow("Naam", self.ed_name)
        self._form = form
        lay.addLayout(form)
        self.nfs_note = _status(
            "NFS werkt zonder gebruikersnaam en wachtwoord: de NAS bepaalt de toegang op het IP-adres van deze pc. "
            "Zet bij de NFS-instellingen van de NAS dit IP-adres (of het hele thuisnetwerk) op ‘lezen’ of "
            "‘lezen/schrijven’. Wil je inloggen met een gebruiker, kies dan SMB.")
        lay.addWidget(self.nfs_note)
        self.error = _status("", ok=False)
        self.error.hide()
        lay.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Annuleren")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("OK")
        ok.setObjectName("Primary")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.cb_type.currentIndexChanged.connect(self._update)
        self._update()

    def _update(self):
        smb = self.cb_type.currentData() == "smb"
        self.ed_share.setPlaceholderText("bv. Films of video/films" if smb else "export, bv. volume1/films")
        for widget in (self.ed_user, self.ed_password):
            widget.setVisible(smb)
            self._form.labelForField(widget).setVisible(smb)
        self.nfs_note.setVisible(not smb)

    def _accept(self):
        server = self.ed_server.text().strip().strip("\\/")
        share = self.ed_share.text().strip().strip("\\/")
        name = self.ed_name.text().strip() or share.replace("\\", "/").split("/")[-1]
        moved = self._editing and (name != self._original.get("name") or server != self._original.get("server"))
        needs_password = (self.cb_type.currentData() == "smb" and self.ed_user.text().strip()
                          and not self.ed_password.text() and (moved or not self._original.get("username")))
        problem = ("Vul de server in." if not server else "Vul de share in." if not share
                   else f"Er is al een share met de naam ‘{name}’." if name in self._taken
                   else "Vul het wachtwoord (opnieuw) in." if needs_password else "")
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        self.ed_name.setText(name)
        self.accept()

    def result_share(self):
        """(share, password or None when unchanged)."""
        smb = self.cb_type.currentData() == "smb"
        share = {"name": self.ed_name.text().strip(), "type": self.cb_type.currentData(),
                 "server": self.ed_server.text().strip().strip("\\/"),
                 "share": self.ed_share.text().strip().strip("\\/").replace("\\", "/"),
                 "username": self.ed_user.text().strip() if smb else ""}
        password = self.ed_password.text()
        if not share["username"]:
            return share, None
        return share, (password if password or not self._editing else None)


class SettingsDialog(FramelessDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.data = copy.deepcopy(app.cfg.data)
        self.setWindowTitle("Instellingen – CinemaViewer")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(16)
        title = QLabel("Instellingen")
        title.setObjectName("H1")
        lay.addWidget(title)
        tabs = QTabWidget()
        for page, name in ((self._screens_tab(), "Schermen"), (self._outputs_tab(), "Uitgangen"),
                           (self._jellyfin_tab(), "Jellyfin"), (self._plex_tab(), "Plex"),
                           (self._local_tab(), "Lokaal, USB && disc"), (self._radio_tab(), "Radio"),
                           (self._intermission_tab(), "Pauze"),
                           (self._devices_tab(), "Apparaten"), (self._dmx_tab(), "Licht (DMX)"),
                           (self._system_tab(), "Systeem")):
            tabs.addTab(_scroll(page), name)  # long tabs scroll instead of stretching the dialog
        self.tabs = tabs
        self._dmx_original = copy.deepcopy(self.data["dmx"])
        lay.addWidget(tabs, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Annuleren")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Opslaan")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)
        self._fit_to_screen()

    def _fit_to_screen(self):
        """Comfortable size, but never larger than the screen the operator works on."""
        screen = (self.parentWidget() or self).screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        self.setMinimumSize(min(640, avail.width() - 40), min(480, avail.height() - 40))
        w = min(1000, int(avail.width() * 0.9))
        h = min(820, int(avail.height() * 0.88))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)

    # -- tabs -----------------------------------------------------------------
    def _page(self):
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(20, 20, 20, 20)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return w, form

    def _screens_tab(self):
        w, form = self._page()
        screens = QGuiApplication.screens()
        self.cb_control = QComboBox()
        self.cb_video = QComboBox()
        for i, s in enumerate(screens):
            for cb in (self.cb_control, self.cb_video):
                cb.addItem(screen_description(i, s), s.name())
        self.cb_control.setCurrentIndex(self.app.resolve_screen_index(self.data["control_screen"], 0))
        default_video = 1 if len(screens) > 1 else 0
        self.cb_video.setCurrentIndex(self.app.resolve_screen_index(self.data["video_screen"], default_video))
        form.addRow("Bedieningsscherm", self.cb_control)
        form.addRow("Projectiescherm (film)", self.cb_video)
        identify = QPushButton("Schermen identificeren")
        identify.clicked.connect(self._identify)
        system_settings = QPushButton("Beeldscherminstellingen openen")
        system_settings.setToolTip("Opent de scherminstellingen van " + ("Windows" if os.name == "nt" else "Linux")
                                   + " (resolutie, uitbreiden/dupliceren, volgorde)")
        system_settings.clicked.connect(self._open_display_settings)
        row = QHBoxLayout()
        row.addWidget(identify)
        row.addWidget(system_settings)
        row.addStretch()
        form.addRow("", row)
        self.chk_lock_cursor = QCheckBox("Muisaanwijzer op het bedieningsscherm houden")
        self.chk_lock_cursor.setToolTip("Op de overige schermen is de muisaanwijzer onzichtbaar; zo raak je hem "
                                        "daar niet kwijt. Geldt alleen zolang CinemaViewer het actieve programma is.")
        self.chk_lock_cursor.setChecked(self.data["lock_cursor"])
        form.addRow("", self.chk_lock_cursor)
        form.addRow("", _status(
            "Het projectiescherm toont de film. Alle overige schermen (behalve het bedieningsscherm) tonen de "
            "placeholder en tijdens het afspelen optioneel een ‘Nu te zien’-kaart. Wil je de film op meerdere "
            "projectoren tegelijk? Zet die schermen in Windows op ‘Dupliceren’.\n"
            "Met één scherm: Tab of dubbelklik wisselt tussen film en bediening."))
        self.ed_title = QLineEdit(self.data["placeholder_title"])
        self.ed_subtitle = QLineEdit(self.data["placeholder_subtitle"])
        form.addRow("Placeholder titel", self.ed_title)
        form.addRow("Placeholder ondertitel", self.ed_subtitle)
        self.chk_wallpaper = QCheckBox("Placeholder ook gebruiken als Windows-bureaubladachtergrond")
        self.chk_wallpaper.setChecked(self.data["desktop_wallpaper"])
        form.addRow("", self.chk_wallpaper)
        save_images = QPushButton("Achtergrondafbeeldingen opslaan (1920×1080 en 1920×1200)")
        save_images.clicked.connect(self._save_wallpapers)
        form.addRow("", save_images)
        self.chk_now = QCheckBox("Toon ‘Nu te zien’ op overige schermen tijdens het afspelen")
        self.chk_now.setChecked(self.data["now_showing_on_other_screens"])
        form.addRow("", self.chk_now)
        self.chk_preview = QCheckBox("Toon daarop een klein live-voorbeeld van de film")
        self.chk_preview.setChecked(self.data["preview_on_other_screens"])
        self.chk_preview.setEnabled(self.chk_now.isChecked())
        self.chk_now.toggled.connect(self.chk_preview.setEnabled)
        form.addRow("", self.chk_preview)
        self.chk_report = QCheckBox("Kijkvoortgang terugmelden aan Jellyfin / Plex (hervatten, ‘bekeken’)")
        self.chk_report.setChecked(self.data["report_progress"])
        form.addRow("", self.chk_report)
        return w

    def _outputs_tab(self):
        w, form = self._page()
        outputs = display.display_outputs()
        if os.name == "nt":  # Windows cannot drive an output without a monitor; hide the empty ones
            outputs = [o for o in outputs if o["connected"] or o["active"]]
        self._outputs = outputs
        form.addRow("", _status(
            "Zet een uitgang bij het starten van CinemaViewer zelf aan met een vaste resolutie, ook als er "
            "geen scherm wordt herkend. Handig voor een HDBaseT-extender of matrix die de schermgegevens "
            "(EDID) niet of te laat doorgeeft. Lukt het bij het starten niet, dan probeert CinemaViewer het "
            "nog een paar keer (tot 40 seconden). Kies daarna onder ‘Schermen’ welk scherm de film toont."))
        self.out_table = QTableWidget(0, 4)
        self.out_table.setHorizontalHeaderLabels(["Uitgang", "Resolutie", "Hz", "Positie"])
        self.out_table.verticalHeader().setVisible(False)
        for col, width in enumerate((250, 120, 80, 220)):
            self.out_table.setColumnWidth(col, width)
        self.out_table.setMinimumHeight(110)
        self.out_table.setMaximumHeight(170)
        form.addRow("Geforceerd", self.out_table)
        add = QPushButton("+ Uitgang toevoegen")
        add.clicked.connect(lambda: self._add_output_row())
        rem = QPushButton("Verwijderen")
        rem.clicked.connect(self._remove_output_row)
        apply_now = QPushButton("Nu toepassen")
        apply_now.clicked.connect(self._apply_outputs_now)
        system_settings = QPushButton("Beeldscherminstellingen openen")
        system_settings.clicked.connect(self._open_display_settings)
        row = QHBoxLayout()
        for b in (add, rem, apply_now, system_settings):
            row.addWidget(b)
        row.addStretch()
        form.addRow("", row)
        self.out_status = _status("")
        form.addRow("", self.out_status)
        for f in self.data["forced_outputs"]:
            self._add_output_row(f)
        found = ", ".join(f"{o['name']} ({'actief' if o['active'] else 'verbonden' if o['connected'] else 'niets herkend'})"
                          for o in outputs)
        form.addRow("Gevonden", _status(found or "Geen uitgangen gevonden (onder Linux is xrandr/X11 nodig)."))

        self.cb_audio = QComboBox()
        self.cb_audio.setMinimumWidth(420)
        self.cb_audio.addItem("Standaard (systeeminstelling)", {"device": "", "card": "", "profile": ""})
        try:
            devices = self.app.player.audio_devices()
        except Exception:  # noqa: BLE001 - libVLC without device enumeration
            devices = []
        for dev, desc in devices:
            self.cb_audio.addItem(desc, {"device": dev, "card": "", "profile": ""})
        for card, profile, desc in display.hdmi_audio_profiles():
            self.cb_audio.addItem(f"HDMI forceren: {desc}", {"device": "", "card": card, "profile": profile})
        current = self.data["audio_output"]
        idx = next((i for i in range(self.cb_audio.count()) if self.cb_audio.itemData(i) == current), -1)
        if idx < 0:  # e.g. a device that is not connected right now: keep it
            self.cb_audio.addItem(f"(niet gevonden) {current.get('profile') or current.get('device')}", dict(current))
            idx = self.cb_audio.count() - 1
        self.cb_audio.setCurrentIndex(idx)
        form.addRow("Geluidsuitgang", self.cb_audio)
        form.addRow("", _status(
            "Het geluid van films gaat naar deze uitgang, los van de standaarduitgang van het systeem. "
            + ("‘HDMI forceren’ zet onder Linux het HDMI-profiel van de geluidskaart aan, ook als het systeem "
               "geen ontvanger herkent (een extender geeft dat vaak niet door); de andere uitgangen van die "
               "kaart (bv. de luidsprekers van de laptop) gaan dan uit. " if os.name != "nt" else "")
            + "Wordt toegepast bij Opslaan."))
        return w

    def _add_output_row(self, forced=None):
        forced = forced or {}
        r = self.out_table.rowCount()
        self.out_table.insertRow(r)
        out = QComboBox()
        out.setEditable(True)
        for o in self._outputs:
            state = "actief" if o["active"] else "verbonden" if o["connected"] else "niets herkend"
            out.addItem(f"{o['name']}  ({state})", o["name"])
        name = forced.get("output")
        if name:
            i = out.findData(name)
            if i < 0:
                out.addItem(f"{name}  (niet gevonden)", name)
                i = out.count() - 1
            out.setCurrentIndex(i)
        else:  # new row: suggest an output that is not in use yet
            i = next((n for n, o in enumerate(self._outputs) if not o["active"]), 0)
            out.setCurrentIndex(min(i, out.count() - 1))
        self.out_table.setCellWidget(r, 0, out)
        res = QComboBox()
        res.setEditable(True)
        modes = {(m[0], m[1]) for o in self._outputs for m in o["modes"]} | set(COMMON_MODES)
        res.addItems([f"{mw}x{mh}" for mw, mh in sorted(modes, key=lambda m: (m[0] * m[1], m[0]), reverse=True)])
        res.setCurrentText(f"{forced.get('width', 1920)}x{forced.get('height', 1080)}")
        self.out_table.setCellWidget(r, 1, res)
        rate = QDoubleSpinBox()
        rate.setRange(23.0, 240.0)
        rate.setDecimals(2)
        rate.setValue(float(forced.get("rate") or 60))
        self.out_table.setCellWidget(r, 2, rate)
        pos = QComboBox()
        for key, text in display.POSITIONS.items():
            pos.addItem(text, key)
        pos.setCurrentIndex(max(0, pos.findData(forced.get("position", "right"))))
        self.out_table.setCellWidget(r, 3, pos)

    def _remove_output_row(self):
        r = self.out_table.currentRow()
        if r < 0:
            r = self.out_table.rowCount() - 1
        if r >= 0:
            self.out_table.removeRow(r)

    def _forced_outputs(self):
        """The table as config list; raises ValueError with a readable text on a wrong resolution."""
        out = []
        for r in range(self.out_table.rowCount()):
            combo = self.out_table.cellWidget(r, 0)
            i = combo.findText(combo.currentText())
            name = combo.itemData(i) if i >= 0 else combo.currentText().split(" ")[0].strip()
            if not name:
                continue
            text = self.out_table.cellWidget(r, 1).currentText().lower().replace("×", "x").replace(" ", "")
            try:
                width, height = (int(v) for v in text.split("x"))
            except ValueError:
                raise ValueError(f"Resolutie ‘{text}’ bij {name} is ongeldig; gebruik bv. 1920x1080.") from None
            out.append({"output": name, "width": width, "height": height,
                        "rate": round(self.out_table.cellWidget(r, 2).value(), 2),
                        "position": self.out_table.cellWidget(r, 3).currentData()})
        return out

    def _apply_outputs_now(self):
        try:
            forced = self._forced_outputs()
        except ValueError as e:
            self.out_status.setText(str(e))
            self.out_status.setStyleSheet("color: #E07A75;")
            return
        results = []
        ok = True
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for f in forced:
                try:
                    changed = display.force_output(f["output"], f["width"], f["height"], f["rate"], f["position"])
                    results.append(f"{f['output']}: {'aangezet' if changed else 'stond al goed'}")
                except OSError as e:
                    ok = False
                    results.append(f"{f['output']}: {e}")
        finally:
            QApplication.restoreOverrideCursor()
        self.out_status.setText("\n".join(results) or "Geen uitgangen ingesteld.")
        self.out_status.setStyleSheet(f"color: {'#7BC67E' if ok else '#E07A75'};")
        QTimer.singleShot(2000, self._refresh_screen_combos)  # the new screen can now be chosen

    def _refresh_screen_combos(self):
        screens = QGuiApplication.screens()
        for cb in (self.cb_control, self.cb_video):
            name = cb.currentData()
            cb.clear()
            for i, s in enumerate(screens):
                cb.addItem(screen_description(i, s), s.name())
            cb.setCurrentIndex(max(0, cb.findData(name)))

    def _open_display_settings(self):
        try:
            display.open_display_settings()
        except OSError as e:
            message(self, "Beeldscherminstellingen", f"Kon de beeldscherminstellingen niet openen:\n{e}")

    def _jellyfin_tab(self):
        w, form = self._page()
        j = self.data["jellyfin"]
        self.jf_enabled = QCheckBox("Jellyfin gebruiken")
        self.jf_enabled.setChecked(j["enabled"])
        self.jf_url = QLineEdit(j["url"])
        self.jf_url.setPlaceholderText("http://192.168.1.10:8096")
        self.jf_user = QLineEdit(j["username"])
        self.jf_pass = QLineEdit()
        self.jf_pass.setEchoMode(QLineEdit.Password)
        self.jf_pass.setPlaceholderText("wordt niet opgeslagen")
        login = QPushButton("Verbinden / inloggen")
        login.clicked.connect(self._jellyfin_login)
        self.jf_status = _status("Verbonden (token opgeslagen)." if j["token"] else "Nog niet verbonden.",
                                 True if j["token"] else None)
        form.addRow("", self.jf_enabled)
        form.addRow("Server-adres", self.jf_url)
        form.addRow("Gebruikersnaam", self.jf_user)
        form.addRow("Wachtwoord", self.jf_pass)
        form.addRow("", login)
        form.addRow("", self.jf_status)
        return w

    def _plex_tab(self):
        w, form = self._page()
        p = self.data["plex"]
        self.px_enabled = QCheckBox("Plex gebruiken")
        self.px_enabled.setChecked(p["enabled"])
        self.px_url = QLineEdit(p["url"])
        self.px_url.setPlaceholderText("http://192.168.1.10:32400")
        self.px_token = QLineEdit(p["token"])
        self.px_token.setEchoMode(QLineEdit.Password)
        self.px_token.setPlaceholderText("X-Plex-Token")
        test = QPushButton("Verbinding testen")
        test.clicked.connect(self._plex_test)
        self.px_status = _status("")
        form.addRow("", self.px_enabled)
        form.addRow("Server-adres", self.px_url)
        form.addRow("Token", self.px_token)
        form.addRow("", test)
        form.addRow("", self.px_status)
        form.addRow("", _status(
            "Token vinden: open een film in Plex Web → ⋯ → ‘Media-info’ → ‘XML bekijken’. "
            "In de adresbalk staat ‘X-Plex-Token=…’."))
        return w

    def _local_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)
        lay.addWidget(QLabel("Mediamappen (lokaal of netwerk, bv. \\\\NAS\\Films)"))
        self.folders = QListWidget()
        self.folders.setObjectName("Plain")
        self.folders.addItems(self.data["local_folders"])
        lay.addWidget(self.folders, 1)
        row = QHBoxLayout()
        add = QPushButton("Map toevoegen…")
        add.clicked.connect(self._add_folder)
        remove = QPushButton("Verwijderen")
        remove.clicked.connect(lambda: [self.folders.takeItem(self.folders.row(i))
                                        for i in self.folders.selectedItems()])
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        lay.addLayout(row)

        lay.addWidget(QLabel("Netwerkshares (SMB / NFS), bv. van een NAS"))
        self._shares = copy.deepcopy(self.data["network_shares"])
        self._share_passwords = {}  # name → newly entered password (applied when saving)
        self.share_list = QListWidget()
        self.share_list.setObjectName("Plain")
        self.share_list.setMaximumHeight(110)
        self.share_list.itemDoubleClicked.connect(lambda _i: self._edit_share())
        lay.addWidget(self.share_list)
        row = QHBoxLayout()
        for text, slot in (("Share toevoegen…", self._add_share), ("Wijzigen…", self._edit_share),
                           ("Verwijderen", self._remove_share), ("Verbinding testen", self._test_share)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.share_status = _status("Een share verschijnt links onder ‘Mappen’. "
                                    + ("Gebruikersnaam en wachtwoord bewaart Windows in Referentiebeheer."
                                       if os.name == "nt" else
                                       "Bij opslaan wordt één keer om het beheerderswachtwoord gevraagd; de share "
                                       "wordt daarna bij elke start vanzelf gekoppeld."))
        lay.addWidget(self.share_status)
        self._fill_shares()

        self.chk_usb = QCheckBox("USB-schijven automatisch tonen")
        self.chk_usb.setChecked(self.data["show_usb_drives"])
        self.chk_disc = QCheckBox("DVD- / Blu-ray-stations tonen")
        self.chk_disc.setChecked(self.data["show_disc_drives"])
        lay.addWidget(self.chk_usb)
        lay.addWidget(self.chk_disc)
        lay.addWidget(_status(
            "Blu-ray: commerciële (versleutelde) Blu-rays vereisen libaacs + KEYDB.cfg naast VLC. "
            "DVD’s en onversleutelde Blu-rays werken direct."))
        return w

    def _fill_shares(self):
        self.share_list.clear()
        for s in self._shares:
            self.share_list.addItem(f"{s['name']}   —   {shares.describe(s)}"
                                    + (f"   ({s['username']})" if s.get("username") else ""))

    def _add_share(self):
        self._share_dialog(None)

    def _edit_share(self):
        row = self.share_list.currentRow()
        if row >= 0:
            self._share_dialog(row)

    def _share_dialog(self, row):
        dlg = ShareDialog(self._shares[row] if row is not None else None,
                          {s["name"] for i, s in enumerate(self._shares) if i != row}, self)
        if dlg.exec() != QDialog.Accepted:
            return
        share, password = dlg.result_share()
        if row is None:
            self._shares.append(share)
        else:
            self._share_passwords.pop(self._shares[row]["name"], None)
            self._shares[row] = share
        if password is not None:
            self._share_passwords[share["name"]] = password
        self._fill_shares()
        self.share_status.setText("Wordt ingesteld bij Opslaan.")

    def _remove_share(self):
        row = self.share_list.currentRow()
        if row >= 0:
            self._share_passwords.pop(self._shares.pop(row)["name"], None)
            self._fill_shares()
            self.share_status.setText("Wordt verwijderd bij Opslaan.")

    def _test_share(self):
        row = self.share_list.currentRow()
        if row < 0:
            return
        share = self._shares[row]
        if share not in self.data["network_shares"] or share["name"] in self._share_passwords:
            self.share_status.setText("Sla eerst op; daarna kun je de verbinding testen.")
            return
        self.share_status.setText(f"Verbinden met {shares.describe(share)}…")
        run_async(shares.check, lambda r: self.share_status.setText(r[1]),
                  lambda err: self.share_status.setText(f"Testen mislukt: {err}"), share)

    def _apply_shares(self):
        """Set up changed network shares (asks for administrator rights on Linux); False keeps the dialog open."""
        if self._shares == self.data["network_shares"] and not self._share_passwords:
            return True
        ok, problem = shares.apply(self.data["network_shares"], self._shares, self._share_passwords)
        if not ok:
            message(self, "Netwerkshares", problem)
            return False
        self.data["network_shares"] = copy.deepcopy(self._shares)
        self._share_passwords = {}
        return True

    def _radio_tab(self):
        w, form = self._page()
        r = self.data["radio"]
        self._radio_was_default = r.get("stations") is None
        self._radio = copy.deepcopy(r["stations"]) if r.get("stations") is not None else radio.default_stations()
        self.chk_radio = QCheckBox("Radiozenders tonen in de bibliotheek")
        self.chk_radio.setChecked(r.get("enabled", True))
        form.addRow("", self.chk_radio)

        self.radio_list = QListWidget()
        self.radio_list.setMinimumHeight(180)
        self.radio_list.setMaximumHeight(260)
        form.addRow("Mijn zenders", self.radio_list)
        row = QHBoxLayout()
        for text, fn in (("Omhoog", lambda: self._radio_move(-1)), ("Omlaag", lambda: self._radio_move(1)),
                         ("Verwijderen", self._radio_remove), ("Standaardlijst (Nederland)", self._radio_reset)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        form.addRow("", row)

        own = QHBoxLayout()
        self.radio_name = QLineEdit()
        self.radio_name.setPlaceholderText("Naam")
        self.radio_url = QLineEdit()
        self.radio_url.setPlaceholderText("Stream-adres, bv. https://…/stream.mp3")
        add_own = QPushButton("Toevoegen")
        add_own.clicked.connect(self._radio_add_own)
        own.addWidget(self.radio_name, 1)
        own.addWidget(self.radio_url, 2)
        own.addWidget(add_own)
        form.addRow("Eigen zender", own)

        box = QGroupBox("Zenders zoeken in Radio Browser")
        bl = QVBoxLayout(box)
        search = QHBoxLayout()
        self.radio_country = QComboBox()
        self.radio_country.setMinimumWidth(240)
        self.radio_country.addItem("Nederland (NL)", "NL")
        self.radio_country.addItem("Alle landen", "")
        self.radio_query = QLineEdit()
        self.radio_query.setPlaceholderText("Naam van de zender (leeg = populairste)")
        self.radio_query.returnPressed.connect(self._radio_search)
        go = QPushButton("Zoeken")
        go.clicked.connect(self._radio_search)
        search.addWidget(self.radio_country)
        search.addWidget(self.radio_query, 1)
        search.addWidget(go)
        bl.addLayout(search)
        self.radio_results = QListWidget()
        self.radio_results.setSelectionMode(QListWidget.ExtendedSelection)
        self.radio_results.setMinimumHeight(200)
        self.radio_results.itemDoubleClicked.connect(lambda _i: self._radio_add_results())
        bl.addWidget(self.radio_results)
        res_row = QHBoxLayout()
        add = QPushButton("Geselecteerde toevoegen")
        add.clicked.connect(self._radio_add_results)
        self.radio_status = _status("")
        res_row.addWidget(add)
        res_row.addWidget(self.radio_status, 1)
        bl.addLayout(res_row)
        bl.addWidget(_status(
            "Radio Browser (radio-browser.info) is een open, door gebruikers bijgehouden lijst van meer dan "
            "50.000 zenders met hun stream-adressen, per land. Kies een land en zoek op naam, of laat het "
            "zoekveld leeg voor de populairste zenders. Dubbelklik of kies ‘Geselecteerde toevoegen’."))
        form.addRow(box)
        self._radio_fill()
        run_async(radio.countries, lambda items: _if_alive(self._radio_countries, items), None)  # in the background
        return w

    def _radio_fill(self, select=None):
        self.radio_list.clear()
        for s in self._radio:
            extra = radio.describe(s)
            self.radio_list.addItem(s["name"] + (f"   —   {extra}" if extra else ""))
        if select is not None and 0 <= select < self.radio_list.count():
            self.radio_list.setCurrentRow(select)

    def _radio_move(self, delta):
        r = self.radio_list.currentRow()
        if r < 0 or not 0 <= r + delta < len(self._radio):
            return
        self._radio[r], self._radio[r + delta] = self._radio[r + delta], self._radio[r]
        self._radio_fill(r + delta)

    def _radio_remove(self):
        r = self.radio_list.currentRow()
        if r >= 0:
            del self._radio[r]
            self._radio_fill(min(r, len(self._radio) - 1))

    def _radio_reset(self):
        self._radio = radio.default_stations()
        self._radio_fill()

    def _radio_add(self, stations):
        known = {s["url"] for s in self._radio} | {s.get("uuid") for s in self._radio if s.get("uuid")}
        added = [s for s in stations if s["url"] not in known and not (s.get("uuid") and s["uuid"] in known)]
        self._radio.extend(added)
        self._radio_fill(len(self._radio) - 1)
        return len(added)

    def _radio_add_own(self):
        url = self.radio_url.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            self.radio_url.setFocus()
            return
        self._radio_add([{"name": self.radio_name.text().strip() or url, "url": url, "logo": "", "uuid": "",
                          "tags": "", "codec": "", "bitrate": 0, "country": ""}])
        self.radio_name.clear()
        self.radio_url.clear()

    def _radio_countries(self, items):
        current = self.radio_country.currentData()
        self.radio_country.clear()
        self.radio_country.addItem("Alle landen", "")
        for code, name, count in items:
            self.radio_country.addItem(f"{name} ({code}) – {count} zenders", code)
        self.radio_country.setCurrentIndex(max(0, self.radio_country.findData(current)))

    def _radio_search(self):
        self.radio_status.setText("Zoeken…")
        self.radio_status.setStyleSheet("")
        self.radio_results.clear()
        token = self._radio_token = getattr(self, "_radio_token", 0) + 1

        def done(stations):
            if token != self._radio_token:
                return
            for s in stations:
                extra = radio.describe(s)
                it = QListWidgetItem(s["name"] + (f"   —   {extra}" if extra else ""))
                it.setData(Qt.UserRole, s)
                self.radio_results.addItem(it)
            self.radio_status.setText(f"{len(stations)} zenders gevonden." if stations else "Niets gevonden.")

        def failed(err):
            if token == self._radio_token:
                self.radio_status.setText(f"Zoeken mislukt: {err}")
                self.radio_status.setStyleSheet("color: #E07A75;")

        run_async(radio.search, lambda r: _if_alive(done, r), lambda e: _if_alive(failed, e),
                  self.radio_country.currentData(), self.radio_query.text().strip())

    def _radio_add_results(self):
        chosen = [it.data(Qt.UserRole) for it in self.radio_results.selectedItems()]
        if chosen:
            n = self._radio_add(chosen)
            self.radio_status.setText(f"{n} zender{'s' if n != 1 else ''} toegevoegd." if n
                                      else "Die staan al in je lijst.")
            self.radio_status.setStyleSheet("color: #7BC67E;" if n else "")

    def _intermission_tab(self):
        w, form = self._page()

        def spin(value, maximum, suffix):
            box = QSpinBox()
            box.setRange(0, maximum)
            box.setValue(int(value))
            box.setSuffix(suffix)
            box.setFixedWidth(160)
            return box

        self.sp_minutes = spin(self.data["intermission_minutes"], 120, " minuten")
        self.sp_minutes.setSpecialValueText("tot handmatig hervat")
        self.sp_countdown = spin(self.data["intermission_countdown"], 60, " seconden")
        self.sp_rewind = spin(self.data["intermission_rewind"], 120, " seconden")
        form.addRow("", _status(
            "De knop ‘Pauze’ bij het afspelen zet de film stil en toont ‘Pauze’ op de publieksschermen. "
            "Bij hervatten (knop, spatie of automatisch na de pauzeduur) volgt eerst een aftelling, daarna "
            "gaat de film een stukje vóór het pauzemoment verder."))
        form.addRow("Duur van de pauze", self.sp_minutes)
        form.addRow("Aftellen voor hervatten", self.sp_countdown)
        form.addRow("Terugspoelen bij hervatten", self.sp_rewind)
        return w

    def _devices_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)

        def form_box(title):
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setHorizontalSpacing(18)
            form.setVerticalSpacing(12)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lay.addWidget(box)
            return form

        p = self.data["projector"]
        form = form_box("Projector")
        self.pj_form = form
        self.pj_enabled = QCheckBox("Projector bedienen: knop ‘Beamer aan/uit’ bovenin, uit bij het afsluiten")
        self.pj_enabled.setChecked(p["enabled"])
        self.pj_auto = QCheckBox("Ook automatisch aanzetten bij het starten van CinemaViewer")
        self.pj_auto.setToolTip("Uit: de beamer gaat pas aan met de knop bovenin, zodat alleen muziek "
                                "luisteren geen lampuren kost")
        self.pj_auto.setChecked(p.get("auto_on", False))
        self.pj_protocol = QComboBox()
        for key, text in (("pjlink", "PJLink (netwerk)"), ("infocus_net", "InFocus via netwerk (poort 23)"),
                          ("infocus_serial", "InFocus via RS-232 (seriële kabel)")):
            self.pj_protocol.addItem(text, key)
        self.pj_protocol.setCurrentIndex(max(0, self.pj_protocol.findData(p.get("protocol", "pjlink"))))
        self.pj_host = QLineEdit(p["host"])
        self.pj_host.setPlaceholderText("192.168.1.20")
        self.pj_password = QLineEdit(p["password"])
        self.pj_password.setEchoMode(QLineEdit.Password)
        self.pj_password.setPlaceholderText("alleen als PJLink-beveiliging aan staat")
        self.pj_port = QComboBox()
        self.pj_port.setEditable(True)
        self.pj_port.addItems(serial_ports())
        self.pj_port.setCurrentText(p.get("serial_port", ""))
        self.pj_baud = QComboBox()
        self.pj_baud.addItem("Automatisch", 0)
        for baud in INFOCUS_BAUDS:
            self.pj_baud.addItem(f"{baud} baud", baud)
        self.pj_baud.setCurrentIndex(max(0, self.pj_baud.findData(int(p.get("baud") or 0))))
        self.pj_baud.setFixedWidth(160)
        self.pj_lamp_mode = QComboBox()
        for key, text in LAMP_MODES.items():
            self.pj_lamp_mode.addItem(text, key)
        self.pj_lamp_mode.setCurrentIndex(max(0, self.pj_lamp_mode.findData(p.get("lamp_mode", ""))))
        self.pj_lamp_power = QComboBox()
        for key, text in LAMP_POWERS.items():
            self.pj_lamp_power.addItem(text, key)
        self.pj_lamp_power.setCurrentIndex(max(0, self.pj_lamp_power.findData(p.get("lamp_power", ""))))
        for cb in (self.pj_lamp_mode, self.pj_lamp_power):
            cb.setFixedWidth(160)
        self.pj_warn = QSpinBox()
        self.pj_warn.setRange(0, 20000)
        self.pj_warn.setSingleStep(250)
        self.pj_warn.setSuffix(" uur")
        self.pj_warn.setSpecialValueText("Niet waarschuwen")
        self.pj_warn.setValue(int(p.get("lamp_warn_hours", 2000)))
        self.pj_warn.setFixedWidth(160)
        test = QPushButton("Verbinding testen / status opvragen")
        test.clicked.connect(self._projector_test)
        self.pj_status = _status("")
        self.pj_help = _status("")
        form.addRow("", self.pj_enabled)
        form.addRow("", self.pj_auto)
        form.addRow("Besturing", self.pj_protocol)
        form.addRow("IP-adres", self.pj_host)
        form.addRow("PJLink-wachtwoord", self.pj_password)
        form.addRow("Seriële poort", self.pj_port)
        form.addRow("Snelheid", self.pj_baud)
        form.addRow("Lampmodus", self.pj_lamp_mode)
        form.addRow("Lampvermogen", self.pj_lamp_power)
        form.addRow("Waarschuwen vanaf", self.pj_warn)
        form.addRow("", test)
        form.addRow("", self.pj_status)
        form.addRow("", self.pj_help)
        self.pj_protocol.currentIndexChanged.connect(self._projector_protocol_changed)
        self._projector_protocol_changed()

        dc = self.data["displaycast"]
        form = form_box("Displaycast (YouTube en DLNA vanaf telefoons)")
        self.dc_name = QLineEdit(dc.get("name", ""))
        self.dc_name.setPlaceholderText(default_cast_name())
        self.dc_auto = QCheckBox("Displaycast meteen starten wanneer CinemaViewer start")
        self.dc_auto.setChecked(dc.get("autostart", False))
        form.addRow("Naam op telefoons", self.dc_name)
        form.addRow("", self.dc_auto)
        form.addRow("", _status(
            "Zonder dit vinkje start Displaycast pas als je het links in het zijpaneel opent. Gekoppelde "
            "telefoons blijven gekoppeld, ook na een herstart."))

        r = self.data["receiver"]
        form = form_box("Receiver (Denon, bv. AVR-4311)")
        self.av_enabled = QCheckBox("Receiver aanzetten op de gekozen ingang bij het starten en uitzetten bij het afsluiten")
        self.av_enabled.setChecked(r["enabled"])
        self.av_host = QLineEdit(r["host"])
        self.av_host.setPlaceholderText("192.168.1.21")
        self.av_input = QComboBox()
        self.av_input.setEditable(True)
        self.av_input.addItems(DENON_INPUTS)
        self.av_input.setCurrentText(r["input"])
        self.av_input.setFixedWidth(160)
        self.av_volume = QCheckBox("Volume en dempen van CinemaViewer regelen de receiver")
        self.av_volume.setChecked(r["volume_control"])
        self.av_max = QSpinBox()
        self.av_max.setRange(-70, 18)
        self.av_max.setValue(int(r["max_volume_db"]))
        self.av_max.setSuffix(" dB")
        self.av_max.setFixedWidth(160)
        self.av_max.setEnabled(self.av_volume.isChecked())
        self.av_volume.toggled.connect(self.av_max.setEnabled)
        test = QPushButton("Verbinding testen")
        test.clicked.connect(self._receiver_test)
        self.av_status = _status("")
        form.addRow("", self.av_enabled)
        form.addRow("IP-adres", self.av_host)
        form.addRow("Ingang", self.av_input)
        form.addRow("", self.av_volume)
        form.addRow("Volume bij 100%", self.av_max)
        form.addRow("", test)
        form.addRow("", self.av_status)
        form.addRow("", _status(
            "De ingang is de naam zoals de receiver hem kent (BD, DVD, SAT/CBL, …), niet een eigen naam die "
            "je in het menu hebt gegeven. Met volumeregeling speelt CinemaViewer zelf altijd op vol niveau en "
            "bepaalt de schuifregelaar het volume van de receiver: 100% is het hierboven gekozen maximum "
            "(0 dB is referentieniveau). Draai je aan de knop of de afstandsbediening, dan volgt de "
            "schuifregelaar. Zet in de receiver onder Netwerk ‘Network Standby’ (netwerkstand-by) aan, "
            "anders kan hij vanuit stand-by niet worden aangezet."))
        lay.addStretch()
        return w

    def _system_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)

        start = QGroupBox("Opstarten")
        sl = QVBoxLayout(start)
        self.chk_autostart = QCheckBox("CinemaViewer automatisch starten wanneer Windows opstart")
        self.chk_autostart.setChecked(autostart_enabled())
        sl.addWidget(self.chk_autostart)
        self.chk_shell = QCheckBox("Bioscoop-pc: CinemaViewer vervangt het Windows-bureaublad (geen startmenu of taakbalk)")
        self.chk_shell.setChecked(shell_enabled())
        self.chk_shell.toggled.connect(self._shell_toggled)
        sl.addWidget(self.chk_shell)
        self._shell_toggled(self.chk_shell.isChecked())
        self.chk_taskbar = QCheckBox("Windows-taakbalk verbergen, ook tijdens het opstarten")
        self.chk_taskbar.setChecked(self.data["hide_taskbar"])
        sl.addWidget(self.chk_taskbar)
        sl.addWidget(_status(
            "CinemaViewer start zodra deze Windows-gebruiker is aangemeld. Wil je dat de pc na het aanzetten "
            "zonder inlogscherm direct de bioscoop toont, zet dan hieronder ook automatisch aanmelden aan. "
            "De taakbalk-optie zet ‘taakbalk automatisch verbergen’ aan (zodat hij bij het opstarten al weg is) "
            "en verbergt hem op alle schermen zolang CinemaViewer draait; bij ‘Naar Windows’ verschijnt hij "
            "weer als je de muis naar de schermrand beweegt."))
        sl.addWidget(_status(
            "Als bioscoop-pc start Windows na het aanmelden CinemaViewer in plaats van het bureaublad: er is dan "
            "niets anders te openen. ‘Naar Windows’ start het gewone bureaublad. Loopt CinemaViewer vast, dan "
            "start het opnieuw; na drie keer binnen twee minuten verschijnt het bureaublad. Noodroute: houd Shift "
            "ingedrukt direct na het aanmelden. Andere programma's die normaal met Windows meestarten, starten "
            "pas na ‘Naar Windows’. Geldt vanaf de volgende keer aanmelden."))
        lay.addWidget(start)

        signin = QGroupBox("Windows-aanmelding")
        gl = QVBoxLayout(signin)
        self._logon_user, self._logon_domain, label = logon_account()
        self._autologon_was = autologon_enabled()
        self._lockscreen_was = lockscreen_enabled(lockscreen_image_path())
        self.chk_autologon = QCheckBox(f"Automatisch aanmelden als {label} bij het starten van Windows")
        self.chk_autologon.setChecked(self._autologon_was)
        if self._autologon_was and autologon_user() not in ("", self._logon_user):
            self.chk_autologon.setText(f"Automatisch aanmelden (nu ingesteld voor {autologon_user()})")
        gl.addWidget(self.chk_autologon)
        self.chk_lockscreen = QCheckBox("Vergrendel- en aanmeldscherm: placeholder-achtergrond (zonder tekst)")
        self.chk_lockscreen.setChecked(self._lockscreen_was)
        gl.addWidget(self.chk_lockscreen)
        gl.addWidget(_status(
            "Bij automatisch aanmelden vraagt CinemaViewer eenmalig je Windows-wachtwoord (bij een Microsoft-account "
            "het wachtwoord van dat account, niet je pincode). Het wordt gecontroleerd en daarna versleuteld door "
            "Windows bewaard, net als bij Microsofts eigen Autologon. Voor beide opties vraagt Windows om "
            "beheerdersrechten."))
        lay.addWidget(signin)

        lock = QGroupBox("Afsluiten voorkomen")
        ll = QVBoxLayout(lock)
        self.chk_kiosk = QCheckBox("Windows-toets, Alt+Tab en het Ctrl+Alt+Del-menu blokkeren zolang CinemaViewer draait")
        self.chk_kiosk.setChecked(self.data["kiosk_mode"])
        ll.addWidget(self.chk_kiosk)
        ll.addWidget(_status(
            "Alt+F4 wordt altijd genegeerd. Deze optie blokkeert ook de Windows-toets, Alt+Tab, Alt+Esc, "
            "Ctrl+Esc en Ctrl+Shift+Esc. Ctrl+Alt+Del zelf kan door geen enkel programma worden geblokkeerd; "
            "daarin worden Taakbeheer, Vergrendelen, Afmelden en Wachtwoord wijzigen uitgeschakeld (vereist "
            "eenmalig ‘Rechten instellen’). Bij ‘Naar Windows’ of uitschakelen wordt alles weer opgeheven."))
        rights_row = QHBoxLayout()
        self.btn_rights = QPushButton("Rechten instellen (eenmalig)")
        self.btn_rights.clicked.connect(self._grant_rights)
        rights_row.addWidget(self.btn_rights)
        self.kiosk_status = _status("")
        rights_row.addWidget(self.kiosk_status, 1)
        ll.addLayout(rights_row)
        self._update_kiosk_status()
        lay.addWidget(lock)

        pw_box = QGroupBox("Wachtwoord")
        form = QFormLayout(pw_box)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        default = not self.data["settings_password_hash"]
        form.addRow("", _status(
            "Dit wachtwoord beveiligt de instellingen en ‘Naar Windows’ bij afsluiten. "
            + ("Nu is het standaardwachtwoord actief; wijzig het hieronder."
               if default else "Er is een eigen wachtwoord ingesteld.")))
        self.pw_new = QLineEdit()
        self.pw_new.setEchoMode(QLineEdit.Password)
        self.pw_new.setPlaceholderText("leeg laten = niet wijzigen")
        self.pw_repeat = QLineEdit()
        self.pw_repeat.setEchoMode(QLineEdit.Password)
        form.addRow("Nieuw wachtwoord", self.pw_new)
        form.addRow("Herhaal wachtwoord", self.pw_repeat)
        self.pw_status = _status("")
        form.addRow("", self.pw_status)
        lay.addWidget(pw_box)
        lay.addWidget(self._updates_box())
        lay.addStretch()
        return w

    def _updates_box(self):
        box = QGroupBox(f"Updates (nu versie {__version__})")
        form = QFormLayout(box)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        u = self.data["updates"]
        self.up_repo = QLineEdit(u["repo"])
        self.up_repo.setPlaceholderText("eigenaar/naam, bv. lex-vd-poel/CinemaViewer")
        self.up_token = QLineEdit(u["token"])
        self.up_token.setEchoMode(QLineEdit.Password)
        self.up_token.setPlaceholderText("alleen nodig voor een privé-repository")
        self.chk_up_start = QCheckBox("Bij het starten controleren op een nieuwe versie")
        self.chk_up_start.setChecked(u.get("check_at_start", True))
        row = QHBoxLayout()
        check = QPushButton("Controleren op updates")
        check.clicked.connect(self._check_update)
        self.btn_update = QPushButton("")
        self.btn_update.setObjectName("Primary")
        self.btn_update.clicked.connect(self._install_update)
        self.btn_update.hide()
        row.addWidget(check)
        row.addWidget(self.btn_update)
        row.addStretch()
        self.up_status = _status("")
        self._release = None
        self.restart_after = False  # read by the app: start again once the dialog has closed
        form.addRow("GitHub-repository", self.up_repo)
        form.addRow("Token", self.up_token)
        form.addRow("", self.chk_up_start)
        form.addRow("", row)
        form.addRow("", self.up_status)
        form.addRow("", _status(
            "Token: op GitHub → Settings → Developer settings → Fine-grained tokens, alleen deze repository, "
            "met ‘Contents: Read-only’. Bijwerken vervangt alleen het programma; instellingen en de "
            "bibliotheek blijven staan. Tijdens het afspelen wordt niet bijgewerkt."))
        if self.app.available_update:
            self._show_release(self.app.available_update)
        return box

    def _updates_values(self):
        return {"repo": self.up_repo.text().strip().strip("/"), "token": self.up_token.text().strip(),
                "check_at_start": self.chk_up_start.isChecked()}

    def _check_update(self):
        values = self._updates_values()
        self.btn_update.hide()
        self.up_status.setText("Controleren…")
        run_async(updater.latest, self._show_release, lambda err: self.up_status.setText(f"Controleren mislukt: {err}"),
                  values["repo"], values["token"])

    def _show_release(self, release):
        self._release = release
        if not release["newer"]:
            self.up_status.setText(f"Je hebt de nieuwste versie ({__version__}).")
            return
        notes = release["notes"].splitlines()
        self.up_status.setText(f"Versie {release['version']} is beschikbaar."
                               + (f"\n{chr(10).join(notes[:8])}" if notes else ""))
        self.btn_update.setText(f"Bijwerken naar {release['version']}")
        self.btn_update.show()

    def _install_update(self):
        if not self._release:
            return
        values = self._updates_values()
        self.btn_update.setEnabled(False)
        self.up_status.setText(f"Versie {self._release['version']} wordt gedownload en geïnstalleerd…")

        def done(result):
            report, restart = result
            self.data["updates"] = values
            self.restart_after = restart
            message(self, "Bijgewerkt", report + ("\n\nCinemaViewer start nu opnieuw." if restart else ""))
            self._save()

        def failed(err):
            self.btn_update.setEnabled(True)
            self.up_status.setText(f"Bijwerken mislukt: {err}")

        self.app.install_update(values["repo"], values["token"], self._release["tag"], done, failed)

    def _shell_toggled(self, on):
        # As the shell CinemaViewer always starts at sign-in; switched off again, autostart stays on.
        if on:
            self.chk_autostart.setChecked(True)
        self.chk_autostart.setEnabled(not on)

    def _save_wallpapers(self):
        from .wallpaper import save_wallpapers  # imports the screens module; only needed here
        title = self.ed_title.text().strip() or "CinemaViewer"
        paths = save_wallpapers(title, self.ed_subtitle.text().strip())
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", str(paths[0].parent)])
        else:
            subprocess.Popen(["xdg-open", str(paths[0].parent)])
        message(self, "Achtergrondafbeeldingen", "Opgeslagen in " + str(paths[0].parent) + ":\n"
                + "\n".join("• " + p.name for p in paths))

    def _apply_sign_in(self):
        """Autologon / lock screen changes need admin rights: one UAC prompt for both.
        Returns False to keep the settings dialog open."""
        autologon = self.chk_autologon.isChecked()
        lockscreen = self.chk_lockscreen.isChecked()
        change_auto = autologon != self._autologon_was
        change_lock = lockscreen != self._lockscreen_was
        if not (change_auto or change_lock):
            return True
        blob = ""
        if change_auto and autologon:
            pw = WindowsPasswordDialog(self._logon_user, self).ask()
            if pw is None:
                return False
            if not check_windows_password(self._logon_user, self._logon_domain, pw):
                message(self, "Automatisch aanmelden",
                        "Windows accepteert dit wachtwoord niet. Automatisch aanmelden is niet ingesteld, zodat de "
                        "computer bij het opstarten niet blijft hangen.")
                return False
            blob = protect_for_user(pw)
            del pw
        image = ""
        if change_lock and lockscreen:
            from .wallpaper import make_lockscreen_image
            image = str(make_lockscreen_image())
        script = sign_in_script(
            autologon=autologon if change_auto else None, user=self._logon_user, domain=self._logon_domain,
            password_blob=blob, lockscreen=lockscreen if change_lock else None, image_path=image)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            code = run_elevated(script)
        except (OSError, subprocess.SubprocessError):
            code = -1
        finally:
            QApplication.restoreOverrideCursor()
        if code != 0:
            message(self, "Windows-aanmelding", "Niet gewijzigd: de beheerdersvraag is geweigerd."
                    if code == 1223 else f"Wijzigen is mislukt (code {code}).")
            return False
        return True

    def _update_kiosk_status(self):
        ok = kiosk_writable()
        self.btn_rights.setVisible(not ok)
        self.kiosk_status.setText(
            "Rechten in orde." if ok else
            "Windows vraagt eenmalig om beheerdersrechten; daarna werkt de beperking zonder vragen.")
        self.kiosk_status.setStyleSheet(f"color: {'#7BC67E' if ok else '#E0B15A'};")

    def _grant_rights(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok = grant_kiosk_rights()
        except (OSError, subprocess.SubprocessError):
            ok = False
        finally:
            QApplication.restoreOverrideCursor()
        self._update_kiosk_status()
        if not ok:
            self.kiosk_status.setText("Niet gelukt (geannuleerd of geen beheerder).")
            self.kiosk_status.setStyleSheet("color: #E07A75;")

    # -- actions --------------------------------------------------------------
    def _identify(self):
        for i, s in enumerate(QGuiApplication.screens()):
            IdentifyOverlay(s, i + 1, screen_description(i, s))

    def _add_folder(self):
        path = pick_folder(self, "Kies een mediamap")
        if path:
            self.folders.addItem(path.replace("/", "\\"))

    def _jellyfin_login(self):
        url = self.jf_url.text().strip().rstrip("/")
        if not url or not self.jf_user.text().strip():
            self.jf_status.setText("Vul server-adres en gebruikersnaam in.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            token, user_id = JellyfinSource.login(url, self.jf_user.text().strip(), self.jf_pass.text(),
                                                  self.data["device_id"])
        except Exception as e:  # noqa: BLE001
            self.jf_status.setText(f"Inloggen mislukt: {e}")
            self.jf_status.setStyleSheet("color: #E07A75;")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.data["jellyfin"].update(url=url, username=self.jf_user.text().strip(), token=token,
                                     user_id=user_id, enabled=True)
        self.jf_enabled.setChecked(True)
        self.jf_pass.clear()
        self.jf_status.setText("Verbonden! Klik op Opslaan om de bibliotheken te laden.")
        self.jf_status.setStyleSheet("color: #7BC67E;")

    def _plex_test(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            name = PlexSource(self.px_url.text().strip(), self.px_token.text().strip(), self.data["device_id"]).test()
        except Exception as e:  # noqa: BLE001
            self.px_status.setText(f"Mislukt: {e}")
            self.px_status.setStyleSheet("color: #E07A75;")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.px_status.setText(f"Verbonden met ‘{name}’.")
        self.px_status.setStyleSheet("color: #7BC67E;")
        self.px_enabled.setChecked(True)

    def _device_test(self, label, test, *args):
        if not args[0]:
            label.setText("Vul eerst het IP-adres in.")
            label.setStyleSheet("color: #E07A75;")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            text, ok = test(*args), True
        except (OSError, DeviceError) as e:
            text, ok = f"Mislukt: {e}", False
        finally:
            QApplication.restoreOverrideCursor()
        label.setText(text if not ok else f"Verbonden: {text}.")
        label.setStyleSheet(f"color: {'#7BC67E' if ok else '#E07A75'};")

    def _projector_cfg(self):
        return {"protocol": self.pj_protocol.currentData(), "host": self.pj_host.text().strip(),
                "password": self.pj_password.text(), "serial_port": self.pj_port.currentText().strip(),
                "baud": self.pj_baud.currentData(), "lamp_mode": self.pj_lamp_mode.currentData(),
                "lamp_power": self.pj_lamp_power.currentData(), "lamp_warn_hours": self.pj_warn.value()}

    def _projector_protocol_changed(self):
        proto = self.pj_protocol.currentData()
        infocus = proto != "pjlink"
        form = self.pj_form
        for widget, visible in ((self.pj_host, proto != "infocus_serial"), (self.pj_password, proto == "pjlink"),
                                (self.pj_port, proto == "infocus_serial"), (self.pj_baud, proto == "infocus_serial"),
                                (self.pj_lamp_mode, infocus), (self.pj_lamp_power, infocus), (self.pj_warn, infocus)):
            form.setRowVisible(widget, visible)
        self.pj_help.setText({
            "pjlink": "Sluit de projector aan op het netwerk (LAN-poort) en zet in het projectormenu PJLink / "
                      "netwerkbesturing aan, met stand-by-stroom op ‘netwerk’ (niet ‘eco’/laag), anders kan hij niet "
                      "via het netwerk worden aangezet. Geef de projector bij voorkeur een vast IP-adres.",
            "infocus_net": "Voor InFocus-projectoren zonder PJLink (bv. IN5530-serie). Zet in het projectormenu "
                           "Setup → Always-On Functions → Network op Yes (anders is hij in stand-by onbereikbaar) en "
                           "geef hem een vast IP-adres. Open daarna zijn IP-adres in een browser, log in als "
                           "Administrator en zet onder Network Settings ‘Command and Control Pass-Through’ aan.",
            "infocus_serial": "Verbind de RS-232-poort van de projector met een USB-naar-serieel-kabel (recht, geen "
                              "null-modem). ‘Automatisch’ probeert de gangbare snelheden en kiest de snelheid waarop "
                              "de projector antwoordt.",
        }[proto] + ("" if not infocus else
                    "\nTwee lampen: CinemaViewer leest de branduren en status van beide lampen, zet desgewenst de "
                    "lampmodus (beide of één) en het vermogen bij het aanzetten, en waarschuwt als een lamp niet "
                    "brandt, de projector een storing meldt of een lamp de ingestelde uren bereikt."))

    def _projector_test(self):
        cfg = self._projector_cfg()
        if cfg["protocol"] == "pjlink":
            self._device_test(self.pj_status, Projector.test, cfg["host"], cfg["password"])
            return
        if not (cfg["serial_port"] if cfg["protocol"] == "infocus_serial" else cfg["host"]):
            self.pj_status.setText("Kies eerst de seriële poort." if cfg["protocol"] == "infocus_serial"
                                   else "Vul eerst het IP-adres in.")
            self.pj_status.setStyleSheet("color: #E07A75;")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            text, ok = InFocusProjector.test(cfg), True
        except (OSError, DeviceError) as e:
            text, ok = f"Mislukt: {e}", False
        finally:
            QApplication.restoreOverrideCursor()
        self.pj_status.setText(text)
        self.pj_status.setStyleSheet(f"color: {'#7BC67E' if ok else '#E07A75'};")

    def _receiver_test(self):
        host = self.av_host.text().strip()
        running = self.app.devices.receiver
        if running is not None and running.host == host:
            # The receiver allows one connection: ask through the one CinemaViewer already has.
            self._device_test(self.av_status, lambda _host: running.ask_status(), host)
        else:
            self._device_test(self.av_status, Receiver.test, host)

    # -- licht (DMX) -------------------------------------------------------------
    def _dmx_tab(self):
        w, form = self._page()
        d = self.data["dmx"]
        self.dmx_enabled = QCheckBox("Lampen via DMX aansturen: bij het starten gaan ze aan, bij afsluiten uit")
        self.dmx_enabled.setChecked(d["enabled"])
        form.addRow("", self.dmx_enabled)
        self.dmx_backend = QComboBox()
        self.dmx_backend.addItems(["ArtNet (netwerk)", "USB-DMX adapter"])
        self.dmx_backend.setCurrentIndex(1 if d["backend"] == "usb" else 0)
        self.dmx_artnet_mode = QComboBox()
        self.dmx_artnet_mode.addItems(["Broadcast", "Vast IP-adres"])
        self.dmx_artnet_mode.setCurrentIndex(1 if d["artnet_mode"] == "ip" else 0)
        self.dmx_artnet_mode.setFixedWidth(160)
        self.dmx_ip = QLineEdit(d["artnet_ip"])
        self.dmx_ip.setPlaceholderText("192.168.1.30")
        self.dmx_universe = QSpinBox()
        self.dmx_universe.setRange(0, 511)
        self.dmx_universe.setValue(int(d["universe"]))
        self.dmx_universe.setFixedWidth(160)
        self.dmx_port = QComboBox()
        self.dmx_port.setEditable(True)
        self.dmx_port.addItems(serial_ports())
        self.dmx_port.setCurrentText(d["usb_port"])
        self.dmx_protocol = QComboBox()
        self.dmx_protocol.addItems(["Automatisch", "Enttec Open (simple DMX)", "Enttec Pro (FTDI)"])
        self.dmx_protocol.setCurrentIndex({"auto": 0, "open": 1, "pro": 2}.get(d.get("usb_protocol", "auto"), 0))
        self.dmx_protocol.setFixedWidth(200)
        form.addRow("Uitgang", self.dmx_backend)
        form.addRow("ArtNet-bestemming", self.dmx_artnet_mode)
        form.addRow("IP-adres node", self.dmx_ip)
        form.addRow("Universe", self.dmx_universe)
        form.addRow("Seriële poort", self.dmx_port)
        form.addRow("USB-protocol", self.dmx_protocol)
        form.addRow("", _status(
            "ArtNet stuurt via het netwerk naar een ArtNet-node (broadcast of vast IP-adres). USB-DMX werkt "
            "met Enttec-achtige adapters; ‘Automatisch’ kiest het protocol aan de hand van de adapter."))

        self.dmx_lamps = QTableWidget(0, 3)
        self.dmx_lamps.setHorizontalHeaderLabels(["Naam", "Type", "Startadres"])
        self.dmx_lamps.verticalHeader().setVisible(False)
        self.dmx_lamps.setColumnWidth(0, 170)
        self.dmx_lamps.setColumnWidth(1, 110)
        self.dmx_lamps.setColumnWidth(2, 90)
        self.dmx_lamps.setMinimumHeight(110)
        self.dmx_lamps.setMaximumHeight(200)
        form.addRow("Lampen", self.dmx_lamps)
        add = QPushButton("+ Lamp toevoegen")
        add.clicked.connect(lambda: self._dmx_add_lamp_row())
        rem = QPushButton("Lamp verwijderen")
        rem.clicked.connect(self._dmx_remove_lamp)
        row_btns = QHBoxLayout()
        row_btns.addWidget(add)
        row_btns.addWidget(rem)
        row_btns.addStretch()
        form.addRow("", row_btns)
        for lamp in d["lamps"]:
            self._dmx_add_lamp_row(lamp)
        form.addRow("", _status(
            "Eén DMX-universe van 512 kanalen. Startadres 1–512; het type bepaalt het aantal kanalen: "
            "dimmer = 1, rgb = 3 (rood/groen/blauw), rgbw = 4 (+ wit), cct = 2 (warm/koud)."))

        self.dmx_states = {}
        for name in ("film", "pauze", "muziek"):
            st = d["states"].get(name, {})
            box = QGroupBox(STATE_NAMES[name])
            vbox = QVBoxLayout(box)
            vbox.setContentsMargins(14, 10, 14, 10)
            top = QHBoxLayout()
            top.addWidget(QLabel("Overgang"))
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 30.0)
            spin.setSingleStep(0.5)
            spin.setSuffix(" s")
            spin.setValue(float(st.get("transition", 2.0)))
            top.addWidget(spin)
            top.addStretch()
            vbox.addLayout(top)
            container = QWidget()
            vbox.addWidget(container)
            test = QPushButton("Deze staat testen")
            test.clicked.connect(lambda _, n=name: self._dmx_test(n))
            vbox.addWidget(test, alignment=Qt.AlignLeft)
            self.dmx_states[name] = {"transition": spin, "vbox": vbox, "container": container,
                                     "rows": [], "seed": list(st.get("lamps", []))}
            form.addRow("", box)

        m = d["music"]
        self.dmx_music_enabled = QCheckBox("Lampen laten meebewegen met afgespeelde nummers")
        self.dmx_music_enabled.setChecked(m["enabled"])
        self.dmx_music_mode = QComboBox()
        self.dmx_music_mode.addItems(["Mee met de bas", "Kleurencyclus", "Spectrum over de lampen"])
        self.dmx_music_mode.setCurrentIndex(("bass", "cycle", "spectrum").index(m["mode"]))
        self.dmx_music_mode.setFixedWidth(220)
        self.dmx_music_sens = QSpinBox()
        self.dmx_music_sens.setRange(0, 100)
        self.dmx_music_sens.setValue(int(m["sensitivity"]))
        self.dmx_music_sens.setFixedWidth(160)
        form.addRow("", self.dmx_music_enabled)
        form.addRow("Muziekmodus", self.dmx_music_mode)
        form.addRow("Gevoeligheid", self.dmx_music_sens)
        form.addRow("", _status(
            "Muziekmodus geldt bij afspelen van een nummer (geen film). CinemaViewer luisters mee via een "
            "extra, stille VLC-verbinding; de lampen reageren dan binnen een tiende seconde."))
        self._rebuild_dmx_states()
        return w

    def _dmx_lamp_list(self):
        lamps = []
        for r in range(self.dmx_lamps.rowCount()):
            name_item = self.dmx_lamps.item(r, 0)
            combo = self.dmx_lamps.cellWidget(r, 1)
            addr_item = self.dmx_lamps.item(r, 2)
            text = addr_item.text().strip() if addr_item else ""
            lamps.append({"name": (name_item.text().strip() if name_item else "") or f"Lamp {r + 1}",
                          "type": combo.currentText() if combo else "rgbw",
                          "address": int(text) if text.isdigit() else 1})
        return lamps

    def _dmx_add_lamp_row(self, lamp=None):
        lamp = lamp or {}
        r = self.dmx_lamps.rowCount()
        self.dmx_lamps.insertRow(r)
        self.dmx_lamps.setItem(r, 0, QTableWidgetItem(lamp.get("name", f"Lamp {r + 1}")))
        combo = QComboBox()
        combo.addItems(LAMP_TYPES)
        combo.setCurrentText(lamp.get("type", "rgbw"))
        combo.currentIndexChanged.connect(self._rebuild_dmx_states)
        self.dmx_lamps.setCellWidget(r, 1, combo)
        self.dmx_lamps.setItem(r, 2, QTableWidgetItem(str(lamp.get("address", 1))))
        self._rebuild_dmx_states()

    def _dmx_remove_lamp(self):
        r = self.dmx_lamps.currentRow()
        if r < 0:
            r = self.dmx_lamps.rowCount() - 1
        if r >= 0:
            self.dmx_lamps.removeRow(r)
            self._rebuild_dmx_states()

    def _rebuild_dmx_states(self):
        if not getattr(self, "dmx_states", None):
            return
        lamps = self._dmx_lamp_list()
        for widgets in self.dmx_states.values():
            rows_data = widgets.pop("seed", None)
            if rows_data is None:
                rows_data = [{"color": list(row["color"]), "level": row["slider"].value()}
                             for row in widgets["rows"]]
            old = widgets["container"]
            widgets["vbox"].removeWidget(old)
            old.deleteLater()
            container = QWidget()
            grid = QGridLayout(container)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            rows = []
            for i, lamp in enumerate(lamps):
                entry = rows_data[i] if i < len(rows_data) else {}
                color = list(entry.get("color") or []) or ([255, 255, 255] if lamp["type"] != "dimmer" else [])
                level = int(max(0, min(100, float(entry.get("level", 50)))))
                grid.addWidget(QLabel(lamp["name"]), i, 0)
                row = {"color": color, "type": lamp["type"], "btn": None}
                col = 1
                if lamp["type"] != "dimmer":
                    btn = QPushButton()
                    btn.setFixedSize(30, 20)
                    btn.clicked.connect(lambda _, r=row: self._pick_color(r))
                    self._dmx_paint_btn(btn, color, lamp["type"])
                    grid.addWidget(btn, i, 1)
                    row["btn"] = btn
                    col = 2
                slider = QSlider(Qt.Horizontal)
                slider.setRange(0, 100)
                slider.setValue(level)
                slider.setFixedWidth(150)
                grid.addWidget(slider, i, col)
                val = QLabel(f"{level}%")
                slider.valueChanged.connect(lambda v, l=val: l.setText(f"{v}%"))
                grid.addWidget(val, i, col + 1)
                row["slider"] = slider
                rows.append(row)
            widgets["container"] = container
            widgets["rows"] = rows
            widgets["vbox"].addWidget(container)

    @staticmethod
    def _dmx_paint_btn(btn, color, kind):
        c = (list(color) + [0, 0, 0, 0])[:4]
        rgb = (c[0], 0, c[1]) if kind == "cct" else (c[0], c[1], c[2])
        btn.setStyleSheet(f"background: rgb({rgb[0]},{rgb[1]},{rgb[2]}); border: 1px solid #555;")

    def _pick_color(self, row):
        c = (list(row["color"]) + [0, 0, 0, 0])[:4]
        if row["type"] == "cct":
            col = QColorDialog.getColor(QColor(c[0], 0, c[1]), self, "Kleur: rood = warm, blauw = koud")
            if col.isValid():
                row["color"] = [col.red(), col.blue()]
        elif row["type"] == "rgbw":
            col = QColorDialog.getColor(QColor(c[0], c[1], c[2]), self)
            if col.isValid():
                white = min(col.red(), col.green(), col.blue())
                row["color"] = [col.red() - white, col.green() - white, col.blue() - white, white]
        else:
            col = QColorDialog.getColor(QColor(c[0], c[1], c[2]), self)
            if col.isValid():
                row["color"] = [col.red(), col.green(), col.blue()]
        self._dmx_paint_btn(row["btn"], row["color"], row["type"])

    def _collect_dmx(self):
        states = {}
        for name, widgets in self.dmx_states.items():
            states[name] = {"transition": widgets["transition"].value(),
                            "lamps": [{"color": list(row["color"]), "level": row["slider"].value()}
                                      for row in widgets["rows"]]}
        self.data["dmx"].update(
            enabled=self.dmx_enabled.isChecked(),
            backend="usb" if self.dmx_backend.currentIndex() == 1 else "artnet",
            artnet_mode="ip" if self.dmx_artnet_mode.currentIndex() == 1 else "broadcast",
            artnet_ip=self.dmx_ip.text().strip(), universe=self.dmx_universe.value(),
            usb_port=self.dmx_port.currentText().strip(),
            usb_protocol=("auto", "open", "pro")[self.dmx_protocol.currentIndex()],
            lamps=self._dmx_lamp_list(), states=states)
        self.data["dmx"]["music"].update(
            enabled=self.dmx_music_enabled.isChecked(),
            mode=("bass", "cycle", "spectrum")[self.dmx_music_mode.currentIndex()],
            sensitivity=self.dmx_music_sens.value())

    def _dmx_test(self, name):
        """Past de staat direct toe met de waarden in dit venster (opslaan of annuleren maakt definitief)."""
        self._collect_dmx()
        self.app.cfg.data["dmx"] = copy.deepcopy(self.data["dmx"])
        self.app.dmx.start()
        self.app.dmx.set_state(name)

    def reject(self):
        if self.app.cfg.data["dmx"] != self._dmx_original:
            self.app.cfg.data["dmx"] = copy.deepcopy(self._dmx_original)
            self.app.dmx.stop_output()
            self.app.dmx.start()
        super().reject()

    def _save(self):
        d = self.data
        try:
            d["forced_outputs"] = self._forced_outputs()
        except ValueError as e:
            self.out_status.setText(str(e))
            self.out_status.setStyleSheet("color: #E07A75;")
            self.tabs.setCurrentIndex(1)
            return
        d["audio_output"] = dict(self.cb_audio.currentData())
        new_pw = self.pw_new.text()
        if new_pw or self.pw_repeat.text():
            problem = ""
            if new_pw != self.pw_repeat.text():
                problem = "De wachtwoorden zijn niet gelijk."
            elif len(new_pw) < MIN_PASSWORD_LENGTH:
                problem = f"Gebruik minstens {MIN_PASSWORD_LENGTH} tekens."
            if problem:
                self.pw_status.setText(problem)
                self.pw_status.setStyleSheet("color: #E07A75;")
                self.tabs.setCurrentIndex(self.tabs.count() - 1)
                return
            d["settings_password_hash"] = hash_password(new_pw)
        d["kiosk_mode"] = self.chk_kiosk.isChecked()
        d["hide_taskbar"] = self.chk_taskbar.isChecked()
        d["intermission_minutes"] = self.sp_minutes.value()
        d["intermission_countdown"] = self.sp_countdown.value()
        d["intermission_rewind"] = self.sp_rewind.value()
        if not self._apply_sign_in():
            return  # stay in the dialog (password cancelled or wrong)
        shell = self.chk_shell.isChecked()
        autostart = self.chk_autostart.isChecked() and not shell  # as the shell it starts anyway
        try:
            if shell != shell_enabled() or shell:
                set_shell(shell)  # also refreshes the path when enabled
            if autostart != autostart_enabled() or autostart:
                set_autostart(autostart)
        except OSError as e:
            message(self, "Automatisch starten", f"Kon automatisch starten niet instellen:\n{e}")
        d["control_screen"] = {"name": self.cb_control.currentData(), "index": self.cb_control.currentIndex()}
        d["video_screen"] = {"name": self.cb_video.currentData(), "index": self.cb_video.currentIndex()}
        d["placeholder_title"] = self.ed_title.text().strip() or "CinemaViewer"
        d["placeholder_subtitle"] = self.ed_subtitle.text().strip()
        d["desktop_wallpaper"] = self.chk_wallpaper.isChecked()
        d["lock_cursor"] = self.chk_lock_cursor.isChecked()
        d["now_showing_on_other_screens"] = self.chk_now.isChecked()
        d["preview_on_other_screens"] = self.chk_preview.isChecked()
        d["report_progress"] = self.chk_report.isChecked()
        jf_url = self.jf_url.text().strip().rstrip("/")
        if jf_url != d["jellyfin"]["url"] or self.jf_user.text().strip() != d["jellyfin"]["username"]:
            d["jellyfin"].update(token="", user_id="")  # server/user changed: log in again
        d["jellyfin"].update(enabled=self.jf_enabled.isChecked(), url=jf_url, username=self.jf_user.text().strip())
        d["plex"].update(enabled=self.px_enabled.isChecked(), url=self.px_url.text().strip().rstrip("/"),
                         token=self.px_token.text().strip())
        d["updates"] = self._updates_values()
        d["local_folders"] = [self.folders.item(i).text() for i in range(self.folders.count())]
        if not self._apply_shares():
            self.tabs.setCurrentIndex(4)  # Lokaal, USB & disc
            return
        d["show_usb_drives"] = self.chk_usb.isChecked()
        d["show_disc_drives"] = self.chk_disc.isChecked()
        unchanged = self._radio_was_default and self._radio == radio.default_stations()
        d["radio"] = {"enabled": self.chk_radio.isChecked(), "stations": None if unchanged else self._radio}
        d["projector"].update(enabled=self.pj_enabled.isChecked(), auto_on=self.pj_auto.isChecked(),
                              **self._projector_cfg())
        d["displaycast"].update(name=self.dc_name.text().strip(), autostart=self.dc_auto.isChecked())
        d["receiver"].update(enabled=self.av_enabled.isChecked(), host=self.av_host.text().strip(),
                             input=self.av_input.currentText().strip().upper() or "BD",
                             volume_control=self.av_volume.isChecked(), max_volume_db=self.av_max.value())
        self._collect_dmx()
        self.accept()
