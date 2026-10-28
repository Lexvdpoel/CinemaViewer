import argparse
import os
import sys

from PySide6.QtCore import QDir, QLibraryInfo, QLocale, QLockFile, Qt, QTimer, QTranslator
from PySide6.QtGui import QColor, QGuiApplication, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from .dialogs import message
from .style import ACCENT, BG, PANEL, PANEL2, QSS, TEXT
from .shell import RESTART_ARG
from .system import AUTOSTART_ARG, release_startup_mouse


def _dark_palette():
    pal = QPalette()
    for role, color in (
        (QPalette.Window, BG), (QPalette.Base, PANEL2), (QPalette.AlternateBase, PANEL),
        (QPalette.Button, PANEL2), (QPalette.Text, TEXT), (QPalette.WindowText, TEXT),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#000000"),
        (QPalette.ToolTipBase, PANEL2), (QPalette.ToolTipText, TEXT),
    ):
        pal.setColor(role, QColor(color))
    return pal


def _splash(qapp):
    """Small start screen: loading VLC and the screens can take a while on a cold PC."""
    from .screens import render_placeholder
    ratio = qapp.primaryScreen().devicePixelRatio() if qapp.primaryScreen() else 1.0
    pm = QPixmap.fromImage(render_placeholder(640, 360, "CinemaViewer", "wordt gestart…", ratio))
    splash = QSplashScreen(pm, Qt.WindowStaysOnTopHint)
    splash.show()
    qapp.processEvents()
    return splash


def main():
    parser = argparse.ArgumentParser(prog="cinemaviewer")
    parser.add_argument("--windowed", action="store_true", help="vensters i.p.v. volledig scherm (testen)")
    parser.add_argument("--herstel", action="store_true",
                        help="Ctrl+Alt+Del-beperkingen opheffen (na een crash) en stoppen")
    parser.add_argument(AUTOSTART_ARG, action="store_true",
                        help="gestart door Windows bij aanmelden (muis geblokkeerd tot CinemaViewer klaar is)")
    args, _ = parser.parse_known_args()

    if args.herstel:
        from .config import Config
        from .system import remove_kiosk_policies
        cfg = Config.load()
        remove_kiosk_policies(cfg["kiosk_applied"])
        cfg["kiosk_applied"] = []
        cfg.save()
        print("Ctrl+Alt+Del-beperkingen van CinemaViewer opgeheven.")
        return 0

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # The browser (Qt WebEngine) moves between the operator and projection windows: they must share GL contexts.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401 -- must be loaded before the QApplication exists
    except ImportError:
        pass  # no browser on this installation
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("CinemaViewer")
    qapp.setStyle("Fusion")
    qapp.setPalette(_dark_palette())
    qapp.setStyleSheet(QSS)
    qapp.setQuitOnLastWindowClosed(False)
    # Dutch texts in Qt's own dialogs (folder picker, standard buttons)
    translator = QTranslator(qapp)
    if translator.load(QLocale("nl_NL"), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        qapp.installTranslator(translator)

    # Only one CinemaViewer at a time (e.g. autostart plus a manual start).
    lock = QLockFile(os.path.join(QDir.tempPath(), "cinemaviewer.lock"))
    # After an update the previous CinemaViewer may still be closing: give it time to let go.
    if not lock.tryLock(20000 if RESTART_ARG in sys.argv else 100):
        release_startup_mouse()
        message(None, "CinemaViewer", "CinemaViewer is al gestart. Zie je hem nog niet, dan is hij nog bezig met "
                "opstarten; wacht even, hij verschijnt vanzelf.")
        return 0

    try:
        from .app import CinemaApp
    except Exception as e:  # noqa: BLE001 - most likely libVLC missing
        release_startup_mouse()
        message(None, "VLC niet gevonden",
                "De afspeelmodule (VLC) kan niet worden geladen.\n"
                "Verwijder runtime\\ready.txt en start CinemaViewer opnieuw met run.bat; de meegeleverde "
                "onderdelen worden dan opnieuw geïnstalleerd.\n\n"
                f"Details: {e}")
        return 1

    splash = _splash(qapp)
    try:
        app = CinemaApp(qapp, windowed=args.windowed)
        app.start()
    finally:
        # Runs once the event loop has shown the windows; on a failed start the process ends anyway.
        QTimer.singleShot(0, release_startup_mouse)
        QTimer.singleShot(0, splash.close)
    return qapp.exec()
