"""QML bridges of the Qt Quick spike: simulation state, graph data, measurements.

Everything here runs on the GUI thread. Per displayed frame QML's ``FrameAnimation``
calls :meth:`SimBridge.advance`, which reads the interpolated state from the physics
source (``sim_source.py``), updates a handful of float properties and emits one signal;
the QML scene positions its items from them. The render thread never calls Python –
except in the two comparison variants written as custom scene-graph items
(``scene_items.py``), which exist to be measured.
"""

from __future__ import annotations

import math
import statistics
import time

import numpy as np
from PySide6.QtCore import Property, QObject, QPointF, QTimer, Signal, Slot
from PySide6.QtGui import QPolygonF

from ...mobile.plot_math import downsample_minmax
from ...physics import MultiLinkPendulum
from ...simulation import AppConfig
from .sim_source import ForkSource, make_source

try:
    from shiboken6 import VoidPtr as _VoidPtr
except ImportError:  # pragma: no cover
    _VoidPtr = None

READOUT_HZ = 15.0
GRAPH_HZ = 30.0
GRAPH_WINDOW_S = 10.0


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


def fill_series(series: QObject, xs: np.ndarray, ys: np.ndarray) -> None:
    """Replace the points of a Qt Graphs line series (NumPy fast path where PySide has it)."""
    replace_np = getattr(series, "replaceNp", None)
    if replace_np is not None:
        replace_np(np.ascontiguousarray(xs, dtype=np.float64), np.ascontiguousarray(ys, dtype=np.float64))
    else:
        series.replace(polygon(xs, ys))


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


class FrameRecorder:
    """Frame gaps and GUI-thread Python time per displayed frame."""

    def __init__(self) -> None:
        self.recording: list[tuple[float, float]] | None = None
        self.gaps: list[float] = []            # last second, for the on-screen fps
        self.fps = 0.0
        self.busy_ms = 0.0
        self._second = time.perf_counter()

    def add(self, gap_ms: float, busy_ms: float) -> None:
        self.gaps.append(gap_ms)
        self.busy_ms = 0.9 * self.busy_ms + 0.1 * busy_ms
        if self.recording is not None:
            self.recording.append((gap_ms, busy_ms))
        now = time.perf_counter()
        if now - self._second >= 0.5:
            self.fps = len(self.gaps) / (now - self._second)
            self.gaps.clear()
            self._second = now


