"""Dialogs without the Windows title bar, in the CinemaViewer style."""

import ctypes

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QSpinBox, QVBoxLayout)

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def round_corners(widget):
    """Windows 11 rounds frameless windows on request; older Windows ignores it."""
    try:
        pref = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(int(widget.winId())),
                                                   DWMWA_WINDOW_CORNER_PREFERENCE,
                                                   ctypes.byref(pref), ctypes.sizeof(pref))
    except (AttributeError, OSError):
        pass


class FramelessDialog(QDialog):
    """QDialog without title bar. Drag it by its background; Esc closes it (reject)."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("Frameless")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._drag_offset = None

    def showEvent(self, e):
        super().showEvent(e)
        round_corners(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None
        super().mouseReleaseEvent(e)


def message(parent, title, text):
    """Simple notice with an OK button (replaces QMessageBox, which always has a title bar)."""
    _notice(parent, title, text, "OK").exec()


def ask(parent, title, text, yes="Ja", no="Nee"):
    """Yes/no question; True when the first button was chosen. Esc or 'no' gives False."""
    return _notice(parent, title, text, yes, no).exec() == QDialog.Accepted


def _notice(parent, title, text, ok_text, cancel_text=None):
    dlg = FramelessDialog(parent)
    dlg.setWindowTitle(title)  # still used by the taskbar / screen readers
    dlg.setMinimumWidth(420)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(24, 22, 24, 20)
    lay.setSpacing(14)
    head = QLabel(title)
    head.setObjectName("H1")
    lay.addWidget(head)
    body = QLabel(text)
    body.setWordWrap(True)
    body.setObjectName("Body")
    lay.addWidget(body)
    row = QHBoxLayout()
    row.addStretch()
    if cancel_text:
        cancel = QPushButton(cancel_text)
        cancel.clicked.connect(dlg.reject)
        row.addWidget(cancel)
    ok = QPushButton(ok_text)
    ok.setObjectName("Primary")
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    row.addWidget(ok)
    lay.addLayout(row)
    return dlg


class ImportDialog(FramelessDialog):
    """Before copying a file or disc to the bibliotheek: is it a film or an episode, and what is it called?
    `info` is the guess from cache.guess(); result() gives the (edited) dict back."""

    def __init__(self, item, info, parent=None):
        super().__init__(parent)
        self._info = info
        self.setWindowTitle("Naar bibliotheek kopiëren")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(14)
        head = QLabel("Naar bibliotheek kopiëren")
        head.setObjectName("H1")
        lay.addWidget(head)
        source = QLabel(item.extra.get("path") or item.title)
        source.setObjectName("Body")
        source.setWordWrap(True)
        lay.addWidget(source)
        kinds = QHBoxLayout()
        self.rb_film = QRadioButton("Film")
        self.rb_episode = QRadioButton("Aflevering van een serie")
        kinds.addWidget(self.rb_film)
        kinds.addWidget(self.rb_episode)
        kinds.addStretch()
        lay.addLayout(kinds)
        form = QFormLayout()
        form.setSpacing(10)
        self.title = QLineEdit(info["title"] or "")
        self.year = QSpinBox()
        self.year.setRange(0, 2100)
        self.year.setSpecialValueText("–")
        self.year.setValue(info.get("year") or 0)
        self.series = QLineEdit(info.get("series") or "")
        self.season = QSpinBox()
        self.season.setRange(0, 99)
        self.season.setValue(info.get("season") or 1)
        self.episode = QSpinBox()
        self.episode.setRange(0, 999)
        self.episode.setValue(info.get("episode") or 1)
        form.addRow("Titel", self.title)
        form.addRow("Jaar", self.year)
        form.addRow("Serie", self.series)
        form.addRow("Seizoen", self.season)
        form.addRow("Aflevering", self.episode)
        self._form = form
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Annuleren")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Kopiëren")
        ok.setObjectName("Primary")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.rb_film.toggled.connect(self._update)
        (self.rb_episode if info["category"] == "episode" else self.rb_film).setChecked(True)
        self._update()

    def _update(self):
        film = self.rb_film.isChecked()
        for widget, show in ((self.year, film), (self.series, not film), (self.season, not film),
                             (self.episode, not film)):
            widget.setVisible(show)
            self._form.labelForField(widget).setVisible(show)

    def _accept(self):
        if self.rb_episode.isChecked() and not self.series.text().strip():
            self.series.setFocus()
            return
        self.accept()

    def result_info(self):
        if self.rb_film.isChecked():
            return {"category": "film", "title": self.title.text().strip(), "year": self.year.value() or None,
                    "series": "", "season": None, "episode": None}
        return {"category": "episode", "title": self.title.text().strip(), "year": None,
                "series": self.series.text().strip(), "season": self.season.value(), "episode": self.episode.value(),
                "series_image": self._info.get("series_image", "")}


def pick_folder(parent, title):
    """Folder picker in the app style (the native Windows picker always has a title bar)."""
    dlg = QFileDialog(parent, title)
    dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dlg.setOption(QFileDialog.DontUseNativeDialog, True)
    dlg.setOption(QFileDialog.ShowDirsOnly, True)
    dlg.setFileMode(QFileDialog.Directory)
    dlg.setLabelText(QFileDialog.LookIn, "Zoeken in:")      # missing from Qt's Dutch translation
    dlg.setLabelText(QFileDialog.FileType, "Bestandstype:")
    dlg.setObjectName("Frameless")
    dlg.resize(820, 520)
    round_corners(dlg)
    if dlg.exec() == QDialog.Accepted and dlg.selectedFiles():
        return dlg.selectedFiles()[0]
    return ""
