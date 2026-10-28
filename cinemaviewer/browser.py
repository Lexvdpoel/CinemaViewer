"""Browser: a web page in the middle of the operator window that can be streamed to the projection.

The page is laid out at the projection's resolution and shown scaled down (zoom factor). 'Naar beamer'
moves the view itself onto the projection window at full size, so video in the page plays at full quality;
the operator window then shows a live copy of it (ScreenMirror) and passes mouse, wheel and keys on to it.
"""

from PySide6.QtCore import QEvent, QPointF, QRect, QSize, Qt, QUrl
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .config import DATA_DIR
from .mirror import ScreenMirror
from .style import G_BACK, G_CAST, G_FORWARD, G_REFRESH, G_STOP, G_WEB, MUTED, glyph_icon

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:  # Windows installation without the browser parts (see README)
    QWebEngineView = None

ALLOWED_SCHEMES = {"http", "https", "about", "data", "blob"}  # no file://, mailto:, external programs
EDIT_KEYS = {Qt.Key_A, Qt.Key_C, Qt.Key_V, Qt.Key_X, Qt.Key_Z, Qt.Key_Y, Qt.Key_F}
APP_KEYS = {Qt.Key_MediaTogglePlayPause, Qt.Key_MediaPlay, Qt.Key_MediaPause, Qt.Key_MediaStop,
            Qt.Key_VolumeUp, Qt.Key_VolumeDown, Qt.Key_VolumeMute}


def available():
    return QWebEngineView is not None


def to_url(text):
    """What was typed in the address bar: an address, or else a search."""
    text = text.strip()
    if not text:
        return None
    if "://" in text or text.startswith("about:"):
        return QUrl(text)
    if " " not in text and ("." in text or text.startswith("localhost")):
        return QUrl.fromUserInput(text)
    return QUrl("https://www.google.com/search?q=" + QUrl.toPercentEncoding(text).data().decode())


if QWebEngineView is not None:
    class _Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, _type, _main_frame):
            return url.scheme() in ALLOWED_SCHEMES

    class _View(QWebEngineView):
        def createWindow(self, _type):
            return self  # links for a new window or tab open in the same view


