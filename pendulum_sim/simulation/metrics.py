"""Performance metrics for recorded runs.

Definitions (applied to each segment's angle error ``e(t)`` in degrees):

* **Max deviation** – ``max |e(t)|``.
* **RMSE** – ``sqrt(mean(e²))`` over the whole run.
* **Settling time** – the earliest time ``t_s`` such that ``|e(t)| ≤ tol``
  for every ``t ≥ t_s`` (``None`` if the run never settles).
* **Overshoot** – let ``e_pk`` be the sample with the largest ``|e|``; the
  overshoot is the largest excursion to the *opposite* side after that peak,
  as a percentage of ``|e_pk|``. For a release from an initial tilt this is
  the classic percentage overshoot past the setpoint.
* **Control effort** – RMS and peak of the applied actuator output and its
  integral of squares ``∫u² dt``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

FALL_ANGLE_DEG = 90.0


@dataclass
class SignalMetrics:
    max_deviation: float
    rmse: float
    settling_time: float | None
    overshoot_pct: float


@dataclass
class RunMetrics:
    segments: list[SignalMetrics]
    max_deviation: float          # worst segment [deg]
    rmse: float                   # combined over segments [deg]
    settling_time: float | None   # slowest segment [s]
    overshoot_pct: float          # worst segment [%]
    control_rms: float
    control_peak: float
    control_energy: float         # ∫u² dt
    max_cart_displacement: float
    fell: bool

    def to_dict(self) -> dict:
        return asdict(self)


def settling_time(t: np.ndarray, e: np.ndarray, tolerance: float) -> float | None:
    outside = np.nonzero(np.abs(e) > tolerance)[0]
    if outside.size == 0:
        return 0.0
    last = outside[-1]
    if last == len(e) - 1:
        return None
    return float(t[last + 1])


def overshoot_pct(e: np.ndarray) -> float:
    if e.size == 0:
        return 0.0
    k = int(np.argmax(np.abs(e)))
    peak = e[k]
    if peak == 0:
        return 0.0
    opposite = -np.sign(peak) * e[k:]
    return float(max(0.0, opposite.max()) / abs(peak) * 100.0)


def signal_metrics(t: np.ndarray, e_deg: np.ndarray, tolerance_deg: float) -> SignalMetrics:
    return SignalMetrics(
        max_deviation=float(np.max(np.abs(e_deg))) if e_deg.size else 0.0,
        rmse=float(np.sqrt(np.mean(e_deg**2))) if e_deg.size else 0.0,
        settling_time=settling_time(t, e_deg, tolerance_deg),
        overshoot_pct=overshoot_pct(e_deg),
    )


def run_metrics(
    t: np.ndarray,
    angle_errors_deg: list[np.ndarray],
    control: np.ndarray,
    cart_position: np.ndarray,
    tolerance_deg: float,
    diverged: bool = False,
) -> RunMetrics:
    segs = [signal_metrics(t, e, tolerance_deg) for e in angle_errors_deg]
    settle = [s.settling_time for s in segs]
    dt = np.diff(t, prepend=t[0]) if t.size else t
    all_err = np.vstack(angle_errors_deg) if angle_errors_deg else np.zeros((1, 0))
    fell = diverged or bool(np.any(np.abs(all_err) > FALL_ANGLE_DEG))
    return RunMetrics(
        segments=segs,
        max_deviation=max(s.max_deviation for s in segs),
        rmse=float(np.sqrt(np.mean(all_err**2))) if all_err.size else 0.0,
        settling_time=None if fell or any(s is None for s in settle) else max(s for s in settle if s is not None),
        overshoot_pct=max(s.overshoot_pct for s in segs),
        control_rms=float(np.sqrt(np.mean(control**2))) if control.size else 0.0,
        control_peak=float(np.max(np.abs(control))) if control.size else 0.0,
        control_energy=float(np.sum(control**2 * dt)),
        max_cart_displacement=float(np.max(np.abs(cart_position))) if cart_position.size else 0.0,
        fell=fell,
    )
