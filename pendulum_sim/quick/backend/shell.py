"""QML singleton ``App``: start-up state, the current screen and app-level signals.

The loading screen (``Main.qml``) only needs ``App`` and ``Settings``; everything else
is created after its first frame is on screen. ``ready`` then switches on the loader of
the app shell, which builds the Simulate screen asynchronously; other screens are built
on first open.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

SCREENS = ["simulate", "graphs", "controller", "settings"]


class AppBridge(QObject):
    statusChanged = Signal()
    readyChanged = Signal()
    screenChanged = Signal()
    loadingShown = Signal()          # first frame of the loading screen is on screen
    shellLoaded = Signal()           # the shell and the Simulate screen are built
    shellShown = Signal()            # … and their first frame is on screen
    exitRequested = Signal()
    failed = Signal()                # the shell could not be built (QML error)
    continueRequested = Signal()     # safe mode: the user read the previous start's report
    previousChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._status = "Starting…"
        self._progress = 0.1
        self._ready = False
        self._screen = "simulate"
        self._loading_shown = self._shell_loaded = self._shell_shown = False
        self._previous = ""
        self._copied = False

    def set_status(self, text: str, progress: float) -> None:
        self._status, self._progress = text, progress
        self.statusChanged.emit()

    status = Property(str, lambda self: self._status, notify=statusChanged)
    progress = Property(float, lambda self: self._progress, notify=statusChanged)

    def set_previous_report(self, text: str) -> None:
        self._previous = text
        self.previousChanged.emit()

    previousReport = Property(str, lambda self: self._previous, notify=previousChanged)
    previousCopied = Property(bool, lambda self: self._copied, notify=previousChanged)

    @Slot()
    def copyPreviousReport(self) -> None:  # noqa: N802 - QML API
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(self._previous)
        self._copied = True
        self.previousChanged.emit()

    @Slot()
    def continueStart(self) -> None:  # noqa: N802 - QML API
        self._previous = ""
        self.previousChanged.emit()
        self.continueRequested.emit()

    def set_ready(self) -> None:
        if not self._ready:
            self._ready = True
            self.readyChanged.emit()

    ready = Property(bool, lambda self: self._ready, notify=readyChanged)

    def _set_screen(self, key: str) -> None:
        if key in SCREENS and key != self._screen:
            self._screen = key
            self.screenChanged.emit()

    screen = Property(str, lambda self: self._screen, _set_screen, notify=screenChanged)
    screens = Property("QVariantList", lambda self: SCREENS, constant=True)

    # -------------------------------------------------------- from QML
    @Slot()
    def loadingFrame(self) -> None:  # noqa: N802 - QML API
        if not self._loading_shown:
            self._loading_shown = True
            self.loadingShown.emit()

    @Slot()
    def shellReady(self) -> None:  # noqa: N802 - QML API
        if not self._shell_loaded:
            self._shell_loaded = True
            self.shellLoaded.emit()

    @Slot()
    def shellFailed(self) -> None:  # noqa: N802 - QML API
        self.set_status("The app's screens could not be loaded (QML error; see the log).", 0.0)
        self.failed.emit()

    @Slot()
    def shellFrame(self) -> None:  # noqa: N802 - QML API
        if self._shell_loaded and not self._shell_shown:
            self._shell_shown = True
            self.shellShown.emit()

    @property
    def shown(self) -> bool:
        return self._shell_shown

    @Slot()
    def back(self) -> None:
        """Android back: to the Simulate screen, or ask to exit from there."""
        if self._screen != "simulate":
            self._set_screen("simulate")
        else:
            self.exitRequested.emit()
