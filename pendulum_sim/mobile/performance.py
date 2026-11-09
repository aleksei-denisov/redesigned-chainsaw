"""Rendering-performance settings and measurements for the mobile interface.

High-density tablets (e.g. 2880×1800 at a device-pixel ratio of 2) make Qt's
software rasteriser paint four times as many pixels as a desktop window of the
same logical size. Physics is cheap in comparison (≈0.05 ms per step), so the
knobs here are all about drawing:

* the pendulum view's background grid is cached (see ``touch_view``), which removed
  ~85 % of its drawing time; the remaining knobs are:
* **render resolution** of the pendulum view – the scene is painted into an image
  at a reduced pixel ratio and scaled up (``native`` = no reduction, the default);
* **graph animation rate** – how often the scrolling graphs take in new samples
  (60, 30 or 15 per second; each step draws only the new samples);
* **frame rate** – 60 or 30 fps; 30 halves the drawing work on slow devices;
* **antialiasing** of the pendulum view.

:class:`FrameStats` collects what the on-screen read-out shows: display fps,
the longest frame gap in the last second, and time per frame spent in physics,
the pendulum view and the graphs.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, fields

from PySide6.QtCore import QSettings

# device pixels per logical pixel used to draw the pendulum view (None = the screen's own)
RENDER_SCALES = {"native": None, "high": 1.5, "balanced": 1.25, "fast": 1.0}
GRAPH_RATES_HZ = [60, 30, 15]     # graph animation (scrolling) rate; ≤ the frame rate
RANGE_CHECK_S = 0.1               # y-range bookkeeping and the status read-outs (10 Hz)
FRAME_RATES = [60, 30]


@dataclass
class PerformanceSettings:
    render: str = "native"
    graph_fps: int = 60           # graph scrolling rate (new samples drawn per second)
    fps: int = 60
    antialias: bool = True
    debug_overlay: bool = False   # timing read-out on the pendulum view
    log_stats: bool = False       # one timing line per second on stdout (adb logcat)
    fast_cores: bool = True       # keep the simulation/UI thread on the performance cores

    @property
    def render_scale(self) -> float | None:
        return RENDER_SCALES.get(self.render)

    @property
    def frame_interval_ms(self) -> int:
        return max(1, int(round(1000 / self.fps)))

    @property
    def plot_interval_s(self) -> float:
        """Interval of the window's slow refresh (graph ranges, read-outs)."""
        return RANGE_CHECK_S

    @property
    def graph_interval_s(self) -> float:
        return 1.0 / max(min(self.graph_fps, self.fps), 1)

    # -------------------------------------------------------------- persistence
    @classmethod
    def load(cls, settings: QSettings | None = None) -> "PerformanceSettings":
        settings = settings or QSettings()
        out = cls()
        for f in fields(cls):
            key = f"performance/{f.name}"
            if settings.contains(key):
                raw = settings.value(key)
                try:
                    value = (str(raw).lower() in ("1", "true")) if f.type in (bool, "bool") else \
                        int(raw) if f.type in (int, "int") else str(raw)
                except (TypeError, ValueError):
                    continue
                setattr(out, f.name, value)
        out.render = out.render if out.render in RENDER_SCALES else cls.render
        out.graph_fps = out.graph_fps if out.graph_fps in GRAPH_RATES_HZ else cls.graph_fps
        out.fps = out.fps if out.fps in FRAME_RATES else cls.fps
        return out

    def save(self, settings: QSettings | None = None) -> None:
        settings = settings or QSettings()
        for name, value in asdict(self).items():
            settings.setValue(f"performance/{name}", value)
        settings.sync()


class Smoothed:
    """Exponentially smoothed value (per-frame milliseconds)."""

    __slots__ = ("value", "alpha")

    def __init__(self, alpha: float = 0.1) -> None:
        self.value = 0.0
        self.alpha = alpha

    def add(self, sample: float) -> None:
        self.value += self.alpha * (sample - self.value)


