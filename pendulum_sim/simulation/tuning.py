"""Robust automatic tuning of the multi-loop PID controller.

Goal
----
Find, for the *given* physical system (masses, lengths, actuator limits, lag,
controller rate, …), the controller that recovers from the **largest push**,
and among equally robust ones the one that recovers fastest.

Robustness metric
-----------------
``max_recoverable_push``: starting at rest in the upright position, a
horizontal force pulse (default 0.1 s) is applied at the pendulum tip. A run
*recovers* if no segment ever leans more than ``fall_deg`` and every segment is
back within ``settle_deg`` at the end of the horizon. The largest recoverable
force is found by doubling followed by bisection on the full nonlinear
simulator (same engine as the live view, including saturation, actuator lag
and the controller sample rate).

Search space
------------
Searching raw PID gains directly is hopeless for 2–3 segments: almost every
random gain vector is unstable. Instead the search runs over the five LQR
weights (log scale); every candidate is a *stabilising* linear design mapped
onto the loops' P and D gains (see ``control/lqr_tuning.py``). This turns the
problem into a smooth, low-dimensional search. Stage 1 samples weights
randomly around the defaults, stage 2 refines around the best candidate with a
shrinking step. Candidates are evaluated in parallel processes when possible.

Integral gains are set to zero in the result: integral action adds states the
design does not account for and only reduces push tolerance; users can add a
little afterwards.
"""

from __future__ import annotations

import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..control import (
    AntiWindup,
    ControllerConfig,
    DerivativeMode,
    LQRWeights,
    lqr_controller,
)
from .config import AppConfig, config_from_dict, config_to_dict
from .engine import SimulationDiverged, Simulator

ProgressCallback = Callable[[float, str], bool]  # (fraction, message) → False cancels

DEFAULT_WEIGHTS = LQRWeights()
WEIGHT_BOUNDS = np.log10(np.array([
    [1e-2, 1e3],    # cart position
    [1e-1, 1e5],    # angles
    [1e-3, 1e3],    # cart velocity
    [1e-3, 1e3],    # angular velocities
    [1e-4, 1e1],    # effort R
]))


@dataclass
class TuningSettings:
    push_duration: float = 0.1     # s, length of the tip force pulse
    push_time: float = 0.2         # s, when the pulse starts
    horizon: float = 4.0           # s, simulated time per test
    fall_deg: float = 60.0
    settle_deg: float = 3.0
    resolution: float = 0.03       # relative bisection accuracy of the push force
    samples: int = 24              # stage 1 random candidates
    refine_rounds: int = 3         # stage 2 rounds
    refine_samples: int = 8        # candidates per refine round
    workers: int = 0               # 0 = automatic (CPU count, max 8); 1 = no subprocesses
    seed: int = 0

    @staticmethod
    def preset(name: str) -> "TuningSettings":
        if name == "quick":
            return TuningSettings(samples=10, refine_rounds=1, refine_samples=6, resolution=0.05)
        if name == "thorough":
            return TuningSettings(samples=48, refine_rounds=5, refine_samples=10, resolution=0.02)
        return TuningSettings()


@dataclass
class Candidate:
    log_weights: np.ndarray
    max_push: float
    recovery_cost: float           # ISE of angles [rad²·s] after a push of half the tested force

    def better_than(self, other: "Candidate | None", tolerance: float) -> bool:
        if other is None:
            return True
        if self.max_push > other.max_push * (1.0 + tolerance):
            return True
        if self.max_push >= other.max_push * (1.0 - tolerance):
            return self.recovery_cost < other.recovery_cost
        return False


@dataclass
class TuningResult:
    controller: ControllerConfig
    weights: LQRWeights
    max_push: float                 # N, recoverable tip force (pulse of push_duration)
    baseline_max_push: float        # N, same metric for the controller before tuning
    recommended_push: float         # N, a comfortable test push (half of max_push)
    evaluations: int
    elapsed: float
    cancelled: bool = False
    history: list[float] = field(default_factory=list)


