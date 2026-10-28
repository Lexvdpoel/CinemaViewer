"""Presentations (PowerPoint, Impress, PDF) on the projection, with a presenter view on the operator screen.

Everything goes through LibreOffice (no Microsoft software needed):
- the projection shows a real Impress slide show (animations, transitions, video), run by impress_show.py
  with LibreOffice's own Python; CinemaViewer sends 'next', 'previous', ... and follows which slide is shown;
- the presenter view (slides overview, next slide, notes) uses a PDF made once per file version, and the
  speaker notes read from the file itself.
When the slide show cannot run (PDF files, LibreOffice missing or failing), the projection shows the PDF's
slides without animations.
"""

import hashlib
import json
import os
import queue
import posixpath
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QPushButton,
                               QSizePolicy, QTextEdit, QVBoxLayout, QWidget)

from .config import DATA_DIR
from .style import (ACCENT, BORDER, G_CHECK, G_LEFT, G_PAUSE, G_PLAY, G_REFRESH, G_RIGHT, G_STOP, MUTED, PANEL2,
                    TEXT, glyph_icon)

try:
    from PySide6.QtPdf import QPdfDocument
except ImportError:  # Windows installation without the PDF part (see README)
    QPdfDocument = None

PRESENTATION_EXT = {".ppt", ".pptx", ".pps", ".ppsx", ".pot", ".potx", ".pptm", ".ppsm", ".odp", ".otp", ".pdf"}
BINARY_EXT = {".ppt", ".pps", ".pot"}  # old PowerPoint format: no XML to read the notes from
CONVERTED_DIR = DATA_DIR / "presentaties"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

class ConvertError(RuntimeError):
    pass


def _soffice():
    for name in ("soffice", "libreoffice"):
        if found := shutil.which(name):
            return found
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
            exe = Path(base) / "LibreOffice" / "program" / "soffice.exe"
            if base and exe.is_file():
                return str(exe)
    return None


def _soffice_convert(soffice, path, fmt, work):
    # own profile: no clash with an open LibreOffice and no first-start questions
    profile = (DATA_DIR / "libreoffice").as_uri()
    cmd = [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--nolockcheck",
           "--convert-to", fmt, "--outdir", str(work), str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, creationflags=NO_WINDOW)
    made = work / f"{path.stem}.{fmt}"
    if not made.is_file():
        raise ConvertError("LibreOffice kon de presentatie niet omzetten"
                           + (f" ({result.stderr.strip()[-200:]})" if result.stderr.strip() else ""))
    return made


