"""In-app loading screen shown while the simulator starts.

Android first shows the native splash (logo and name on the theme background, see
docs/ANDROID.md). As soon as Python has created the QApplication this screen takes
over with the *same* logo and title at the same place, adds a progress bar and
the current step ("Loading NumPy…", "Loading physics engine…", …). The screen is
a page of :class:`AppShell`, the app's only top-level window, which then switches to
the main window in one step (no second window, no fade over the GPU-drawn view).
Only PySide6 is imported here, so it appears before NumPy, the simulation and the
UI modules are loaded.

The geometry (in dp) matches ``android/splash/splash_logo.png``: a 160 dp logo, a
24 dp gap and the two-line title, centred on the screen.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QEventLoop, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QStackedLayout, QWidget

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



class AppShell(QWidget):
    """The app's only top-level window: the loading screen first, then the main window.

    On Android every top-level Qt window is a surface of its own, and two of them alive at
    once (a loading window closing while the main window opens) were composed in changing
    order: the loading screen flashed up again over the new main window. Here the main
    window is embedded as a child page, so start-up never creates a second surface. Keys
    (Android Back) and closing are passed on to the embedded window.
    """

    def __init__(self, title: str = "Inverted Pendulum Simulator") -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.setAutoFillBackground(True)
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.content: QWidget | None = None

    def show_page(self, widget: QWidget) -> None:
        """Add ``widget`` (if new) and make it the visible page (the previous one stays below)."""
        if self.stack.indexOf(widget) < 0:
            self.stack.addWidget(widget)       # reparents: a top-level widget becomes a child page
        self.stack.setCurrentWidget(widget)

    def present(self, window: QWidget) -> None:
        """Switch from the loading screen to ``window`` in one step and drop the other pages."""
        self.content = window
        self.show_page(window)
        window.setFocus()
        self.activateWindow()                  # keep the application "active" (frame timer runs)
        for i in reversed(range(self.stack.count())):
            page = self.stack.widget(i)
            if page is not window:
                self.stack.removeWidget(page)
                page.hide()
                page.deleteLater()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - no focus widget: keys go to the content
        if self.content is not None and not getattr(self, "_forwarding", False):
            self._forwarding = True            # unhandled keys propagate back up to us
            try:
                QApplication.sendEvent(self.content, event)
            finally:
                self._forwarding = False
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - the content's shutdown (workers, files)
        if self.content is not None and not self.content.close():
            event.ignore()
            return
        super().closeEvent(event)


class BusyOverlay(QWidget):
    """In-window "Loading …" cover for slow, blocking work (loading a scenario or a file).

    ``run(text, work)`` paints the cover *now* (the event loop is blocked while ``work``
    runs), calls ``work`` and removes the cover once the new screen is drawn.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.text = ""
        self.hide()
        self._logo = logo_pixmap(72, self.devicePixelRatioF())
        self._font = QFont(self.font())
        self._font.setPixelSize(theme.BODY_PX + 2)
        self._font.setBold(True)
        self._depth = 0
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def run(self, text: str, work) -> None:
        """Cover, work, uncover – once. A nested request (work that itself loads) just works.

        No fade: an opacity effect re-renders the cover on every animation frame, which
        flickered over the GPU-drawn view and graphs. The cover stays until the new
        screen has been drawn, then disappears in one step.
        """
        if self._depth:
            work()
            return
        self._depth += 1
        parent = self.parentWidget()
        self.text = text
        self._hide_timer.stop()
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        try:
            self._settle()                     # painted and on screen before the work blocks
            work()
        finally:
            self._depth -= 1
            self._hide_timer.start(150)        # after the new screen's first frames

    SETTLE_S = 0.12

    def _settle(self) -> None:
        """Present the cover for a few frames before the event loop blocks.

        Loads usually start from a popup menu, which on Android is a window (surface) of
        its own; closing it while the work blocked the Qt thread left the main surface
        black or half-drawn for seconds. Pumping events for ~0.1 s lets the popup go
        and puts the cover into every buffer of the window's swap chain.
        """
        import time

        end = time.perf_counter() + self.SETTLE_S
        while True:
            self.repaint()
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            if time.perf_counter() >= end:
                break
            time.sleep(0.016)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - swallow taps while covering
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))   # opaque: nothing shows through
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

