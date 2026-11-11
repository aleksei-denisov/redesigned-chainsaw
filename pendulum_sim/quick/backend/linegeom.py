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
           dash: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (:data:`VERTEX`) and triangle indices (relative to 0) of one polyline.

    ``width``: core width in pixels (item units); ``feather``: fade width on each side
    (one physical pixel); ``dash``: dash length (0 = solid line).
    """
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    if x.size >= 2:                                             # drop repeated points (no direction)
        moved = np.empty(x.size, dtype=bool)
        moved[0] = True
        np.logical_or(x[1:] != x[:-1], y[1:] != y[:-1], out=moved[1:])
        if not moved.all():
            x, y = x[moved], y[moved]
    n = x.size
    if n < 2:
        return np.zeros(0, dtype=VERTEX), np.zeros(0, dtype=np.uint32)
    dx, dy = np.diff(x), np.diff(y)
    length = np.hypot(dx, dy)
    nx, ny = -dy / length, dx / length                          # unit normals per segment
    # vertex normals: miter of the two adjacent segment normals (ends: the segment's normal)
    mx = np.empty(n)
    my = np.empty(n)
    scale = np.ones(n)
    mx[0], my[0], mx[-1], my[-1] = nx[0], ny[0], nx[-1], ny[-1]
    if n > 2:
        sx, sy = nx[:-1] + nx[1:], ny[:-1] + ny[1:]
        norm = np.hypot(sx, sy)
        flat = norm < 1e-6                                       # a full reversal: use the segment normal
        if flat.any():
            sx[flat], sy[flat], norm[flat] = nx[1:][flat], ny[1:][flat], 1.0
        sx /= norm
        sy /= norm
        cos = np.abs(sx * nx[1:] + sy * ny[1:])
        np.maximum(cos, 1.0 / MITER_LIMIT, out=cos)
        scale[1:-1] = 1.0 / cos
        mx[1:-1], my[1:-1] = sx, sy
    half = 0.5 * width
    k = np.array([half + feather, half, -half, -(half + feather)])
    off = scale[:, None] * k[None, :]                           # (n, 4) offsets along the normal
    buf = np.empty((n, 4, 3), dtype=np.float32)                 # x, y, rgba (bit pattern)
    buf[:, :, 0] = x[:, None] + mx[:, None] * off
    buf[:, :, 1] = y[:, None] + my[:, None] * off
    r, g, b, a = rgba
    solid = _rgba_as_float((r * a // 255, g * a // 255, b * a // 255, a))   # premultiplied
    clear = _rgba_as_float((0, 0, 0, 0))
    buf[:, 0, 2] = buf[:, 3, 2] = clear
    buf[:, 1, 2] = buf[:, 2, 2] = solid
    vertices = buf.reshape(-1).view(VERTEX)
    if dash > 0:
        start = np.concatenate(([0.0], np.cumsum(length)[:-1]))
        segments = np.flatnonzero((start // dash) % 2 == 0).astype(np.uint32)
        return vertices, (segments[:, None] * 4 + _QUADS[None, :]).ravel()
    return vertices, _indices(n - 1)


def merge(parts: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """One vertex and one index array from several strokes."""
    parts = [(v, i) for v, i in parts if v.size and i.size]
    if not parts:
        return np.zeros(0, dtype=VERTEX), np.zeros(0, dtype=np.uint32)
    offsets = np.cumsum([0] + [v.size for v, _i in parts[:-1]]).astype(np.uint32)
    vertices = np.concatenate([v for v, _i in parts])
    indices = np.concatenate([i + o for (_v, i), o in zip(parts, offsets)])
    return vertices, indices
