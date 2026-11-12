"""QML context object ``trail``: the trajectory line in the Simulate view.

Settings (remembered): ``mode`` – off, the tip, or every joint – and ``seconds``. The
drawing reuses ``GraphLines``: this object offers the same interface as a graph view
(``setSize``, ``setActive``, ``set_style``, ``output``, ``updated``), and the QML view
tells it how metres map to pixels (``setMapping``). A point is added on every shown
frame while the simulation advances (``on_frame``, called by ``frames``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Property, QObject, QSettings, Signal, Slot

from .trailgeom import MODES, SECONDS, TrailBuffer, geometry, joints


@dataclass
class _Output:
    curves: list = field(default_factory=list)
    dx: float = 0.0


class TrailBridge(QObject):
    updated = Signal()
    modeChanged = Signal()
    secondsChanged = Signal()

    def __init__(self, sim, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self.store = QSettings()
        mode = str(self.store.value("quick/view/trail", "tip"))
        self._mode = mode if mode in MODES else "tip"
        try:
            seconds = int(self.store.value("quick/view/trail_s", 3))
        except (TypeError, ValueError):
            seconds = 3
        self._seconds = seconds if seconds in SECONDS else 3
        self.buffer = TrailBuffer(self._seconds)
        self.output = _Output()
        self.dpr = 1.0
        self.active = False
        self.style = None
        self._mapping = (1.0, 0.0, 0.0)
        self._epoch = None
        sim.structureChanged.connect(self.clear)

    # ------------------------------------------------------------ settings
    def _set_mode(self, mode: str) -> None:
        if mode in MODES and mode != self._mode:
            self._mode = mode
            self.store.setValue("quick/view/trail", mode)
            self.modeChanged.emit()
            self._draw()

    mode = Property(str, lambda self: self._mode, _set_mode, notify=modeChanged)

    def _set_seconds(self, seconds: int) -> None:
        seconds = int(seconds)
        if seconds in SECONDS and seconds != self._seconds:
            self._seconds = seconds
            self.buffer.seconds = float(seconds)
            self.store.setValue("quick/view/trail_s", seconds)
            self.secondsChanged.emit()

    seconds = Property(int, lambda self: self._seconds, _set_seconds, notify=secondsChanged)
    modes = Property("QVariantList", lambda self: list(MODES), constant=True)
    secondsOptions = Property("QVariantList", lambda self: list(SECONDS), constant=True)

    @Slot()
    def clear(self) -> None:
        self.buffer.clear()
        self._draw()

    # ------------------------------------------- GraphLines view interface
    @Slot(float, float)
    def setSize(self, width: float, height: float) -> None:  # noqa: N802 - QML API
        pass

    @Slot(bool)
    def setActive(self, on: bool) -> None:  # noqa: N802 - QML API
        if bool(on) != self.active:
            self.active = bool(on)
            self._draw()

    def set_style(self, style) -> None:
        self.style = style
        self._draw()

    @Slot(float, float, float)
    def setMapping(self, px_per_m: float, cx: float, origin_y: float) -> None:  # noqa: N802 - QML API
        mapping = (float(px_per_m), float(cx), float(origin_y))
        if mapping != self._mapping:
            self._mapping = mapping
            self._draw()

    # ------------------------------------------------------------ per frame
    def on_frame(self, _now: float) -> None:
        sim = self.sim
        if self._mode == "off":
            return
        epoch = getattr(sim, "_epoch", None)
        if epoch != self._epoch:                   # reset / new run
            self._epoch = epoch
            self.buffer.clear()
        positions = joints(sim._cart, sim._th, sim._lengths)
        if self.buffer.add(float(sim._t), positions):
            self._draw()

    def _draw(self) -> None:
        if not self.active or self.style is None:
            if self.output.curves:
                self.output = _Output()
                self.updated.emit()
            return
        px, cx, oy = self._mapping
        st = self.style
        self.output = _Output(geometry(self.buffer, self._mode, px, cx, oy, st.colors, st.width, st.feather))
        self.updated.emit()
