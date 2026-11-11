"""QML context object ``graphs``: graph selection and the data of the graphs on screen.

A graph item in QML asks for its :class:`GraphView` (``Graphs.view("angle")``), tells it
its size and whether it is visible, and draws the view's ``paths`` – per series a list of
polygons for a ``PathMultiline``. Only active views are computed, at the graph rate
(20–30 Hz, a setting), from one telemetry read for all of them; the maths is
``graphdata.py`` (Qt-free, shared chart maths with the Widgets app).
"""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import Property, QObject, QPointF, QSettings, QTimer, Signal, Slot
from PySide6.QtGui import QPolygonF

from ...mobile.plot_math import DEFAULT_SELECTION, GRAPHS, MAX_VISIBLE, format_tick, graph_series
from ...session import rules
from . import graphdata
from .simulation import SimulationBridge

try:
    from shiboken6 import VoidPtr as _VoidPtr
except ImportError:  # pragma: no cover
    _VoidPtr = None

WINDOWS_S = [5, 10, 20, 30]
TRACE_WINDOW_S = 10.0
DEFAULT_RATE_HZ = 25.0


def polygon(xs: np.ndarray, ys: np.ndarray) -> QPolygonF:
    """``QPolygonF`` filled from two arrays through its buffer (no per-point Python objects)."""
    n = int(xs.size)
    poly = QPolygonF()
    poly.resize(n)
    if n == 0:
        return poly
    ptr = poly.data()
    if _VoidPtr is None or ptr is None:
        return QPolygonF([QPointF(float(a), float(b)) for a, b in zip(xs, ys)])
    buf = np.frombuffer(_VoidPtr(ptr, 16 * n, True), np.float64).reshape(n, 2)
    buf[:, 0] = xs
    buf[:, 1] = ys
    return poly


class GraphView(QObject):
    """The data of one graph item (or of the Simulate screen's trace)."""

    updated = Signal()
    legendChanged = Signal()

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.width = 0.0
        self.height = 0.0
        self.active = False
        self.axis = graphdata.trace_axes() if key == "trace" else graphdata.graph_axis(key)
        self._paths: list[list[QPolygonF]] = []
        self._ticks: list[dict] = []
        self._legend: list[dict] = []
        self.lo, self.hi = 0.0, 1.0

    title = Property(str, lambda self: GRAPHS[self.key].title if self.key in GRAPHS else "Trace", constant=True)
    unit = Property(str, lambda self: GRAPHS[self.key].unit if self.key in GRAPHS else "", constant=True)
    paths = Property("QVariantList", lambda self: self._paths, notify=updated)
    ticks = Property("QVariantList", lambda self: self._ticks, notify=updated)
    legend = Property("QVariantList", lambda self: self._legend, notify=legendChanged)
    minimum = Property(float, lambda self: self.lo, notify=updated)
    maximum = Property(float, lambda self: self.hi, notify=updated)

    @Slot(float, float)
    def setSize(self, width: float, height: float) -> None:  # noqa: N802 - QML API
        self.width, self.height = float(width), float(height)

    @Slot(bool)
    def setActive(self, on: bool) -> None:  # noqa: N802 - QML API
        self.active = bool(on)

    def set_legend(self, legend: list[dict]) -> None:
        if legend != self._legend:
            self._legend = legend
            self.legendChanged.emit()

    def reset_axis(self) -> None:
        for axis in (self.axis if isinstance(self.axis, list) else [self.axis]):
            axis.reset()

    def show(self, frame: graphdata.GraphFrame) -> None:
        self._paths = [[polygon(xs, ys) for xs, ys in runs] for runs in frame.series]
        if self.key != "trace" and self.height > 0 and frame.hi > frame.lo:
            scale = self.height / (frame.hi - frame.lo)
            self._ticks = [{"value": v, "text": format_tick(v, frame.step), "y": self.height - (v - frame.lo) * scale}
                           for v in frame.ticks]
        self.lo, self.hi = frame.lo, frame.hi
        self.updated.emit()