class SimBridge(QObject):
    """Simulation state for QML: ``cartX``, ``th1..3`` per frame; read-outs at 15 Hz."""

    frame = Signal()
    readoutsChanged = Signal()
    runningChanged = Signal()
    physicsChanged = Signal()
    pendulumModeChanged = Signal()

    def __init__(self, config: AppConfig, physics: str = "thread", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        model = MultiLinkPendulum(config.plant)
        self.n = model.n
        self._lengths = [float(v) for v in model.lengths]
        self._physics = physics if (physics != "process" or ForkSource.available()) else "thread"
        self.source = make_source(self._physics, config)
        self.source.start()
        self.recorder = FrameRecorder()
        self._running = False
        self._pendulum_mode = "items"
        self._cart = 0.0
        self._th = [0.0, 0.0, 0.0]
        self._omega = 0.0
        self._applied = 0.0
        self._t = 0.0
        self._last = 0.0
        self._busy = 0.0
        self._readout_due = 0.0
        self._texts = {"angle": "+0.00", "rate": "+0.0", "cart": "+0.000", "force": "+0.00",
                       "time": "T 000.00 s", "fps": "0 FPS"}
        self.advance()

    # ------------------------------------------------------------ per frame
    @Slot()
    def advance(self) -> None:
        """One displayed frame: fetch the interpolated state and notify QML."""
        now = time.perf_counter()
        s = self.source.sample()
        if s is not None:
            n, st = self.n, s.state
            self._cart = float(st[0])
            th = self._th
            for i in range(n):
                th[i] = float(st[1 + i])
            self._omega = float(st[n + 2])
            self._applied = s.applied
            self._t = s.t
        if self._last:
            self.recorder.add((now - self._last) * 1000.0, self._busy)
        self._last = now
        self.frame.emit()                         # QML places its items in this call
        if now >= self._readout_due:
            self._readout_due = now + 1.0 / READOUT_HZ
            t = self._texts
            t["angle"] = f"{math.degrees(self._wrap(self._th[0])):+.2f}"
            t["rate"] = f"{math.degrees(self._omega):+.1f}"
            t["cart"] = f"{self._cart:+.3f}"
            t["force"] = f"{self._applied:+.2f}"
            t["time"] = f"T {self._t:06.2f} s"
            t["fps"] = f"{self.recorder.fps:.0f} FPS"
            self.readoutsChanged.emit()
        self._busy = (time.perf_counter() - now) * 1000.0

    @staticmethod
    def _wrap(a: float) -> float:
        return (a + math.pi) % (2.0 * math.pi) - math.pi

    cartX = Property(float, lambda self: self._cart, notify=frame)
    th1 = Property(float, lambda self: self._th[0], notify=frame)
    th2 = Property(float, lambda self: self._th[1], notify=frame)
    th3 = Property(float, lambda self: self._th[2], notify=frame)

    # ------------------------------------------------------------- constants
    segments = Property(int, lambda self: self.n, constant=True)
    lengths = Property("QVariantList", lambda self: self._lengths, constant=True)
    trackLength = Property(float, lambda self: float(self.config.plant.track_length), constant=True)
    scenarioName = Property(str, lambda self: self.config.name, constant=True)
    processAvailable = Property(bool, lambda self: ForkSource.available(), constant=True)

    # -------------------------------------------------------------- read-outs
    angleText = Property(str, lambda self: self._texts["angle"], notify=readoutsChanged)
    rateText = Property(str, lambda self: self._texts["rate"], notify=readoutsChanged)
    cartText = Property(str, lambda self: self._texts["cart"], notify=readoutsChanged)
    forceText = Property(str, lambda self: self._texts["force"], notify=readoutsChanged)
    timeText = Property(str, lambda self: self._texts["time"], notify=readoutsChanged)
    fpsText = Property(str, lambda self: self._texts["fps"], notify=readoutsChanged)

    # --------------------------------------------------------------- control
    def _get_running(self) -> bool:
        return self._running

    def _set_running(self, on: bool) -> None:
        if on != self._running:
            self._running = on
            self.source.set_running(on)
            self.runningChanged.emit()

    running = Property(bool, _get_running, _set_running, notify=runningChanged)

    @Slot()
    def toggle(self) -> None:
        self._set_running(not self._running)

    @Slot()
    def reset(self) -> None:
        self.source.reset()

    @Slot(float)
    def push(self, direction: float) -> None:
        self.source.push(float(self.config.push_force) * (1.0 if direction >= 0 else -1.0))

    # ------------------------------------------------------- spike variants
    def _get_physics(self) -> str:
        return self._physics

    def _set_physics(self, kind: str) -> None:
        if kind == self._physics or (kind == "process" and not ForkSource.available()):
            return
        self.source.stop()
        self._physics = kind
        self.source = make_source(kind, self.config)
        self.source.start()
        self.source.set_running(self._running)
        self.physicsChanged.emit()

    physics = Property(str, _get_physics, _set_physics, notify=physicsChanged)

    def _get_pendulum_mode(self) -> str:
        return self._pendulum_mode

    def _set_pendulum_mode(self, mode: str) -> None:
        if mode != self._pendulum_mode:
            self._pendulum_mode = mode
            self.pendulumModeChanged.emit()

    pendulumMode = Property(str, _get_pendulum_mode, _set_pendulum_mode, notify=pendulumModeChanged)

    def state(self) -> tuple[float, list[float]]:
        """(cart x, angles) of the current frame – for the scene-graph variant."""
        return self._cart, self._th

    def shutdown(self) -> None:
        self.source.stop()


class GraphBridge(QObject):
    """Two traces (θ1 and cart x) of the last ``GRAPH_WINDOW_S`` seconds, in item pixels.

    Decimated to the item's pixel width, refreshed at 30 Hz and only while a graph
    variant is shown. Each trace is normalised to the item's height on its own.
    """

    updated = Signal()
    modeChanged = Signal()

    def __init__(self, sim: SimBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self._mode = "shape"
        self._w, self._h = 300.0, 120.0
        self._polys = [QPolygonF(), QPolygonF()]
        self.last_curves: list[tuple[np.ndarray, np.ndarray]] = []   # for the scene-graph variant
        self._series: list[QObject] = []
        self.ms = 0.0
        self.recording: list[float] | None = None
        self.error = ""
        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / GRAPH_HZ))
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def _get_mode(self) -> str:
        return self._mode

    def _set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode = mode
            self._series = []
            self.modeChanged.emit()

    mode = Property(str, _get_mode, _set_mode, notify=modeChanged)
    trace1 = Property("QVariant", lambda self: self._polys[0], notify=updated)
    trace2 = Property("QVariant", lambda self: self._polys[1], notify=updated)
    windowS = Property(float, lambda self: GRAPH_WINDOW_S, constant=True)

    @Slot(float, float)
    def setSize(self, w: float, h: float) -> None:  # noqa: N802 - QML API
        self._w, self._h = max(w, 16.0), max(h, 16.0)

    @Slot(QObject, QObject)
    def setSeries(self, first: QObject, second: QObject) -> None:  # noqa: N802 - QML API
        """Qt Graphs variant: the two LineSeries to fill."""
        self._series = [first, second]

    @Slot(str)
    def reportError(self, text: str) -> None:  # noqa: N802 - QML API
        self.error = text

    def polygons(self) -> list[QPolygonF]:
        return self._polys

    def curves(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """Pixel-space (x, y) arrays of both traces for the current item size."""
        data = self.sim.source.tail(("t", "theta1", "x"), GRAPH_WINDOW_S)
        t = data["t"]
        if t.size < 2:
            return []
        t1 = float(t[-1])
        t0 = t1 - GRAPH_WINDOW_S
        w, h = self._w, self._h
        out = []
        for y, min_span in ((np.degrees(data["theta1"]), 2.0), (data["x"], 0.05)):
            xs, ys = downsample_minmax(t, y, t0, t1, int(w))
            lo, hi = float(np.min(ys)), float(np.max(ys))
            span = max(hi - lo, min_span) * 1.12
            mid = 0.5 * (lo + hi)
            out.append(((xs - t0) * (w / GRAPH_WINDOW_S), h * 0.5 - (ys - mid) * (h / span)))
        return out

    def refresh(self) -> None:
        if self._mode == "off":
            return
        started = time.perf_counter()
        curves = self.curves()
        if not curves:
            return
        self.last_curves = curves
        if self._mode == "qtgraphs":
            if len(self._series) == 2:
                try:
                    for series, (xs, ys) in zip(self._series, curves):
                        fill_series(series, xs, self._h - ys)      # the axis points up
                except Exception as exc:  # noqa: BLE001 - reported, the other variants go on
                    self.error = f"Qt Graphs series update failed: {type(exc).__name__}: {exc}"
                    self._series = []
        elif self._mode != "node":
            self._polys = [polygon(xs, ys) for xs, ys in curves]
        self.updated.emit()
        self.ms = (time.perf_counter() - started) * 1000.0
        if self.recording is not None:
            self.recording.append(self.ms)


def summarise(label: str, frames: list[tuple[float, float]], seconds: float, graph_ms: list[float],
              swaps: int, stats0: dict, stats1: dict,
              cpu0: dict[str, float] | None = None, cpu1: dict[str, float] | None = None) -> str:
    """One report block from recorded frames ``(gap ms, GUI-thread Python ms)``.

    ``cpu0``/``cpu1`` are :func:`threads.thread_times` at the start and end of the phase.
    """
    if not frames:
        return f"{label}\n  no frames recorded"
    gaps = [f[0] for f in frames]
    busy = [f[1] for f in frames]
    steps = stats1["steps"] - stats0["steps"]
    phys = (stats1["busy_s"] - stats0["busy_s"]) / max(seconds, 1e-9)
    lines = [
        label,
        f"  {len(frames) / seconds:6.1f} fps (GUI frames)   {swaps / seconds:6.1f} fps (frames presented)",
        f"  frame gap p50 {statistics.median(gaps):5.1f}  p95 {percentile(gaps, 0.95):5.1f}  "
        f"max {max(gaps):6.1f} ms   gaps > 33 ms: {sum(1 for g in gaps if g > 33.0)}",
        f"  GUI-thread Python per frame: mean {sum(busy) / len(busy):5.2f}  p95 {percentile(busy, 0.95):5.2f}  "
        f"max {max(busy):5.2f} ms",
        f"  physics: {steps / seconds:6.0f} steps/s, {100 * phys:4.1f} % of one core, "
        f"real-time factor {stats1['realtime_factor']:.2f}",
    ]
    if cpu0 and cpu1:
        def per_frame(key: str) -> str:
            if key not in cpu0 or key not in cpu1:
                return "    –"
            return f"{1000.0 * (cpu1[key] - cpu0[key]) / len(frames):5.2f}"

        other = 100.0 * (cpu1.get("other", 0.0) - cpu0.get("other", 0.0)) / max(seconds, 1e-9)
        lines.append(f"  CPU per frame: GUI thread {per_frame('gui')} ms, render thread {per_frame('render')} ms; "
                     f"other threads {other:4.1f} % of one core")
    if graph_ms:
        lines.append(f"  graph data per update: mean {sum(graph_ms) / len(graph_ms):5.2f}  "
                     f"p95 {percentile(graph_ms, 0.95):5.2f} ms  ({len(graph_ms) / seconds:4.1f} updates/s)")
    return "\n".join(lines)
