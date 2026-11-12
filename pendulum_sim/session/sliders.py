"""Slider mappings and steps of the parameter editors (Qt-free, shared by every interface).

A slider position ``t`` (0 … 1) maps to a value by the field's ``mapping``:

* ``linear`` – ``v = lo + t (hi - lo)``;
* ``log`` – positive quantities spanning decades (masses, frequencies, weights):
  ``log10 v`` is linear in ``t``;
* ``symlog`` – signed quantities that are fine near zero and span decades further out
  (gains, friction): ``asinh(v / f)`` is linear in ``t``, with
  ``f = max(10^-decimals, 10^-3 · max(|lo|, |hi|))``, so the slider is linear for
  ``|v| ≲ f`` and logarithmic beyond.

:func:`nudge` is one tap on − / +: a fixed ``step`` if the field has one; for log and
symlog fields about 2 % of the value rounded to a 1/2/5 step (one unit of the last
decimal near zero); for linear fields 1 % of the range.
"""

from __future__ import annotations

import math


def symlog_scale(minimum: float, maximum: float, decimals: int) -> float:
    return max(10 ** (-decimals), 1e-3 * max(abs(minimum), abs(maximum)))


def to_position(v: float, minimum: float, maximum: float, mapping: str = "linear", decimals: int = 3) -> float:
    lo, hi = minimum, maximum
    if mapping == "log" and lo > 0:
        return (math.log10(max(v, lo)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    if mapping == "symlog":
        f = symlog_scale(lo, hi, decimals)
        a, b = math.asinh(lo / f), math.asinh(hi / f)
        return (math.asinh(v / f) - a) / (b - a)
    return (v - lo) / (hi - lo) if hi > lo else 0.0


def from_position(t: float, minimum: float, maximum: float, mapping: str = "linear", decimals: int = 3) -> float:
    lo, hi = minimum, maximum
    if mapping == "log" and lo > 0:
        return 10 ** (math.log10(lo) + t * (math.log10(hi) - math.log10(lo)))
    if mapping == "symlog":
        f = symlog_scale(lo, hi, decimals)
        a, b = math.asinh(lo / f), math.asinh(hi / f)
        return f * math.sinh(a + t * (b - a))
    return lo + t * (hi - lo)


def nudge(v: float, direction: int, minimum: float, maximum: float, mapping: str = "linear",
          decimals: int = 3, step: float | None = None) -> float:
    """The value after one tap on − (``direction`` −1) or + (+1), not yet clamped."""
    if step is not None:
        return v + direction * step
    if mapping in ("log", "symlog"):
        mag = abs(v)
        unit = 10 ** (-decimals)
        if mag < 10 * unit:
            return v + direction * unit
        raw = 0.02 * mag                                  # ~2 % of the value, as a "nice" 1/2/5 step
        e = 10 ** math.floor(math.log10(raw))
        nice = min((m * e for m in (1, 2, 5, 10) if m * e >= raw), default=raw)
        return v + direction * nice
    return v + direction * (maximum - minimum) / 100.0
