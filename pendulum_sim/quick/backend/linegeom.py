"""Line geometry for the Qt Quick graphs (Qt-free, NumPy only).

**Decimation** (:func:`decimate`). A curve is reduced to the samples that matter at the
display's resolution: per *physical* pixel column the sample with the smallest and the
one with the largest value, kept at their own times and in time order. The curve stays a
polyline through real samples – no staircase – and a short peak keeps its true height.
With no more than two samples per column the input is used as it is.

**Stroking** (:func:`stroke`). A polyline becomes triangles for the scene graph: a core
band of the line width and a one-pixel feather on each side whose colour fades to
transparent, which antialiases the edges without multisampling. Each point gets four
vertices (outer left, inner left, inner right, outer right), offset along the miter
direction of its two segments (limited, so sharp spikes stay narrow); each segment
adds three quads (18 indices). Colours are premultiplied, as the scene graph's vertex
colour material expects. Dashed series drop every other dash of segments.
"""

from __future__ import annotations

import numpy as np

VERTEX = np.dtype([("x", np.float32), ("y", np.float32), ("rgba", np.uint8, 4)])
MITER_LIMIT = 2.0
# two triangles for each of the three quads between point i (vertices 0..3) and i + 1 (4..7)
_QUADS = np.array([0, 1, 4, 1, 5, 4,          # left feather
                   1, 2, 5, 2, 6, 5,          # core
                   2, 3, 6, 3, 7, 6],         # right feather
                  dtype=np.uint32)


def decimate(t: np.ndarray, y: np.ndarray, t0: float, t1: float, columns: int) -> tuple[np.ndarray, np.ndarray]:
    """The samples of ``(t, y)`` to draw in ``columns`` pixel columns spanning ``[t0, t1]``.

    ``t`` is increasing and ``y`` has no NaNs (draw NaN-free runs separately).
    """
    n = t.size
    columns = max(int(columns), 1)
    if n <= 2 * columns or t1 <= t0:
        return t, y
    col = ((t - t0) * (columns / (t1 - t0))).astype(np.int64)
    np.clip(col, 0, columns - 1, out=col)
    starts = np.flatnonzero(np.r_[True, col[1:] != col[:-1]])
    counts = np.diff(np.r_[starts, n])
    lo = np.minimum.reduceat(y, starts)
    hi = np.maximum.reduceat(y, starts)
    owner = np.repeat(np.arange(starts.size), counts)          # column index (0..k-1) of each sample
    keep = np.zeros(n, dtype=bool)
    # first sample per column equal to its minimum / maximum
    for target in (lo, hi):
        hit = np.flatnonzero(y == target[owner])
        first = hit[np.r_[True, owner[hit][1:] != owner[hit][:-1]]]
        keep[first] = True
    keep[0] = keep[-1] = True                                  # the curve reaches the window's ends
    return t[keep], y[keep]


def _rgba_as_float(rgba: tuple[int, int, int, int]) -> np.float32:
    """The four colour bytes as the float32 with the same bit pattern (for one-pass filling)."""
    return np.frombuffer(bytes(bytearray(rgba)), dtype=np.float32)[0]


_INDEX_CACHE: dict[int, np.ndarray] = {}


def _indices(segments: int) -> np.ndarray:
    cached = _INDEX_CACHE.get(segments)
    if cached is None:
        cached = (np.arange(segments, dtype=np.uint32)[:, None] * 4 + _QUADS[None, :]).ravel()
        if len(_INDEX_CACHE) > 64:
            _INDEX_CACHE.clear()
        _INDEX_CACHE[segments] = cached
    return cached


