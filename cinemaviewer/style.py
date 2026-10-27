"""Colours, icon glyphs and the global stylesheet."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap

ACCENT = "#E5B53A"
ACCENT_DIM = "#8A6D24"
BG = "#0B0C0F"
PANEL = "#14161B"
PANEL2 = "#1C1F26"
BORDER = "#2A2E37"
TEXT = "#ECE8E0"
MUTED = "#8E939D"
DANGER = "#D9534F"

# Segoe Fluent Icons / Segoe MDL2 Assets code points
G_PLAY = ""
G_PAUSE = ""
G_STOP = ""
G_VOLUME = ""
G_MUTE = ""
G_SETTINGS = ""
G_BACK = ""
G_POWER = ""
G_FOLDER = ""
G_USB = ""
G_DISC = ""
G_MOVIE = ""
G_TV = ""
G_SEARCH = ""
G_PREV = ""
G_NEXT = ""
G_REWIND = ""
G_FORWARD = ""
G_CC = ""
G_AUDIO = ""
G_REFRESH = ""
G_SERVER = ""
G_CHECK = ""
G_UP = ""
G_SHUFFLE = ""
G_DOWN = ""
G_LEFT = ""
G_RIGHT = ""
G_MENU = ""
G_LIBRARY = ""
G_CAST = ""
G_MUSIC = ""
G_DOWNLOAD = ""
G_DELETE = ""

if sys.platform != "win32":
    # The Segoe icon fonts only exist on Windows; elsewhere the same icons come from Google's
    # Material Icons (Apache 2.0; Debian/Ubuntu package fonts-material-design-icons-iconfont).
    (G_PLAY, G_PAUSE, G_STOP, G_VOLUME, G_MUTE, G_SETTINGS, G_BACK, G_POWER, G_FOLDER, G_USB, G_DISC,
     G_MOVIE, G_TV, G_SEARCH, G_PREV, G_NEXT, G_REWIND, G_FORWARD, G_CC, G_AUDIO, G_REFRESH, G_SERVER,
     G_CHECK, G_UP, G_SHUFFLE, G_DOWN, G_LEFT, G_RIGHT, G_MENU, G_LIBRARY, G_CAST, G_MUSIC, G_DOWNLOAD,
     G_DELETE) = (
        "", "", "", "", "", "", "", "", "", "",
        "", "", "", "", "", "", "", "", "", "",
        "", "", "", "", "", "", "", "", "", "",
        "", "", "", "")


def icon_family():
    families = set(QFontDatabase.families())
    for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets", "Material Icons"):
        if name in families:
            return name
    return "Segoe UI Symbol"


def icon_font(px):
    f = QFont(icon_family())
    f.setPixelSize(px)
    return f


def glyph_pixmap(glyph, color=TEXT, px=20, ratio=2.0):
    size = int(px * ratio)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setFont(icon_font(int(px * ratio * 0.8)))
    p.setPen(QColor(color))
    p.drawText(pm.rect(), Qt.AlignCenter, glyph)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def glyph_icon(glyph, color=TEXT, px=20, active_color=None):
    icon = QIcon()
    icon.addPixmap(glyph_pixmap(glyph, color, px), QIcon.Normal)
    icon.addPixmap(glyph_pixmap(glyph, MUTED if color == TEXT else color, px), QIcon.Disabled)
    if active_color:
        icon.addPixmap(glyph_pixmap(glyph, active_color, px), QIcon.Selected)
    return icon


KIND_GLYPHS = {
    "library": G_LIBRARY, "folder": G_FOLDER, "usb": G_USB, "dvd": G_DISC, "bluray": G_DISC,
    "audiocd": G_DISC, "empty": G_DISC, "series": G_TV, "season": G_TV, "episode": G_TV,
    "collection": G_LIBRARY, "track": G_MUSIC, "album": G_MUSIC, "artist": G_MUSIC,
    "playlist": G_LIBRARY, "playlists": G_LIBRARY,
}


def kind_glyph(kind):
    return KIND_GLYPHS.get(kind, G_MOVIE)


QSS = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI";
    font-size: 14px;
    color: {TEXT};
    outline: none;
}}
#ControlRoot, QDialog {{ background: {BG}; }}
QDialog#Frameless, QFileDialog#Frameless {{ background: {BG}; border: 1px solid #3A3F4A; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 8px; }}

#TopBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
#Logo {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 22px; font-weight: 600; color: {TEXT}; }}
#LogoAccent {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 22px; font-weight: 300; color: {ACCENT}; }}
#Crumb {{ font-size: 16px; color: {MUTED}; }}
#Clock {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 22px; font-weight: 300; color: {TEXT}; padding: 0 8px; }}

#Sidebar {{ background: {PANEL}; border: none; border-right: 1px solid {BORDER}; padding: 8px 0; }}
#Sidebar::item {{ padding: 9px 14px; border-radius: 8px; margin: 1px 8px; }}
#Sidebar::item:hover {{ background: {PANEL2}; }}
#Sidebar::item:selected {{ background: #2A2416; color: {ACCENT}; }}

#Grid {{ background: {BG}; border: none; }}
#RightPanel {{ background: {PANEL}; border-left: 1px solid {BORDER}; }}
#Transport {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}

#H1 {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 24px; font-weight: 600; }}
#H2 {{ font-size: 15px; color: {MUTED}; }}
#Meta {{ font-size: 13px; color: {MUTED}; }}
#Body {{ font-size: 14px; color: #CFCBC3; }}
#Section {{ font-size: 11px; font-weight: 700; color: {ACCENT}; letter-spacing: 2px; }}
#Chip {{ font-size: 11px; font-weight: 700; letter-spacing: 1px; color: {BG}; background: {ACCENT};
         border-radius: 4px; padding: 3px 8px; }}
#ChipIdle {{ font-size: 11px; font-weight: 700; letter-spacing: 1px; color: {MUTED}; background: {PANEL2};
             border-radius: 4px; padding: 3px 8px; }}
#NowTitle {{ font-size: 16px; font-weight: 600; }}
#Time {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 15px; color: {MUTED}; min-width: 70px; }}
#EndTime {{ font-size: 13px; color: {MUTED}; }}
#Empty {{ font-size: 18px; color: {MUTED}; }}
#Toast {{ background: #3A1F1F; color: #FFDADA; border: 1px solid #6B3434; border-radius: 8px; padding: 10px 16px; }}
#ToastInfo {{ background: #2A2416; color: {TEXT}; border: 1px solid {ACCENT_DIM}; border-radius: 8px; padding: 10px 16px; }}

QPushButton {{
    background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 16px;
}}
QPushButton:hover {{ background: #252932; border-color: #3A3F4A; }}
QPushButton:pressed {{ background: #2E333D; }}
QPushButton:disabled {{ color: #5A5F68; background: {PANEL}; }}
QPushButton#Primary {{ background: {ACCENT}; color: #16130B; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: #F0C450; }}
QPushButton#Icon, QToolButton#Icon {{ background: transparent; border: none; border-radius: 22px; padding: 6px; }}
QPushButton#Icon:hover, QToolButton#Icon:hover {{ background: {PANEL2}; }}
QPushButton#Icon:disabled {{ background: transparent; }}
QPushButton#Big {{ background: {ACCENT}; border: none; border-radius: 30px; }}
QPushButton#Big:hover {{ background: #F0C450; }}
QPushButton#Big:disabled {{ background: {ACCENT_DIM}; }}
QPushButton#Tab {{ background: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0;
                   padding: 12px 6px; color: {MUTED}; font-weight: 600; }}
QPushButton#Tab:checked {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}

QLineEdit, QComboBox, QSpinBox {{
    background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 10px;
    selection-background-color: {ACCENT}; selection-color: #000;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT_DIM}; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; border: 1px solid {BORDER}; selection-background-color: #2A2416; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}

QSlider::groove:horizontal {{ height: 6px; background: #2C3039; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}
QSlider:disabled::sub-page:horizontal {{ background: #3A3F4A; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #333844; border-radius: 4px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: #454B58; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QMenu {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 6px; border-radius: 8px; }}
QMenu::item {{ padding: 8px 28px 8px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: #2A2416; color: {ACCENT}; }}
QMenu::item:disabled {{ color: #5A5F68; }}
QMenu::indicator {{ width: 0; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 4px; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; background: {PANEL}; }}
QTabBar::tab {{ background: transparent; padding: 10px 18px; color: {MUTED}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QListWidget#Plain {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; }}
QListWidget#Plain::item {{ padding: 10px 12px; border-bottom: 1px solid {BORDER}; border-radius: 6px; }}
QListWidget#Plain::item:selected {{ background: #2A2416; color: {ACCENT}; }}
QTableWidget {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px;
                gridline-color: {BORDER}; selection-background-color: #2A2416; selection-color: {ACCENT}; }}
QDialog QListWidget {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px;
                       padding: 4px; }}
QDialog QListWidget::item {{ padding: 4px 6px; border-radius: 5px; }}
QDialog QListWidget::item:selected {{ background: #2A2416; color: {ACCENT}; }}
QDialog QListWidget::item:hover:!selected {{ background: #232731; }}
QHeaderView {{ background: {PANEL}; border: none; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
                        padding: 6px 8px; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding: 14px 10px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {ACCENT}; }}
"""
