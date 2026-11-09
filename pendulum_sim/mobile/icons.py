"""Bundled SVG icons (``pendulum_sim/mobile/icons/*.svg``), tinted and cached.

Every icon is drawn with ``currentColor``; :func:`icon` substitutes the theme colour,
renders the SVG with QtSvg at the sizes the screen needs and returns a ``QIcon`` with
normal, disabled and checked variants. Nothing depends on the device's fonts, so the
icons look the same on every Android version (the desktop's text glyphs such as
⟲ ⟳ ☰ ▾ have no glyph in Android's fonts and showed as empty boxes).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap

try:
    from PySide6.QtSvg import QSvgRenderer
except ImportError:  # pragma: no cover - QtSvg is part of PySide6-Essentials
    QSvgRenderer = None

ICON_DIR = Path(__file__).with_name("icons")
SIZES = (16, 20, 24, 32, 40, 48, 64)


def names() -> list[str]:
    return sorted(p.stem for p in ICON_DIR.glob("*.svg"))


def _svg(name: str) -> str:
    return (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")


def pixmap(name: str, size: int, color: str | QColor, dpr: float | None = None) -> QPixmap:
    """Render icon ``name`` at ``size`` logical pixels in ``color``."""
    if dpr is None:
        app = QGuiApplication.instance()
        dpr = app.devicePixelRatio() if app is not None else 1.0
    c = QColor(color)
    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    if QSvgRenderer is not None:
        data = _svg(name).replace("currentColor", c.name(QColor.NameFormat.HexArgb if c.alpha() < 255
                                                         else QColor.NameFormat.HexRgb))
        renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(p, QRectF(0, 0, px, px))
        p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


@lru_cache(maxsize=256)
def icon(name: str, color: str = "#E8EAED", disabled: str = "#5F6368", checked: str | None = None) -> QIcon:
    ic = QIcon()
    for s in SIZES:
        ic.addPixmap(pixmap(name, s, color), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(pixmap(name, s, disabled), QIcon.Mode.Disabled, QIcon.State.Off)
        ic.addPixmap(pixmap(name, s, checked or color), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(pixmap(name, s, disabled), QIcon.Mode.Disabled, QIcon.State.On)
    return ic


def icon_size(px: int) -> QSize:
    return QSize(px, px)
