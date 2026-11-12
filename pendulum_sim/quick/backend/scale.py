"""Stable axis scales for live graphs (Qt-free).

A graph whose range follows the data on every refresh jumps whenever a transient enters
or leaves the time window. :class:`StableScale` has three modes:

* ``auto`` – padded range on round values (1/2/5·10ⁿ steps), at least ``min_span``
  wide. It **expands at once** when the data leave it, and **shrinks only** after the
  data have used less than ``SHRINK_RATIO`` of it for ``SHRINK_DELAY_S`` seconds;
* ``symmetric`` – the same, but always ±max around zero;
* ``fixed`` – the user's minimum and maximum.

The *shown* range moves to a new target over ``EASE_S`` seconds (smoothstep), so even
a necessary change is a short glide rather than a jump. ``moving`` tells the graph to
redraw its curves for the new scale; between changes the scale stands still, which lets
the curves be drawn incrementally.
"""

from __future__ import annotations

import math

from ...mobile.plot_math import nice_step

MODES = ("auto", "symmetric", "fixed")
PAD = 0.08
SHRINK_RATIO = 0.4
SHRINK_DELAY_S = 3.0
EASE_S = 0.3


class StableScale:
    def __init__(self, mode: str = "auto", min_span: float = 1.0, fixed: tuple[float, float] | None = None) -> None:
        self.mode = mode if mode in MODES else "auto"
        self.min_span = float(min_span)
        self.fixed = fixed
        self.lo = self.hi = math.nan                   # shown range
        self._target = (math.nan, math.nan)
        self._from = (math.nan, math.nan)
        self._ease_start = 0.0
        self._small_since: float | None = None
        self.moving = False

    def reset(self) -> None:
        self.lo = self.hi = math.nan
        self._target = self._from = (math.nan, math.nan)
        self._small_since = None
        self.moving = False

    # ---------------------------------------------------------------- target
    def _range_for(self, dmin: float, dmax: float) -> tuple[float, float]:
        if self.mode == "symmetric":
            m = max(abs(dmin), abs(dmax), 0.5 * self.min_span) * (1.0 + PAD)
            step = nice_step(2.0 * m, 4)
            m = math.ceil(m / step - 1e-9) * step
            return -m, m
        centre = 0.5 * (dmin + dmax)
        span = max(dmax - dmin, self.min_span) * (1.0 + 2.0 * PAD)
        lo, hi = centre - 0.5 * span, centre + 0.5 * span
        step = nice_step(hi - lo, 4)
        return math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step

    def _new_target(self, dmin: float, dmax: float, now: float) -> tuple[float, float]:
        lo, hi = self._target
        if math.isnan(lo):
            return self._range_for(dmin, dmax)
        if dmin < lo or dmax > hi:                     # data left the range: expand now
            nlo, nhi = self._range_for(min(dmin, lo), max(dmax, hi)) if self.mode == "symmetric" \
                else self._range_for(dmin, dmax)
            self._small_since = None
            return min(nlo, lo), max(nhi, hi)
        used = max(dmax - dmin, self.min_span) if self.mode == "auto" \
            else max(2.0 * max(abs(dmin), abs(dmax)), self.min_span)
        if used < SHRINK_RATIO * (hi - lo):
            if self._small_since is None:
                self._small_since = now
            elif now - self._small_since >= SHRINK_DELAY_S:
                self._small_since = None
                return self._range_for(dmin, dmax)
        else:
            self._small_since = None
        return lo, hi

    # ---------------------------------------------------------------- update
    def update(self, dmin: float, dmax: float, now: float) -> tuple[float, float]:
        """New data extremes (NaN: no data); returns the range to draw with."""
        if self.mode == "fixed" and self.fixed is not None and self.fixed[1] > self.fixed[0]:
            target = (float(self.fixed[0]), float(self.fixed[1]))
        elif not (math.isfinite(dmin) and math.isfinite(dmax)):
            target = self._target if not math.isnan(self._target[0]) else (-0.5 * self.min_span, 0.5 * self.min_span)
        else:
            target = self._new_target(dmin, dmax, now)
        if math.isnan(self.lo):                        # first range: no glide
            self.lo, self.hi = target
            self._target = self._from = target
            self.moving = True
            return self.lo, self.hi
        if target != self._target:
            self._from = (self.lo, self.hi)
            self._target = target
            self._ease_start = now
        if (self.lo, self.hi) == self._target:
            self.moving = False
            return self.lo, self.hi
        u = min(max((now - self._ease_start) / EASE_S, 0.0), 1.0)
        s = u * u * (3.0 - 2.0 * u)
        if u >= 1.0:
            self.lo, self.hi = self._target
        else:
            self.lo = self._from[0] + (self._target[0] - self._from[0]) * s
            self.hi = self._from[1] + (self._target[1] - self._from[1]) * s
        self.moving = True
        return self.lo, self.hi