class FrameStats:
    """Per-frame timings for the read-out and the performance test. Every call is O(1).

    Between two frames the window adds what it spent on: the graphs' data work
    (``add_graph``, with the number of samples drawn), the graphs' painting
    (``add_graph_paint``), the pendulum view (``add_view``) and the whole screen
    update – painting plus composing and handing the frame to the display
    (``add_update``). ``frame()`` closes the interval; once per second the averages
    are published. While ``recording`` is a list, one tuple per frame is appended:
    ``(gap_ms, physics_ms, view_ms, graph_data_ms, graph_paint_ms, update_ms, points)``.
    """

    def __init__(self) -> None:
        self.view_ms = Smoothed()
        self.graph_ms = 0.0               # graph data + painting per frame (1 s average)
        self.graph_data_ms = 0.0
        self.graph_paint_ms = 0.0
        self.update_ms = 0.0              # whole screen update per frame (1 s average)
        self.points_per_s = 0.0
        self._acc = [0.0, 0.0, 0.0, 0]    # graph data, graph paint, update, points (per second)
        self._cur = [0.0, 0.0, 0.0, 0.0, 0]   # view, graph data, graph paint, update, points (per frame)
        self._frames = 0
        self._window_start = time.perf_counter()
        self._worst_gap = 0.0
        self.fps = 0.0
        self.worst_gap_ms = 0.0
        self.recording: list | None = None
        self.physics_source = None        # callable → physics ms of the last frame

    def add_view(self, ms: float) -> None:
        self.view_ms.add(ms)
        self._cur[0] += ms

    def add_graph(self, ms: float, points: int = 0) -> None:
        self._acc[0] += ms
        self._acc[3] += points
        self._cur[1] += ms
        self._cur[4] += points

    def add_graph_paint(self, ms: float) -> None:
        self._acc[1] += ms
        self._cur[2] += ms

    def add_update(self, ms: float) -> None:
        self._acc[2] += ms
        self._cur[3] += ms

    on_second = None   # optional callback(self) once per measurement window

    def frame(self, gap_s: float) -> None:
        self._frames += 1
        self._worst_gap = max(self._worst_gap, gap_s)
        if self.recording is not None:
            phys = self.physics_source() if self.physics_source is not None else 0.0
            v, gd, gp, up, pts = self._cur
            self.recording.append((gap_s * 1000.0, phys, v, gd, gp, up, pts))
        self._cur = [0.0, 0.0, 0.0, 0.0, 0]
        now = time.perf_counter()
        span = now - self._window_start
        if span >= 1.0:
            n = max(self._frames, 1)
            self.fps = self._frames / span
            self.worst_gap_ms = self._worst_gap * 1000.0
            self.graph_data_ms = self._acc[0] / n
            self.graph_paint_ms = self._acc[1] / n
            self.graph_ms = self.graph_data_ms + self.graph_paint_ms
            self.update_ms = self._acc[2] / n
            self.points_per_s = self._acc[3] / span
            self._frames, self._worst_gap = 0, 0.0
            self._acc = [0.0, 0.0, 0.0, 0]
            self._window_start = now
            if self.on_second is not None:
                self.on_second(self)


_graph_hooked = False


def instrument_graphs(stats: FrameStats) -> None:
    """Time every pyqtgraph view repaint (pyqtgraph's ``GraphicsView.paintEvent`` is Python)."""
    global _graph_hooked
    if _graph_hooked:
        return
    import pyqtgraph as pg

    original = pg.GraphicsView.paintEvent

    def timed_paint(self, ev):  # noqa: ANN001 - Qt event
        t = time.perf_counter()
        try:
            return original(self, ev)
        finally:
            stats.add_graph_paint((time.perf_counter() - t) * 1000.0)

    pg.GraphicsView.paintEvent = timed_paint
    _graph_hooked = True