class MirrorView(QWidget):
    """Live copy of the streamed browser; clicks, scrolling and typing go through to the real view."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.frame = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.ArrowCursor)

    def set_frame(self, image):
        self.frame = image
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self.frame is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(self.rect(), self.frame)

    def _target(self):
        view = self.panel.view
        return view.focusProxy() or view

    def _map(self, pos):
        view = self.panel.view
        return QPointF(pos.x() * view.width() / max(1, self.width()), pos.y() * view.height() / max(1, self.height()))

    def _mouse(self, e):
        target = self._target()
        local = self._map(e.position())
        glob = QPointF(target.mapToGlobal(local.toPoint()))
        QApplication.sendEvent(target, QMouseEvent(e.type(), local, glob, e.button(), e.buttons(), e.modifiers()))

    mousePressEvent = mouseReleaseEvent = mouseMoveEvent = mouseDoubleClickEvent = _mouse

    def wheelEvent(self, e):
        target = self._target()
        local = self._map(e.position())
        QApplication.sendEvent(target, QWheelEvent(local, QPointF(target.mapToGlobal(local.toPoint())),
                                                   e.pixelDelta(), e.angleDelta(), e.buttons(), e.modifiers(),
                                                   e.phase(), e.inverted()))

    def _key(self, e):
        QApplication.sendEvent(self._target(), QKeyEvent(e.type(), e.key(), e.modifiers(), e.nativeScanCode(),
                                                         e.nativeVirtualKey(), e.nativeModifiers(), e.text(),
                                                         e.isAutoRepeat(), e.count()))

    keyPressEvent = keyReleaseEvent = _key

    def focusNextPrevChild(self, _next):
        return False  # Tab goes to the page (form fields), not to the next button


class BrowserPanel(QWidget):
    """Middle of the operator window: address bar, the page (scaled) and the 'Naar beamer' button."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.view = None
        self.mirror = None
        self._mirror_window = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(6)

        def tool(glyph, tip, fn):
            b = QPushButton()
            b.setIcon(glyph_icon(glyph, px=18))
            b.setToolTip(tip)
            b.setFixedSize(40, 38)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            bar.addWidget(b)
            return b

        self.btn_back = tool(G_BACK, "Terug", lambda: self.view and self.view.back())
        self.btn_forward = tool(G_FORWARD, "Vooruit", lambda: self.view and self.view.forward())
        tool(G_REFRESH, "Opnieuw laden", lambda: self.view and self.view.reload())
        tool(G_WEB, "Startpagina", self.go_home)
        self.address = QLineEdit()
        self.address.setPlaceholderText("Webadres of zoekterm")
        self.address.setMinimumHeight(38)
        self.address.returnPressed.connect(self._on_address)
        bar.addWidget(self.address, 1)
        self.btn_stream = QPushButton()
        self.btn_stream.setObjectName("Primary")
        self.btn_stream.setMinimumHeight(38)
        self.btn_stream.setFocusPolicy(Qt.NoFocus)
        self.btn_stream.setCursor(Qt.PointingHandCursor)
        self.btn_stream.clicked.connect(self._toggle_stream)
        bar.addWidget(self.btn_stream)
        lay.addLayout(bar)
        self.stage = QWidget()
        self.stage.setObjectName("BrowserStage")
        self.stage.setStyleSheet("#BrowserStage { background: black; border-radius: 8px; }")
        lay.addWidget(self.stage, 1)
        self.mirror_view = MirrorView(self)
        self.mirror_view.setParent(self.stage)
        self.mirror_view.hide()
        self.note = QLabel(self.stage)  # text on the stage instead of the page
        self.note.setAlignment(Qt.AlignCenter)
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {MUTED}; font-size: 16px;")
        self.note.hide()
        # Filters only on the browser's own widgets: an application-wide Python event filter also sees
        # objects that are being destroyed, which can crash PySide.
        self.stage.installEventFilter(self)
        self.mirror_view.installEventFilter(self)
        self._set_stream_button(False)

    # -- the web view -----------------------------------------------------------
    def ensure_view(self):
        if self.view is not None or not available():
            return self.view
        profile = QWebEngineProfile("CinemaViewer", QApplication.instance())  # cookies/logins are kept
        storage = DATA_DIR / "browser"
        profile.setPersistentStoragePath(str(storage))
        profile.setCachePath(str(storage / "cache"))
        profile.downloadRequested.connect(
            lambda _d: self.app.control.toast("Downloaden kan niet vanuit de browser van CinemaViewer."))
        self.view = _View(self.stage)
        self.view.installEventFilter(self)  # its child that takes the keys comes later (ChildAdded)
        page = _Page(profile, self.view)
        page.settings().setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
        page.fullScreenRequested.connect(lambda request: request.accept())  # the view is the 'screen'
        self.view.setPage(page)
        for child in self.view.findChildren(QWidget):
            child.installEventFilter(self)
        self.view.urlChanged.connect(self._on_url)
        self.view.loadFinished.connect(lambda _ok: self._apply_zoom())
        self.view.show()
        self.go_home()
        self._layout()
        return self.view

    def go_home(self):
        if self.ensure_view() is not None:
            self.view.load(to_url(self.app.cfg["browser"]["home"]) or QUrl("about:blank"))

    def _on_address(self):
        url = to_url(self.address.text())
        if url is not None and self.ensure_view() is not None:
            self.view.load(url)
            if self.streaming:
                self.mirror_view.setFocus()
            else:
                self.view.setFocus()

    def _on_url(self, url):
        self.address.setText(url.toString())
        self.address.setCursorPosition(0)
        history = self.view.history()
        self.btn_back.setEnabled(history.canGoBack())
        self.btn_forward.setEnabled(history.canGoForward())
        self._apply_zoom()

    # -- layout: the page as it will look on the projection, scaled to fit ---------------------------
    @property
    def streaming(self):
        return self.app.browser_streaming

    def _beamer_size(self):
        w = self.app.video_window
        return w.size() if w is not None and w.width() > 0 else QSize(1920, 1080)

    def _fit(self):
        size = self._beamer_size()
        avail = self.stage.rect()
        scale = min(avail.width() / size.width(), avail.height() / size.height())
        w, h = int(size.width() * scale), int(size.height() * scale)
        return QRect((avail.width() - w) // 2, (avail.height() - h) // 2, w, h)

    def _apply_zoom(self):
        if self.view is None:
            return
        if self.streaming:
            self.view.setZoomFactor(1.0)
        else:
            self.view.setZoomFactor(max(0.25, self.view.width() / max(1, self._beamer_size().width())))

    def _layout(self):
        rect = self._fit()
        self.mirror_view.setGeometry(rect)
        self.note.setGeometry(self.stage.rect())
        if self.view is not None and not self.streaming:
            self.view.setGeometry(rect)
            self._apply_zoom()

    def eventFilter(self, obj, e):
        kind = e.type()
        if kind == QEvent.Resize and obj is self.stage:
            self._layout()
        elif kind == QEvent.ChildAdded and obj is self.view and e.child().isWidgetType():
            e.child().installEventFilter(self)
        elif kind == QEvent.ShortcutOverride and self._takes_key(e):
            e.accept()  # the page gets the key instead of a CinemaViewer shortcut (space, M, J, ...)
            return True
        return False

    def _takes_key(self, e):
        key, mods = e.key(), e.modifiers() & ~Qt.KeypadModifier
        if key in APP_KEYS or (key in (Qt.Key_Tab, Qt.Key_Backtab) and self.app.single_screen):
            return False
        if mods & Qt.ControlModifier:
            return key in EDIT_KEYS
        return True

    def showEvent(self, e):
        super().showEvent(e)
        if self.ensure_view() is None:
            self.note.setText("De browser is op deze installatie niet beschikbaar.\n"
                              "Windows: zet de nieuwe vendor-map van de release neer en start run.bat opnieuw.")
            self.note.show()
            self.btn_stream.setEnabled(False)
            return
        self._layout()

    # -- streaming to the projection ---------------------------------------------
    def _toggle_stream(self):
        if self.streaming:
            self.app.stop_browser_stream()
        elif self.ensure_view() is not None:
            self.app.start_browser_stream()

    def _set_stream_button(self, on):
        self.btn_stream.setText("  Stop beamer" if on else "  Naar beamer")
        self.btn_stream.setIcon(glyph_icon(G_STOP if on else G_CAST, "#16130B", 16))
        self.btn_stream.setToolTip("Browser niet meer op de beamer tonen" if on
                                   else "De browser op de beamer tonen")

    def show_on(self, window):
        """Called by the app when the projection shows the browser (also after a screen re-layout)."""
        host = window.browser_host
        if self.view.parent() is not host:
            self.view.setParent(host)
            host.layout().addWidget(self.view)
            self.view.show()
        self._apply_zoom()
        self.view.setFocus()
        target = self.view.focusProxy() or self.view
        QApplication.sendEvent(target, QFocusEvent(QEvent.FocusIn, Qt.OtherFocusReason))  # caret in form fields
        self._set_stream_button(True)
        if self.app.single_screen:
            self._stop_mirror()
            self.mirror_view.hide()
            self.note.setText("De browser staat schermvullend op het scherm.\nDruk Tab om te wisselen.")
            self.note.show()
            return
        self.note.hide()
        self.mirror_view.show()
        self.mirror_view.raise_()
        self.mirror_view.setFocus()
        if self.mirror is None or self._mirror_window is not window:
            self._stop_mirror()
            self.mirror = ScreenMirror(window.winId(), width=1280, fps=20)
            self.mirror.frame.connect(self.mirror_view.set_frame)
            self.mirror.start()
            self._mirror_window = window

    def reclaim(self, pause_media=True):
        """Take the view back from the projection window (stream stopped, or the window is replaced)."""
        self._stop_mirror()
        self.mirror_view.hide()
        self.mirror_view.frame = None
        self.note.hide()
        self._set_stream_button(False)
        if self.view is None:
            return
        if self.view.parent() is not self.stage:
            self.view.setParent(self.stage)
            self.view.show()
        if pause_media:
            self.pause_media()
        self._layout()

    def pause_media(self):
        if self.view is not None:
            self.view.page().runJavaScript("document.querySelectorAll('video, audio').forEach(m => m.pause());")

    def _stop_mirror(self):
        if self.mirror is not None:
            self.mirror.frame.disconnect(self.mirror_view.set_frame)
            self.mirror.stop()
            self.mirror = None
            self._mirror_window = None
