"""Rendering-performance settings and measurements for the mobile interface.

High-density tablets (e.g. 2880×1800 at a device-pixel ratio of 2) make Qt's
software rasteriser paint four times as many pixels as a desktop window of the
same logical size. Physics is cheap in comparison (≈0.05 ms per step), so the
knobs here are all about drawing:

* **render resolution** of the pendulum view – the scene is painted into an image
  at a reduced pixel ratio and scaled up (``native`` = no reduction);
* **graph refresh rate** – the upper limit; the window still lowers it
  automatically when frames get slow (same logic as the desktop);
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
GRAPH_RATES_HZ = [15, 10, 5, 2]
FRAME_RATES = [60, 30]


@dataclass
class PerformanceSettings:
    render: str = "balanced"
    graph_hz: int = 10
    fps: int = 60
    antialias: bool = True
    show_stats: bool = True

    @property
    def render_scale(self) -> float | None:
        return RENDER_SCALES.get(self.render)

    @property
    def frame_interval_ms(self) -> int:
        return max(1, int(round(1000 / self.fps)))

    @property
    def plot_interval_s(self) -> float:
        return 1.0 / max(self.graph_hz, 1)

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
        out.graph_hz = out.graph_hz if out.graph_hz in GRAPH_RATES_HZ else cls.graph_hz
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
    """Per-frame timings for the read-out. Every ``add_*`` call is O(1)."""

    def __init__(self) -> None:
        self.view_ms = Smoothed()
        self.graph_ms = 0.0               # graph painting per second → shown per frame
        self._graph_accum = 0.0
        self._frames = 0
        self._window_start = time.perf_counter()
        self._worst_gap = 0.0
        self.fps = 0.0
        self.worst_gap_ms = 0.0

    def add_view(self, ms: float) -> None:
        self.view_ms.add(ms)

    def add_graph(self, ms: float) -> None:
        self._graph_accum += ms

    def frame(self, gap_s: float) -> None:
        self._frames += 1
        self._worst_gap = max(self._worst_gap, gap_s)
        now = time.perf_counter()
        span = now - self._window_start
        if span >= 1.0:
            self.fps = self._frames / span
            self.worst_gap_ms = self._worst_gap * 1000.0
            self.graph_ms = self._graph_accum / max(self._frames, 1)
            self._frames, self._worst_gap, self._graph_accum = 0, 0.0, 0.0
            self._window_start = now


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
            stats.add_graph((time.perf_counter() - t) * 1000.0)

    pg.GraphicsView.paintEvent = timed_paint
    _graph_hooked = True