# ---------------------------------------------------------------- metric
def _push_test(cfg: AppConfig, force: float, s: TuningSettings) -> tuple[bool, float]:
    """Simulate one push; return (recovered, ISE of the angles)."""
    sim = Simulator(cfg, telemetry_window_s=0.5)
    sim.apply_disturbance(force, s.push_duration, start=s.push_time)
    dt = cfg.simulation.dt
    steps = int(round(s.horizon / dt))
    fall = math.radians(s.fall_deg)
    check_every = max(1, int(0.01 / dt))
    ise = 0.0
    try:
        for k in range(steps):
            sim.step()
            if k % check_every == 0:
                ang = sim.angles
                ise += float(np.dot(ang, ang)) * dt * check_every
                if np.max(np.abs(ang)) > fall:
                    return False, math.inf
    except SimulationDiverged:
        return False, math.inf
    settled = bool(np.max(np.abs(sim.angles)) < math.radians(s.settle_deg))
    return settled, ise


MIN_PUSH = 1e-3        # N; below this a "recovery" is meaningless
INITIAL_TILT = math.radians(0.2)  # tiny tilt so an unstable controller cannot balance by accident


def _upright_config(cfg: AppConfig) -> AppConfig:
    c = cfg.copy()
    for k, seg in enumerate(c.plant.segments):
        seg.initial_angle = INITIAL_TILT if k == 0 else 0.0
        seg.initial_velocity = 0.0
    c.simulation.telemetry_rate_hz = 20.0
    return c


def max_recoverable_push(cfg: AppConfig, settings: TuningSettings | None = None,
                         upper: float = 100.0) -> float:
    """Largest tip force pulse [N] the configured controller recovers from."""
    s = settings or TuningSettings()
    c = _upright_config(cfg)
    lo, hi = 0.0, None
    force = 0.01 * max(1.0, c.plant.actuator.max_output / 10.0)
    while force <= upper:
        ok, _ = _push_test(c, force, s)
        if not ok:
            hi = force
            break
        lo = force
        force *= 2.0
    if hi is None:
        return lo
    if lo == 0.0 and not _push_test(c, MIN_PUSH, s)[0]:
        return 0.0
    lo = max(lo, MIN_PUSH)
    while hi - lo > s.resolution * hi:
        mid = 0.5 * (lo + hi)
        if _push_test(c, mid, s)[0]:
            lo = mid
        else:
            hi = mid
    return lo


# ------------------------------------------------------------- candidate
def normalised_controller(cfg: AppConfig, weights: LQRWeights) -> ControllerConfig:
    """LQR-seeded controller with the recommended settings for every loop."""
    p = cfg.plant
    base = cfg.controller
    ctrl = lqr_controller(p, weights, base=base)
    limit = p.actuator.max_output
    ctrl.enabled = True
    ctrl.output_min, ctrl.output_max = -limit, limit
    for loop in ctrl.loops:
        loop.enabled = True
        loop.p_enabled = loop.i_enabled = loop.d_enabled = True
        loop.ki = 0.0
        loop.setpoint = 0.0
        loop.integral_limit = 0.0
        loop.anti_windup = AntiWindup.CLAMP
        loop.derivative_mode = DerivativeMode.MEASURED_RATE
    return ctrl


def _weights(z: np.ndarray) -> LQRWeights:
    return LQRWeights(*(float(v) for v in 10.0 ** np.asarray(z)))


def evaluate_candidate(config_dict: dict, log_weights: list[float], settings: TuningSettings) -> tuple[float, float]:
    """Top-level (picklable) evaluation used by worker processes."""
    cfg = config_from_dict(config_dict)
    try:
        cfg.controller = normalised_controller(cfg, _weights(np.array(log_weights)))
    except (ValueError, np.linalg.LinAlgError, ArithmeticError):
        return 0.0, math.inf
    push = max_recoverable_push(cfg, settings)
    if push <= 0.0:
        return 0.0, math.inf
    _, cost = _push_test(_upright_config(cfg), 0.5 * push, settings)
    return push, cost


# ---------------------------------------------------------------- search
def multiprocessing_supported() -> bool:
    """Worker processes are unavailable inside an Android app (no fork/sem_open there)."""
    return not (hasattr(sys, "getandroidapilevel") or sys.platform == "android")


