"""Windows shown on the audience screens: placeholder, video and 'now showing'."""

import os
from datetime import datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import QFrame, QLabel, QStackedLayout, QVBoxLayout, QWidget

from .models import fmt_runtime, fmt_time
from .presentation import SlideView
from .style import ACCENT


def _display_font(px, weight=QFont.Light, spacing=None):
    f = QFont("Segoe UI Variable Display")
    if not f.exactMatch():
        f = QFont("Segoe UI")
    f.setPixelSize(max(1, int(px)))
    f.setWeight(weight)
    if spacing is not None:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def place_window(window, screen, windowed=False, size=None):
    """Put a top-level window full screen on `screen` (or as a normal window when testing)."""
    geo = screen.geometry()
    if window.isFullScreen():
        window.showNormal()
    # Show it inside the target screen first; Windows picks the fullscreen monitor from its position.
    w, h = size or (geo.width() // 2, geo.height() // 2)
    window.setGeometry(geo.x() + 40, geo.y() + 40, min(w, geo.width() - 80), min(h, geo.height() - 80))
    window.show()
    window.windowHandle().setScreen(screen)
    if not windowed:
        window.showFullScreen()


def paint_stage(p, w, h):
    """Dark theatre background: warm spotlight, velvet curtain edges and vignette."""
    p.fillRect(0, 0, w, h, QColor(4, 4, 5))
    spot = QRadialGradient(QPointF(w / 2, h * 0.42), max(w, h) * 0.62)
    spot.setColorAt(0.0, QColor(58, 42, 26))
    spot.setColorAt(0.35, QColor(26, 19, 14))
    spot.setColorAt(1.0, QColor(4, 4, 5))
    p.fillRect(0, 0, w, h, spot)
    paint_curtains(p, w, h)


def paint_curtains(p, w, h):
    """Velvet curtain edges and vignette (the stage frame), also drawn over other backgrounds."""
    cw = w * 0.085
    folds = 7
    for side in (0, 1):
        for i in range(folds):
            fw = cw / folds
            x = i * fw if side == 0 else w - (i + 1) * fw
            g = QLinearGradient(x, 0, x + fw, 0)
            g.setColorAt(0.0, QColor(34, 4, 8))
            g.setColorAt(0.5, QColor(92, 14, 22))
            g.setColorAt(1.0, QColor(34, 4, 8))
            p.fillRect(QRectF(x, 0, fw + 0.5, h), g)
        fade = QLinearGradient(cw * 0.4 if side == 0 else w - cw * 0.4, 0, cw * 1.6 if side == 0 else w - cw * 1.6, 0)
        fade.setColorAt(0.0, QColor(0, 0, 0, 0))
        fade.setColorAt(0.45, QColor(4, 4, 5, 200))
        fade.setColorAt(1.0, QColor(4, 4, 5, 0))
        x0 = 0 if side == 0 else w - cw * 1.6
        p.fillRect(QRectF(x0, 0, cw * 1.6, h), fade)
        shade = QLinearGradient(0, 0, 0, h)
        shade.setColorAt(0.0, QColor(0, 0, 0, 150))
        shade.setColorAt(0.25, QColor(0, 0, 0, 0))
        shade.setColorAt(0.8, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(0, 0, 0, 170))
        p.fillRect(QRectF(0 if side == 0 else w - cw, 0, cw, h), shade)

    vignette = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.75)
    vignette.setColorAt(0.6, QColor(0, 0, 0, 0))
    vignette.setColorAt(1.0, QColor(0, 0, 0, 190))
    p.fillRect(0, 0, w, h, vignette)


def render_placeholder(width, height, title, subtitle, ratio=1.0, with_text=True):
    """The 'CinemaViewer / by …' placeholder as an image; also used for the desktop wallpaper.
    Without text it is just the stage (curtains and spotlight), used for the Windows lock screen."""
    w, h = max(1, width), max(1, height)
    img = QImage(int(w * ratio), int(h * ratio), QImage.Format_RGB32)
    img.setDevicePixelRatio(ratio)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    paint_stage(p, w, h)
    if not with_text:
        p.end()
        return img
    unit = min(h, w * 0.5625)

    p.setFont(_display_font(unit * 0.13, QFont.Light, unit * 0.004))
    # the block (title, line, subtitle) sits just above the middle, also on 16:10 screens
    title_rect = QRectF(0, h / 2 - unit * 0.21, w, unit * 0.2)
    p.setPen(QColor(229, 181, 58, 28))  # soft glow
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        p.drawText(title_rect.translated(dx, dy), Qt.AlignHCenter | Qt.AlignVCenter, title)
    p.setPen(QColor(244, 239, 228))
    p.drawText(title_rect, Qt.AlignHCenter | Qt.AlignVCenter, title)

    line_y = title_rect.bottom() + unit * 0.035
    lw = unit * 0.34
    g = QLinearGradient(w / 2 - lw / 2, 0, w / 2 + lw / 2, 0)
    g.setColorAt(0.0, QColor(229, 181, 58, 0))
    g.setColorAt(0.5, QColor(229, 181, 58, 230))
    g.setColorAt(1.0, QColor(229, 181, 58, 0))
    p.fillRect(QRectF(w / 2 - lw / 2, line_y, lw, max(1.5, unit * 0.0025)), g)

    p.setFont(_display_font(unit * 0.034, QFont.Normal, unit * 0.006))
    p.setPen(QColor(ACCENT))
    p.drawText(QRectF(0, line_y + unit * 0.03, w, unit * 0.06), Qt.AlignHCenter | Qt.AlignTop, subtitle)
    p.end()
    return img


