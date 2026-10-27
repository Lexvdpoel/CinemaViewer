"""The operator window: libraries, details, now playing and transport controls."""

import sys
from datetime import datetime, timedelta

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QStackedWidget, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from .cast import ytdl
from .dialogs import ImportDialog
from .models import SOURCE_LABELS, MediaItem, fmt_runtime, fmt_size, fmt_time
from .screens import place_window
from .sources.cache import guess as cache_guess
from .style import (ACCENT, BORDER, G_AUDIO, G_BACK, G_CC, G_CHECK, G_DELETE, G_DISC, G_DOWN, G_DOWNLOAD, G_FORWARD,
                    G_LEFT, G_LIBRARY, G_MUTE, G_NEXT, G_PAUSE, G_PLAY, G_POWER, G_PREV, G_REFRESH, G_REWIND,
                    G_RIGHT,
                    G_SEARCH, G_SERVER, G_SETTINGS, G_SHUFFLE, G_STOP, G_TV, G_UP, G_USB, G_VOLUME, MUTED, PANEL2, TEXT,
                    glyph_icon, icon_font, kind_glyph)
from .workers import run_async

ROLE_ITEM = Qt.UserRole
ROLE_KIND = Qt.UserRole + 1
ROLE_GROUP = Qt.UserRole + 2  # sidebar header: (key, title) of its group


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------
class ClickSlider(QSlider):
    """Slider that jumps to the clicked position; can show a marker (e.g. the planned break)."""

    marker = None  # value in slider units, or None

    def set_marker(self, value):
        self.marker = value
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.marker is None or self.maximum() <= self.minimum():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x = 8 + (self.width() - 16) * (self.marker - self.minimum()) / (self.maximum() - self.minimum())
        cy = self.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(QRectF(x - 2, cy - 9, 4, 18), 2, 2)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(e.position().x()), self.width())
            self.setValue(value)
            self.sliderMoved.emit(value)
        super().mousePressEvent(e)


def icon_button(glyph, tooltip, px=20, size=44, object_name="Icon", color=TEXT):
    b = QPushButton()
    b.setObjectName(object_name)
    b.setIcon(glyph_icon(glyph, color, px))
    b.setIconSize(QSize(px, px))
    b.setFixedSize(size, size)
    b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    return b


def label(text="", name=None, wrap=False):
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    lbl.setTextInteractionFlags(Qt.NoTextInteraction)
    return lbl


def item_meta(item, length_ms=0):
    parts = []
    if item.year:
        parts.append(str(item.year))
    runtime = fmt_runtime(length_ms or item.runtime_ms)
    if runtime:
        parts.append(runtime)
    if item.rating:
        parts.append(item.rating)
    if item.community_rating:
        parts.append(f"★ {float(item.community_rating):.1f}")
    return "  ·  ".join(parts)


