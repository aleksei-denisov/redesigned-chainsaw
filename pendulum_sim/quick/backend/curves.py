"""One live graph, from telemetry to scene-graph triangles (Qt-free, incremental).

A :class:`GraphState` turns the telemetry window into the vertices of every curve, with
up to two y axes (one per unit), stable scales (``scale.StableScale``) and optional
display smoothing. It works **incrementally**: each curve keeps its decimated points
(in data units) and their triangles (in pixels); a refresh only

* re-decimates the newest samples (columns fixed in time, ``linegeom.decimate_columns``)
  – from the start of the column that may still change, or that the smoothing window of
  the newest samples still reaches;
* strokes those points (plus the one before, whose miter changes);
* drops points that scrolled out of the window;
* moves the whole curve left by the elapsed time (``dx``, applied by the scene graph).

Everything is redrawn only when the scale glides to a new range, when the item's size or
style changes, or after a reset. Curves are stored relative to a reference time
``t_ref`` so float32 vertices stay exact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ...mobile.plot_math import finite_runs, format_tick, nice_ticks, wrap_degrees
from .linegeom import decimate_columns, segment_indices, smooth, stroke_vertices
from .scale import StableScale
from .signals import Signal

SMOOTHING_S = {"off": 0.0, "light": 0.010, "medium": 0.030, "strong": 0.100}
WINDOWS_S = (5, 10, 20, 30)
MAX_SIGNALS = 4
MAX_AXES = 2


def columns_for(width: float, dpr: float) -> int:
    """Decimation columns for an item ``width`` wide: two samples per column, so about one
    sample per physical pixel."""
    return max(int(width * max(dpr, 1.0) / 2.0), 1)


@dataclass
class GraphSpec:
    signals: list[str]
    window_s: float = 10.0
    scale: str = "auto"                                   # auto | symmetric | fixed
    fixed: list[tuple[float, float]] = field(default_factory=list)   # per axis, for "fixed"
    smoothing: str = "off"
    angle_unit: str = "deg"


@dataclass
class Style:
    colors: list[tuple[int, int, int, int]] = field(default_factory=lambda: [(0, 0, 0, 255)])
    width: float = 2.0
    feather: float = 0.5


@dataclass
class Axis:
    unit: str
    lo: float = 0.0
    hi: float = 1.0
    ticks: list[tuple[float, str]] = field(default_factory=list)   # (y pixel, label)


@dataclass
class Output:
    curves: list[tuple[np.ndarray, np.ndarray]]           # per series: vertices (VERTEX), indices
    dx: float
    axes: list[Axis]
    legend: list[dict]
    redrawn: bool


class _Curve:
    """Decimated points of one series and their triangles."""

    def __init__(self) -> None:
        self.reset_points()

    def reset_points(self) -> None:
        self.t = np.zeros(0)
        self.y = np.zeros(0)
        self.brk = np.zeros(0, dtype=bool)
        self.final = -math.inf                           # points before this time are final
        self.verts = np.zeros((0, 4, 3), dtype=np.float32)
        self.stroked = 0                                 # points whose vertices are current


class GraphState:
    def __init__(self, spec: GraphSpec, catalogue: dict[str, Signal], rate_hz: float = 200.0) -> None:
        self.rate_hz = rate_hz
        self.style = Style()
        self.configure(spec, catalogue)

    # ------------------------------------------------------------------ set-up
    def configure(self, spec: GraphSpec, catalogue: dict[str, Signal]) -> None:
        """New spec or structure: everything starts again."""
        self.spec = spec
        self.series: list[Signal] = [catalogue[k] for k in spec.signals[:MAX_SIGNALS]
                                     if k in catalogue and catalogue[k].graphable]
        self.units: list[str] = []
        self.axis_of: list[int] = []
        self.conv: list[float] = []
        spans: dict[int, float] = {}
        for s in self.series:
            scale, unit, span, _dec = s.display(spec.angle_unit)
            if unit not in self.units:
                if len(self.units) >= MAX_AXES:          # a third unit: shares the second axis
                    unit = self.units[-1]
                else:
                    self.units.append(unit)
            a = self.units.index(unit)
            self.axis_of.append(a)
            self.conv.append(scale)
            spans[a] = max(spans.get(a, 0.0), span)
        self.scales = []
        for a, unit in enumerate(self.units):
            fixed = spec.fixed[a] if a < len(spec.fixed) else None
            self.scales.append(StableScale(spec.scale, spans.get(a, 1.0), fixed))
        self.curves = [_Curve() for _ in self.series]
        self._geom: tuple | None = None
        self._ticks: dict[int, tuple] = {}
        self._t_ref = 0.0
        self._last_t = -math.inf

    def set_style(self, style: Style) -> None:
        self.style = style
        for c in self.curves:
            c.stroked = 0

    def columns(self) -> list[str]:
        return [s.key for s in self.series]

    @property
    def legend(self) -> list[dict]:
        return [{"index": k, "key": s.key, "label": s.label, "axis": self.axis_of[k], "dashed": s.dashed,
                 "unit": self.units[self.axis_of[k]]} for k, s in enumerate(self.series)]

    # ------------------------------------------------------------------ update
    def update(self, data: dict[str, np.ndarray], width: float, height: float, dpr: float, now: float) -> Output:
        t = data.get("t", np.zeros(0))
        if t.size == 0 or width < 2 or height < 2 or not self.series:
            for c in self.curves:
                c.reset_points()
            return Output([(np.zeros(0), np.zeros(0, dtype=np.uint32)) for _ in self.series], 0.0,
                          [Axis(u) for u in self.units], self.legend, True)
        window = float(self.spec.window_s)
        col_s = window / columns_for(width, dpr)
        smooth_s = SMOOTHING_S.get(self.spec.smoothing, 0.0)
        smooth_n = int(round(smooth_s * self.rate_hz)) | 1 if smooth_s > 0 else 1
        t_last = float(t[-1])
        geom = (round(width, 2), round(dpr, 3), window, smooth_n)
        if geom != self._geom or t_last < self._last_t:  # size, window, smoothing or a reset
            self._geom = geom
            for c in self.curves:
                c.reset_points()
        self._last_t = t_last
        t0 = t_last - window
        for k, s in enumerate(self.series):
            self._extend(self.curves[k], t, data.get(s.key), self.conv[k], s.wrap, col_s, smooth_n,
                         t_last, t0 - col_s)
        # scales from the visible points
        axes = []
        moving = False
        for a, scale in enumerate(self.scales):
            dmin, dmax = math.inf, -math.inf
            for k, c in enumerate(self.curves):
                if self.axis_of[k] == a and c.t.size:
                    vis = c.y[c.t >= t0]
                    if vis.size:
                        dmin, dmax = min(dmin, float(vis.min())), max(dmax, float(vis.max()))
            if not math.isfinite(dmin):
                dmin = dmax = math.nan
            lo, hi = scale.update(dmin, dmax, now)
            moving |= scale.moving
            axes.append(Axis(self.units[a], lo, hi))
        sx = width / window
        redraw = moving or (t_last - self._t_ref) * sx > 1e5 or any(c.stroked == 0 for c in self.curves) \
            or self._mapping != (round(height, 2), sx, tuple((ax.lo, ax.hi) for ax in axes))
        if redraw:
            self._t_ref = t_last
            self._mapping = (round(height, 2), sx, tuple((ax.lo, ax.hi) for ax in axes))
            for c in self.curves:
                c.stroked = 0
        out = []
        for k, c in enumerate(self.curves):
            ax = axes[self.axis_of[k]]
            out.append(self._stroke(c, k, sx, ax.lo, ax.hi, height))
        for a, ax in enumerate(axes):
            key = (ax.lo, ax.hi, round(height, 2))
            cached = self._ticks.get(a)
            if cached is None or cached[0] != key:
                ticks, step = nice_ticks(ax.lo, ax.hi)
                sy = height / (ax.hi - ax.lo) if ax.hi > ax.lo else 0.0
                cached = (key, [((ax.hi - v) * sy, format_tick(v, step)) for v in ticks])
                self._ticks[a] = cached
            ax.ticks = cached[1]
        return Output(out, width - (t_last - self._t_ref) * sx, axes, self.legend, redraw)

    _mapping: tuple = ()

    def _extend(self, c: _Curve, t: np.ndarray, col: np.ndarray | None, conv: float, wrap: bool,
                col_s: float, smooth_n: int, t_last: float, t_keep: float) -> None:
        """Bring the curve's points up to date with the telemetry."""
        if col is None:
            c.reset_points()
            return
        cut = math.floor(c.final / col_s) * col_s if math.isfinite(c.final) else -math.inf
        start = int(np.searchsorted(t, cut, side="left"))
        ctx = start - (smooth_n // 2) - 1 if math.isfinite(cut) else 0     # samples the smoothing needs
        ctx = max(ctx, 0)
        tt = t[ctx:]
        y = col[ctx:] * conv
        if wrap:
            y = wrap_degrees(y)
        new_t, new_y, new_b = [], [], []
        for sl in finite_runs(y, wrap=wrap):
            rt, ry = tt[sl], y[sl]
            if smooth_n > 1:
                ry = smooth(ry, smooth_n)
            sel = rt >= cut
            if not sel.any():
                continue
            continues = math.isfinite(cut) and not sel[0]   # the run began before the cut
            dt, dy = decimate_columns(rt[sel], ry[sel], col_s)
            b = np.zeros(dt.size, dtype=bool)
            b[0] = not continues
            new_t.append(dt)
            new_y.append(dy)
            new_b.append(b)
        keep = int(np.searchsorted(c.t, cut, side="left"))
        # a run that continues across the cut joins the point before it
        if new_t and keep and new_b[0][0] and math.isfinite(cut):
            first_new = new_t[0][0]
            raw_prev = int(np.searchsorted(t, first_new, side="left")) - 1
            if raw_prev >= 0 and np.isfinite(col[raw_prev]):
                pv = col[raw_prev] * conv
                pv = float(wrap_degrees(np.array([pv]))[0]) if wrap else pv
                if not (wrap and abs(pv - new_y[0][0]) > 180.0):
                    new_b[0][0] = False
        nt = np.concatenate([c.t[:keep]] + new_t) if new_t else c.t[:keep]
        ny = np.concatenate([c.y[:keep]] + new_y) if new_t else c.y[:keep]
        nb = np.concatenate([c.brk[:keep]] + new_b) if new_t else c.brk[:keep]
        c.stroked = min(c.stroked, max(keep - 1, 0))     # the last kept point's miter changes
        drop = int(np.searchsorted(nt, t_keep, side="left"))
        drop = max(drop - 1, 0)                          # keep one point left of the window
        if drop:
            nt, ny, nb = nt[drop:], ny[drop:], nb[drop:]
            if nb.size:
                nb = nb.copy()
                nb[0] = True
            c.verts = c.verts[drop:]
            c.stroked = max(c.stroked - drop, 0)
        c.t, c.y, c.brk = nt, ny, nb
        c.final = math.floor((t_last - 0.5 * smooth_n / self.rate_hz) / col_s) * col_s

    def _stroke(self, c: _Curve, k: int, sx: float, lo: float, hi: float, height: float):
        n = c.t.size
        if n == 0:
            c.verts = np.zeros((0, 4, 3), dtype=np.float32)
            return np.zeros(0), np.zeros(0, dtype=np.uint32)
        sy = height / (hi - lo) if hi > lo else 0.0
        x = (c.t - self._t_ref) * sx
        y = (hi - c.y) * sy
        st = self.style
        color = st.colors[k % len(st.colors)] if st.colors else (0, 0, 0, 255)
        first = max(c.stroked - 1, 0)                   # re-stroke from one point before (its miter)
        lead = 1 if first > 0 else 0
        part = stroke_vertices(x[first - lead:], y[first - lead:], st.width, st.feather, color,
                               c.brk[first - lead:]).view(np.float32).reshape(-1, 4, 3)
        c.verts = np.concatenate((c.verts[:first], part[lead:])) if first else part
        c.stroked = n
        dash = 4.0 * st.width if self.series[k].dashed else 0.0
        indices = segment_indices(x, y, dash, c.brk)
        return c.verts.reshape(-1).view(np.dtype([("x", np.float32), ("y", np.float32), ("rgba", np.uint8, 4)])), indices
