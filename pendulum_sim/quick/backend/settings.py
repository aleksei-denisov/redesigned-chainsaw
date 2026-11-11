"""QML singletons ``Settings`` and ``Scenarios``.

``Settings`` keeps the app's own choices (remembered with ``QSettings``): theme
(System / Light / Dark, applied at once), frame rate (Auto or a fixed 120/60/40/30),
graph refresh rate, physics in a process or a thread, the debug overlay.

``Scenarios`` lists the built-in scenarios and loads one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, QSettings, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication

if TYPE_CHECKING:  # the core is imported after the loading screen is up
    from .simulation import SimulationBridge

THEMES = ["system", "light", "dark"]
FRAME_RATES = ["auto", "120", "60", "40", "30"]
GRAPH_RATES = [20, 25, 30]


class SettingsBridge(QObject):
    themeChanged = Signal()
    darkChanged = Signal()
    frameRateChanged = Signal()
    graphRateChanged = Signal()
    physicsChanged = Signal()
    overlayChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = QSettings()
        self._theme = self._read("theme", "system", THEMES)
        self._frame_rate = self._read("frame_rate", "auto", FRAME_RATES)
        self._graph_rate = int(self._read("graph_rate", "25", [str(r) for r in GRAPH_RATES]))
        self._physics = self._read("physics", "process", ["process", "thread"])
        self._overlay = self.store.value("quick/overlay", False) in (True, "true", "1", 1)
        self._system_dark = self._read_system_dark()
        hints = QGuiApplication.styleHints() if QGuiApplication.instance() is not None else None
        signal = getattr(hints, "colorSchemeChanged", None) if hints is not None else None
        if signal is not None:
            signal.connect(self._on_system_scheme)

    def _read(self, key: str, default: str, allowed: list[str]) -> str:
        value = str(self.store.value(f"quick/{key}", default))
        return value if value in allowed else default

    @staticmethod
    def _read_system_dark() -> bool:
        if QGuiApplication.instance() is None:
            return False
        scheme = getattr(QGuiApplication.styleHints(), "colorScheme", None)
        return scheme is not None and scheme() == Qt.ColorScheme.Dark

    def _on_system_scheme(self, *_args) -> None:
        dark = self._read_system_dark()
        if dark != self._system_dark:
            self._system_dark = dark
            if self._theme == "system":
                self.darkChanged.emit()

    # ---------------------------------------------------------------- theme
    def _set_theme(self, value: str) -> None:
        if value in THEMES and value != self._theme:
            before = self.dark
            self._theme = value
            self.store.setValue("quick/theme", value)
            self.themeChanged.emit()
            if self.dark != before:
                self.darkChanged.emit()

    theme = Property(str, lambda self: self._theme, _set_theme, notify=themeChanged)

    def override_theme(self, value: str) -> None:
        """Theme for this run only (command line, tests): not saved."""
        if value in THEMES and value != self._theme:
            before = self.dark
            self._theme = value
            self.themeChanged.emit()
            if self.dark != before:
                self.darkChanged.emit()
    themes = Property("QVariantList", lambda self: THEMES, constant=True)

    @property
    def dark(self) -> bool:
        return self._theme == "dark" or (self._theme == "system" and self._system_dark)

    isDark = Property(bool, lambda self: self.dark, notify=darkChanged)

    # ----------------------------------------------------------- performance
    def _set_frame_rate(self, value: str) -> None:
        value = str(value)
        if value in FRAME_RATES and value != self._frame_rate:
            self._frame_rate = value
            self.store.setValue("quick/frame_rate", value)
            self.frameRateChanged.emit()

    frameRate = Property(str, lambda self: self._frame_rate, _set_frame_rate, notify=frameRateChanged)
    frameRates = Property("QVariantList", lambda self: FRAME_RATES, constant=True)

    def _set_graph_rate(self, value: int) -> None:
        value = int(value)
        if value in GRAPH_RATES and value != self._graph_rate:
            self._graph_rate = value
            self.store.setValue("quick/graph_rate", str(value))
            self.graphRateChanged.emit()

    graphRate = Property(int, lambda self: self._graph_rate, _set_graph_rate, notify=graphRateChanged)
    graphRates = Property("QVariantList", lambda self: GRAPH_RATES, constant=True)

    def _set_physics(self, value: str) -> None:
        if value in ("process", "thread") and value != self._physics:
            self._physics = value
            self.store.setValue("quick/physics", value)
            self.physicsChanged.emit()

    physics = Property(str, lambda self: self._physics, _set_physics, notify=physicsChanged)

    def _set_overlay(self, on: bool) -> None:
        if bool(on) != self._overlay:
            self._overlay = bool(on)
            self.store.setValue("quick/overlay", self._overlay)
            self.overlayChanged.emit()

    overlay = Property(bool, lambda self: self._overlay, _set_overlay, notify=overlayChanged)


class ScenarioBridge(QObject):
    """Built-in scenarios: list and load."""

    def __init__(self, sim: SimulationBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self._items: list[dict] | None = None

    def _get_items(self) -> list[dict]:
        from ...simulation import SCENARIOS, scenario

        if self._items is None:              # built on first use: each scenario seeds its LQR gains
            self._items = []
            for key in SCENARIOS:
                cfg = scenario(key)
                self._items.append({"key": key, "name": cfg.name, "description": cfg.description})
        return self._items

    items = Property("QVariantList", _get_items, constant=True)

    @Slot(str)
    def load(self, key: str) -> None:
        from ...simulation import SCENARIOS, scenario

        if key in SCENARIOS:
            self.sim.load(scenario(key))