# ---------------------------------------------------------------------------
# Poster grid
# ---------------------------------------------------------------------------
class PosterDelegate(QStyledItemDelegate):
    PW, PH, TEXT_H, PAD = 164, 246, 50, 9

    def __init__(self, images, view):
        super().__init__(view)
        self.images = images
        self.view = view
        self._scaled = {}
        images.loaded.connect(lambda _u: view.viewport().update())

    def sizeHint(self, option, index):
        return QSize(self.PW + 2 * self.PAD, self.PH + self.TEXT_H + 2 * self.PAD)

    def _pixmap(self, url):
        key = (url, self.PW, self.PH)
        pm = self._scaled.get(key)
        if pm is not None:
            return pm
        src = self.images.get(url)
        if src is None:
            self.images.request(url)
            return None
        ratio = self.view.devicePixelRatioF()
        w, h = int(self.PW * ratio), int(self.PH * ratio)
        mode = Qt.KeepAspectRatioByExpanding if src.width() < src.height() * 0.9 else Qt.KeepAspectRatio
        pm = src.scaled(w, h, mode, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(ratio)
        if len(self._scaled) > 400:
            self._scaled.clear()
        self._scaled[key] = pm
        return pm

    def paint(self, p, option, index):
        item = index.data(ROLE_ITEM)
        if item is None:
            return
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        r = option.rect.adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        poster = QRectF(r.x(), r.y(), self.PW, self.PH)
        path = QPainterPath()
        path.addRoundedRect(poster, 8, 8)
        p.fillPath(path, QColor(PANEL2))

        pm = self._pixmap(item.image_url) if item.image_url else None
        if pm is not None:
            pw, ph = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
            p.setClipPath(path)
            p.drawPixmap(QRectF(poster.x() + (self.PW - pw) / 2, poster.y() + (self.PH - ph) / 2, pw, ph),
                         pm, QRectF(0, 0, pm.width(), pm.height()))
            p.setClipping(False)
        else:
            p.setFont(icon_font(54))
            p.setPen(QColor("#4A505C"))
            p.drawText(poster.adjusted(0, -24, 0, -24), Qt.AlignCenter, kind_glyph(item.kind))
            p.setFont(QFont("Segoe UI", 11))
            p.setPen(QColor(MUTED))
            p.drawText(poster.adjusted(12, 120, -12, -10), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, item.title)

        if item.runtime_ms and item.resume_ms and not item.played:
            frac = min(1.0, item.resume_ms / item.runtime_ms)
            bar = QRectF(poster.x() + 10, poster.bottom() - 12, self.PW - 20, 4)
            p.fillRect(bar, QColor(0, 0, 0, 170))
            p.fillRect(QRectF(bar.x(), bar.y(), bar.width() * frac, bar.height()), QColor(ACCENT))
        if item.played:
            badge = QRectF(poster.right() - 32, poster.y() + 8, 24, 24)
            p.setBrush(QColor(ACCENT))
            p.setPen(Qt.NoPen)
            p.drawEllipse(badge)
            p.setFont(icon_font(12))
            p.setPen(QColor("#16130B"))
            p.drawText(badge, Qt.AlignCenter, G_CHECK)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            p.setPen(QPen(QColor(ACCENT) if selected else QColor(255, 255, 255, 90), 2.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(poster.adjusted(-1, -1, 1, 1), 9, 9)

        f = QFont("Segoe UI", 10)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(ACCENT if selected else TEXT))
        fm = p.fontMetrics()
        tr = QRect(int(poster.x()), int(poster.bottom()) + 8, self.PW, 20)
        p.drawText(tr, Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(item.title, Qt.ElideRight, self.PW))
        sub = item.subtitle if item.kind == "episode" else " · ".join(
            x for x in (str(item.year) if item.year else "", item.subtitle if not item.year else "") if x)
        if sub:
            p.setFont(QFont("Segoe UI", 9))
            p.setPen(QColor(MUTED))
            p.drawText(tr.translated(0, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(sub, Qt.ElideRight, self.PW))
        p.restore()


# ---------------------------------------------------------------------------
# Info views
# ---------------------------------------------------------------------------
class InfoView(QWidget):
    POSTER_W, POSTER_H = 150, 225

    def __init__(self, images):
        super().__init__()
        self.images = images
        self.item = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(16)
        self.poster = QLabel()
        self.poster.setFixedSize(self.POSTER_W, self.POSTER_H)
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setStyleSheet(f"background: {PANEL2}; border-radius: 8px;")
        top.addWidget(self.poster, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.title = label("", "H1", wrap=True)
        self.subtitle = label("", "H2", wrap=True)
        self.meta = label("", "Meta", wrap=True)
        self.genres = label("", "Meta", wrap=True)
        self.source = label("", "Meta")
        for w in (self.title, self.subtitle, self.meta, self.genres, self.source):
            col.addWidget(w)
        col.addStretch()
        top.addLayout(col, 1)
        lay.addLayout(top)
        self.overview = label("", "Body", wrap=True)
        lay.addWidget(self.overview)
        images.loaded.connect(self._on_image)

    def set_item(self, item, length_ms=0):
        self.item = item
        self.title.setText(item.title)
        self.subtitle.setText(item.subtitle)
        self.subtitle.setVisible(bool(item.subtitle))
        self.update_meta(length_ms)
        self.genres.setText(", ".join(item.genres))
        self.genres.setVisible(bool(item.genres))
        self.source.setText(f"Bron: {SOURCE_LABELS.get(item.source, item.source)}")
        self.overview.setText(item.overview)
        self.overview.setVisible(bool(item.overview))
        self._set_poster(None)
        self.images.request(item.image_url)
        if item.image_url and self.images.get(item.image_url):
            self._set_poster(self.images.get(item.image_url))

    def update_meta(self, length_ms=0):
        if self.item:
            text = item_meta(self.item, length_ms)
            self.meta.setText(text)
            self.meta.setVisible(bool(text))

    def _on_image(self, url):
        if self.item and url == self.item.image_url:
            self._set_poster(self.images.get(url))

    def _set_poster(self, pm):
        if pm is None:
            self.poster.setPixmap(glyph_icon(kind_glyph(self.item.kind if self.item else ""), "#4A505C", 56)
                                  .pixmap(QSize(56, 56)))
            return
        ratio = self.devicePixelRatioF()
        scaled = pm.scaled(int(self.POSTER_W * ratio), int(self.POSTER_H * ratio), Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        rounded = scaled.copy()
        rounded.fill(Qt.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, scaled.width() / ratio, scaled.height() / ratio), 8, 8)
        p.setClipPath(path)
        p.drawPixmap(0, 0, scaled)
        p.end()
        self.poster.setPixmap(rounded)


def scroll_wrap(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(widget)
    return area


class DetailsPanel(QWidget):
    cache_progress = Signal(object, object)  # bytes: a film is > 2 GB, too big for Qt's 32-bit int

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.item = None
        self._downloading = False
        self._busy_item = None  # what is being downloaded/copied
        self._gathering = False
        self._gather_token = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(16)
        self.empty = label("Selecteer een film of aflevering om details te zien.", "Empty", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty, 1)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        self.info = InfoView(app.images)
        body.addWidget(self.info)
        self.btn_play = QPushButton("  Afspelen")
        self.btn_play.setObjectName("Primary")
        self.btn_play.setIcon(glyph_icon(G_PLAY, "#16130B", 16))
        self.btn_play.setMinimumHeight(46)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_resume = QPushButton("")
        self.btn_resume.setObjectName("Primary")
        self.btn_resume.setIcon(glyph_icon(G_PLAY, "#16130B", 16))
        self.btn_resume.setMinimumHeight(46)
        self.btn_resume.setCursor(Qt.PointingHandCursor)
        self.btn_play_all = QPushButton("  Alles afspelen")
        self.btn_play_all.setObjectName("Primary")
        self.btn_play_all.setIcon(glyph_icon(G_PLAY, "#16130B", 16))
        self.btn_play_all.setMinimumHeight(46)
        self.btn_play_all.setCursor(Qt.PointingHandCursor)
        self.btn_queue = QPushButton("  In wachtrij")
        self.btn_queue.setIcon(glyph_icon(G_NEXT, TEXT, 16))
        self.btn_queue.setMinimumHeight(46)
        self.btn_queue.setCursor(Qt.PointingHandCursor)
        self.btn_open = QPushButton("Openen")
        self.btn_open.setMinimumHeight(46)
        self.btn_cache = QPushButton("  Naar bibliotheek downloaden")
        self.btn_cache.setIcon(glyph_icon(G_DOWNLOAD, TEXT, 16))
        self.btn_cache.setMinimumHeight(46)
        self.btn_cache.setCursor(Qt.PointingHandCursor)
        self.btn_uncache = QPushButton("  Uit bibliotheek verwijderen")
        self.btn_uncache.setIcon(glyph_icon(G_DELETE, TEXT, 16))
        self.btn_uncache.setMinimumHeight(46)
        self.btn_uncache.setCursor(Qt.PointingHandCursor)
        self.chk_auto = QCheckBox("Pauze halverwege inplannen (vóór de aftiteling gerekend)")
        self.chk_auto.setChecked(app.cfg["auto_intermission"])
        self.chk_auto.setFocusPolicy(Qt.NoFocus)
        self.chk_auto.toggled.connect(app.set_auto_intermission)
        body.addWidget(self.chk_auto)
        for b in (self.btn_play_all, self.btn_resume, self.btn_play, self.btn_queue, self.btn_cache, self.btn_uncache,
                  self.btn_open):
            b.setFocusPolicy(Qt.NoFocus)
            body.addWidget(b)
        body.addStretch()
        self.scroll = scroll_wrap(self.body)
        lay.addWidget(self.scroll, 1)
        self.scroll.hide()
        self.btn_play.clicked.connect(lambda: self.item and app.play(self.item, resume=False))
        self.btn_resume.clicked.connect(lambda: self.item and app.play(self.item, resume=True))
        self.btn_play_all.clicked.connect(self._on_play_all)
        self.btn_queue.clicked.connect(lambda: self.item and app.queue_append(self.item))
        self.btn_open.clicked.connect(lambda: self.item and app.control.open_item(self.item))
        self.btn_cache.clicked.connect(self._on_cache)
        self.btn_uncache.clicked.connect(self._on_uncache)
        self.cache_progress.connect(self._on_cache_progress)

    def set_item(self, item):
        self.item = item
        self._gather_token += 1
        self._gathering = False
        self.btn_play_all.setText("  Alles afspelen")
        self.btn_play_all.setEnabled(True)
        self.empty.hide()
        self.scroll.show()
        self.info.set_item(item)
        resume = item.playable and item.resume_ms > 60000 and not item.played
        self.btn_resume.setVisible(resume)
        self.btn_resume.setText(f"  Hervatten vanaf {fmt_time(item.resume_ms)}")
        self.btn_play.setVisible(item.playable)
        self.btn_play.setText("  Vanaf begin" if resume else "  Afspelen")
        self.btn_play.setObjectName("" if resume else "Primary")
        self.btn_play.setIcon(glyph_icon(G_PLAY, TEXT if resume else "#16130B", 16))
        self.btn_play.style().unpolish(self.btn_play)
        self.btn_play.style().polish(self.btn_play)
        self.btn_play_all.setVisible(item.is_folder)
        self.btn_queue.setVisible(item.playable)
        self.btn_open.setVisible(item.is_folder)
        self.chk_auto.setVisible(item.playable and item.kind not in ("track", "audiocd", "radio"))  # films only
        show_cache = self._can_cache(item)
        self.btn_cache.setVisible(show_cache)
        if show_cache:
            self._set_cache_state()
        else:
            self.btn_uncache.setVisible(item.source == "cache" and item.playable)

    @staticmethod
    def _can_cache(item):
        """Plex/Jellyfin: download. Files from folders, USB, network shares and unprotected discs: copy."""
        if item.source in ("plex", "jellyfin"):
            return item.playable
        return item.source == "local" and item.playable and item.kind in ("video", "movie", "dvd", "bluray")

    def clear(self):
        self.item = None
        self.scroll.hide()
        self.empty.show()

    def _verb(self, item):
        return "kopiëren" if item.source == "local" else "downloaden"

    def _set_cache_state(self):
        cached = self.app.is_cached(self.item)
        busy = self._downloading and self._busy_item is self.item
        if cached:
            self.btn_cache.setText("  In bibliotheek")
            self.btn_cache.setIcon(glyph_icon(G_CHECK, TEXT, 16))
            self.btn_cache.setEnabled(False)
        elif not busy:
            self.btn_cache.setText(f"  Naar bibliotheek {self._verb(self.item)}")
            self.btn_cache.setIcon(glyph_icon(G_DOWNLOAD, TEXT, 16))
            self.btn_cache.setEnabled(not self._downloading)
        self.btn_uncache.setVisible(cached)

    def _on_uncache(self):
        item = self.item
        if item is None or not self.app.cache_remove(item):
            return
        if item.source == "cache":
            self.app.control.cache_item_removed(item)  # the item itself is gone
        elif self.item is item:
            self._set_cache_state()

    def _on_cache(self):
        item = self.item
        if item is None or self._downloading or self.app.is_cached(item):
            return
        progress = lambda done, total: self.cache_progress.emit(done, total)  # noqa: E731
        if item.source == "local":
            dlg = ImportDialog(item, cache_guess(item), self.app.control)
            if dlg.exec() != QDialog.Accepted:
                return
            start = lambda: self.app.cache_import(item, dlg.result_info(), progress,  # noqa: E731
                                                  self._cache_done, self._cache_failed)
        else:
            start = lambda: self.app.cache_download(item, progress, self._cache_done,  # noqa: E731
                                                    self._cache_failed)
        self._downloading, self._busy_item = True, item
        self.btn_cache.setEnabled(False)
        self.btn_cache.setText(f"  {self._verb(item).capitalize()}…")
        start()

    def _on_cache_progress(self, done, total):
        if self._downloading and self._busy_item is self.item:
            verb = self._verb(self.item).capitalize()
            self.btn_cache.setText(f"  {verb}… {done * 100 // total}%" if total
                                   else f"  {verb}… {done // 1048576} MB")

    def _cache_done(self, _folder):
        item, self._downloading, self._busy_item = self._busy_item, False, None
        if self.item is not None:
            self._set_cache_state()
        self.app.control.toast(f"‘{item.title}’ staat in je bibliotheek.", error=False, ms=4000)

    def _cache_failed(self, err):
        item, self._downloading, self._busy_item = self._busy_item, False, None
        if self.item is not None:
            self._set_cache_state()
        self.app.control.toast(f"{self._verb(item).capitalize()} van ‘{item.title}’ mislukt: {err}", ms=10000)

    def _on_play_all(self):
        item = self.item
        if item is None or self._gathering:
            return
        self._gathering = True
        token = self._gather_token
        self.btn_play_all.setEnabled(False)
        self.btn_play_all.setText("  Laden…")
        self.app.gather_playable(item, lambda items: self._gather_done(token, items),
                                 lambda err: self._gather_failed(token, err))

    def _gather_done(self, token, items):
        if token != self._gather_token:
            return
        self._gathering = False
        self.btn_play_all.setEnabled(True)
        self.btn_play_all.setText("  Alles afspelen")
        if items:
            self.app.play_all(items)
        else:
            self.app.control.toast("Er is niets afspeelbaars in deze lijst.")

    def _gather_failed(self, token, err):
        if token != self._gather_token:
            return
        self._gathering = False
        self.btn_play_all.setEnabled(True)
        self.btn_play_all.setText("  Alles afspelen")
        self.app.control.toast(f"Laden mislukt: {err}")


class NowPlayingPanel(QWidget):
    TECH_ROWS = [("source", "Bron"), ("video", "Video"), ("audio", "Audio"), ("audio_track", "Audiospoor"),
                 ("subtitle_track", "Ondertitels"), ("chapter", "Hoofdstuk"), ("started", "Gestart om"),
                 ("pause", "Geplande pauze"), ("ends", "Eindigt om")]

    def __init__(self, app):
        super().__init__()
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(16)
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch()
        icon = QLabel()
        icon.setPixmap(glyph_icon(G_PLAY, "#3A3F4A", 64).pixmap(QSize(64, 64)))
        icon.setAlignment(Qt.AlignCenter)
        el.addWidget(icon)
        msg = label("Er wordt nu niets afgespeeld.", "Empty", wrap=True)
        msg.setAlignment(Qt.AlignCenter)
        el.addWidget(msg)
        hint = label("Kies een titel in de bibliotheek. De publieksschermen tonen tot die tijd het "
                     "CinemaViewer-scherm.", "Meta", wrap=True)
        hint.setAlignment(Qt.AlignCenter)
        el.addWidget(hint)
        el.addStretch()
        lay.addWidget(self.empty, 1)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        self.chip = label("AFSPELEN", "Chip")
        chip_row = QHBoxLayout()
        chip_row.addWidget(self.chip)
        chip_row.addStretch()
        body.addLayout(chip_row)
        self.info = InfoView(app.images)
        body.addWidget(self.info)
        body.addWidget(label("TECHNISCHE INFORMATIE", "Section"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        self.tech = {}
        for row, (key, name) in enumerate(self.TECH_ROWS):
            k = label(name, "Meta")
            v = label("–", "Body", wrap=True)
            grid.addWidget(k, row, 0, Qt.AlignTop)
            grid.addWidget(v, row, 1)
            self.tech[key] = (k, v)
        grid.setColumnStretch(1, 1)
        body.addLayout(grid)
        body.addStretch()
        self.scroll = scroll_wrap(self.body)
        lay.addWidget(self.scroll, 1)
        self.scroll.hide()
        self.started = None

    def set_item(self, item):
        if item is None:
            self.scroll.hide()
            self.empty.show()
            return
        self.started = datetime.now()
        self.empty.hide()
        self.scroll.show()
        self.info.set_item(item)
        for key in self.tech:
            self.set_tech(key, "")
        self.set_tech("source", SOURCE_LABELS.get(item.source, item.source) +
                      ({"dvd": " · DVD", "bluray": " · Blu-ray", "audiocd": " · Audio-cd"}.get(item.kind, "")))
        self.set_tech("started", f"{self.started:%H:%M}")

    def set_tech(self, key, value):
        k, v = self.tech[key]
        v.setText(value or "")
        k.setVisible(bool(value))
        v.setVisible(bool(value))

    def set_state(self, state):
        text = {"playing": "AFSPELEN", "paused": "GEPAUZEERD", "opening": "LADEN…", "intermission": "PAUZE",
                "countdown": "HERVATTEN…"}.get(state, state.upper())
        self.chip.setText(text)
        self.chip.setObjectName("Chip" if state == "playing" else "ChipIdle")
        self.chip.style().unpolish(self.chip)
        self.chip.style().polish(self.chip)


class DiscPad(QFrame):
    """Popup with DVD/Blu-ray menu navigation."""

    def __init__(self, app, parent=None):
        super().__init__(parent, Qt.Popup)
        self.app = app
        self.setStyleSheet(f"DiscPad {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 10px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.addWidget(label("DISCMENU", "Section"))
        grid = QGridLayout()
        nav = {(0, 1): (G_UP, "up"), (1, 0): (G_LEFT, "left"), (1, 2): (G_RIGHT, "right"), (2, 1): (G_DOWN, "down")}
        for (r, c), (glyph, action) in nav.items():
            b = icon_button(glyph, action, 18, 48)
            b.setObjectName("")
            b.clicked.connect(lambda _=False, a=action: app.player.navigate(a))
            grid.addWidget(b, r, c)
        ok = QPushButton("OK")
        ok.setObjectName("Primary")
        ok.setFixedSize(48, 48)
        ok.setFocusPolicy(Qt.NoFocus)
        ok.clicked.connect(lambda: app.player.navigate("activate"))
        grid.addWidget(ok, 1, 1)
        lay.addLayout(grid)
        row = QHBoxLayout()
        for text, fn in (("Hoofdmenu", app.player.disc_menu), ("Pop-up", lambda: app.player.navigate("popup"))):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        for glyph, text, d in ((G_PREV, " Vorig hoofdstuk", -1), (G_NEXT, " Volgend", 1)):
            b = QPushButton(text)
            b.setIcon(glyph_icon(glyph, TEXT, 14))
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, dd=d: app.player.chapter(dd))
            row2.addWidget(b)
        lay.addLayout(row2)


class QueuePanel(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        self.shuffle_note = label("", "Meta", wrap=True)
        self.shuffle_note.hide()
        lay.addWidget(self.shuffle_note)
        self.empty = label("De wachtrij is leeg.", "Empty", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty, 1)
        self.list = QListWidget()
        self.list.setObjectName("Plain")
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setUniformItemSizes(False)
        self.list.itemDoubleClicked.connect(self._play_selected)
        self.list.currentRowChanged.connect(lambda _row: self._update_buttons())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.btn_play = QPushButton("Nu afspelen")
        self.btn_play.setMinimumHeight(40)
        self.btn_play.setFocusPolicy(Qt.NoFocus)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.clicked.connect(self._play_selected)
        self.btn_remove = QPushButton("Verwijderen")
        self.btn_remove.setMinimumHeight(40)
        self.btn_remove.setFocusPolicy(Qt.NoFocus)
        self.btn_remove.setCursor(Qt.PointingHandCursor)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear = QPushButton("Wis alles")
        self.btn_clear.setMinimumHeight(40)
        self.btn_clear.setFocusPolicy(Qt.NoFocus)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(app.clear_queue)
        row.addWidget(self.btn_play)
        row.addWidget(self.btn_remove)
        row.addWidget(self.btn_clear)
        lay.addLayout(row)
        self.set_items([])

    def set_items(self, items):
        self.list.blockSignals(True)
        self.list.clear()
        for i, item in enumerate(items, 1):
            text = f"{i}.  {item.title}"
            sub = item.subtitle or item_meta(item)
            if sub:
                text += f"\n      {sub}"
            li = QListWidgetItem(text)
            li.setData(ROLE_ITEM, item)
            self.list.addItem(li)
        self.list.blockSignals(False)
        empty = not items
        self.empty.setVisible(empty)
        self.list.setVisible(not empty)
        self._update_buttons()

    def _selected_row(self):
        return self.list.currentRow()

    def _play_selected(self):
        row = self._selected_row()
        if row >= 0:
            self.app.play_from_queue(row)

    def _remove_selected(self):
        row = self._selected_row()
        if row >= 0:
            self.app.remove_from_queue(row)

    def _update_buttons(self):
        has_sel = self.list.currentRow() >= 0
        self.btn_play.setEnabled(has_sel)
        self.btn_remove.setEnabled(has_sel)
        self.btn_clear.setEnabled(self.list.count() > 0)


# ---------------------------------------------------------------------------
# Main control window
# ---------------------------------------------------------------------------
class CastPanel(QWidget):
    """Displaycast in the middle of the operator window: TV code, connected phones, how to cast."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(18)
        outer.addWidget(scroll_wrap(body))

        head = QHBoxLayout()
        title = label("Displaycast", "H1")
        head.addWidget(title)
        head.addSpacing(12)
        self.chip = label("UIT", "ChipIdle")
        head.addWidget(self.chip, 0, Qt.AlignVCenter)
        head.addStretch()
        self.btn_toggle = QPushButton("Stoppen")
        self.btn_toggle.setMinimumHeight(38)
        self.btn_toggle.setFocusPolicy(Qt.NoFocus)
        self.btn_toggle.clicked.connect(self._toggle)
        head.addWidget(self.btn_toggle)
        lay.addLayout(head)
        self.intro = label("", "Body", wrap=True)
        lay.addWidget(self.intro)

        def card(section):
            frame = QFrame()
            frame.setStyleSheet(f"QFrame#Card {{ background: {PANEL2}; border: 1px solid {BORDER}; "
                                "border-radius: 12px; }")
            frame.setObjectName("Card")
            box = QVBoxLayout(frame)
            box.setContentsMargins(22, 18, 22, 20)
            box.setSpacing(10)
            box.addWidget(label(section, "Section"))
            lay.addWidget(frame)
            return box

        yt = card("YOUTUBE")
        self.yt_text = label("", "Body", wrap=True)
        yt.addWidget(self.yt_text)
        self.code = QLabel("…")
        self.code.setStyleSheet(f"color: {ACCENT}; font-size: 44px; font-weight: 300; letter-spacing: 4px;")
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        yt.addWidget(self.code)
        self.yt_state = label("", "Meta", wrap=True)
        yt.addWidget(self.yt_state)

        dl = card("DLNA / UPNP  ·  FOTO'S, VIDEO'S EN MUZIEK")
        self.dlna_text = label("", "Body", wrap=True)
        dl.addWidget(self.dlna_text)
        self.addresses = label("", "Meta", wrap=True)
        dl.addWidget(self.addresses)

        extra = card("ONDERHOUD")
        row = QHBoxLayout()
        self.btn_firewall = QPushButton("Toegang in Windows Firewall instellen")
        self.btn_firewall.setFocusPolicy(Qt.NoFocus)
        self.btn_firewall.clicked.connect(app.allow_cast_firewall)
        self.btn_firewall.setVisible(sys.platform == "win32")
        self.btn_ytdlp = QPushButton("yt-dlp bijwerken")
        self.btn_ytdlp.setFocusPolicy(Qt.NoFocus)
        self.btn_ytdlp.clicked.connect(self._update_ytdlp)
        row.addWidget(self.btn_firewall)
        row.addWidget(self.btn_ytdlp)
        row.addStretch()
        extra.addLayout(row)
        self.maint = label("", "Meta", wrap=True)
        extra.addWidget(self.maint)
        self.error = label("", "Meta", wrap=True)
        self.error.setStyleSheet("color: #E07A75;")
        lay.addWidget(self.error)
        lay.addStretch()
        self._version = ""
        self.set_status({"running": False, "name": ""})

    def showEvent(self, e):
        super().showEvent(e)
        if not self._version:
            run_async(ytdl.version, self._got_version, None)

    def _got_version(self, version):
        self._version = version or "niet gevonden"
        self._show_maint()

    def _show_maint(self, extra=""):
        text = (f"yt-dlp {self._version} haalt de YouTube-streams op. Werkt YouTube ineens niet meer, dan is er "
                "meestal een nieuwe versie nodig; dat gebeurt bij een mislukte video ook automatisch.")
        if sys.platform == "win32":
            text += (" Vraagt Windows om toegang voor Python, kies dan ‘Toestaan’ (privénetwerk); of stel het "
                     "eenmalig in met de knop.")
        self.maint.setText(text + (f"\n{extra}" if extra else ""))

    def _update_ytdlp(self):
        self.btn_ytdlp.setEnabled(False)
        self.btn_ytdlp.setText("Bijwerken…")

        def done(new):
            self.btn_ytdlp.setEnabled(True)
            self.btn_ytdlp.setText("yt-dlp bijwerken")
            if new:
                self._version = new
            self._show_maint(f"Bijgewerkt naar {new}." if new else "Er is geen nieuwere versie (of PyPI is niet bereikbaar).")

        run_async(ytdl.update, done, lambda err: done(""))

    def _toggle(self):
        if self.app.cast.running:
            self.app.cast.stop()
        else:
            self.app.cast.start()

    def set_status(self, st):
        running = st.get("running")
        name = st.get("name") or self.app.cast.name
        self.chip.setText("ACTIEF" if running else "UIT")
        self.chip.setObjectName("Chip" if running else "ChipIdle")
        self.chip.style().unpolish(self.chip)
        self.chip.style().polish(self.chip)
        self.btn_toggle.setText("Stoppen" if running else "Starten")
        self.intro.setText(
            f"CinemaViewer is op het netwerk zichtbaar als ‘{name}’. Wat een telefoon of tablet stuurt, wordt "
            "direct afgespeeld en vervangt wat er speelt; bediening (pauze, spoelen, volume) kan vanaf de telefoon "
            "én hier." if running else
            "Displaycast staat uit. Start het om vanaf een telefoon of tablet YouTube of eigen media naar "
            "CinemaViewer te sturen.")
        self.yt_text.setText(
            f"Open de YouTube-app op een telefoon in hetzelfde netwerk, tik op het cast-icoon en kies ‘{name}’. "
            "Lukt dat niet (ander netwerk), ga dan in de app naar Instellingen → Tv bekijken → Koppelen met "
            "tv-code en vul deze code in:")
        code = st.get("code") or ("…" if running else "—")
        self.code.setText(code)
        remotes = st.get("remotes") or []
        if not running:
            state = ""
        elif st.get("youtube_error") and not st.get("youtube_ok"):
            state = st["youtube_error"] + " – opnieuw proberen…"
        elif remotes:
            state = "Verbonden: " + ", ".join(remotes)
        elif st.get("youtube_ok"):
            state = "Klaar; nog geen telefoon verbonden. De code wordt elke 5 minuten vernieuwd; gekoppelde telefoons blijven gekoppeld."
        else:
            state = "Verbinden met YouTube…"
        self.yt_state.setText(state)
        self.dlna_text.setText(
            f"Kies ‘{name}’ in een app die naar een DLNA/UPnP-tv of ‘mediaspeler’ kan sturen, bijvoorbeeld "
            "BubbleUPnP of Web Video Cast (Android), VLC, een galerij-app of ‘Casten naar apparaat’ in Windows. "
            "Netflix, Prime Video en andere beveiligde diensten werken niet via casten.")
        addresses = st.get("addresses") or []
        self.addresses.setText(("Netwerkadres: " + ", ".join(addresses)) if addresses else "")
        self.error.setText(st.get("error", ""))
        self.error.setVisible(bool(st.get("error")))


class ControlWindow(QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.app = app
        self.setObjectName("ControlRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle("CinemaViewer")
        self.nav_stack = []          # list of MediaItem (folders) currently opened
        self._load_token = 0
        self._libs = {}              # group key -> list[MediaItem] | str (error)
        self._seeking = False
        self._length = 0
        self._now_item = None
        self._planned_at = None
        self._planned_reason = ""
        self._music = False          # music plays: no intermission ('Pauze'-screen) or planned break

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_center(), 1)
        body.addWidget(self._build_right())
        root.addLayout(body, 1)
        root.addWidget(self._build_transport())

        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(1000)
        self._tick_clock()
        self._tech_timer = QTimer(self)
        self._tech_timer.timeout.connect(self._refresh_tech)
        self._tech_timer.start(2000)
        self.set_now_playing(None)
        self.set_queue([])

    # -- building -------------------------------------------------------------
    def _build_topbar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(64)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 14, 0)
        lay.setSpacing(10)
        logo = QHBoxLayout()
        logo.setSpacing(0)
        logo.addWidget(label("Cinema", "Logo"))
        logo.addWidget(label("Viewer", "LogoAccent"))
        lay.addLayout(logo)
        lay.addSpacing(36)
        self.btn_back = icon_button(G_BACK, "Terug", 16, 38)
        self.btn_back.clicked.connect(self.go_back)
        lay.addWidget(self.btn_back)
        self.crumb = label("", "Crumb")
        self.crumb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.crumb, 1)
        self.btn_play_list = QPushButton("  Alles afspelen")
        self.btn_play_list.setObjectName("Primary")
        self.btn_play_list.setIcon(glyph_icon(G_PLAY, "#16130B", 14))
        self.btn_play_list.setFixedHeight(38)
        self.btn_play_list.setCursor(Qt.PointingHandCursor)
        self.btn_play_list.setFocusPolicy(Qt.NoFocus)
        self.btn_play_list.setToolTip("Speel alle titels in deze lijst achter elkaar af (wachtrij)")
        self.btn_play_list.clicked.connect(self._play_current_list)
        self.btn_play_list.hide()
        lay.addWidget(self.btn_play_list)
        self.btn_shuffle = QPushButton("  Willekeurig")
        self.btn_shuffle.setIcon(glyph_icon(G_SHUFFLE, TEXT, 14))
        self.btn_shuffle.setFixedHeight(38)
        self.btn_shuffle.setCursor(Qt.PointingHandCursor)
        self.btn_shuffle.setFocusPolicy(Qt.NoFocus)
        self.btn_shuffle.setToolTip("Speel doorlopend willekeurige nummers uit deze lijst (hooguit twee keer "
                                    "per uur hetzelfde nummer)")
        self.btn_shuffle.clicked.connect(lambda: self._play_current_list(shuffle=True))
        self.btn_shuffle.hide()
        lay.addWidget(self.btn_shuffle)
        self.btn_clean = QPushButton("  Gekeken verwijderen")
        self.btn_clean.setIcon(glyph_icon(G_DELETE, TEXT, 14))
        self.btn_clean.setFixedHeight(38)
        self.btn_clean.setCursor(Qt.PointingHandCursor)
        self.btn_clean.setFocusPolicy(Qt.NoFocus)
        self.btn_clean.setToolTip("Alle gekeken films en afleveringen uit de bibliotheek verwijderen om ruimte vrij "
                                  "te maken (gekeken = in CinemaViewer tot de aftiteling bekeken)")
        self.btn_clean.clicked.connect(self.app.cache_remove_watched)
        self.btn_clean.hide()
        lay.addWidget(self.btn_clean)
        lay.addSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Zoeken in deze lijst…")
        self.search.addAction(glyph_icon(G_SEARCH, MUTED, 14), QLineEdit.LeadingPosition)
        self.search.setFixedWidth(300)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        lay.addWidget(self.search)
        lay.addSpacing(12)
        self.btn_projector = QPushButton()
        self.btn_projector.setFixedHeight(38)
        self.btn_projector.setMinimumWidth(190)  # the text changes with the state: keep the bar still
        self.btn_projector.setCursor(Qt.PointingHandCursor)
        self.btn_projector.setFocusPolicy(Qt.NoFocus)
        self.btn_projector.clicked.connect(self.app.toggle_projector)
        self.btn_projector.hide()
        lay.addWidget(self.btn_projector)
        lay.addSpacing(6)
        self.clock = label("", "Clock")
        lay.addWidget(self.clock)
        b_settings = icon_button(G_SETTINGS, "Instellingen", 18)
        b_settings.clicked.connect(self.app.open_settings)
        lay.addWidget(b_settings)
        b_quit = icon_button(G_POWER, "Afsluiten", 18)
        b_quit.clicked.connect(self.app.request_quit)
        lay.addWidget(b_quit)
        return bar

    def _build_sidebar(self):
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(270)
        self.sidebar.setIconSize(QSize(18, 18))
        self.sidebar.setFocusPolicy(Qt.NoFocus)
        self.sidebar.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # long titles are elided instead
        self.sidebar.itemClicked.connect(self._on_sidebar_clicked)
        return self.sidebar

    def _build_center(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.center_stack = QStackedWidget()
        self.grid = QListWidget()
        self.grid.setObjectName("Grid")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(4)
        self.grid.setMouseTracking(True)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.verticalScrollBar().setSingleStep(30)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setContentsMargins(16, 16, 16, 16)
        self.grid.setViewportMargins(14, 14, 0, 0)
        self.grid.setItemDelegate(PosterDelegate(self.app.images, self.grid))
        self.grid.itemClicked.connect(self._on_grid_clicked)
        self.grid.itemDoubleClicked.connect(self._on_grid_double)
        self.grid.itemActivated.connect(self._on_grid_double)
        self.center_stack.addWidget(self.grid)
        self.message = label("", "Empty", wrap=True)
        self.message.setAlignment(Qt.AlignCenter)
        self.center_stack.addWidget(self.message)
        self.cast_panel = CastPanel(self.app)
        self.center_stack.addWidget(self.cast_panel)
        lay.addWidget(self.center_stack)
        self._show_message("Kies links een bibliotheek, map, USB-schijf of disc.")

        self._toast_lbl = label("", "Toast", wrap=True)
        self._toast_lbl.setParent(w)
        self._toast_lbl.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast_lbl.hide)
        return w

    def _build_right(self):
        panel = QFrame()
        panel.setObjectName("RightPanel")
        panel.setFixedWidth(460)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tabs = QHBoxLayout()
        tabs.setContentsMargins(22, 4, 22, 0)
        tabs.setSpacing(22)
        self.tab_now = QPushButton("NU SPEELT")
        self.tab_details = QPushButton("DETAILS")
        self.tab_queue = QPushButton("WACHTRIJ")
        for b in (self.tab_now, self.tab_details, self.tab_queue):
            b.setObjectName("Tab")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            tabs.addWidget(b)
        tabs.addStretch()
        lay.addLayout(tabs)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {BORDER};")
        lay.addWidget(line)
        self.right_stack = QStackedWidget()
        self.now_panel = NowPlayingPanel(self.app)
        self.details_panel = DetailsPanel(self.app)
        self.queue_panel = QueuePanel(self.app)
        self.right_stack.addWidget(self.now_panel)
        self.right_stack.addWidget(self.details_panel)
        self.right_stack.addWidget(self.queue_panel)
        lay.addWidget(self.right_stack, 1)
        self.tab_now.clicked.connect(lambda: self.right_stack.setCurrentWidget(self.now_panel))
        self.tab_details.clicked.connect(lambda: self.right_stack.setCurrentWidget(self.details_panel))
        self.tab_queue.clicked.connect(lambda: self.right_stack.setCurrentWidget(self.queue_panel))
        self.tab_now.setChecked(True)
        return panel

    def _build_transport(self):
        bar = QFrame()
        bar.setObjectName("Transport")
        bar.setFixedHeight(132)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setSpacing(14)
        self.lbl_pos = label("0:00", "Time")
        self.lbl_pos.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.slider = ClickSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.lbl_rem = label("0:00", "Time")
        row1.addWidget(self.lbl_pos)
        row1.addWidget(self.slider, 1)
        row1.addWidget(self.lbl_rem)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        left = QWidget()
        ll = QHBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(12)
        self.mini_poster = QLabel()
        self.mini_poster.setFixedSize(40, 56)
        self.mini_poster.setStyleSheet(f"background: {PANEL2}; border-radius: 4px;")
        ll.addWidget(self.mini_poster)
        tcol = QVBoxLayout()
        tcol.setSpacing(0)
        self.now_title = label("", "NowTitle")
        self.now_sub = label("", "Meta")
        for l_ in (self.now_title, self.now_sub):
            l_.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        tcol.addStretch()
        tcol.addWidget(self.now_title)
        tcol.addWidget(self.now_sub)
        tcol.addStretch()
        ll.addLayout(tcol, 1)
        row2.addWidget(left, 1)

        center = QHBoxLayout()
        center.setSpacing(10)
        self.btn_rew = icon_button(G_REWIND, "10 seconden terug (Ctrl+←)", 20, 48)
        self.btn_rew.clicked.connect(self.app.skip_back)
        self.btn_play = icon_button(G_PLAY, "Afspelen / pauze (spatie)", 24, 60, "Big", "#16130B")
        self.btn_play.clicked.connect(self.app.toggle_pause)
        self.btn_stop = icon_button(G_STOP, "Stoppen", 20, 48)
        self.btn_stop.clicked.connect(self.app.stop)
        self.btn_fwd = icon_button(G_FORWARD, "30 seconden vooruit (Ctrl+→)", 20, 48)
        self.btn_fwd.clicked.connect(self.app.skip_forward)
        for b in (self.btn_rew, self.btn_play, self.btn_stop, self.btn_fwd):
            center.addWidget(b)
        center.addSpacing(14)
        self.btn_intermission = QPushButton()
        self.btn_intermission.setMinimumSize(170, 44)
        self.btn_intermission.setFocusPolicy(Qt.NoFocus)
        self.btn_intermission.setCursor(Qt.PointingHandCursor)
        self._intermission_tip = "Pauze met ‘Pauze’-scherm; bij hervatten aftellen en een stukje terugspoelen (P)"
        self.btn_intermission.setToolTip(self._intermission_tip)
        self.btn_intermission.clicked.connect(self.app.toggle_intermission)
        center.addWidget(self.btn_intermission)
        self.chk_auto = QCheckBox("Halverwege")
        self._auto_tip = "Automatisch een pauze halverwege de film (rekening houdend met de aftiteling)"
        self.chk_auto.setToolTip(self._auto_tip)
        self.chk_auto.setChecked(self.app.cfg["auto_intermission"])
        self.chk_auto.setFocusPolicy(Qt.NoFocus)
        self.chk_auto.toggled.connect(self.app.set_auto_intermission)
        center.addSpacing(6)
        center.addWidget(self.chk_auto)
        self.set_intermission(None)
        row2.addLayout(center)

        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        rl.addStretch()
        self.end_time = label("", "EndTime")
        rl.addWidget(self.end_time)
        rl.addSpacing(10)
        self.btn_disc = icon_button(G_DISC, "Discmenu (DVD / Blu-ray)", 18)
        self.btn_disc.clicked.connect(self._show_disc_pad)
        self.btn_audio = icon_button(G_AUDIO, "Audiospoor", 18)
        self.btn_audio.clicked.connect(lambda: self._show_track_menu("audio", self.btn_audio))
        self.btn_subs = icon_button(G_CC, "Ondertitels", 18)
        self.btn_subs.clicked.connect(lambda: self._show_track_menu("subs", self.btn_subs))
        self.btn_mute = icon_button(G_VOLUME, "Geluid aan/uit (M)", 18)
        self.btn_mute.clicked.connect(self.app.toggle_mute)
        for b in (self.btn_disc, self.btn_audio, self.btn_subs, self.btn_mute):
            rl.addWidget(b)
        self.volume = ClickSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(150)
        self.volume.setFocusPolicy(Qt.NoFocus)
        self.volume.valueChanged.connect(self.app.set_volume)
        rl.addWidget(self.volume)
        self.lbl_vol = label("", "Time")
        self.lbl_vol.setMinimumWidth(48)
        rl.addWidget(self.lbl_vol)
        row2.addWidget(right, 1)
        lay.addLayout(row2)
        return bar

    # -- placement ------------------------------------------------------------
    def place_on(self, screen, windowed=False):
        place_window(self, screen, windowed, (1600, 950))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._position_toast()

    # -- sidebar --------------------------------------------------------------
    def set_libraries(self, key, value):
        self._libs[key] = value
        self._rebuild_sidebar()

    def _rebuild_sidebar(self):
        current = self.sidebar.currentItem()
        current_id = None
        if current and current.data(ROLE_ITEM):
            it = current.data(ROLE_ITEM)
            current_id = (it.source, it.id)
        self.sidebar.clear()

        def header(text, key):
            h = QListWidgetItem(text.upper())
            h.setFlags(Qt.ItemIsEnabled)  # clickable: shows the overview of the whole group
            h.setData(ROLE_KIND, "group")
            h.setData(ROLE_GROUP, (key, text))
            h.setToolTip(f"Overzicht van {text}")
            f = QFont("Segoe UI", 8)
            f.setWeight(QFont.Bold)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
            h.setFont(f)
            h.setForeground(QColor(ACCENT))
            h.setSizeHint(QSize(0, 40))
            self.sidebar.addItem(h)

        def note(text):
            n = QListWidgetItem(text)
            n.setFlags(Qt.NoItemFlags)
            n.setForeground(QColor(MUTED))
            self.sidebar.addItem(n)

        groups = [("jellyfin", "Jellyfin", G_SERVER), ("plex", "Plex", G_SERVER),
                  ("cache", "Bibliotheek", G_LIBRARY), ("folders", "Mappen", None),
                  ("usb", "USB-schijven", G_USB), ("disc", "Disc-stations", G_DISC), ("radio", "Radio", G_AUDIO),
                  ("cast", "Displaycast", G_TV)]
        shown = 0
        for key, title, glyph in groups:
            value = self._libs.get(key)
            if value is None:
                continue
            if key in ("usb", "disc") and not value:
                continue
            header(title, key)
            shown += 1
            if isinstance(value, str):
                note(value)
                continue
            if not value:
                note("Geen items")
            for it in value:
                item_glyph = kind_glyph(it.kind) if glyph is None or key == "disc" else glyph
                if key == "cache" and it.played:
                    item_glyph = G_CHECK  # watched: can go with 'Gekeken verwijderen'
                li = QListWidgetItem(glyph_icon(item_glyph, MUTED if it.kind == "empty" else TEXT, 18, ACCENT),
                                     it.title)
                li.setData(ROLE_ITEM, it)
                if it.kind == "empty":
                    li.setFlags(Qt.NoItemFlags)
                    li.setForeground(QColor(MUTED))
                self.sidebar.addItem(li)
                if current_id == (it.source, it.id):
                    self.sidebar.setCurrentItem(li)
            if key == "cache":
                self._add_cache_usage()
        if not shown:
            note("Nog geen bronnen ingesteld.")
        settings = QListWidgetItem(glyph_icon(G_SETTINGS, MUTED, 18), "Bronnen beheren…")
        settings.setData(ROLE_KIND, "settings")
        settings.setForeground(QColor(MUTED))
        self.sidebar.addItem(QListWidgetItem(""))
        self.sidebar.item(self.sidebar.count() - 1).setFlags(Qt.NoItemFlags)
        self.sidebar.addItem(settings)
        refresh = QListWidgetItem(glyph_icon(G_REFRESH, MUTED, 18), "Vernieuwen")
        refresh.setData(ROLE_KIND, "refresh")
        refresh.setForeground(QColor(MUTED))
        self.sidebar.addItem(refresh)

    def _add_cache_usage(self):
        """Free disk space under the bibliotheek, plus a button for removing what has been watched."""
        usage = self.app.cache_usage()
        if usage is None:
            return
        text = f"{fmt_size(usage['free'])} vrij van {fmt_size(usage['total'])}"
        if usage["count"]:
            text += f"\nIn gebruik: {fmt_size(usage['size'])}"
        info = QListWidgetItem(text)
        info.setFlags(Qt.NoItemFlags)
        info.setForeground(QColor(MUTED))
        info.setToolTip("Vrije ruimte op de schijf waar de bibliotheek staat, en wat de bibliotheek inneemt")
        self.sidebar.addItem(info)
        if usage["watched"]:
            clean = QListWidgetItem(glyph_icon(G_DELETE, MUTED, 18), f"Gekeken verwijderen ({usage['watched']})")
            clean.setData(ROLE_KIND, "cache_clean")
            clean.setForeground(QColor(MUTED))
            clean.setToolTip(f"Alle {usage['watched']} gekeken films en afleveringen uit de bibliotheek verwijderen "
                             f"({fmt_size(usage['watched_size'])} vrijmaken)")
            self.sidebar.addItem(clean)

    def _on_sidebar_clicked(self, li):
        kind = li.data(ROLE_KIND)
        if kind == "cache_clean":
            self.sidebar.clearSelection()
            self.app.cache_remove_watched()
            return
        if kind == "group":
            self.sidebar.clearSelection()
            self.show_group(*li.data(ROLE_GROUP))
            return
        if kind == "settings":
            self.sidebar.clearSelection()
            self.app.open_settings()
            return
        if kind == "refresh":
            self.sidebar.clearSelection()
            self.app.reload_sources()
            return
        item = li.data(ROLE_ITEM)
        if item is None:
            return
        self.nav_stack = []
        if item.kind == "displaycast":
            self.show_cast_panel()
            return
        if item.is_folder:
            self.open_item(item)
        else:
            self._show_items([item], [item.title])
            self.select_details(item)

    # -- navigation -----------------------------------------------------------
    def show_group(self, key, title):
        """Overview of a whole sidebar group: all Jellyfin/Plex libraries, everything in the bibliotheek, ..."""
        if key == "cast":
            self.show_cast_panel()
            return
        self.search.clear()
        self.nav_stack = []
        # The bibliotheek lists its whole content itself; other groups show their sidebar entries.
        source = "cache" if key == "cache" else "group"
        self.open_item(MediaItem(source=source, id="" if key == "cache" else key, title=title, kind="library",
                                 is_folder=True))

    def open_item(self, item):
        if self.nav_stack and self.nav_stack[-1] is item:
            return
        self.nav_stack.append(item)
        self._load_current()

    def go_back(self):
        if len(self.nav_stack) > 1:
            self.nav_stack.pop()
            self._load_current()

    def _load_current(self):
        item = self.nav_stack[-1]
        crumbs = [i.title for i in self.nav_stack]
        self._load_token += 1
        token = self._load_token
        self._show_message("Laden…")
        self._set_crumb(crumbs)
        if item.source == "group":  # overview from a sidebar header
            value = self._libs.get(item.id)
            if isinstance(value, str):
                self._show_message(value)  # e.g. 'Niet bereikbaar: …'
            else:
                self._show_items([i for i in value or [] if i.kind != "empty"], crumbs)
            return
        source = self.app.sources.get(item.source)
        if source is None:
            self._show_message("Deze bron is niet (meer) beschikbaar.")
            return

        def done(items):
            if token == self._load_token:
                self._show_items(items, crumbs)

        def failed(err):
            if token == self._load_token:
                self._show_message(f"Kon niet laden:\n{err}")

        run_async(source.children, done, failed, item)

    def _set_crumb(self, crumbs):
        self.crumb.setText("  ›  ".join(crumbs))
        self.btn_back.setEnabled(len(self.nav_stack) > 1)

    def _show_message(self, text):
        self.message.setText(text)
        self.center_stack.setCurrentWidget(self.message)
        self.btn_play_list.hide()
        self.btn_shuffle.hide()
        self._update_clean_button()

    def _update_clean_button(self):
        """'Gekeken verwijderen' in the top bar, whenever the bibliotheek is shown."""
        in_cache = bool(self.nav_stack) and self.nav_stack[0].source == "cache"
        usage = self.app.cache_usage() if in_cache else None
        self.btn_clean.setVisible(usage is not None and usage["count"] > 0)
        if usage is not None:
            watched = usage["watched"]
            self.btn_clean.setText(f"  Gekeken verwijderen ({watched} · {fmt_size(usage['watched_size'])})"
                                   if watched else "  Gekeken verwijderen")
            self.btn_clean.setEnabled(watched > 0)

    def refresh_cache_view(self):
        """After removing from the bibliotheek: show the current folder again (or the nearest that still exists)."""
        if not self.nav_stack or self.nav_stack[0].source != "cache":
            return
        source = self.app.sources["cache"]
        while len(self.nav_stack) > 1 and not source.children(self.nav_stack[-1]):
            self.nav_stack.pop()
        self._load_current()

    def _show_items(self, items, crumbs):
        self._set_crumb(crumbs)
        self._update_clean_button()
        self.grid.clear()
        # Only in music (playlists, artists, albums, lists of tracks): one button plays everything in it.
        music = bool(self.nav_stack) and (self.nav_stack[-1].kind in ("playlist", "artist", "album")
                                          or any(i.kind in ("track", "album", "artist") for i in items))
        for b, text in ((self.btn_play_list, "  Alles afspelen"), (self.btn_shuffle, "  Willekeurig")):
            b.setVisible(music)
            b.setEnabled(True)
            b.setText(text)
        if not items:
            self._show_message("Deze map is leeg of bevat geen video's.")
            return
        for it in items:
            li = QListWidgetItem()
            li.setData(ROLE_ITEM, it)
            li.setData(Qt.DisplayRole, "")
            self.grid.addItem(li)
        self.center_stack.setCurrentWidget(self.grid)
        self._apply_filter(self.search.text())
        self.grid.scrollToTop()

    def _apply_filter(self, text):
        needle = text.strip().lower()
        for i in range(self.grid.count()):
            li = self.grid.item(i)
            it = li.data(ROLE_ITEM)
            hay = f"{it.title} {it.subtitle} {it.year or ''}".lower()
            li.setHidden(bool(needle) and needle not in hay)

    def _on_grid_clicked(self, li):
        item = li.data(ROLE_ITEM)
        if item.is_folder:
            self.search.clear()
            self.open_item(item)
        else:
            self.select_details(item)

    def _shown_items(self):
        """Items in the grid in their order, without those hidden by the search filter."""
        return [self.grid.item(i).data(ROLE_ITEM) for i in range(self.grid.count())
                if not self.grid.item(i).isHidden()]

    def _play_current_list(self, shuffle=False):
        if not self.nav_stack:
            return
        folder = self.nav_stack[-1]
        items = self._shown_items()
        playable = [i for i in items if i.playable]

        def start(found):
            if shuffle:
                self.app.play_shuffled(found, folder.title)
            else:
                self.app.play_all(found)

        if playable and len(playable) == len(items) or self.search.text().strip():
            start(playable)  # exactly what is shown (in this order)
            return
        # Contains folders (artists, albums, sub-folders): gather recursively from the source.
        button, text = (self.btn_shuffle, "  Willekeurig") if shuffle else (self.btn_play_list, "  Alles afspelen")
        token = self._load_token
        button.setEnabled(False)
        button.setText("  Laden…")

        def done(found):
            if token == self._load_token:
                button.setEnabled(True)
                button.setText(text)
            start(found)

        def failed(err):
            if token == self._load_token:
                button.setEnabled(True)
                button.setText(text)
            self.toast(f"‘{folder.title}’ laden mislukt: {err}")

        self.app.gather_playable(folder, done, failed)

    def _on_grid_double(self, li):
        item = li.data(ROLE_ITEM)
        if item.playable:
            if self.nav_stack and self.nav_stack[-1].kind in ("playlist", "album"):
                # In a playlist/album: start here and let the rest of the list follow.
                items = [i for i in self._shown_items() if i.playable]
                start = next((n for n, i in enumerate(items) if i is item), None)
                self.app.play_all(items[start:] if start is not None else [item])
            else:
                self.app.play(item, resume=False)
        elif item.kind == "playlist":
            self.app.play_playlist(item)

    def select_details(self, item):
        self.details_panel.set_item(item)
        self.tab_details.setChecked(True)
        self.right_stack.setCurrentWidget(self.details_panel)

    def cache_item_removed(self, item):
        """A bibliotheek item was deleted: do not keep showing its details."""
        details = self.details_panel.item
        if details is not None and (details.source, details.id) == (item.source, item.id):
            self.details_panel.clear()

    # -- now playing ----------------------------------------------------------
    def set_now_playing(self, item):
        self._now_item = item
        self._length = 0
        self.now_panel.set_item(item)
        active = item is not None
        for b in (self.btn_play, self.btn_stop, self.btn_rew, self.btn_audio, self.btn_subs):
            b.setEnabled(active)
        self._update_next_button()
        self.btn_intermission.setEnabled(active and not self._music)
        self.btn_disc.setVisible(bool(item and item.kind in ("dvd", "bluray")))
        self.slider.setEnabled(active)
        if not active:
            self.set_planned_pause(None)
            self.slider.setValue(0)
            self.lbl_pos.setText("0:00")
            self.lbl_rem.setText("0:00")
            self.end_time.setText("")
            self.now_title.setText("Niets aan het afspelen")
            self.now_sub.setText("")
            self.mini_poster.clear()
            self.set_play_state("idle")
            return
        self.now_title.setText(item.title)
        self.now_sub.setText(item.subtitle or item_meta(item))
        self.mini_poster.clear()
        self.app.images.request(item.image_url, self._set_mini_poster)
        self.tab_now.setChecked(True)
        self.right_stack.setCurrentWidget(self.now_panel)

    def set_shuffle(self, title, count=0):
        """title None: random play is off."""
        note = self.queue_panel.shuffle_note
        if title is None:
            note.hide()
            return
        note.setText(f"Willekeurig afspelen uit ‘{title}’ ({count} titels). Het volgende nummer wordt steeds "
                     "vooraf gekozen; Stoppen of ‘Wis alles’ beëindigt het.")
        note.show()

    def _update_next_button(self):
        """With music 'next' needs something in the queue."""
        self.btn_fwd.setEnabled(self._now_item is not None and (not self._music or bool(self.app.queue)))

    def set_projector_state(self, state):
        """Top bar button: '' hides it (no projector set up)."""
        b = self.btn_projector
        b.setVisible(bool(state))
        if not state:
            return
        text, primary, enabled, tip = {
            "off": ("  Beamer aan", True, True, "Beamer aanzetten (staat uit; spaart lampuren als je alleen muziek luistert)"),
            "warming": ("  Beamer warmt op…", False, False, "De lamp warmt op; het beeld verschijnt zo"),
            "on": ("  Beamer uit", False, True, "Beamer uitzetten"),
            "cooling": ("  Beamer koelt af…", False, False, "De lamp koelt af; daarna kan hij weer aan"),
            "unreachable": ("  Beamer aan", True, True, "Beamer niet bereikbaar – probeer aan te zetten"),
        }.get(state, ("  Beamer aan", True, True, ""))
        b.setText(text)
        b.setToolTip(tip)
        b.setEnabled(enabled)
        b.setObjectName("Primary" if primary else "")
        b.setIcon(glyph_icon(G_POWER, "#16130B" if primary else (TEXT if enabled else MUTED), 14))
        b.style().unpolish(b)
        b.style().polish(b)

    def show_cast_panel(self):
        """Opening Displaycast starts the receivers (phones can cast from then on)."""
        self._load_token += 1  # a library still loading must not replace the panel
        self._set_crumb(["Displaycast"])
        self.btn_play_list.hide()
        self.btn_shuffle.hide()
        self.center_stack.setCurrentWidget(self.cast_panel)
        if not self.app.cast.running:
            self.app.cast.start()
        self.cast_panel.set_status(self.app.cast.status())

    def set_cast_status(self, status):
        self.cast_panel.set_status(status)

    def set_radio_song(self, song):
        """Radio: the song the station announces, under the station name in the transport bar."""
        if self._now_item is not None and self._now_item.kind == "radio":
            self.now_sub.setText(song or self._now_item.subtitle)

    def set_queue(self, items):
        self.queue_panel.set_items(items)
        self._update_next_button()
        self.tab_queue.setText(f"WACHTRIJ ({len(items)})" if items else "WACHTRIJ")

    def _set_mini_poster(self, url, pm):
        if self._now_item and url == self._now_item.image_url:
            ratio = self.devicePixelRatioF()
            scaled = pm.scaled(int(40 * ratio), int(56 * ratio), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            self.mini_poster.setPixmap(scaled)

    def set_music_mode(self, music):
        """An intermission or a break halfway makes no sense for music: switch both off meanwhile."""
        self._music = music
        self.btn_intermission.setEnabled(self._now_item is not None and not music)
        # music: previous/next song instead of 10 s back / 30 s forward
        self.btn_rew.setIcon(glyph_icon(G_PREV if music else G_REWIND, TEXT, 20))
        self.btn_rew.setToolTip("Vorig nummer (Ctrl+←); na 3 s eerst terug naar het begin" if music
                                else "10 seconden terug (Ctrl+←)")
        self.btn_fwd.setIcon(glyph_icon(G_NEXT if music else G_FORWARD, TEXT, 20))
        self.btn_fwd.setToolTip("Volgend nummer (Ctrl+→)" if music else "30 seconden vooruit (Ctrl+→)")
        self._update_next_button()
        self.chk_auto.setEnabled(not music)
        for w, normal in ((self.btn_intermission, self._intermission_tip), (self.chk_auto, self._auto_tip)):
            w.setToolTip("Niet beschikbaar bij muziek" if music else normal)
        if music:
            self.set_planned_pause(None)

    def set_auto_intermission(self, enabled):
        for box in (self.chk_auto, self.details_panel.chk_auto):
            box.blockSignals(True)
            box.setChecked(enabled)
            box.blockSignals(False)

    def set_planned_pause(self, at_ms, reason=""):
        self._planned_at = at_ms
        self._planned_reason = reason
        self.slider.set_marker(at_ms * 1000 // self._length if at_ms is not None and self._length else None)
        self._update_planned_text(self.app.player.position)

    def _update_planned_text(self, pos):
        at = getattr(self, "_planned_at", None)
        if at is None or not self._now_item:
            self.now_panel.set_tech("pause", "")
            return
        clock = datetime.now() + timedelta(milliseconds=max(0, at - pos))
        self.now_panel.set_tech("pause", f"{fmt_time(at, True)} · ca. {clock:%H:%M}\n({self._planned_reason})")

    def set_intermission(self, mode, seconds_left=0):
        """mode: None (no break), 'pause' (break running) or 'countdown' (resuming)."""
        b = self.btn_intermission
        if mode is None:
            b.setText("  Pauze")
            b.setIcon(glyph_icon(G_PAUSE, TEXT, 16))
            b.setObjectName("")
            b.setEnabled(self._now_item is not None and not self._music)
        elif mode == "pause":
            b.setText("  Film hervatten")
            b.setIcon(glyph_icon(G_PLAY, "#16130B", 16))
            b.setObjectName("Primary")
            b.setEnabled(True)
        else:
            b.setText(f"  Hervat over {seconds_left}…")
            b.setIcon(glyph_icon(G_PLAY, MUTED, 16))
            b.setObjectName("")
            b.setEnabled(False)
        b.style().unpolish(b)
        b.style().polish(b)
        if self._now_item and mode:
            self.now_panel.set_state("intermission" if mode == "pause" else "countdown")

    def set_play_state(self, state):
        playing = state == "playing"
        self.btn_play.setIcon(glyph_icon(G_PAUSE if playing else G_PLAY, "#16130B", 24))
        if self._now_item:
            self.now_panel.set_state(state)

    def set_progress(self, pos, length):
        self._length = length
        if not self._seeking and length > 0:
            self.slider.setValue(int(pos * 1000 / length))
        self.lbl_pos.setText(fmt_time(pos, length >= 3600000))
        self.lbl_rem.setText("-" + fmt_time(max(0, length - pos), length >= 3600000) if length else "")
        if length > 0:
            end = datetime.now() + timedelta(milliseconds=max(0, length - pos))
            self.end_time.setText(f"Eindigt {end:%H:%M}")
            self.now_panel.set_tech("ends", f"{end:%H:%M}")
        self._update_planned_text(pos)

    def set_volume_display(self, volume, muted):
        self.volume.blockSignals(True)
        self.volume.setValue(volume)
        self.volume.blockSignals(False)
        self.lbl_vol.setText("Stil" if muted else f"{volume}%")
        self.btn_mute.setIcon(glyph_icon(G_MUTE if muted or volume == 0 else G_VOLUME, TEXT, 18))

    def _on_slider_moved(self, value):
        if self._length:
            pos = value * self._length // 1000
            self.lbl_pos.setText(fmt_time(pos, self._length >= 3600000))

    def _on_slider_released(self):
        self._seeking = False
        if self._length:
            self.app.player.seek(self.slider.value() * self._length // 1000)

    def _refresh_tech(self):
        if not self._now_item or self.app.player.state not in ("playing", "paused"):
            return
        info = self.app.player.media_info()
        for key in ("video", "audio", "audio_track", "subtitle_track", "chapter"):
            self.now_panel.set_tech(key, info.get(key, ""))
        self.now_panel.info.update_meta(self._length)
        meta_title = info.get("meta_title")
        if self._now_item.source == "local" and self._now_item.is_disc and meta_title and \
                meta_title.lower() not in ("dvd", "bluray", "blu-ray") and not meta_title.startswith(("dvd:", "bluray:")):
            if self._now_item.title in ("DVD", "Blu-ray") and meta_title != self._now_item.title:
                self._now_item.title = meta_title
                self.now_title.setText(meta_title)
                self.now_panel.info.title.setText(meta_title)

    def _show_track_menu(self, kind, button):
        player = self.app.player
        menu = QMenu(self)
        if kind == "audio":
            tracks, current, setter = player.audio_tracks(), player.audio_track(), player.set_audio_track
        else:
            tracks, current, setter = player.subtitle_tracks(), player.subtitle_track(), player.set_subtitle_track
            off = menu.addAction(("✓  " if current == -1 else "     ") + "Uit")
            off.triggered.connect(lambda: setter(-1))
        if not tracks:
            menu.addAction("Geen sporen beschikbaar").setEnabled(False)
        for tid, name in tracks:
            act = menu.addAction(("✓  " if tid == current else "     ") + name)
            act.triggered.connect(lambda _=False, t=tid: setter(t))
        pos = button.mapToGlobal(button.rect().topLeft())
        menu.exec(QPoint(pos.x(), pos.y() - menu.sizeHint().height() - 6))

    def _show_disc_pad(self):
        pad = DiscPad(self.app, self)
        pad.adjustSize()
        top_left = self.btn_disc.mapToGlobal(self.btn_disc.rect().topLeft())
        pad.move(top_left.x() - pad.width() // 2, top_left.y() - pad.height() - 8)
        pad.show()

    # -- misc -----------------------------------------------------------------
    def _tick_clock(self):
        self.clock.setText(datetime.now().strftime("%H:%M"))

    def toast(self, text, error=True, ms=6000):
        self._toast_lbl.setObjectName("Toast" if error else "ToastInfo")
        self._toast_lbl.style().unpolish(self._toast_lbl)
        self._toast_lbl.style().polish(self._toast_lbl)
        self._toast_lbl.setText(text)
        self._toast_lbl.setMaximumWidth(640)
        self._toast_lbl.adjustSize()
        self._position_toast()
        self._toast_lbl.show()
        self._toast_lbl.raise_()
        self._toast_timer.start(ms)

    def hide_toast(self):
        self._toast_timer.stop()
        self._toast_lbl.hide()

    def _position_toast(self):
        parent = self._toast_lbl.parentWidget()
        if parent:
            self._toast_lbl.move((parent.width() - self._toast_lbl.width()) // 2, parent.height() - self._toast_lbl.height() - 24)

    def closeEvent(self, e):
        # Alt+F4 is swallowed; closing only happens through the power button menu.
        if self.app.closing:
            e.accept()
        else:
            e.ignore()
