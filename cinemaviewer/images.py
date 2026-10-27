"""Asynchronous poster/backdrop loader with an in-memory LRU cache."""

from collections import OrderedDict

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .net import USER_AGENT
from .workers import run_async

MAX_HEIGHT = 1080


def _load_local(path):
    img = QImage(path)
    if not img.isNull() and img.height() > MAX_HEIGHT:
        img = img.scaledToHeight(MAX_HEIGHT, Qt.SmoothTransformation)
    return img


class ImageCache(QObject):
    loaded = Signal(str)

    def __init__(self, limit=500):
        super().__init__()
        self._nam = QNetworkAccessManager(self)
        self._cache = OrderedDict()
        self._pending = {}
        self._failed = set()
        self._limit = limit

    def get(self, url):
        pix = self._cache.get(url)
        if pix is not None:
            self._cache.move_to_end(url)
        return pix

    def request(self, url, callback=None):
        if not url or url in self._failed:
            return
        pix = self.get(url)
        if pix is not None:
            if callback:
                callback(url, pix)
            return
        if url in self._pending:
            if callback:
                self._pending[url].append(callback)
            return
        self._pending[url] = [callback] if callback else []
        if url.startswith(("http://", "https://")):
            req = QNetworkRequest(QUrl(url))
            req.setHeader(QNetworkRequest.UserAgentHeader, USER_AGENT)
            reply = self._nam.get(req)
            reply.finished.connect(lambda r=reply, u=url: self._on_reply(u, r))
        else:
            run_async(_load_local, lambda img, u=url: self._finish(u, img),
                      lambda _e, u=url: self._finish(u, QImage()), url)

    def _on_reply(self, url, reply):
        img = QImage()
        if reply.error() == QNetworkReply.NoError:
            img.loadFromData(reply.readAll())
            if not img.isNull() and img.height() > MAX_HEIGHT:
                img = img.scaledToHeight(MAX_HEIGHT, Qt.SmoothTransformation)
        reply.deleteLater()
        self._finish(url, img)

    def _finish(self, url, img):
        callbacks = self._pending.pop(url, [])
        if img.isNull():
            self._failed.add(url)
            return
        pix = QPixmap.fromImage(img)
        self._cache[url] = pix
        while len(self._cache) > self._limit:
            self._cache.popitem(last=False)
        self.loaded.emit(url)
        for cb in callbacks:
            cb(url, pix)