class PlaceholderWidget(QWidget):
    def __init__(self, title, subtitle, parent=None):
        super().__init__(parent)
        self.title = title
        self.subtitle = subtitle
        self._cache = None
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_texts(self, title, subtitle):
        self.title, self.subtitle = title, subtitle
        self._cache = None
        self.update()

    def resizeEvent(self, e):
        self._cache = None
        super().resizeEvent(e)

    def _render(self):
        w, h = max(1, self.width()), max(1, self.height())
        return QPixmap.fromImage(render_placeholder(w, h, self.title, self.subtitle, self.devicePixelRatioF()))

    def paintEvent(self, e):
        ratio = self.devicePixelRatioF()
        if self._cache is None or self._cache.width() != int(max(1, self.width()) * ratio):
            self._cache = self._render()
        QPainter(self).drawPixmap(0, 0, self._cache)


class NowShowingWidget(QWidget):
    """Shown on extra audience screens while a film plays on the projection screen.

    Without preview: poster with the texts beside it. With the live preview the screen is
    split into three columns (poster | texts | preview + progress) that share one top and
    bottom line. Background, poster and texts are rendered once into a cache; only the
    preview and the progress bar are repainted while playing.
    """

    def __init__(self, images, parent=None):
        super().__init__(parent)
        self.images = images
        self.item = None
        self.length_ms = 0
        self.position_ms = 0
        self.frame = None
        self._cache = None
        self._preview_rect = QRectF()
        self._progress_rect = QRectF()
        self._shown_second = -1
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        images.loaded.connect(self._on_image)

    def _invalidate(self):
        self._cache = None
        self.update()

    def set_item(self, item):
        if item is not self.item:
            self.frame = None
        self.item = item
        if item:
            self.images.request(item.image_url)
            self.images.request(item.backdrop_url)
        self._invalidate()

    def set_progress(self, pos, length):
        changed = abs(pos - self.position_ms) > 30000 or length != self.length_ms
        self.position_ms, self.length_ms = pos, length
        if changed:
            self._invalidate()
        elif not self._progress_rect.isEmpty() and pos // 1000 != self._shown_second:
            self.update(self._progress_rect.adjusted(-4, -4, 4, 4).toAlignedRect())

    def set_preview_frame(self, image):
        had_frame = self.frame is not None
        self.frame = image
        if had_frame != (image is not None):
            self._invalidate()  # the layout changes when the preview appears or disappears
        elif image is not None:
            self.update(self._preview_rect.adjusted(-4, -4, 4, 4).toAlignedRect())

    def _on_image(self, url):
        if self.item and url in (self.item.image_url, self.item.backdrop_url):
            self._invalidate()

    def resizeEvent(self, e):
        self._cache = None
        super().resizeEvent(e)

    # -- static layer ---------------------------------------------------------
    def _render(self):
        w, h = max(1, self.width()), max(1, self.height())
        ratio = self.devicePixelRatioF()
        pm = QPixmap(int(w * ratio), int(h * ratio))
        pm.setDevicePixelRatio(ratio)
        p = QPainter(pm)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self._preview_rect = QRectF()
        self._progress_rect = QRectF()
        item = self.item
        backdrop = self.images.get(item.backdrop_url) if item and item.backdrop_url else None
        if backdrop:
            scaled = backdrop.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
            p.fillRect(0, 0, w, h, QColor(5, 5, 6, 205))
        else:
            paint_stage(p, w, h)
        if item:
            if self.frame is not None:
                self._render_wide(p, w, h)
            else:
                self._render_classic(p, w, h)
        p.end()
        return pm

    def _draw_poster(self, p, rect, unit):
        """Draw the poster fitted in `rect` (top aligned); returns the used rect or None."""
        poster = self.images.get(self.item.image_url) if self.item.image_url else None
        if not poster:
            return None
        ratio = self.devicePixelRatioF()
        scaled = poster.scaled(int(rect.width() * ratio), int(rect.height() * ratio), Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
        r = QRectF(rect.x(), rect.y(), scaled.width() / ratio, scaled.height() / ratio)
        path = QPainterPath()
        path.addRoundedRect(r, unit * 0.01, unit * 0.01)
        p.setClipPath(path)
        p.drawPixmap(r, scaled, QRectF(0, 0, scaled.width(), scaled.height()))
        p.setClipping(False)
        return r

    def _meta_text(self):
        item = self.item
        return "   ·   ".join(x for x in (item.subtitle, str(item.year) if item.year else "",
                                         fmt_runtime(self.length_ms or item.runtime_ms), item.rating) if x)

    def _label(self):
        return {"radio": "NU OP DE RADIO", "track": "NU TE HOREN", "audiocd": "NU TE HOREN"}.get(self.item.kind,
                                                                                               "NU TE ZIEN")

    def _end_text(self):
        if self.length_ms <= 0:
            return ""
        end = datetime.now() + timedelta(milliseconds=max(0, self.length_ms - self.position_ms))
        return f"Eindigt om {end:%H:%M}"

    def _render_classic(self, p, w, h):
        item = self.item
        unit = min(h, w * 0.5625)
        ph = unit * 0.62
        x0 = w * 0.14
        y0 = (h - ph) / 2
        used = self._draw_poster(p, QRectF(x0, y0, ph * 2 / 3, ph), unit)
        text_x = used.right() + unit * 0.07 if used else x0
        tw = w - text_x - w * 0.1

        p.setFont(_display_font(unit * 0.03, QFont.DemiBold, unit * 0.008))
        p.setPen(QColor(ACCENT))
        p.drawText(QRectF(text_x, y0, tw, unit * 0.05), Qt.AlignLeft | Qt.AlignVCenter, self._label())
        p.setFont(_display_font(unit * 0.085, QFont.Light))
        p.setPen(QColor(244, 239, 228))
        title_rect = QRectF(text_x, y0 + unit * 0.06, tw, unit * 0.24)
        p.drawText(title_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, item.title)
        bounds = p.boundingRect(title_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, item.title)
        p.setFont(_display_font(unit * 0.03, QFont.Normal))
        p.setPen(QColor(190, 186, 178))
        y = bounds.bottom() + unit * 0.03
        p.drawText(QRectF(text_x, y, tw, unit * 0.05), Qt.AlignLeft | Qt.AlignTop, self._meta_text())
        if item.genres:
            y += unit * 0.055
            p.setPen(QColor(150, 146, 140))
            p.drawText(QRectF(text_x, y, tw, unit * 0.05), Qt.AlignLeft | Qt.AlignTop, ", ".join(item.genres[:4]))
        if self._end_text():
            y += unit * 0.09
            p.setFont(_display_font(unit * 0.028, QFont.Normal, unit * 0.003))
            p.setPen(QColor(ACCENT))
            p.drawText(QRectF(text_x, y, tw, unit * 0.05), Qt.AlignLeft | Qt.AlignTop, self._end_text())

    def _render_wide(self, p, w, h):
        """Three columns on one grid: poster | texts | live preview with progress underneath."""
        item = self.item
        unit = min(h, w * 0.5625)
        margin = w * 0.085                     # clear of the curtains
        gap = w * 0.035

        # Right column: preview, directly followed by progress bar + times. Its height sets the
        # shared height of all three columns, so poster, texts and preview start and end together.
        times_h = unit * 0.03
        bar_h = max(3.0, unit * 0.005)
        footer = unit * 0.03 + bar_h + unit * 0.02 + times_h
        aspect = self.frame.width() / max(1, self.frame.height())
        pv_w = w * 0.32
        pv_h = pv_w / aspect
        if pv_h > unit * 0.5:                  # portrait / 4:3 content: keep it in proportion
            pv_h = unit * 0.5
            pv_w = pv_h * aspect
        block_h = pv_h + footer
        top = (h - block_h) / 2
        bottom = top + block_h
        right = w - margin
        self._preview_rect = QRectF(right - pv_w, top, pv_w, pv_h)
        self._progress_rect = QRectF(right - pv_w, bottom - footer + unit * 0.03, pv_w,
                                     footer - unit * 0.03)
        p.setPen(QPen(QColor(229, 181, 58, 140), 1.5))
        p.setBrush(QColor(0, 0, 0))
        p.drawRoundedRect(self._preview_rect.adjusted(-2, -2, 2, 2), 7, 7)

        # Left column: poster, top aligned.
        used = self._draw_poster(p, QRectF(margin, top, block_h * 2 / 3, block_h), unit)
        text_x = used.right() + gap if used else margin
        tw = self._preview_rect.left() - gap - text_x

        # Middle column: label and title from the top line, end time on the bottom line.
        p.setFont(_display_font(unit * 0.026, QFont.DemiBold, unit * 0.007))
        p.setPen(QColor(ACCENT))
        label_h = unit * 0.04
        p.drawText(QRectF(text_x, top, tw, label_h), Qt.AlignLeft | Qt.AlignTop, self._label())

        # The title gets the room between the label and the meta lines + end time; long titles shrink.
        reserved = unit * (0.025 + 0.045 + (0.05 if item.genres else 0) + 0.07)
        title_rect = QRectF(text_x, top + label_h + unit * 0.01, tw,
                            max(unit * 0.08, bottom - reserved - (top + label_h + unit * 0.01)))
        flags = Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap
        for size in (0.07, 0.06, 0.05, 0.042, 0.036):
            p.setFont(_display_font(unit * size, QFont.Light))
            bounds = p.boundingRect(title_rect, flags, item.title)
            if bounds.height() <= title_rect.height():
                break
        p.setPen(QColor(244, 239, 228))
        p.save()
        p.setClipRect(title_rect)
        p.drawText(title_rect, flags, item.title)
        p.restore()

        y = min(bounds.bottom(), title_rect.bottom()) + unit * 0.025
        p.setFont(_display_font(unit * 0.028, QFont.Normal))
        p.setPen(QColor(190, 186, 178))
        p.drawText(QRectF(text_x, y, tw, unit * 0.045), Qt.AlignLeft | Qt.AlignTop, self._meta_text())
        if item.genres:
            y += unit * 0.05
            p.setFont(_display_font(unit * 0.026, QFont.Normal))
            p.setPen(QColor(150, 146, 140))
            p.drawText(QRectF(text_x, y, tw, unit * 0.045), Qt.AlignLeft | Qt.AlignTop,
                       p.fontMetrics().elidedText(", ".join(item.genres[:4]), Qt.ElideRight, int(tw)))
        if self._end_text():
            p.setFont(_display_font(unit * 0.028, QFont.Normal, unit * 0.003))
            p.setPen(QColor(ACCENT))
            p.drawText(QRectF(text_x, bottom - unit * 0.05, tw, unit * 0.05), Qt.AlignLeft | Qt.AlignBottom,
                       self._end_text())

    # -- dynamic layer --------------------------------------------------------
    def paintEvent(self, e):
        ratio = self.devicePixelRatioF()
        if self._cache is None or self._cache.width() != int(max(1, self.width()) * ratio):
            self._cache = self._render()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        if self.frame is None or self._preview_rect.isEmpty():
            return
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        r = self._preview_rect
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.setClipPath(path)
        p.drawImage(r, self.frame)
        p.setClipping(False)
        badge = QRectF(r.x() + 10, r.y() + 10, max(56.0, r.height() * 0.2), max(22.0, r.height() * 0.075))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(badge, 4, 4)
        dot = badge.height() * 0.36
        p.setBrush(QColor(220, 50, 47))
        p.drawEllipse(QRectF(badge.x() + dot, badge.center().y() - dot / 2, dot, dot))
        p.setFont(_display_font(badge.height() * 0.55, QFont.DemiBold, 1))
        p.setPen(QColor(236, 232, 224))
        p.drawText(badge.adjusted(dot * 2.6, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, "LIVE")
        self._paint_progress(p)

    def _paint_progress(self, p):
        r = self._progress_rect
        if r.isEmpty() or self.length_ms <= 0:
            return
        self._shown_second = self.position_ms // 1000
        unit = min(self.height(), self.width() * 0.5625)
        bar_h = max(3.0, unit * 0.005)
        bar = QRectF(r.x(), r.y(), r.width(), bar_h)
        frac = max(0.0, min(1.0, self.position_ms / self.length_ms))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(bar, bar_h / 2, bar_h / 2)
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * frac, bar_h), bar_h / 2, bar_h / 2)
        hours = self.length_ms >= 3600000
        p.setFont(_display_font(unit * 0.024, QFont.Normal, unit * 0.002))
        p.setPen(QColor(190, 186, 178))
        p.drawText(r, Qt.AlignLeft | Qt.AlignBottom, fmt_time(self.position_ms, hours))
        p.drawText(r, Qt.AlignRight | Qt.AlignBottom,
                   "-" + fmt_time(max(0, self.length_ms - self.position_ms), hours))