def prepare(path):
    """(PDF, speaker notes per slide) for the presentation at path. Converted once per file version and kept
    in the data folder; runs off the UI thread."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return path, []
    try:
        st = path.stat()
    except OSError as e:
        raise ConvertError(f"bestand niet leesbaar: {e.strerror or e}") from e
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    target, notes_file = CONVERTED_DIR / f"{key}.pdf", CONVERTED_DIR / f"{key}.json"
    if target.is_file():
        try:
            return target, json.loads(notes_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return target, []
    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cv-present-", dir=CONVERTED_DIR))
    try:
        soffice = _soffice()
        notes_source = path
        if soffice:
            made = _soffice_convert(soffice, path, "pdf", work)
            if path.suffix.lower() in BINARY_EXT:  # old binary format: notes via a .pptx copy
                try:
                    notes_source = _soffice_convert(soffice, path, "pptx", work)
                except ConvertError:
                    notes_source = None
        else:
            raise ConvertError("hiervoor is LibreOffice (gratis, libreoffice.org) nodig op deze computer")
        notes = read_notes(notes_source) if notes_source else []
        made.replace(target)
        notes_file.write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
        return target, notes
    except subprocess.TimeoutExpired as e:
        raise ConvertError("omzetten duurde te lang") from e
    finally:
        shutil.rmtree(work, ignore_errors=True)


_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_ODF = {"office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
        "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
        "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
        "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0"}


def read_notes(path):
    """Speaker notes of the slides that are shown (hidden slides are not in the PDF either); [] if unknown."""
    try:
        suffix = Path(path).suffix.lower()
        if suffix in (".odp", ".otp"):
            return _odp_notes(path)
        if suffix not in BINARY_EXT and suffix != ".pdf":
            return _pptx_notes(path)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError):
        pass
    return []


def _rels(z, part):
    folder, name = posixpath.split(part)
    try:
        root = ET.fromstring(z.read(f"{folder}/_rels/{name}.rels"))
    except KeyError:
        return []
    out = []
    for rel in root.iter(_REL):
        target = rel.get("Target", "")
        target = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out.append((rel.get("Id"), rel.get("Type", ""), target))
    return out


def _pptx_notes(path):
    with zipfile.ZipFile(path) as z:
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
        targets = {rid: target for rid, _type, target in _rels(z, "ppt/presentation.xml")}
        notes = []
        for sid in pres.iter(_P + "sldId"):
            slide_part = targets[sid.get(_R + "id")]
            if ET.fromstring(z.read(slide_part)).get("show") == "0":
                continue  # hidden slide
            text = ""
            for _rid, kind, target in _rels(z, slide_part):
                if kind.endswith("/notesSlide"):
                    text = _pptx_note_text(ET.fromstring(z.read(target)))
            notes.append(text)
        return notes


def _pptx_note_text(root):
    """The notes placeholder; without one (some exports) the free text boxes on the notes page."""
    body, loose = [], []
    for shape in root.iter(_P + "sp"):
        ph = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
        text = "\n".join("".join(t.text or "" for t in para.iter(_A + "t")) for para in shape.iter(_A + "p")).strip()
        if ph is None:
            loose.append(text)
        elif ph.get("type") == "body":
            body.append(text)
        # other placeholders: slide picture, slide number, header, footer, date
    return "\n".join(t for t in (body or loose) if t)


def _odp_notes(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    ns = _ODF
    hidden_styles = {st.get(f"{{{ns['style']}}}name") for st in root.iter(f"{{{ns['style']}}}style")
                     if any(pr.get(f"{{{ns['presentation']}}}visibility") == "hidden"
                            for pr in st.iter(f"{{{ns['style']}}}drawing-page-properties"))}
    notes = []
    for page in root.iter(f"{{{ns['draw']}}}page"):
        if page.get(f"{{{ns['draw']}}}style-name") in hidden_styles:
            continue
        text = ""
        block = page.find(f"{{{ns['presentation']}}}notes")
        if block is not None:
            for frame in block.iter(f"{{{ns['draw']}}}frame"):
                if frame.get(f"{{{ns['presentation']}}}class") == "notes":
                    text = "\n".join("".join(par.itertext()) for par in frame.iter()
                                     if par.tag.endswith("}p")).strip()
        notes.append(text)
    return notes


def _office_python(soffice):
    """The Python that has LibreOffice's 'uno' module."""
    if os.name == "nt":
        exe = Path(soffice).with_name("python.exe")
        return str(exe) if exe.is_file() else None
    return "/usr/bin/python3" if Path("/usr/bin/python3").is_file() else None


