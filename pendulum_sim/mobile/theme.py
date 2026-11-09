"""Dark theme for the mobile app: palette, font sizes, and a small style sheet.

Applied once at start-up (desktop untouched). Colours are chosen for contrast on
an LCD in daylight: body text #E8EAED on #15181C is 14.8:1, secondary text #A8AEB6
is 8:1, the accent #4FA3FF on the background 6.9:1 (WCAG AA needs 4.5:1).

Sizes are in logical pixels, which Qt maps to Android dp (Xiaomi Pad 6: 1 dp = 2 px):
body text 16, secondary 14, touch targets ≥ 48.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette

BG = "#15181C"          # window background
SURFACE = "#1E2227"     # cards, bars
SURFACE_2 = "#272C33"   # buttons, inputs
OUTLINE = "#3A4048"
TEXT = "#E8EAED"
TEXT_2 = "#A8AEB6"
DISABLED = "#5F6368"
ACCENT = "#4FA3FF"
ACCENT_DARK = "#1F4E7A"
ON_ACCENT = "#0B1420"
DANGER = "#FF6B6B"

BODY_PX = 16
SMALL_PX = 14
TITLE_PX = 20
TOUCH_PX = 48


def palette() -> QPalette:
    pal = QPalette()
    roles = QPalette.ColorRole
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, colour in (
            (roles.Window, BG), (roles.WindowText, TEXT), (roles.Base, SURFACE),
            (roles.AlternateBase, SURFACE_2), (roles.Text, TEXT), (roles.Button, SURFACE_2),
            (roles.ButtonText, TEXT), (roles.BrightText, "#FFFFFF"), (roles.Highlight, ACCENT),
            (roles.HighlightedText, ON_ACCENT), (roles.ToolTipBase, SURFACE_2), (roles.ToolTipText, TEXT),
            (roles.PlaceholderText, TEXT_2), (roles.Link, ACCENT), (roles.Light, "#3A4048"),
            (roles.Midlight, "#30353C"), (roles.Mid, "#2A2F35"), (roles.Dark, "#0F1114"),
            (roles.Shadow, "#000000"),
        ):
            pal.setColor(group, role, QColor(colour))
    for role in (roles.WindowText, roles.Text, roles.ButtonText, roles.PlaceholderText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED))
    for role, colour in ((roles.Window, BG), (roles.Base, SURFACE), (roles.Button, SURFACE_2)):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(colour))
    return pal


STYLE_SHEET = f"""
QWidget {{ font-size: {BODY_PX}px; }}
QLabel[role="secondary"] {{ color: {TEXT_2}; font-size: {SMALL_PX}px; }}
QLabel[role="title"] {{ font-size: {TITLE_PX}px; font-weight: 600; }}
QLabel[role="value"] {{ font-weight: 600; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {OUTLINE}; border-radius: 12px; }}
QWidget#navbar {{ background: {SURFACE}; }}
QWidget#actionbar {{ background: {SURFACE}; border-top: 1px solid {OUTLINE}; }}
QToolButton[nav="true"] {{ border: none; border-radius: 14px; padding: 4px 6px; color: {TEXT_2};
    font-size: 13px; }}
QToolButton[nav="true"]:checked {{ background: {ACCENT_DARK}; color: {TEXT}; }}
QPushButton, QToolButton {{ min-height: {TOUCH_PX}px; border-radius: 10px; padding: 0 14px;
    background: {SURFACE_2}; border: 1px solid {OUTLINE}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {OUTLINE}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {DISABLED}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: {ON_ACCENT}; border: none; font-weight: 600; }}
QPushButton[primary="true"]:disabled {{ background: {ACCENT_DARK}; color: {TEXT_2}; }}
QPushButton[chip="true"] {{ min-height: 40px; border-radius: 20px; padding: 0 16px; }}
QPushButton[chip="true"]:checked {{ background: {ACCENT_DARK}; border: 1px solid {ACCENT}; color: {TEXT}; }}
QToolButton[flat="true"] {{ background: transparent; border: none; }}
QLineEdit {{ min-height: {TOUCH_PX}px; border-radius: 10px; padding: 0 12px; background: {SURFACE_2};
    border: 1px solid {ACCENT}; selection-background-color: {ACCENT}; }}
QComboBox {{ min-height: {TOUCH_PX}px; border-radius: 10px; padding: 0 12px; background: {SURFACE_2};
    border: 1px solid {OUTLINE}; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; selection-background-color: {ACCENT_DARK}; }}
QComboBox QAbstractItemView::item {{ min-height: {TOUCH_PX}px; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {OUTLINE}; padding: 6px; }}
QMenu::item {{ min-height: {TOUCH_PX}px; padding: 0 20px; border-radius: 8px; }}
QMenu::item:selected {{ background: {ACCENT_DARK}; }}
QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {OUTLINE}; }}
QSlider::sub-page:horizontal {{ height: 6px; border-radius: 3px; background: {ACCENT}; }}
QSlider::handle:horizontal {{ width: 28px; height: 28px; margin: -11px 0; border-radius: 14px;
    background: {TEXT}; }}
QSlider {{ min-height: {TOUCH_PX}px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ width: 4px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {OUTLINE}; border-radius: 2px; min-height: 40px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QTabBar::tab {{ min-height: {TOUCH_PX}px; padding: 0 18px; }}
QHeaderView::section {{ background: {SURFACE_2}; color: {TEXT}; padding: 6px; border: none; }}
QTableWidget {{ gridline-color: {OUTLINE}; }}
"""


def apply(app) -> None:
    app.setPalette(palette())
    font = QFont(app.font())
    font.setPixelSize(BODY_PX)
    app.setFont(font)
    app.setStyleSheet(STYLE_SHEET)