def _blurred(pm, w, h):
    """Cheap, strong blur for a full-screen backdrop: crop to the screen's shape, shrink to a few
    pixels and scale back up smoothly in two steps."""
    cover = pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.FastTransformation)
    cover = cover.copy((cover.width() - w) // 2, (cover.height() - h) // 2, w, h)
    tiny = cover.scaled(max(2, w // 48), max(2, h // 48), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    mid = tiny.scaled(max(2, w // 8), max(2, h // 8), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return mid.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def _glow_color(pm):
    """The cover's average colour, pulled towards the accent and kept bright enough to glow."""
    avg = pm.scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).toImage().pixelColor(0, 0)
    gold = QColor(ACCENT)
    c = QColor((avg.red() + gold.red()) // 2, (avg.green() + gold.green()) // 2, (avg.blue() + gold.blue()) // 2)
    return c.lighter(130) if c.lightness() < 110 else c


class MusicWidget(QWidget):
    """Projection screen while music (or any file without picture) plays.

    A blurred cover/artist backdrop inside the stage curtains, the cover with a glow that breathes
    with the bass, the texts beside it, and a mirrored spectrum along the bottom. Background, texts
    and cover are cached; only the glow, the bars and the progress bar are repainted (~30 fps).
    """

    BARS = 28  # per side; low tones in the middle, high tones towards the curtains

    def __init__(self, images, parent=None):
        super().__init__(parent)
        self.images = images
        self.item = None
        self.meta = {}
        self.next_item = None
        self.playing = False
        self.position_ms = 0
        self.length_ms = 0
        self._bands = []
        self._bass_target = 0.0
        self._bass = 0.0
        self._bars = [0.0] * self.BARS
        self._caps = [0.0] * self.BARS
        self._cache = None
        self._art = None              # rounded, scaled cover (or None: drawn record)
        self._glow = QColor(ACCENT)
        self._art_rect = QRectF()
        self._viz_rect = QRectF()
        self._progress_rect = QRectF()
        self._shown_second = -1
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._animate)
        images.loaded.connect(self._on_image)

    # -- state ----------------------------------------------------------------
    def _invalidate(self):
        self._cache = None
        self.update()

    def set_item(self, item):
        if item is not self.item:
            self.meta = {}
            self._bands = []
            self._bass_target = 0.0
        self.item = item
        if item:
            self.images.request(item.image_url)
            self.images.request(item.backdrop_url)
        self._invalidate()

    def set_meta(self, meta):
        if meta != self.meta:
            self.meta = dict(meta)
            if meta.get("artwork"):
                self.images.request(meta["artwork"])
            self._invalidate()

    def set_next(self, item):
        if item is not self.next_item:
            self.next_item = item
            self._invalidate()

    def set_playing(self, playing):
        if playing != self.playing:
            self.playing = playing
            self._invalidate()  # label NU TE HOREN / GEPAUZEERD
            if not playing:
                self._bands, self._bass_target = [], 0.0  # bars sink back
            self._start_timer()

    def set_progress(self, pos, length):
        changed = length != self.length_ms
        self.position_ms, self.length_ms = pos, length
        if changed:
            self._invalidate()
        elif not self._progress_rect.isEmpty() and pos // 1000 != self._shown_second:
            self.update(self._progress_rect.adjusted(-4, -4, 4, 4).toAlignedRect())

    def set_levels(self, levels, bands):
        if self.playing:
            self._bands = list(bands)
            self._bass_target = levels[0] if levels else 0.0
            self._start_timer()

    def _on_image(self, url):
        if self.item and url in (self.item.image_url, self.item.backdrop_url, self.meta.get("artwork")):
            self._invalidate()

    def _start_timer(self):
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def showEvent(self, e):
        super().showEvent(e)
        self._start_timer()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def resizeEvent(self, e):
        self._cache = None
        super().resizeEvent(e)

    # -- texts and images -----------------------------------------------------
    def _cover(self):
        for url in (self.item.image_url, self.meta.get("artwork")):
            pm = self.images.get(url) if url else None
            if pm is not None:
                return pm
        return None

    def _texts(self):
        """(title, artist, album line). Servers know their tags; for local files and audio CDs the
        tags VLC reads win over the file name."""
        item, meta = self.item, self.meta
        parts = [x for x in item.subtitle.split(" · ") if x]
        if item.kind == "radio":
            # stations announce 'Artist - Title' (ICY StreamTitle); without it the station is the title
            song = meta.get("now_playing", "").strip()
            artist, sep, title = song.partition(" - ")
            if not sep:
                artist, title = "", song
            return (title.strip() or item.title, artist.strip(),
                    item.title if title.strip() else (parts[0] if parts else ""))
        if item.source == "local":
            # without tags VLC reports the file name as title; the cleaned-up name is nicer then
            tag_title = meta.get("title", "")
            title = tag_title if tag_title and tag_title != os.path.basename(item.id) else item.title
            artist = meta.get("artist", "")
            album = meta.get("album") or (parts[0] if parts and not item.subtitle.startswith("Audio-cd") else "")
        else:
            title = item.title
            artist = parts[0] if parts else meta.get("artist", "")
            album = parts[1] if len(parts) > 1 else meta.get("album", "")
        album_line = "   ·   ".join(x for x in (album, str(item.year) if item.year else "") if x)
        return title, artist, album_line

    # -- static layer ---------------------------------------------------------
    def _render(self):
        w, h = max(1, self.width()), max(1, self.height())
        ratio = self.devicePixelRatioF()
        pm = QPixmap(int(w * ratio), int(h * ratio))
        pm.setDevicePixelRatio(ratio)
        p = QPainter(pm)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        unit = min(h, w * 0.5625)
        cover = self._cover() if self.item else None
        backdrop = self.images.get(self.item.backdrop_url) if self.item and self.item.backdrop_url else None
        radio = self.item is not None and self.item.kind == "radio"
        if backdrop is None and not radio:  # a station logo makes a poor backdrop: then the stage
            backdrop = cover

        # background: blurred backdrop, darkened, inside the stage curtains
        if backdrop is not None:
            p.fillRect(0, 0, w, h, QColor(4, 4, 5))
            p.setOpacity(0.55)
            p.drawPixmap(0, 0, _blurred(backdrop, w, h))
            p.setOpacity(1.0)
            p.fillRect(0, 0, w, h, QColor(5, 5, 6, 150))
            paint_curtains(p, w, h)
        else:
            paint_stage(p, w, h)
        self._glow = _glow_color(cover) if cover is not None else QColor(ACCENT)

        # layout: cover left, texts right, together centred; spectrum along the bottom
        art = unit * 0.5
        gap = unit * 0.08
        tw = min(unit * 1.05, w * 0.74 - art - gap)
        x0 = (w - (art + gap + tw)) / 2
        top = h * 0.40 - art / 2
        self._art_rect = QRectF(x0, top, art, art)
        self._viz_rect = QRectF(w * 0.13, h * 0.76, w * 0.74, h * 0.15)

        # soft shadow under the cover (the glow is drawn per frame)
        p.setPen(Qt.NoPen)
        for i in range(6, 0, -1):
            p.setBrush(QColor(0, 0, 0, 22))
            p.drawRoundedRect(self._art_rect.adjusted(-i * 3, -i * 2, i * 3, i * 5), 14 + i * 2, 14 + i * 2)
        self._art = self._rounded_cover(cover, art, contain=radio) if cover is not None else None

        if self.item is None:
            p.end()
            return pm
        title, artist, album_line = self._texts()
        tx = self._art_rect.right() + gap
        bottom = self._art_rect.bottom()

        p.setFont(_display_font(unit * 0.026, QFont.DemiBold, unit * 0.007))
        p.setPen(QColor(ACCENT))
        label_h = unit * 0.045
        p.drawText(QRectF(tx, top, tw, label_h), Qt.AlignLeft | Qt.AlignTop,
                   ("NU OP DE RADIO" if radio else "NU TE HOREN") if self.playing else "GEPAUZEERD")

        # footer of the text column: (next up) + progress bar + times, aligned with the cover's bottom
        times_h = unit * 0.03
        bar_h = max(3.0, unit * 0.005)
        progress_h = bar_h + unit * 0.02 + times_h
        self._progress_rect = QRectF(tx, bottom - progress_h, tw, progress_h)
        footer_top = self._progress_rect.top() - unit * 0.04
        if self.next_item is not None:
            p.setFont(_display_font(unit * 0.024, QFont.Normal, unit * 0.002))
            fm = p.fontMetrics()
            nxt = "HIERNA   " + self.next_item.title
            p.setPen(QColor(150, 146, 140))
            p.drawText(QRectF(tx, footer_top - unit * 0.04, tw, unit * 0.04), Qt.AlignLeft | Qt.AlignBottom,
                       fm.elidedText(nxt, Qt.ElideRight, int(tw)))
            footer_top -= unit * 0.06

        # title (shrinks for long titles), artist and album
        reserved = (unit * 0.06 if artist else 0) + (unit * 0.05 if album_line else 0)
        title_rect = QRectF(tx, top + label_h + unit * 0.01, tw,
                            max(unit * 0.08, footer_top - reserved - (top + label_h + unit * 0.01)))
        flags = Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap
        for size in (0.085, 0.072, 0.06, 0.05, 0.042, 0.036):
            p.setFont(_display_font(unit * size, QFont.Light))
            bounds = p.boundingRect(title_rect, flags, title)
            if bounds.height() <= title_rect.height():
                break
        p.setPen(QColor(244, 239, 228))
        p.save()
        p.setClipRect(title_rect)
        p.drawText(title_rect, flags, title)
        p.restore()
        y = min(bounds.bottom(), title_rect.bottom()) + unit * 0.02
        if artist:
            p.setFont(_display_font(unit * 0.04, QFont.Normal))
            p.setPen(QColor(222, 216, 204))
            p.drawText(QRectF(tx, y, tw, unit * 0.06), Qt.AlignLeft | Qt.AlignTop,
                       p.fontMetrics().elidedText(artist, Qt.ElideRight, int(tw)))
            y += unit * 0.06
        if album_line:
            p.setFont(_display_font(unit * 0.03, QFont.Normal))
            p.setPen(QColor(160, 156, 148))
            p.drawText(QRectF(tx, y, tw, unit * 0.05), Qt.AlignLeft | Qt.AlignTop,
                       p.fontMetrics().elidedText(album_line, Qt.ElideRight, int(tw)))
        p.end()
        return pm

    def _rounded_cover(self, cover, size, contain=False):
        """Cover filling a rounded square; with contain (station logos: small, often transparent, any
        shape) the whole logo is shown on a tile that contrasts with it."""
        ratio = self.devicePixelRatioF()
        px = max(1, int(size * ratio))
        out = QPixmap(px, px)
        out.fill(Qt.transparent)
        q = QPainter(out)
        q.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, px, px), px * 0.035, px * 0.035)
        q.setClipPath(path)
        if contain:
            q.fillRect(0, 0, px, px, QColor(28, 30, 36) if _is_light(cover) else QColor(236, 232, 224))
            inner = int(px * 0.76)
            # never blow a tiny logo up more than 3x: then it stays sharp-ish and simply smaller
            limit = max(cover.width(), cover.height()) * 3
            inner = min(inner, limit) if limit > 0 else inner
            scaled = cover.scaled(inner, inner, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            q.drawPixmap((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
        else:
            scaled = cover.scaled(px, px, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            scaled = scaled.copy((scaled.width() - px) // 2, (scaled.height() - px) // 2, px, px)
            q.drawPixmap(0, 0, scaled)
        q.end()
        out.setDevicePixelRatio(ratio)
        return out

    # -- animation ------------------------------------------------------------
    def _animate(self):
        bands = self._bands
        n = len(bands)
        moving = False
        for j in range(self.BARS):
            if n:
                x = j / (self.BARS - 1) * (n - 1)
                i = int(x)
                f = x - i
                target = bands[i] * (1 - f) + bands[min(i + 1, n - 1)] * f
                target = target ** 1.4  # calmer at low levels, punchy peaks
            else:
                target = 0.0
            cur = self._bars[j]
            cur = cur + (target - cur) * 0.55 if target > cur else cur * 0.86 + target * 0.14
            self._bars[j] = cur
            self._caps[j] = max(cur, self._caps[j] - 0.01)
            moving |= cur > 0.003 or self._caps[j] > 0.003
        b = self._bass_target if self.playing else 0.0
        self._bass += (b - self._bass) * (0.5 if b > self._bass else 0.12)
        moving |= self._bass > 0.003
        if not moving and not self.playing:
            self._timer.stop()
        self.update(self._viz_rect.adjusted(-4, -4, 4, 4).toAlignedRect())
        r = self._art_rect
        self.update(r.adjusted(-r.width() * 0.5, -r.height() * 0.5, r.width() * 0.5, r.height() * 0.5).toAlignedRect())

    # -- painting -------------------------------------------------------------
    def paintEvent(self, e):
        ratio = self.devicePixelRatioF()
        if self._cache is None or self._cache.width() != int(max(1, self.width()) * ratio):
            self._cache = self._render()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        if self.item is None:
            return
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self._paint_glow(p)
        self._paint_cover(p)
        self._paint_bars(p)
        self._paint_progress(p)

    def _paint_glow(self, p):
        r = self._art_rect
        c = r.center()
        radius = r.width() * (0.7 + 0.14 * self._bass)
        g = QRadialGradient(c, radius)
        col = QColor(self._glow)
        col.setAlpha(int(40 + 110 * self._bass))
        g.setColorAt(0.45, col)
        col2 = QColor(col)
        col2.setAlpha(0)
        g.setColorAt(1.0, col2)
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, radius, radius)

    def _paint_cover(self, p):
        r = self._art_rect
        if self._art is not None:
            p.drawPixmap(r.topLeft(), self._art)
            return
        # no cover: a record in the house colours
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(18, 18, 21))
        p.drawRoundedRect(r, r.width() * 0.035, r.width() * 0.035)
        c = r.center()
        rad = r.width() * 0.4
        p.setBrush(QColor(8, 8, 9))
        p.drawEllipse(c, rad, rad)
        p.setBrush(Qt.NoBrush)
        for k in range(1, 8):
            p.setPen(QPen(QColor(255, 255, 255, 10 + k * 2), 1))
            p.drawEllipse(c, rad * (0.45 + k * 0.07), rad * (0.45 + k * 0.07))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(c, rad * 0.3, rad * 0.3)
        p.setBrush(QColor(8, 8, 9))
        p.drawEllipse(c, rad * 0.03, rad * 0.03)

    def _paint_bars(self, p):
        v = self._viz_rect
        if v.isEmpty():
            return
        base = v.top() + v.height() * 0.78           # bars above, reflection below
        max_h = base - v.top()
        step = v.width() / 2 / self.BARS
        bw = step * 0.56
        cx = v.center().x()
        grad = QLinearGradient(0, base - max_h, 0, base)
        grad.setColorAt(0.0, QColor(246, 214, 120))
        grad.setColorAt(0.55, QColor(ACCENT))
        grad.setColorAt(1.0, QColor(138, 109, 36))
        p.setPen(Qt.NoPen)
        for j in range(self.BARS):
            bh = max(2.0, self._bars[j] * max_h)
            cap = self._caps[j] * max_h
            for x in (cx + j * step + (step - bw) / 2, cx - (j + 1) * step + (step - bw) / 2):
                p.setOpacity(1.0)
                p.setBrush(grad)
                p.drawRoundedRect(QRectF(x, base - bh, bw, bh), bw / 3, bw / 3)
                p.setBrush(QColor(244, 239, 228, 150))
                p.drawRect(QRectF(x, base - cap - 3, bw, 2))
                p.setOpacity(0.14)
                p.setBrush(QColor(ACCENT))
                p.drawRoundedRect(QRectF(x, base + 3, bw, bh * 0.28), bw / 3, bw / 3)
        p.setOpacity(1.0)

    def _paint_live(self, p, r):
        """Radio has no length: a LIVE label and since when the station is on."""
        self._shown_second = self.position_ms // 1000
        unit = min(self.height(), self.width() * 0.5625)
        p.setFont(_display_font(unit * 0.024, QFont.DemiBold, unit * 0.004))
        fm = p.fontMetrics()
        height = unit * 0.045
        dot = height * 0.32
        badge = QRectF(r.x(), r.bottom() - height, dot * 4.2 + fm.horizontalAdvance("LIVE"), height)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(badge, 4, 4)
        p.setBrush(QColor(220, 50, 47))
        p.drawEllipse(QRectF(badge.x() + dot * 1.1, badge.center().y() - dot / 2, dot, dot))
        p.setPen(QColor(236, 232, 224))
        p.drawText(badge.adjusted(dot * 3.0, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, "LIVE")
        p.setFont(_display_font(unit * 0.024, QFont.Normal, unit * 0.002))
        p.setPen(QColor(190, 186, 178))
        since = datetime.now() - timedelta(milliseconds=self.position_ms)
        p.drawText(r, Qt.AlignRight | Qt.AlignBottom, f"sinds {since:%H:%M}")

    def _paint_progress(self, p):
        r = self._progress_rect
        if not r.isEmpty() and self.item is not None and self.item.kind == "radio":
            self._paint_live(p, r)
            return
        if r.isEmpty() or self.length_ms <= 0:
            return
        self._shown_second = self.position_ms // 1000
        unit = min(self.height(), self.width() * 0.5625)
        bar_h = max(3.0, unit * 0.005)
        bar = QRectF(r.x(), r.y(), r.width(), bar_h)
        frac = max(0.0, min(1.0, self.position_ms / self.length_ms))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(bar, bar_h / 2, bar_h / 2)
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * frac, bar_h), bar_h / 2, bar_h / 2)
        hours = self.length_ms >= 3600000
        p.setFont(_display_font(unit * 0.024, QFont.Normal, unit * 0.002))
        p.setPen(QColor(190, 186, 178))
        p.drawText(r, Qt.AlignLeft | Qt.AlignBottom, fmt_time(self.position_ms, hours))
        p.drawText(r, Qt.AlignRight | Qt.AlignBottom,
                   "-" + fmt_time(max(0, self.length_ms - self.position_ms), hours))


def _is_light(pm):
    """True when the visible (non-transparent) part of a logo is mostly light."""
    img = pm.toImage().scaled(16, 16, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    total = count = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 128:
                total += c.lightness()
                count += 1
    return count > 0 and total / count > 150


class IntermissionWidget(QWidget):
    """'Pauze' screen during an intermission, and the countdown when the film resumes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.film_title = ""
        self.resume_at = None          # datetime when the break ends automatically, or None
        self.countdown_total = 0
        self.countdown_left = None     # seconds (float) while counting down, else None
        self._bg = None
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_state(self, film_title, resume_at, countdown_left, countdown_total):
        self.film_title = film_title
        self.resume_at = resume_at
        self.countdown_left = countdown_left
        self.countdown_total = countdown_total
        self.update()

    def resizeEvent(self, e):
        self._bg = None
        super().resizeEvent(e)

    def paintEvent(self, e):
        w, h = max(1, self.width()), max(1, self.height())
        ratio = self.devicePixelRatioF()
        if self._bg is None or self._bg.width() != int(w * ratio):
            self._bg = QPixmap(int(w * ratio), int(h * ratio))
            self._bg.setDevicePixelRatio(ratio)
            bp = QPainter(self._bg)
            paint_stage(bp, w, h)
            bp.end()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        unit = min(h, w * 0.5625)
        center = Qt.AlignHCenter | Qt.AlignVCenter

        if self.countdown_left is None:
            p.setFont(_display_font(unit * 0.15, QFont.Light, unit * 0.006))
            p.setPen(QColor(244, 239, 228))
            title_rect = QRectF(0, h * 0.28, w, unit * 0.22)
            p.drawText(title_rect, center, "Pauze")
            line_y = title_rect.bottom() + unit * 0.03
            lw = unit * 0.3
            g = QLinearGradient(w / 2 - lw / 2, 0, w / 2 + lw / 2, 0)
            g.setColorAt(0.0, QColor(229, 181, 58, 0))
            g.setColorAt(0.5, QColor(229, 181, 58, 230))
            g.setColorAt(1.0, QColor(229, 181, 58, 0))
            p.fillRect(QRectF(w / 2 - lw / 2, line_y, lw, max(1.5, unit * 0.0025)), g)
            y = line_y + unit * 0.04
            if self.film_title:
                p.setFont(_display_font(unit * 0.036, QFont.Normal, unit * 0.002))
                p.setPen(QColor(200, 196, 188))
                p.drawText(QRectF(0, y, w, unit * 0.06), center, f"‘{self.film_title}’ gaat zo verder")
                y += unit * 0.075
            if self.resume_at is not None:
                left = max(0, int((self.resume_at - datetime.now()).total_seconds()))
                p.setFont(_display_font(unit * 0.032, QFont.Normal, unit * 0.004))
                p.setPen(QColor(ACCENT))
                p.drawText(QRectF(0, y, w, unit * 0.06), center,
                           f"We gaan verder om {self.resume_at:%H:%M}   ·   nog {left // 60}:{left % 60:02d}")
            return

        # countdown: number inside a ring that empties second by second
        left = max(0.0, self.countdown_left)
        p.setFont(_display_font(unit * 0.036, QFont.Normal, unit * 0.006))
        p.setPen(QColor(200, 196, 188))
        p.drawText(QRectF(0, h * 0.2, w, unit * 0.06), center, "De film gaat verder over")
        radius = unit * 0.17
        cx, cy = w / 2, h * 0.55
        ring = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        pen = QPen(QColor(255, 255, 255, 35), unit * 0.012)
        p.setPen(pen)
        p.drawEllipse(ring)
        frac = left / self.countdown_total if self.countdown_total else 0
        pen.setColor(QColor(ACCENT))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(ring, 90 * 16, int(360 * 16 * frac))
        p.setFont(_display_font(unit * 0.17, QFont.Light))
        p.setPen(QColor(244, 239, 228))
        p.drawText(ring, center, str(int(left + 0.999)) if left > 0 else "0")


class CinemaScreen(QWidget):
    """Full-screen window on an audience screen. role: 'video' (projection), 'aux' ('Nu te zien' during
    playback) or 'spare' (placeholder only)."""

    def __init__(self, app, role, topmost=False):
        flags = Qt.FramelessWindowHint | Qt.Window
        if topmost:
            # Nothing (notifications, other apps) may cover the audience screens; this also keeps
            # the live preview, which copies the projection screen, showing the film.
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.app = app
        self.role = role
        self.setWindowTitle("CinemaViewer – " + ("projectie" if role == "video" else "scherm"))
        self.setStyleSheet("background: black;")
        self.setCursor(Qt.BlankCursor)
        cfg = app.cfg
        self.stack = QStackedLayout(self)
        self.placeholder = PlaceholderWidget(cfg["placeholder_title"], cfg["placeholder_subtitle"])
        self.stack.addWidget(self.placeholder)
        self.intermission = IntermissionWidget()
        self.stack.addWidget(self.intermission)
        self.video = None
        self.music = None
        self.browser_host = None
        self.slides = None
        self.now_showing = None
        if role == "video":
            self.video = QFrame()
            self.video.setStyleSheet("background: black;")
            self.video.setAttribute(Qt.WA_NativeWindow, True)
            self.video.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
            self.stack.addWidget(self.video)
            self.music = MusicWidget(app.images)  # music has no picture: cover, texts and spectrum
            self.stack.addWidget(self.music)
            self.browser_host = QWidget()  # the operator's browser view moves in here while streamed
            self.browser_host.setCursor(Qt.ArrowCursor)
            QVBoxLayout(self.browser_host).setContentsMargins(0, 0, 0, 0)
            self.stack.addWidget(self.browser_host)
            self.slides = SlideView()
            self.stack.addWidget(self.slides)
        else:
            self.now_showing = NowShowingWidget(app.images)
            self.stack.addWidget(self.now_showing)

    def video_handle(self):
        return int(self.video.winId())

    def place_on(self, screen, windowed=False):
        self.placed = (screen, windowed)  # to put it back after stepping aside for a slide show
        place_window(self, screen, windowed)

    def show_placeholder(self):
        self.stack.setCurrentWidget(self.placeholder)

    def show_intermission(self, film_title, resume_at, countdown_left, countdown_total):
        self.intermission.set_state(film_title, resume_at, countdown_left, countdown_total)
        self.stack.setCurrentWidget(self.intermission)

    def show_video(self):
        if self.video is not None:
            self.stack.setCurrentWidget(self.video)

    def show_browser(self):
        if self.video is not None:
            self.stack.setCurrentWidget(self.browser_host)

    def show_slides(self, presentation):
        if self.video is not None:
            self.slides.set_presentation(presentation)
            self.stack.setCurrentWidget(self.slides)

    def show_music(self, item, next_item=None):
        if self.music is not None:
            self.music.set_item(item)
            self.music.set_next(next_item)
            self.stack.setCurrentWidget(self.music)

    def set_music_meta(self, meta):
        if self.music is not None:
            self.music.set_meta(meta)

    def set_music_playing(self, playing):
        if self.music is not None:
            self.music.set_playing(playing)

    def set_levels(self, levels, bands):
        if self.music is not None and self.stack.currentWidget() is self.music:
            self.music.set_levels(levels, bands)

    def show_now_showing(self, item):
        if self.now_showing is not None:
            self.now_showing.set_item(item)
            self.stack.setCurrentWidget(self.now_showing)

    def set_progress(self, pos, length):
        if self.now_showing is not None:
            self.now_showing.set_progress(pos, length)
        if self.music is not None:
            self.music.set_progress(pos, length)

    def set_preview_frame(self, image):
        if self.now_showing is not None:
            self.now_showing.set_preview_frame(image)

    def mouseDoubleClickEvent(self, e):
        self.app.toggle_front()

    def release(self):
        """Close for real (screen re-layout or exit)."""
        self._released = True
        self.close()

    def closeEvent(self, e):
        # Alt+F4 on an audience screen must not close it.
        if self.app.closing or getattr(self, "_released", False):
            e.accept()
        else:
            e.ignore()


class IdentifyOverlay(QWidget):
    """Big screen number used by the settings dialog to identify displays."""

    def __init__(self, screen, number, description):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet("background: rgba(10,10,12,235);")
        lay = QVBoxLayout(self)
        num = QLabel(str(number))
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(f"color: {ACCENT}; font-size: 220px; font-weight: 300;")
        desc = QLabel(description)
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("color: #ECE8E0; font-size: 28px;")
        lay.addStretch()
        lay.addWidget(num)
        lay.addWidget(desc)
        lay.addStretch()
        geo = screen.geometry()
        self.setGeometry(geo.x() + geo.width() // 2 - 300, geo.y() + geo.height() // 2 - 220, 600, 440)
        self.show()
        QTimer.singleShot(3500, self.close)