class SlideShow(QThread):
    """Drives impress_show.py: commands go out, the shown slide comes back (polled a few times a second).
    LibreOffice's messages go to diavoorstelling.log in the data folder."""
    status = Signal(dict)
    failed = Signal(str)

    def __init__(self, path, screen_rect, slide=0):
        super().__init__()
        self.path = str(path)
        self.screen = [screen_rect.x(), screen_rect.y(), screen_rect.width(), screen_rect.height()]
        self.slide = slide
        self._commands = queue.Queue()
        self._ended = False
        self._proc = None

    @staticmethod
    def supported(path):
        return Path(path).suffix.lower() != ".pdf" and _soffice() is not None

    def send(self, cmd, **args):
        self._commands.put(dict(args, cmd=cmd))

    def end(self):
        """Ends the show without waiting: LibreOffice closes in the background (see _closing)."""
        self._ended = True
        self._commands.put({"cmd": "end"})
        if self.isRunning():
            _closing.add(self)
            self.finished.connect(lambda: _closing.discard(self))
            QTimer.singleShot(12000, self.kill)  # a hanging LibreOffice must not stay on the projection

    def kill(self):
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:  # the helper and the LibreOffice it started
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                               creationflags=NO_WINDOW)
            else:
                os.killpg(proc.pid, 9)
        except OSError:
            proc.kill()

    def run(self):
        soffice = _soffice()
        python = _office_python(soffice) if soffice else None
        if not python:
            self.failed.emit("LibreOffice met Python-koppeling niet gevonden")
            return
        helper = Path(__file__).with_name("impress_show.py")
        profile = (DATA_DIR / "libreoffice-show").as_uri()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "QT_"))}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        extra = ({"creationflags": NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                 else {"start_new_session": True})
        try:
            with open(DATA_DIR / "diavoorstelling.log", "w", encoding="utf-8") as log:
                self._proc = proc = subprocess.Popen(
                    [python, "-I", str(helper), soffice, profile, f"cinemaviewer{os.getpid()}"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8",
                    env=env, **extra)
        except OSError as e:
            self.failed.emit(f"kon LibreOffice niet starten ({e})")
            return

        def ask(msg):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                raise OSError("LibreOffice is gestopt")
            return json.loads(line)

        try:
            hello = json.loads(proc.stdout.readline() or "{}")
            if not hello.get("ok"):
                raise OSError(hello.get("error") or "LibreOffice startte niet")
            answer = ask({"cmd": "open", "path": self.path, "screen": self.screen, "slide": self.slide})
            if not answer.get("ok"):
                raise OSError(answer.get("error"))
            self.status.emit(answer)
            while not self._ended:
                try:
                    msg = self._commands.get(timeout=0.25)
                except queue.Empty:
                    msg = {"cmd": "status"}
                answer = ask(msg)
                if msg["cmd"] == "end":
                    break
                if answer.get("ok"):
                    self.status.emit(answer)
        except (OSError, ValueError) as e:
            if not self._ended:
                self.failed.emit(str(e) or "de diavoorstelling stopte")
        finally:
            try:
                proc.stdin.close()
                proc.wait(15)
            except (OSError, subprocess.TimeoutExpired):
                self.kill()


_closing = set()  # slide shows still closing; kept alive until their thread is done


def wait_for_closing(timeout_ms=15000):
    """At exit: give LibreOffice the chance to close."""
    for show in list(_closing):
        if not show.wait(timeout_ms):
            show.kill()


def available():
    return QPdfDocument is not None


class Presentation(QObject):
    """The open presentation: its pages, the current slide and whether the screen is blacked out."""
    changed = Signal()

    def __init__(self, item, pdf_path, notes=()):
        super().__init__()
        self.item = item
        self.doc = QPdfDocument(self)
        self.doc.load(str(pdf_path))
        if self.doc.status() != QPdfDocument.Status.Ready or self.doc.pageCount() == 0:
            raise ConvertError("de presentatie bevat geen leesbare dia's")
        self.count = self.doc.pageCount()
        notes = list(notes)
        self.notes = notes if len(notes) == self.count else None  # None: not known (or not matching the slides)
        self.page = 0
        self.black = False
        self.show = None   # SlideShow while LibreOffice shows it on the projection
        self.live = False  # the slide show is running (the projection window steps aside)
        self._cache = {}  # (page, w, h) -> QImage, a few recent ones

    def attach(self, show):
        self.show = show
        show.status.connect(self._on_show_status)

    def _on_show_status(self, st):
        live = bool(st.get("running"))
        page = min(max(0, st.get("slide", self.page)), self.count - 1)
        if (live, page, bool(st.get("black"))) != (self.live, self.page, self.black):
            self.live, self.page, self.black = live, page, bool(st.get("black"))
            self.changed.emit()

    def go(self, page):
        page = max(0, min(self.count - 1, page))
        if self.live:
            self.show.send("goto", slide=page)
        elif page != self.page or self.black:
            self.page, self.black = page, False
            self.changed.emit()

    def next(self):
        if self.live:
            self.show.send("next")  # the next animation first, then the next slide
        else:
            self.go(self.page + 1)

    def previous(self):
        if self.live:
            self.show.send("prev")
        else:
            self.go(self.page - 1)

    def toggle_black(self):
        if self.live:
            self.show.send("black", on=not self.black)
            return
        self.black = not self.black
        self.changed.emit()

    def stop_show(self):
        """Back to the PDF slides (the slide show failed) or closing: end LibreOffice's show."""
        if self.show is not None:
            show, self.show = self.show, None
            show.status.disconnect(self._on_show_status)
            show.end()
        if self.live:
            self.live = False
            self.changed.emit()

    def render(self, page, size):
        """The slide fitted into size (keeping its shape)."""
        pts = self.doc.pagePointSize(page)
        if pts.width() <= 0 or pts.height() <= 0 or size.width() <= 0 or size.height() <= 0:
            return None
        scale = min(size.width() / pts.width(), size.height() / pts.height())
        fit = QSize(max(1, int(pts.width() * scale)), max(1, int(pts.height() * scale)))
        key = (page, fit.width(), fit.height())
        if key not in self._cache:
            if len(self._cache) > 12:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = self.doc.render(page, fit)
        return self._cache[key]


class SlideView(QWidget):
    """One slide on black: the projection, and the current/next slide on the operator screen."""

    def __init__(self, offset=0, parent=None):
        super().__init__(parent)
        self.offset = offset  # 0 = current slide, 1 = the next one
        self.presentation = None
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_presentation(self, presentation):
        if self.presentation is not None:
            self.presentation.changed.disconnect(self.update)
        self.presentation = presentation
        if presentation is not None:
            presentation.changed.connect(self.update)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        pres = self.presentation
        if pres is None or (pres.black and self.offset == 0):
            return
        page = pres.page + self.offset
        if page >= pres.count:
            p.setPen(Qt.gray)
            p.drawText(self.rect(), Qt.AlignCenter, "Einde van de presentatie")
            return
        ratio = self.devicePixelRatioF()
        image = pres.render(page, QSize(int(self.width() * ratio), int(self.height() * ratio)))
        if image is None:
            return
        w, h = image.width() / ratio, image.height() / ratio
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(QRect(int((self.width() - w) / 2), int((self.height() - h) / 2), int(w), int(h)), image)


class PresentationPanel(QWidget):
    """Presenter view in the middle of the operator window, like PowerPoint's: the slide on the screen,
    the next one, the speaker notes, a timer and clock, and all slides to jump to."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.presentation = None
        self._notes_px = 18
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.elapsed = QLabel("0:00:00")
        self.elapsed.setStyleSheet(f"color: {TEXT}; font-size: 24px; font-weight: 300;")
        self.elapsed.setToolTip("Tijd sinds het begin van de presentatie")
        top.addWidget(self.elapsed)
        self.btn_timer = self._button("", G_PAUSE, self._toggle_timer, top, "Tijd pauzeren of verder laten lopen")
        self._button("", G_REFRESH, self._reset_timer, top, "Tijd opnieuw beginnen")
        top.addSpacing(16)
        self.clock = QLabel()
        self.clock.setStyleSheet(f"color: {MUTED}; font-size: 24px; font-weight: 300;")
        top.addWidget(self.clock)
        top.addSpacing(16)
        self.mode = QLabel()
        self.mode.setStyleSheet(f"color: {MUTED};")
        self.mode.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # may be cut off on a small screen
        top.addWidget(self.mode)
        top.addStretch()
        self.btn_black = self._button("Zwart", G_CHECK, lambda: self._act(Presentation.toggle_black), top,
                                      "Beamer even zwart (toets B)")
        self.btn_black.setCheckable(True)
        self._button("Sluiten", G_STOP, app.close_presentation, top, "Presentatie sluiten")
        lay.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(8)
        self.current = SlideView(0)
        left.addWidget(self.current, 1)
        nav = QHBoxLayout()
        nav.setSpacing(8)
        nav.addStretch()
        self.btn_prev = self._button("", G_LEFT, lambda: self._act(Presentation.previous), nav, "Vorige dia (PgUp)")
        self.counter = QLabel()
        self.counter.setAlignment(Qt.AlignCenter)
        self.counter.setMinimumWidth(140)
        self.counter.setStyleSheet(f"color: {ACCENT}; font-size: 16px;")
        nav.addWidget(self.counter)
        self.btn_next = self._button("", G_RIGHT, lambda: self._act(Presentation.next), nav, "Volgende dia (PgDn)")
        nav.addStretch()
        left.addLayout(nav)
        body.addLayout(left, 62)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self._caption("Volgende dia"))
        self.next_view = SlideView(1)
        right.addWidget(self.next_view, 4)
        notes_head = QHBoxLayout()
        notes_head.addWidget(self._caption("Notities"))
        notes_head.addStretch()
        self._button("A-", None, lambda: self._notes_size(-2), notes_head, "Kleinere tekst")
        self._button("A+", None, lambda: self._notes_size(2), notes_head, "Grotere tekst")
        right.addLayout(notes_head)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setFocusPolicy(Qt.NoFocus)
        right.addWidget(self.notes, 5)
        body.addLayout(right, 38)
        lay.addLayout(body, 1)

        self.strip = QListWidget()
        self.strip.setViewMode(QListView.IconMode)
        self.strip.setFlow(QListView.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setMovement(QListView.Static)
        self.strip.setIconSize(QSize(160, 90))
        self.strip.setFixedHeight(140)
        self.strip.setSpacing(4)
        self.strip.setFocusPolicy(Qt.NoFocus)
        self.strip.setHorizontalScrollMode(QListView.ScrollPerPixel)
        self.strip.itemClicked.connect(lambda li: self._act(lambda p: p.go(self.strip.row(li))))
        lay.addWidget(self.strip)

        self._thumb_next = 0
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(0)
        self._thumb_timer.timeout.connect(self._render_thumb)
        self._started = None   # time.monotonic() the running timer counts from
        self._banked = 0.0     # seconds counted before the last pause
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._update_times)
        self._notes_size(0)

    def _button(self, text, glyph, fn, row, tip=""):
        b = QPushButton(("  " + text) if text and glyph else text)
        if glyph:
            b.setIcon(glyph_icon(glyph, TEXT, 16))
        b.setMinimumHeight(38)
        if not text or not glyph:
            b.setFixedWidth(44)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(fn)
        row.addWidget(b)
        return b

    @staticmethod
    def _caption(text):
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
        return lbl

    def _act(self, action):
        if self.presentation is not None:
            action(self.presentation)

    # -- timer and clock (like PowerPoint's presenter view) -----------------------------------
    def _seconds(self):
        return self._banked + (time.monotonic() - self._started if self._started is not None else 0)

    def _toggle_timer(self):
        if self._started is None:
            self._started = time.monotonic()
        else:
            self._banked, self._started = self._seconds(), None
        self.btn_timer.setIcon(glyph_icon(G_PAUSE if self._started is not None else G_PLAY, TEXT, 16))
        self._update_times()

    def _reset_timer(self):
        self._banked = 0.0
        if self._started is not None:
            self._started = time.monotonic()
        self._update_times()

    def _update_times(self):
        s = int(self._seconds())
        self.elapsed.setText(f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}")
        self.clock.setText(datetime.now().strftime("%H:%M"))

    # -- content --------------------------------------------------------------------------
    def _notes_size(self, step):
        self._notes_px = max(12, min(40, self._notes_px + step))
        self.notes.setStyleSheet(f"QTextEdit {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; "
                                 f"padding: 10px; font-size: {self._notes_px}px; }}")

    def set_presentation(self, presentation):
        if self.presentation is not None:
            self.presentation.changed.disconnect(self._on_changed)
        self.presentation = presentation
        self.current.set_presentation(presentation)
        self.next_view.set_presentation(presentation)
        self.strip.clear()
        self._thumb_timer.stop()
        if presentation is None:
            self._tick.stop()
            return
        presentation.changed.connect(self._on_changed)
        for n in range(presentation.count):
            li = QListWidgetItem(str(n + 1))
            li.setSizeHint(QSize(172, 120))
            self.strip.addItem(li)
        self._thumb_next = 0
        self._thumb_timer.start()
        self._banked, self._started = 0.0, time.monotonic()  # the timer starts with the presentation
        self.btn_timer.setIcon(glyph_icon(G_PAUSE, TEXT, 16))
        self._tick.start()
        self._update_times()
        self._on_changed()

    def _render_thumb(self):
        pres = self.presentation
        if pres is None or self._thumb_next >= pres.count:
            self._thumb_timer.stop()
            return
        n = self._thumb_next
        self._thumb_next += 1
        image = pres.doc.render(n, QSize(320, 180))  # not via the cache: those are kept for the big views
        if not image.isNull():
            self.strip.item(n).setIcon(QIcon(QPixmap.fromImage(image)))

    def _on_changed(self):
        pres = self.presentation
        self.counter.setText(f"Dia {pres.page + 1} van {pres.count}")
        self.btn_prev.setEnabled(pres.page > 0)
        self.btn_next.setEnabled(pres.page < pres.count - 1)
        self.btn_black.setChecked(pres.black)
        if pres.live:
            self.mode.setText("Diavoorstelling met animaties")
        elif pres.show is not None:
            self.mode.setText("Diavoorstelling wordt gestart…")
        else:
            self.mode.setText("Dia's zonder animaties")
        if pres.notes is None:
            self.notes.setPlainText("Geen notities beschikbaar voor dit bestand." if pres.item.extra.get("path", "")
                                    .lower().endswith(".pdf") else "Notities konden niet worden gelezen.")
        else:
            self.notes.setPlainText(pres.notes[pres.page] or "Geen notities bij deze dia.")
        self.strip.setCurrentRow(pres.page)
        self.strip.scrollToItem(self.strip.item(pres.page), QListWidget.PositionAtCenter)
        # the next slide's full-size image is ready before it is asked for
        QTimer.singleShot(0, lambda: self.app.prerender_slide(pres.page + 1))