def default_workers() -> int:
    if not multiprocessing_supported():
        return 1
    return max(1, min(8, (os.cpu_count() or 2) - 1))


_default_workers = default_workers  # backwards-compatible name


def robust_tune(
    config: AppConfig,
    settings: TuningSettings | None = None,
    progress: ProgressCallback | None = None,
) -> TuningResult:
    """Search the LQR-weight space for the most push-tolerant controller."""
    s = settings or TuningSettings()
    cfg = config.copy()
    cfg.validate()
    started = time.perf_counter()
    rng = np.random.default_rng(s.seed)
    config_dict = config_to_dict(cfg)
    total = 1 + 1 + s.samples + s.refine_rounds * s.refine_samples
    done = 0
    cancelled = False

    def report(msg: str) -> bool:
        if progress is None:
            return True
        return progress(min(done / total, 1.0), msg)

    report("Measuring the current controller…")
    baseline = max_recoverable_push(cfg, s)
    done += 1

    z0 = np.log10([DEFAULT_WEIGHTS.cart_position, DEFAULT_WEIGHTS.angle, DEFAULT_WEIGHTS.cart_velocity,
                   DEFAULT_WEIGHTS.angular_velocity, DEFAULT_WEIGHTS.effort])
    lo, hi = WEIGHT_BOUNDS[:, 0], WEIGHT_BOUNDS[:, 1]
    stage1 = [z0] + [np.clip(z0 + rng.normal(0.0, 1.2, 5), lo, hi) for _ in range(s.samples)]

    workers = (s.workers or default_workers()) if multiprocessing_supported() else 1
    executor = None
    if workers > 1:
        try:
            executor = ProcessPoolExecutor(max_workers=workers)
        except (OSError, NotImplementedError, ValueError):
            executor = None

    best: Candidate | None = None
    history: list[float] = []

    def run_batch(batch: list[np.ndarray], label: str) -> bool:
        nonlocal done, best
        args = [(config_dict, [float(v) for v in z], s) for z in batch]
        if executor is not None:
            futures = [executor.submit(evaluate_candidate, *a) for a in args]
            results_iter = (f.result() for f in futures)
        else:
            results_iter = (evaluate_candidate(*a) for a in args)
        for z, (push, cost) in zip(batch, results_iter):
            done += 1
            cand = Candidate(np.asarray(z), push, cost)
            if cand.max_push > 0 and cand.better_than(best, s.resolution):
                best = cand
            history.append(best.max_push if best else 0.0)
            current = best.max_push if best else 0.0
            if not report(f"{label}: best recoverable push {current:.3f} N"):
                return False
        return True

    try:
        if not run_batch(stage1, "Exploring"):
            cancelled = True
        sigma = 0.6
        for r in range(s.refine_rounds):
            if cancelled or best is None:
                break
            batch = [np.clip(best.log_weights + rng.normal(0.0, sigma, 5), lo, hi) for _ in range(s.refine_samples)]
            if not run_batch(batch, f"Refining ({r + 1}/{s.refine_rounds})"):
                cancelled = True
            sigma *= 0.6
    finally:
        if executor is not None:
            executor.shutdown(wait=not cancelled, cancel_futures=True)

    if best is None:
        raise ValueError("no stabilising controller found for this system "
                         "(check the actuator limit and the physical parameters)")
    weights = _weights(best.log_weights)
    controller = normalised_controller(cfg, weights)
    done = total
    report("Done")
    return TuningResult(
        controller=controller,
        weights=weights,
        max_push=best.max_push,
        baseline_max_push=baseline,
        recommended_push=round_nice(0.5 * best.max_push),
        evaluations=len(history),
        elapsed=time.perf_counter() - started,
        cancelled=cancelled,
        history=history,
    )


def round_nice(value: float) -> float:
    """Round down to 2 significant digits (for a friendly default push force)."""
    if value <= 0:
        return 0.0
    exponent = math.floor(math.log10(value)) - 1
    return round(math.floor(value / 10**exponent) * 10**exponent, max(0, -exponent))
