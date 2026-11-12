"""Trajectory of the pendulum tip (or of every joint) in the Simulate view (Qt-free).

Like the Widgets app: one point per shown frame, drawn as a thin line that fades from
transparent (oldest) to opaque (newest), in the colour of the segment the joint ends.
Here the length is a time span (seconds of simulation time) instead of a frame count,
so it does not depend on the frame rate. Points are kept in metres and mapped to pixels
when drawn, so zooming or resizing the view keeps the trajectory.
"""

from __future__ import annotations

import math

import numpy as np

from .linegeom import VERTEX, merge, segment_indices, stroke_vertices

MODES = ("off", "tip", "all")
SECONDS = (1, 3, 5, 10)
MAX_POINTS = 2400                 # 10 s at 240 fps
MIN_ALPHA = 0.08                  # the oldest point's opacity


def joints(cart_x: float, angles, lengths) -> np.ndarray:
    """Positions (m) of the pivot and every joint, the tip last; angles from the upward
    vertical, so joint k + 1 = joint k + l_k (sin θ_k, cos θ_k)."""
    n = len(lengths)
    out = np.empty((n + 1, 2))
    x, y = float(cart_x), 0.0
    out[0] = x, y
    for k in range(n):
        x += lengths[k] * math.sin(angles[k])
        y += lengths[k] * math.cos(angles[k])
        out[k + 1] = x, y
    return out


class TrailBuffer:
    """The last ``seconds`` of joint positions."""

    def __init__(self, seconds: float = 3.0) -> None:
        self.seconds = float(seconds)
        self.clear()

    def clear(self) -> None:
        self.t = np.zeros(0)
        self.points = np.zeros((0, 0, 2))           # (sample, joint, xy)

    def add(self, t: float, positions: np.ndarray) -> bool:
        """A new sample at simulation time ``t``; ``False`` if nothing changed (paused)."""
        if self.t.size and (positions.shape[0] != self.points.shape[1] or t < self.t[-1]):
            self.clear()                            # other structure, or the time went back (reset)
        if self.t.size and t == self.t[-1]:
            return False
        keep = int(np.searchsorted(self.t, t - self.seconds, side="left"))
        keep = max(keep, self.t.size + 1 - MAX_POINTS)
        self.t = np.append(self.t[keep:], t)
        self.points = np.concatenate((self.points[keep:], positions[None]), axis=0) \
            if self.points.size else positions[None].copy()
        return True


def geometry(buffer: TrailBuffer, mode: str, px_per_m: float, cx: float, origin_y: float,
             colors: list[tuple[int, int, int, int]], width: float, feather: float
             ) -> list[tuple[np.ndarray, np.ndarray]]:
    """One ``(vertices, indices)`` per drawn joint (the tip only, or joints 1…n)."""
    n = buffer.t.size
    if mode == "off" or n < 2 or not colors:
        return []
    joints_n = buffer.points.shape[1]
    drawn = range(1, joints_n) if mode == "all" else [joints_n - 1]
    fade = (MIN_ALPHA + (1.0 - MIN_ALPHA) * np.linspace(0.0, 1.0, n))[:, None, None]
    out = []
    for j in drawn:
        xs = cx + buffer.points[:, j, 0] * px_per_m
        ys = origin_y - buffer.points[:, j, 1] * px_per_m
        color = colors[(j - 1) % len(colors)]
        vertices = stroke_vertices(xs, ys, width, feather, color).view(np.uint8).reshape(n, 4, 12)
        rgba = vertices[:, :, 8:12]                 # premultiplied: every channel fades
        rgba[...] = (rgba * fade).astype(np.uint8)
        out.append((vertices.reshape(-1).view(VERTEX), segment_indices(xs, ys)))
    return [merge(out)] if mode == "all" and out else out
