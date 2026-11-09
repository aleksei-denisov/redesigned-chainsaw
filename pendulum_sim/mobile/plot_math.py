"""Qt-free numerics for the mobile strip charts (unit-tested without a GUI).

* :func:`window_slice` – the samples inside the visible time window (views, no copies);
* :func:`downsample_minmax` – reduce a series to at most two points per pixel column
  (minimum and maximum), so every peak stays visible however many samples there are;
* :func:`split_wraps` – break a wrapped angle trace where it jumps by more than 180°;
* :func:`nice_ticks` / :func:`format_tick` – readable axis ticks without scientific
  notation;
* :class:`AxisRange` – automatic y range with a per-signal **minimum span** (no
  ``×1e-15`` artefacts on near-zero signals), immediate expansion and delayed,
  stepwise shrinking so the axis does not flicker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def window_slice(t: np.ndarray, window_s: float) -> slice:
    """Slice of samples with ``t >= t[-1] - window_s`` plus one sample before (continuity)."""
    if t.size == 0:
        return slice(0, 0)
    start = int(np.searchsorted(t, t[-1] - window_s, side="left"))
    return slice(max(0, start - 1), t.size)


def downsample_minmax(x: np.ndarray, y: np.ndarray, x0: float, x1: float,
                      columns: int) -> tuple[np.ndarray, np.ndarray]:
    """Min/max decimation of a time-ordered series to ``columns`` pixel columns.

    Returns the input unchanged when it has at most ``2 * columns`` samples; otherwise two
    points (min, max) per occupied column at the column's first time stamp. NaNs are
    ignored inside a column (a column of only NaNs yields NaN).
    """
    n = x.size
    if n <= 2 * max(columns, 1) or x1 <= x0:
        return x, y
    col = ((x - x0) * (columns / (x1 - x0))).astype(np.int64)
    np.clip(col, 0, columns - 1, out=col)
    starts = np.flatnonzero(np.r_[True, col[1:] != col[:-1]])
    with np.errstate(invalid="ignore"):
        lo = np.fmin.reduceat(y, starts)
        hi = np.fmax.reduceat(y, starts)
    xs = np.repeat(x[starts], 2)
    ys = np.empty(xs.size, dtype=np.float64)
    ys[0::2] = lo
    ys[1::2] = hi
    return xs, ys


def wrap_degrees(a: np.ndarray) -> np.ndarray:
    return (a + 180.0) % 360.0 - 180.0


def split_wraps(y: np.ndarray, jump: float = 180.0) -> list[slice]:
    """Index ranges between wrap-around jumps (for drawing each as its own polyline)."""
    if y.size < 2:
        return [slice(0, y.size)]
    cuts = np.flatnonzero(np.abs(np.diff(y)) > jump) + 1
    edges = [0, *cuts.tolist(), y.size]
    return [slice(a, b) for a, b in zip(edges[:-1], edges[1:]) if b - a > 0]


def nice_step(span: float, max_ticks: int) -> float:
    if not math.isfinite(span) or span <= 0:
        return 1.0
    raw = span / max(max_ticks, 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= m * mag + 1e-12 * mag:
            return m * mag
    return 10.0 * mag


def nice_ticks(lo: float, hi: float, max_ticks: int = 5) -> tuple[list[float], float]:
    """Tick values within [lo, hi] at a 1/2/2.5/5·10ⁿ step, and the step."""
    step = nice_step(hi - lo, max_ticks)
    first = math.ceil(lo / step - 1e-9) * step
    ticks = []
    v = first
    while v <= hi + 1e-9 * step and len(ticks) < 50:
        ticks.append(0.0 if abs(v) < 1e-9 * step else v)
        v += step
    return ticks, step


def tick_decimals(step: float) -> int:
    if step <= 0 or not math.isfinite(step):
        return 0
    d = max(0, -math.floor(math.log10(step) + 1e-9))
    if abs(step * 10**d - round(step * 10**d)) > 1e-6:   # 2.5 → one more digit
        d += 1
    return min(d, 6)


def format_tick(value: float, step: float) -> str:
    text = f"{value:.{tick_decimals(step)}f}"
    return "0" if text.strip("-0.") == "" else text


@dataclass
class AxisRange:
    """Auto y-range: padded, at least ``min_span`` wide, snapped to nice values.

    Expands at once when data leave the range; shrinks only after the data have used
    less than ``shrink_ratio`` of the span for ``shrink_delay_s`` (no flicker).
    """

    min_span: float
    pad: float = 0.08
    shrink_ratio: float = 0.35
    shrink_delay_s: float = 1.5
    lo: float = math.nan
    hi: float = math.nan
    _small_since: float | None = None

    def reset(self) -> None:
        self.lo = self.hi = math.nan
        self._small_since = None

    def _target(self, dmin: float, dmax: float) -> tuple[float, float]:
        centre = 0.5 * (dmin + dmax)
        span = max(dmax - dmin, self.min_span) * (1.0 + 2.0 * self.pad)
        lo, hi = centre - 0.5 * span, centre + 0.5 * span
        step = nice_step(hi - lo, 4)
        return math.floor(lo / step) * step, math.ceil(hi / step) * step

    def update(self, dmin: float, dmax: float, now: float) -> tuple[float, float]:
        if not (math.isfinite(dmin) and math.isfinite(dmax)):
            if math.isnan(self.lo):
                self.lo, self.hi = -0.5 * self.min_span, 0.5 * self.min_span
            return self.lo, self.hi
        if math.isnan(self.lo):
            self.lo, self.hi = self._target(dmin, dmax)
            return self.lo, self.hi
        if dmin < self.lo or dmax > self.hi:              # data left the range: expand now
            lo, hi = self._target(dmin, dmax)
            self.lo, self.hi = min(lo, self.lo), max(hi, self.hi)
            self._small_since = None
        elif max(dmax - dmin, self.min_span) < self.shrink_ratio * (self.hi - self.lo):
            if self._small_since is None:
                self._small_since = now
            elif now - self._small_since >= self.shrink_delay_s:
                self.lo, self.hi = self._target(dmin, dmax)
                self._small_since = None
        else:
            self._small_since = None
        return self.lo, self.hi


def finite_extent(arrays: list[np.ndarray]) -> tuple[float, float]:
    """(min, max) over the finite values of all arrays; (nan, nan) if there are none."""
    lo, hi = math.inf, -math.inf
    for a in arrays:
        finite = a[np.isfinite(a)]
        if finite.size:
            lo, hi = min(lo, float(finite.min())), max(hi, float(finite.max()))
    return (lo, hi) if lo <= hi else (math.nan, math.nan)


# ------------------------------------------------------------------ catalog
SEGMENT_COLORS = ["#4FA3FF", "#FFA14A", "#4CD07D"]      # θ1 θ2 θ3 (bright for the dark theme)
AUX_COLORS = ["#E0E0E0", "#C792EA", "#FF6B6B", "#7FDBCA"]


@dataclass(frozen=True)
class Series:
    label: str
    column: str
    scale: float = 1.0
    color: str = "#E0E0E0"
    wrap: bool = False        # wrap to (−180°, 180°]
    dashed: bool = False


@dataclass(frozen=True)
class GraphDef:
    key: str
    title: str
    unit: str
    min_span: float


GRAPHS: dict[str, GraphDef] = {g.key: g for g in (
    GraphDef("angle", "Angle", "deg", 2.0),
    GraphDef("angular_velocity", "Angular velocity", "deg/s", 20.0),
    GraphDef("cart_position", "Cart position", "m", 0.05),
    GraphDef("cart_velocity", "Cart velocity", "m/s", 0.1),
    GraphDef("control", "Control force", "N / N·m", 1.0),
    GraphDef("error", "Control error", "deg", 2.0),
    GraphDef("pid", "PID components", "output", 0.5),
    GraphDef("energy", "Energy", "J", 0.02),
)}
DEFAULT_SELECTION = ["angle", "control"]
MAX_VISIBLE = 4


def graph_available(key: str, n_segments: int, has_cart: bool) -> bool:
    return has_cart or key not in ("cart_position", "cart_velocity")


def graph_series(key: str, n_segments: int, loop_labels: list[str], loop: int = 0) -> list[Series]:
    """Curves of graph ``key`` for the current plant/controller structure."""
    deg = 180.0 / math.pi
    if key == "angle":
        return [Series(f"θ{i + 1}", f"theta{i + 1}", deg, SEGMENT_COLORS[i], wrap=True) for i in range(n_segments)]
    if key == "angular_velocity":
        return [Series(f"ω{i + 1}", f"omega{i + 1}", deg, SEGMENT_COLORS[i]) for i in range(n_segments)]
    if key == "cart_position":
        return [Series("x", "x", 1.0, AUX_COLORS[0])]
    if key == "cart_velocity":
        return [Series("ẋ", "xd", 1.0, AUX_COLORS[0])]
    if key == "control":
        return [Series("command", "command", 1.0, AUX_COLORS[1]),
                Series("actuator", "actuator", 1.0, AUX_COLORS[0]),
                Series("push", "tip_force", 1.0, AUX_COLORS[2], dashed=True)]
    if key == "error":
        out = []
        for k, label in enumerate(loop_labels):
            if label != "cart":
                idx = len(out)
                out.append(Series(f"e {label}", f"error{k}", deg, SEGMENT_COLORS[idx % 3]))
        return out
    if key == "pid":
        if not loop_labels:
            return []
        k = max(0, min(loop, len(loop_labels) - 1))
        return [Series("P", f"p{k}", 1.0, AUX_COLORS[3]), Series("I", f"i{k}", 1.0, AUX_COLORS[1]),
                Series("D", f"d{k}", 1.0, AUX_COLORS[2])]
    if key == "energy":
        return [Series("kinetic", "ke", 1.0, AUX_COLORS[2]), Series("potential", "pe", 1.0, AUX_COLORS[3]),
                Series("total", "energy", 1.0, AUX_COLORS[0])]
    raise KeyError(key)


# ------------------------------------------------------- scrolling strip charts
def ring_runs(first: int, last: int, ring0: int, width: int) -> list[tuple[int, int]]:
    """Image column ranges ``(start, length)`` holding absolute columns ``first..last``.

    A scrolling chart keeps its trace in a circular image of ``width`` columns; absolute
    pixel column ``c`` lives at image column ``(c - ring0) mod width``. The result has
    at most two runs (split where the ring wraps); empty when ``last < first``.
    """
    n = last - first + 1
    if n <= 0 or width <= 0:
        return []
    if n >= width:
        return [(0, width)]
    start = (first - ring0) % width
    if start + n <= width:
        return [(start, n)]
    return [(start, width - start), (0, n - (width - start))]


def ring_blits(newest: int, ring0: int, width: int) -> list[tuple[int, int, int]]:
    """``(src_x, length, dst_x)`` copies that put absolute column ``newest`` at the right edge.

    The oldest visible column (``newest - width + 1``) lands at ``dst_x = 0``.
    """
    head = (newest - ring0) % width              # image column of the newest data
    first = head + 1                              # image column of the oldest data
    if first >= width:
        return [(0, width, 0)]
    return [(first, width - first, 0), (0, first, width - first)]


def finite_runs(y: np.ndarray, wrap: bool = False, jump: float = 180.0) -> list[slice]:
    """Index ranges of consecutive finite values (also split at wrap-around jumps)."""
    n = y.size
    if n == 0:
        return []
    ok = np.isfinite(y)
    brk = np.zeros(n, dtype=bool)             # True where a new run must start at i
    brk[0] = True
    brk[1:] = ~ok[:-1]
    if wrap and n > 1:
        with np.errstate(invalid="ignore"):
            brk[1:] |= np.abs(np.diff(y)) > jump
    starts = np.flatnonzero(ok & brk)
    if starts.size == 0:
        return []
    # each run ends before the next start or the next non-finite value
    stops = np.empty_like(starts)
    bad = np.flatnonzero(~ok)
    nxt_bad = np.searchsorted(bad, starts, side="left")
    limit = np.where(nxt_bad < bad.size, bad[np.minimum(nxt_bad, max(bad.size - 1, 0))] if bad.size else n, n)
    stops[:-1] = np.minimum(starts[1:], limit[:-1])
    stops[-1] = limit[-1]
    return [slice(int(a), int(b)) for a, b in zip(starts, stops) if b > a]
