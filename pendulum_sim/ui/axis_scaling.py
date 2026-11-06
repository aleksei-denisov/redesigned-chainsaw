"""Calm, rate-limited automatic axis scaling for the live graphs (no Qt code).

Plain autoscaling rescales on every refresh and makes the graphs "jump". This
scaler uses asymmetric behaviour:

* **Expand immediately** when the data leaves the current range (with a margin,
  so a growing signal does not trigger an expansion on every refresh) – no
  data ever disappears off the plot.
* **Shrink slowly** – only when the data has used less than ``shrink_below``
  of the range, and then exponentially with time constant ``shrink_tau`` – so
  the range settles calmly after a disturbance instead of snapping back.
* Signed signals (errors, control, velocities) are kept **symmetric about
  zero**, so the zero line stays in the same place.
* An optional **cap** limits the range (the control graph is capped near the
  actuator limit so a spike of the *unsaturated* command cannot squash the
  real actuator output into a flat line).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np


@dataclass
class SmoothAxis:
    symmetric: bool = False
    min_span: float = 1.0            # smallest displayed span (data units)
    margin: float = 0.25             # extra head-room added when expanding (fraction of span)
    shrink_below: float = 0.45       # shrink only if the data fills less than this fraction
    shrink_tau: float = 2.5          # s, time constant of the shrinking
    cap: float | None = None         # max |value| shown (symmetric axes)

    def __post_init__(self) -> None:
        self.lo: float | None = None
        self.hi: float | None = None
        self._last_time: float | None = None

    def reset(self) -> None:
        self.lo = self.hi = None
        self._last_time = None

    def _target(self, data_lo: float, data_hi: float) -> tuple[float, float]:
        if self.symmetric:
            half = max(abs(data_lo), abs(data_hi), 0.5 * self.min_span)
            if self.cap is not None:
                half = min(half, self.cap)
            return -half, half
        span = max(data_hi - data_lo, self.min_span)
        centre = 0.5 * (data_lo + data_hi)
        return centre - 0.5 * span, centre + 0.5 * span

    def update(self, data_lo: float, data_hi: float, now: float) -> tuple[float, float]:
        """Feed the current data extent; return the range to display."""
        if not (math.isfinite(data_lo) and math.isfinite(data_hi)):
            return (self.lo, self.hi) if self.lo is not None else (-1.0, 1.0)  # type: ignore[return-value]
        t_lo, t_hi = self._target(data_lo, data_hi)
        span = t_hi - t_lo
        pad = self.margin * (0.5 * span if self.symmetric else span)
        dt = 0.0 if self._last_time is None else max(0.0, now - self._last_time)
        self._last_time = now
        if self.lo is None or self.hi is None:
            self.lo, self.hi = t_lo - pad, t_hi + pad
            return self.lo, self.hi
        # Expand at once.
        if t_lo < self.lo:
            self.lo = t_lo - pad
        if t_hi > self.hi:
            self.hi = t_hi + pad
        # Shrink slowly when the data uses only a small part of the range.
        current_span = self.hi - self.lo
        if span < self.shrink_below * current_span and dt > 0.0:
            alpha = 1.0 - math.exp(-dt / self.shrink_tau)
            self.lo += alpha * ((t_lo - pad) - self.lo)
            self.hi += alpha * ((t_hi + pad) - self.hi)
        if self.symmetric:
            half = max(-self.lo, self.hi)
            if self.cap is not None:
                half = min(half, self.cap * (1.0 + self.margin))
            self.lo, self.hi = -half, half
        return self.lo, self.hi


def data_extent(arrays: list[np.ndarray]) -> tuple[float, float]:
    """(min, max) over several arrays, ignoring NaN; (nan, nan) if there is no data."""
    lo, hi = math.inf, -math.inf
    for a in arrays:
        if a.size:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices
                a_lo, a_hi = np.nanmin(a), np.nanmax(a)
            if math.isfinite(a_lo):
                lo = min(lo, float(a_lo))
                hi = max(hi, float(a_hi))
    if lo > hi:
        return math.nan, math.nan
    return lo, hi
