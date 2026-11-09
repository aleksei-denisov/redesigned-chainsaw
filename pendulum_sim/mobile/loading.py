"""In-app loading screen shown while the simulator starts.

Android first shows the native splash (logo and name on the theme background, see
docs/ANDROID.md). As soon as Python has created the QApplication this screen takes
over with the *same* logo and title at the same place, adds a progress bar and
the current step ("Loading NumPy…", "Loading physics engine…", …), and finally
fades into the main window. Only PySide6 is imported here, so it appears before
NumPy, the simulation and the UI modules are loaded.

The geometry (in dp) matches ``android/splash/splash_logo.png``: a 160 dp logo, a
24 dp gap and the two-line title, centred on the screen.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel, QWidget

from . import theme
from .controls import ProgressBar

LOGO = Path(__file__).with_name("logo.svg")
LOGO_DP = 160
GAP_DP = 24
TITLE_LINES = ("Inverted Pendulum", "Simulator")
TITLE_PX = 26
BAR_W, BAR_H = 280, 8


def logo_pixmap(size: int, dpr: float) -> QPixmap:
    pm = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    pm.fill(Qt.GlobalColor.transparent)
    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:  # pragma: no cover
        pm.setDevicePixelRatio(dpr)
        return pm
    renderer = QSvgRenderer(QByteArray(LOGO.read_bytes()))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


class LoadingScreen(QWidget):
    """Logo, title, progress bar and the current start-up step."""

    def __init__(self, steps: int) -> None:
        super().__init__()
        self.setWindowTitle("Inverted Pendulum Simulator")
        self.setAutoFillBackground(True)
        self.steps = max(1, steps)
        self.done = 0
        self.status = "Starting…"
        self._logo = logo_pixmap(LOGO_DP, self.devicePixelRatioF())
        self.bar = ProgressBar(self.steps * 10, show_text=False, height=BAR_H)
        self.bar.setParent(self)
        self._title_font = QFont(self.font())
        self._title_font.setPixelSize(TITLE_PX)
        self._title_font.setBold(True)
        self._status_font = QFont(self.font())
        self._status_font.setPixelSize(theme.BODY_PX - 1)

    # ---------------------------------------------------------------- steps
    def step(self, text: str) -> None:
        """Show the next step and paint it now (the event loop is not running yet)."""
        self.status = text
        self.bar.setValue(self.done * 10 + 3)
        self.done += 1
        self.repaint()
        QApplication.processEvents()

    def finish(self) -> None:
        self.status = "Ready"
        self.bar.setValue(self.steps * 10)
        self.repaint()

    # --------------------------------------------------------------- layout
    def _block_top(self) -> float:
        title_h = len(TITLE_LINES) * TITLE_PX * 1.3
        return (self.height() - (LOGO_DP + GAP_DP + title_h)) / 2

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        top = self._block_top() + LOGO_DP + GAP_DP + len(TITLE_LINES) * TITLE_PX * 1.3 + 40
        w = min(BAR_W, self.width() - 64)
        self.bar.setGeometry(round((self.width() - w) / 2), round(top), w, BAR_H)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        top = self._block_top()
        p.drawPixmap(round((self.width() - LOGO_DP) / 2), round(top), self._logo)
        p.setFont(self._title_font)
        p.setPen(QColor(theme.TEXT))
        y = top + LOGO_DP + GAP_DP
        for line in TITLE_LINES:
            p.drawText(QRectF(0, y, self.width(), TITLE_PX * 1.3), Qt.AlignmentFlag.AlignCenter, line)
            y += TITLE_PX * 1.3
        p.setFont(self._status_font)
        p.setPen(QColor(theme.TEXT_2))
        bar_bottom = self.bar.geometry().bottom()
        p.drawText(QRectF(16, bar_bottom + 14, self.width() - 32, 28), Qt.AlignmentFlag.AlignCenter,
                   self.status)
        p.end()

    # ----------------------------------------------------------- transition
    def fade_into(self, window: QWidget, ms: int = 320) -> None:
        """Cover ``window`` with a picture of this screen, close it, fade the picture out."""
        picture = self.grab()
        cover = QLabel(window)
        cover.setPixmap(picture)
        cover.setScaledContents(True)
        cover.setGeometry(window.rect())
        cover.show()
        cover.raise_()
        effect = QGraphicsOpacityEffect(cover)
        cover.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", cover)
        anim.setDuration(ms)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(cover.deleteLater)
        self.close()
        anim.start()
        window._loading_cover = cover     # keep alive until the animation ends


class BusyOverlay(QWidget):
    """In-window "Loading …" cover for slow, blocking work (loading a scenario or a file).

    ``run(text, work)`` paints the cover *now* (the event loop is blocked while ``work``
    runs), calls ``work`` and then fades the cover out once the new screen is drawn.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.text = ""
        self.hide()
        self._logo = logo_pixmap(72, self.devicePixelRatioF())
        self._font = QFont(self.font())
        self._font.setPixelSize(theme.BODY_PX + 2)
        self._font.setBold(True)
        self._anim = None

    def run(self, text: str, work) -> None:
        parent = self.parentWidget()
        self.text = text
        self.setGraphicsEffect(None)
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.repaint()                         # painted and shown before the work blocks
        QApplication.processEvents(QApplication.ProcessEventsFlag.ExcludeUserInputEvents)
        try:
            work()
        finally:
            QTimer.singleShot(60, self._fade)

    def _fade(self) -> None:
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(220)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self.hide)
        anim.start()
        self._anim = anim

    def mousePressEvent(self, event) -> None:  # noqa: N802 - swallow taps while covering
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        bg = QColor(theme.BG)
        bg.setAlpha(240)
        p.fillRect(self.rect(), bg)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        top = self.height() / 2 - 72
        p.drawPixmap(round((self.width() - 72) / 2), round(top), self._logo)
        p.setFont(self._font)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(16, top + 88, self.width() - 32, 32), Qt.AlignmentFlag.AlignCenter, self.text)
        # static "busy" bar (nothing animates while the work blocks the event loop)
        w = min(220, self.width() - 64)
        r = QRectF((self.width() - w) / 2, top + 132, w, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SURFACE_2))
        p.drawRoundedRect(r, 3, 3)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(r.left(), r.top(), r.width() * 0.4, r.height()), 3, 3)
        p.end()