class GraphsBridge(QObject):
    selectionChanged = Signal()
    availableChanged = Signal()
    windowChanged = Signal()
    loopChanged = Signal()
    rateChanged = Signal()

    def __init__(self, sim: SimulationBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self.settings = QSettings()
        stored = self.settings.value("quick/graphs/selected", DEFAULT_SELECTION)
        self._selection = [str(k) for k in (stored if isinstance(stored, list) else [stored]) if str(k) in GRAPHS]
        self._window = int(self.settings.value("quick/graphs/window_s", 10))
        self._loop = 0
        self._rate = DEFAULT_RATE_HZ
        self._views: dict[str, GraphView] = {}
        self._trace = GraphView("trace", self)
        self.ms = 0.0                                  # last refresh, for reports
        self.recording: list[float] | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(1000 / self._rate))
        sim.structureChanged.connect(self._on_structure)
        sim.controllerChanged.connect(self._on_structure)
        sim.configChanged.connect(self._reset_axes)
        self._on_structure()

    # ------------------------------------------------------------ choices
    def _labels(self) -> list[str]:
        return rules.loop_labels(self.sim.session.config.controller)

    def _available(self) -> list[str]:
        plant = self.sim.session.config.plant
        return graphdata.available_graphs(plant.n_segments, plant.has_cart)

    available = Property("QVariantList", lambda self: [{"key": k, "title": GRAPHS[k].title, "unit": GRAPHS[k].unit}
                                                       for k in self._available()], notify=availableChanged)
    selection = Property("QVariantList", lambda self: [k for k in self._selection if k in self._available()],
                         notify=selectionChanged)
    maxVisible = Property(int, lambda self: MAX_VISIBLE, constant=True)
    windows = Property("QVariantList", lambda self: WINDOWS_S, constant=True)
    loopLabels = Property("QVariantList", lambda self: self._labels(), notify=availableChanged)
    trace = Property(QObject, lambda self: self._trace, constant=True)

    @Slot(str, bool)
    def select(self, key: str, on: bool) -> None:
        if key not in GRAPHS:
            return
        sel = [k for k in self._selection if k != key]
        if on:
            sel.append(key)
            sel = sel[-MAX_VISIBLE:]
        if sel != self._selection:
            self._selection = sel
            self.settings.setValue("quick/graphs/selected", sel)
            self.selectionChanged.emit()

    def _get_window(self) -> int:
        return self._window

    def _set_window(self, seconds: int) -> None:
        if int(seconds) != self._window:
            self._window = int(seconds)
            self.settings.setValue("quick/graphs/window_s", self._window)
            self.windowChanged.emit()

    windowS = Property(int, _get_window, _set_window, notify=windowChanged)

    def _set_loop(self, k: int) -> None:
        if int(k) != self._loop:
            self._loop = int(k)
            self._legends()
            self.loopChanged.emit()

    pidLoop = Property(int, lambda self: self._loop, _set_loop, notify=loopChanged)

    def _set_rate(self, hz: float) -> None:
        hz = min(max(float(hz), 5.0), 60.0)
        if hz != self._rate:
            self._rate = hz
            self.timer.setInterval(int(1000 / hz))
            self.rateChanged.emit()

    rateHz = Property(float, lambda self: self._rate, _set_rate, notify=rateChanged)

    @Slot(str, result=QObject)
    def view(self, key: str) -> QObject:
        if key == "trace":
            return self._trace
        if key not in self._views:
            self._views[key] = GraphView(key, self)
            self._legends()
        return self._views[key]

    # ----------------------------------------------------------- updates
    def _on_structure(self) -> None:
        labels = self._labels()
        self._loop = min(self._loop, max(len(labels) - 1, 0))
        self._legends()
        self.availableChanged.emit()
        self.selectionChanged.emit()

    def _legends(self) -> None:
        n = self.sim.n
        labels = self._labels()
        for key, v in self._views.items():
            if key in GRAPHS:
                v.set_legend([{"label": s.label, "index": i, "dashed": s.dashed}
                              for i, s in enumerate(graph_series(key, n, labels, self._loop))])
        self._trace.set_legend([{"label": "θ1", "index": 0, "dashed": False},
                                {"label": "x", "index": 1, "dashed": False}])

    def _reset_axes(self) -> None:
        for v in list(self._views.values()) + [self._trace]:
            v.reset_axis()

    def refresh(self) -> None:
        """Compute the active views (one telemetry read for all of them)."""
        active = [v for v in self._views.values() if v.active and v.key in self._available()]
        trace = self._trace.active
        if not active and not trace:
            return
        started = time.perf_counter()
        n, labels = self.sim.n, self._labels()
        names = graphdata.columns([v.key for v in active], n, labels, self._loop)
        if trace:
            names += [c for c in ("theta1", "x") if c not in names]
        window = max(float(self._window), TRACE_WINDOW_S if trace else 0.0)
        data = self.sim.source.tail(names, window)
        now = time.monotonic()
        for v in active:
            v.show(graphdata.graph_frame(v.key, data, n, labels, self._loop, float(self._window),
                                         v.width, v.height, v.axis, now))
        if trace:
            self._trace.show(graphdata.trace_frame(data, TRACE_WINDOW_S, self._trace.width, self._trace.height,
                                                   self._trace.axis, now, self.sim.session.config.plant.has_cart))
        self.ms = (time.perf_counter() - started) * 1000.0
        if self.recording is not None:
            self.recording.append(self.ms)
