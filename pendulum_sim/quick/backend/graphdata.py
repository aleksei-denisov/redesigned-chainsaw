"""Graph data for the Qt Quick screens: telemetry window → decimated pixel polylines (Qt-free).

Uses the chart maths of the Widgets app (``mobile/plot_math.py``: graph catalogue,
automatic y range with a minimum span, nice ticks), so both apps show the same axes. The
curves are decimated with ``linegeom.decimate`` at the display's physical resolution
(real samples, at most about one per physical pixel), which keeps them smooth on a
high-density screen. Each series becomes a list of *runs* –
pieces without gaps or wrap-around jumps – which ``GraphLines`` (``lineitem.py``) draws.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ...mobile.plot_math import (
    GRAPHS,
    AxisRange,
    finite_runs,
    graph_available,
    graph_series,
    nice_ticks,
    wrap_degrees,
)
from .linegeom import decimate

Run = tuple[np.ndarray, np.ndarray]          # pixel x, pixel y


@dataclass
class GraphFrame:
    """One refresh of one graph, in item pixels (y grows downwards)."""

    lo: float = 0.0
    hi: float = 1.0
    ticks: list[float] = field(default_factory=list)
    step: float = 1.0
    series: list[list[Run]] = field(default_factory=list)


def available_graphs(n_segments: int, has_cart: bool) -> list[str]:
    return [key for key in GRAPHS if graph_available(key, n_segments, has_cart)]


def columns(keys: list[str], n_segments: int, loop_labels: list[str], loop: int) -> list[str]:
    """Telemetry columns the graphs ``keys`` need (``t`` first)."""
    out = ["t"]
    for key in keys:
        for s in graph_series(key, n_segments, loop_labels, loop):
            if s.column not in out:
                out.append(s.column)
    return out


def columns_for(width: float, dpr: float) -> int:
    """Decimation columns for an item ``width`` wide: two samples per column, so about one
    sample per physical pixel."""
    return max(int(width * max(dpr, 1.0) / 2.0), 1)


def _runs(t: np.ndarray, y: np.ndarray, wrap: bool, t0: float, t1: float, width: int) -> list[Run]:
    start = int(np.searchsorted(t, t0, side="left"))       # only the visible window
    t, y = t[start:], y[start:]
    runs = []
    for sl in finite_runs(y, wrap=wrap):
        xs, ys = decimate(t[sl], y[sl], t0, t1, width)
        if xs.size:
            runs.append((xs, ys))
    return runs


def _to_pixels(runs: list[Run], t0: float, window: float, lo: float, hi: float,
               width: float, height: float) -> list[Run]:
    sx = width / window
    sy = height / (hi - lo) if hi > lo else 0.0
    return [((xs - t0) * sx, height - (ys - lo) * sy) for xs, ys in runs]


def graph_frame(key: str, data: dict[str, np.ndarray], n_segments: int, loop_labels: list[str], loop: int,
                window_s: float, width: float, height: float, axis: AxisRange, now: float,
                dpr: float = 1.0) -> GraphFrame:
    """Curves of graph ``key`` over the last ``window_s`` seconds of ``data``."""
    series = graph_series(key, n_segments, loop_labels, loop)
    t = data.get("t", np.zeros(0))
    if t.size == 0 or width < 2 or height < 2:
        return GraphFrame(series=[[] for _ in series])
    t1 = float(t[-1])
    t0 = t1 - window_s
    columns_px = columns_for(width, dpr)
    raw: list[list[Run]] = []
    lo, hi = np.inf, -np.inf
    for s in series:
        y = data[s.column] * s.scale
        if s.wrap:
            y = wrap_degrees(y)
        runs = _runs(t, y, s.wrap, t0, t1, columns_px)
        raw.append(runs)
        for xs, ys in runs:
            visible = ys[xs >= t0]
            if visible.size:
                lo, hi = min(lo, float(visible.min())), max(hi, float(visible.max()))
    if not np.isfinite(lo):
        lo = hi = float("nan")
    lo, hi = axis.update(lo, hi, now)
    ticks, step = nice_ticks(lo, hi)
    return GraphFrame(lo, hi, ticks, step, [_to_pixels(r, t0, window_s, lo, hi, width, height) for r in raw])


TRACE = (("theta1", 180.0 / np.pi, True, 2.0), ("x", 1.0, False, 0.05))   # column, scale, wrap, min span


def trace_frame(data: dict[str, np.ndarray], window_s: float, width: float, height: float,
                axes: list[AxisRange], now: float, has_cart: bool = True, dpr: float = 1.0) -> GraphFrame:
    """The Simulate screen's compact trace: θ1 and cart x, each on its own scale."""
    t = data.get("t", np.zeros(0))
    frame = GraphFrame(series=[[], []])
    if t.size == 0 or width < 2 or height < 2:
        return frame
    t1 = float(t[-1])
    t0 = t1 - window_s
    for k, (column, scale, wrap, _span) in enumerate(TRACE):
        if column == "x" and not has_cart:
            continue
        y = data[column] * scale
        if wrap:
            y = wrap_degrees(y)
        runs = _runs(t, y, wrap, t0, t1, columns_for(width, dpr))
        values = [ys[xs >= t0] for xs, ys in runs]
        values = [v for v in values if v.size]
        dmin = min((float(v.min()) for v in values), default=float("nan"))
        dmax = max((float(v.max()) for v in values), default=float("nan"))
        lo, hi = axes[k].update(dmin, dmax, now)
        frame.series[k] = _to_pixels(runs, t0, window_s, lo, hi, width, height)
    return frame


def trace_axes() -> list[AxisRange]:
    return [AxisRange(min_span=span) for _c, _s, _w, span in TRACE]


def graph_axis(key: str) -> AxisRange:
    return AxisRange(min_span=GRAPHS[key].min_span)