def stroke(xs: np.ndarray, ys: np.ndarray, width: float, feather: float, rgba: tuple[int, int, int, int],
           dash: float = 0.0, breaks: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (:data:`VERTEX`, four per point) and triangle indices (relative to 0).

    ``width``: core width in pixels (item units); ``feather``: fade width on each side
    (one physical pixel); ``dash``: dash length in x pixels (0 = solid line), measured on
    the x axis so dashes stay put while a curve scrolls; ``breaks[i]``: point ``i`` starts
    a new piece (no segment from ``i - 1``). Zero-length segments are skipped as well.
    """
    vertices = stroke_vertices(xs, ys, width, feather, rgba, breaks)
    return vertices, segment_indices(xs, ys, dash, breaks)


def _valid_segments(x: np.ndarray, y: np.ndarray, breaks: np.ndarray | None) -> np.ndarray:
    valid = (x[1:] != x[:-1]) | (y[1:] != y[:-1])
    if breaks is not None:
        valid &= ~breaks[1:]
    return valid


def stroke_vertices(xs: np.ndarray, ys: np.ndarray, width: float, feather: float,
                    rgba: tuple[int, int, int, int], breaks: np.ndarray | None = None) -> np.ndarray:
    """The four vertices of each point (see :func:`stroke`)."""
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    n = x.size
    if n == 0:
        return np.zeros(0, dtype=VERTEX)
    mx = np.zeros(n)
    my = np.ones(n)
    scale = np.ones(n)
    if n >= 2:
        dx, dy = np.diff(x), np.diff(y)
        length = np.hypot(dx, dy)
        valid = _valid_segments(x, y, breaks)
        safe = np.where(valid, length, 1.0)
        nx = np.where(valid, -dy / safe, 0.0)
        ny = np.where(valid, dx / safe, 0.0)
        # per point: normal of the segment before (if any) and after (if any)
        bx = np.zeros(n)
        by = np.zeros(n)
        bv = np.zeros(n, dtype=bool)
        ax = np.zeros(n)
        ay = np.zeros(n)
        av = np.zeros(n, dtype=bool)
        bx[1:], by[1:], bv[1:] = nx, ny, valid
        ax[:-1], ay[:-1], av[:-1] = nx, ny, valid
        sx, sy = bx + ax, by + ay                       # only the existing neighbours count
        norm = np.hypot(sx, sy)
        both = bv & av
        flat = both & (norm < 1e-6)                     # a full reversal: use the segment after
        sx = np.where(flat, ax, sx)
        sy = np.where(flat, ay, sy)
        norm = np.where(flat, 1.0, norm)
        has = norm > 1e-12
        mx = np.where(has, sx / np.where(has, norm, 1.0), 0.0)
        my = np.where(has, sy / np.where(has, norm, 1.0), 1.0)
        cos = np.abs(mx * ax + my * ay)
        cos = np.where(both & ~flat, np.maximum(cos, 1.0 / MITER_LIMIT), 1.0)
        scale = 1.0 / cos
    half = 0.5 * width
    k = np.array([half + feather, half, -half, -(half + feather)])
    off = scale[:, None] * k[None, :]                   # (n, 4) offsets along the normal
    buf = np.empty((n, 4, 3), dtype=np.float32)          # x, y, rgba (bit pattern)
    buf[:, :, 0] = x[:, None] + mx[:, None] * off
    buf[:, :, 1] = y[:, None] + my[:, None] * off
    r, g, b, a = rgba
    buf[:, 0, 2] = buf[:, 3, 2] = _rgba_as_float((0, 0, 0, 0))
    buf[:, 1, 2] = buf[:, 2, 2] = _rgba_as_float((r * a // 255, g * a // 255, b * a // 255, a))   # premultiplied
    return buf.reshape(-1).view(VERTEX)


def segment_indices(xs: np.ndarray, ys: np.ndarray, dash: float = 0.0,
                    breaks: np.ndarray | None = None) -> np.ndarray:
    """Triangle indices of the drawn segments (see :func:`stroke`)."""
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    if x.size < 2:
        return np.zeros(0, dtype=np.uint32)
    valid = _valid_segments(x, y, breaks)
    if dash > 0:
        mid = 0.5 * (x[1:] + x[:-1])
        valid &= np.floor(mid / dash) % 2 == 0
    if valid.all():
        return _indices(x.size - 1)
    segments = np.flatnonzero(valid).astype(np.uint32)
    return (segments[:, None] * 4 + _QUADS[None, :]).ravel()


def decimate_columns(t: np.ndarray, y: np.ndarray, column_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Like :func:`decimate`, on columns fixed in time (column ``floor(t / column_s)``).

    Column boundaries do not move as the window scrolls, so points already decimated stay
    valid and a curve can be extended incrementally. With no more than two samples per
    column on average the input is used as it is.
    """
    n = t.size
    if n < 3 or column_s <= 0:
        return t, y
    col = np.floor(t / column_s).astype(np.int64)
    if n <= 2 * (int(col[-1] - col[0]) + 1):
        return t, y
    change = np.empty(n, dtype=bool)
    change[0] = True
    np.not_equal(col[1:], col[:-1], out=change[1:])
    owner = np.cumsum(change) - 1                       # column index (0..k-1) of each sample
    starts = np.flatnonzero(change)
    lo = np.minimum.reduceat(y, starts)
    hi = np.maximum.reduceat(y, starts)
    keep = np.zeros(n, dtype=bool)
    for target in (lo, hi):
        hit = np.flatnonzero(y == target[owner])
        first = np.empty(hit.size, dtype=bool)
        first[0] = True
        oh = owner[hit]
        np.not_equal(oh[1:], oh[:-1], out=first[1:])
        keep[hit[first]] = True
    keep[-1] = True                      # the newest sample: the curve reaches "now"
    return t[keep], y[keep]


def smooth(y: np.ndarray, samples: int) -> np.ndarray:
    """Centred moving average over ``samples`` (odd; ≤ 1: unchanged), shorter at the ends.

    Centred, so the curve is not delayed; at the newest end the average is one-sided until
    the samples after it arrive.
    """
    n = y.size
    if samples <= 1 or n < 3:
        return y
    h = samples // 2
    c = np.concatenate(([0.0], np.cumsum(y, dtype=np.float64)))
    i = np.arange(n)
    lo = np.maximum(i - h, 0)
    hi = np.minimum(i + h + 1, n)
    return (c[hi] - c[lo]) / (hi - lo)


def merge(parts: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """One vertex and one index array from several strokes."""
    parts = [(v, i) for v, i in parts if v.size and i.size]
    if not parts:
        return np.zeros(0, dtype=VERTEX), np.zeros(0, dtype=np.uint32)
    offsets = np.cumsum([0] + [v.size for v, _i in parts[:-1]]).astype(np.uint32)
    vertices = np.concatenate([v for v, _i in parts])
    indices = np.concatenate([i + o for (_v, i), o in zip(parts, offsets)])
    return vertices, indices
