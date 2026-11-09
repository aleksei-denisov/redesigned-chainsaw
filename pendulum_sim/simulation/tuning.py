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
shrinking step. Candidates are evaluated in parallel (see :mod:`.parallel`:
process pool on the desktop, forked workers on Android, serial as a fallback);
results are applied in candidate order, so the outcome does not depend on the
number of workers.

Cost reductions that do not change which candidate wins
--------------------------------------------------------
* **Pruning** – a candidate is first pushed with slightly less than the best push
  found before its batch; if it already falls there it cannot win, so one
  simulation replaces the ~15 of a full doubling/bisection search.
* **Early stop** – a push test that has clearly recovered (all angles, angular
  rates and the cart speed inside a tight band after the pulse) ends early; the
  outcome at the horizon would be "recovered" anyway. The recovery-cost run, whose
  integral uses the whole horizon, always runs to the end.

Integral gains are set to zero in the result: integral action adds states the
design does not account for and only reduces push tolerance; users can add a
little afterwards.
"""

from __future__ import annotations

import math
import threading
import time
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
from . import parallel
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
    workers: int = 0               # 0 = automatic (CPU count − 1, max 8); 1 = no subprocesses
    seed: int = 0
    prune: bool = True             # one-shot rejection of candidates that cannot win
    early_stop: bool = True        # end clearly recovered push tests early
    still_deg: float = 0.5         # early stop: |angles| below this …
    still_rate_dps: float = 3.0    # … |angular rates| below this …
    still_cart_mps: float = 0.03   # … and |cart speed| below this, after the pulse

    @property
    def candidates(self) -> int:
        """Candidate controllers evaluated (without the baseline)."""
        return 1 + self.samples + self.refine_rounds * self.refine_samples

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


class TuningCancelled(Exception):
    """Raised when tuning is cancelled before any candidate was evaluated."""


class CancelToken:
    """Thread-safe cancellation flag for :func:`robust_tune`."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


# Token of the tuning run in *this* process; push tests poll it, so a serial run (and the
# in-process part of a parallel one) stops within a fraction of a second.
_active_token: CancelToken | None = None


# ---------------------------------------------------------------- metric
def _push_test(cfg: AppConfig, force: float, s: TuningSettings,
               allow_early_stop: bool = True) -> tuple[bool, float]:
    """Simulate one push; return (recovered, ISE of the angles)."""
    sim = Simulator(cfg, telemetry_window_s=0.5)
    sim.apply_disturbance(force, s.push_duration, start=s.push_time)
    dt = cfg.simulation.dt
    steps = int(round(s.horizon / dt))
    fall = math.radians(s.fall_deg)
    check_every = max(1, int(0.01 / dt))
    early = s.early_stop and allow_early_stop
    calm_after = s.push_time + s.push_duration + 0.3
    still = math.radians(s.still_deg)
    still_rate = math.radians(s.still_rate_dps)
    cart_index = sim.model.n + 1
    ise = 0.0
    token = _active_token
    try:
        for k in range(steps):
            sim.step()
            if k % check_every == 0:
                ang = sim.angles
                ise += float(np.dot(ang, ang)) * dt * check_every
                if np.max(np.abs(ang)) > fall:
                    return False, math.inf
                if early and sim.time >= calm_after and np.max(np.abs(ang)) < still \
                        and np.max(np.abs(sim.angular_velocities)) < still_rate \
                        and abs(float(sim.state[cart_index])) < s.still_cart_mps:
                    return True, ise
                if token is not None and k % (10 * check_every) == 0 and token.cancelled:
                    raise TuningCancelled
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
                         upper: float = 100.0, counter: list[int] | None = None) -> float:
    """Largest tip force pulse [N] the configured controller recovers from.

    ``counter[0]`` (optional) is increased by the number of simulations run.
    """
    s = settings or TuningSettings()
    c = _upright_config(cfg)
    counter = counter if counter is not None else [0]

    def test(force: float) -> bool:
        counter[0] += 1
        return _push_test(c, force, s)[0]

    lo, hi = 0.0, None
    force = 0.01 * max(1.0, c.plant.actuator.max_output / 10.0)
    while force <= upper:
        ok = test(force)
        if not ok:
            hi = force
            break
        lo = force
        force *= 2.0
    if hi is None:
        return lo
    if lo == 0.0 and not test(MIN_PUSH):
        return 0.0
    lo = max(lo, MIN_PUSH)
    while hi - lo > s.resolution * hi:
        mid = 0.5 * (lo + hi)
        if test(mid):
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


def evaluate_candidate(config_dict: dict, log_weights: list[float], settings: TuningSettings,
                       min_push: float = 0.0) -> tuple[float, float, int]:
    """Top-level (picklable) work unit: ``(max push, recovery cost, simulations run)``.

    With ``min_push > 0`` (and ``settings.prune``) the candidate is first tested at that
    force; failing there it cannot beat the current best and is rejected after one run.
    """
    cfg = config_from_dict(config_dict)
    try:
        cfg.controller = normalised_controller(cfg, _weights(np.array(log_weights)))
    except (ValueError, np.linalg.LinAlgError, ArithmeticError):
        return 0.0, math.inf, 0
    sims = [0]
    if settings.prune and min_push > 0.0:
        sims[0] += 1
        if not _push_test(_upright_config(cfg), min_push, settings)[0]:
            return 0.0, math.inf, sims[0]
    push = max_recoverable_push(cfg, settings, counter=sims)
    if push <= 0.0:
        return 0.0, math.inf, sims[0]
    sims[0] += 1
    _, cost = _push_test(_upright_config(cfg), 0.5 * push, settings, allow_early_stop=False)
    return push, cost, sims[0]


def evaluate_baseline(config_dict: dict, settings: TuningSettings) -> tuple[float, float, int]:
    """Work unit for the controller as configured before tuning: ``(max push, nan, sims)``."""
    sims = [0]
    push = max_recoverable_push(config_from_dict(config_dict), settings, counter=sims)
    return push, math.nan, sims[0]


# ---------------------------------------------------------------- search
def multiprocessing_supported() -> bool:
    """Whether worker processes may be used (Android: forked workers, see :mod:`.parallel`)."""
    return True


def default_workers() -> int:
    return parallel.default_workers()


_default_workers = default_workers  # backwards-compatible name


@dataclass
class TuneProgress:
    """Snapshot of a running search, for progress screens (all fields plain data)."""

    phase: str                       # "baseline" | "explore" | "refine" | "done"
    message: str                     # plain-language status line
    done: int                        # finished work units (baseline + candidates)
    total: int
    elapsed: float                   # s
    eta: float | None                # s remaining (estimate) or None at the start
    workers: int
    pool: str                        # "serial" | "processes" | "fork"
    pool_note: str = ""              # why the search runs on one core, if it does
    simulations: int = 0
    candidates_done: int = 0
    candidates_total: int = 0
    baseline_push: float | None = None
    best_push: float = 0.0
    best_cost: float = math.inf
    best_weights: LQRWeights | None = None
    best_controller: ControllerConfig | None = None
    history: list[float] = field(default_factory=list)   # best push after each candidate
    candidate_pushes: list[float] = field(default_factory=list)   # each candidate's own push (0 = rejected)

    @property
    def fraction(self) -> float:
        return min(self.done / self.total, 1.0) if self.total else 0.0


UpdateCallback = Callable[[TuneProgress], bool]   # False cancels


def robust_tune(
    config: AppConfig,
    settings: TuningSettings | None = None,
    progress: ProgressCallback | None = None,
    on_update: UpdateCallback | None = None,
    pool: parallel.Pool | None = None,
    cancel: CancelToken | None = None,
) -> TuningResult:
    """Search the LQR-weight space for the most push-tolerant controller.

    ``progress(fraction, message)`` (legacy) and ``on_update(TuneProgress)`` are called
    after every finished work unit; either returning ``False``, or ``cancel.cancel()``
    from another thread, stops the search. A cancelled search returns the best
    candidate so far (``result.cancelled``) or raises :class:`TuningCancelled` if
    there is none yet.
    """
    global _active_token
    s = settings or TuningSettings()
    cfg = config.copy()
    cfg.validate()
    started = time.perf_counter()
    rng = np.random.default_rng(s.seed)
    config_dict = config_to_dict(cfg)
    total = 1 + s.candidates
    token = cancel or CancelToken()
    own_pool = pool is None
    pool = parallel.make_pool(s.workers) if own_pool else pool
    state = TuneProgress(phase="baseline", message="Starting worker processes…", done=0, total=total,
                         elapsed=0.0, eta=None, workers=pool.workers, pool=pool.kind,
                         pool_note=getattr(pool, "reason", ""), candidates_total=s.candidates)
    best: Candidate | None = None
    history: list[float] = []

    def emit(phase: str, message: str) -> bool:
        state.phase, state.message = phase, message
        state.elapsed = time.perf_counter() - started
        state.eta = state.elapsed / state.done * (total - state.done) if state.done else None
        keep_going = not token.cancelled
        if progress is not None and not progress(state.fraction, message):
            keep_going = False
        if on_update is not None and on_update(state) is False:
            keep_going = False
        if not keep_going:
            token.cancel()
        return keep_going

    def set_best(cand: Candidate) -> None:
        nonlocal best
        best = cand
        state.best_push, state.best_cost = cand.max_push, cand.recovery_cost
        state.best_weights = _weights(cand.log_weights)
        try:
            state.best_controller = normalised_controller(cfg, state.best_weights)
        except (ValueError, np.linalg.LinAlgError, ArithmeticError):
            state.best_controller = None

    def run_batch(points: list[np.ndarray], phase: str, label: str, with_baseline: bool = False) -> bool:
        """Evaluate a batch; results are applied in input order (worker-count independent)."""
        threshold = best.max_push * (1.0 - 2.0 * s.resolution) if (best is not None and s.prune) else 0.0
        tasks: list[parallel.Task] = []
        if with_baseline:
            tasks.append((evaluate_baseline, (config_dict, s)))
        offset = len(tasks)
        tasks += [(evaluate_candidate, (config_dict, [float(v) for v in z], s, threshold)) for z in points]
        finished: dict[int, tuple] = {}
        next_index = 0
        for index, result in pool.imap_unordered(tasks):
            finished[index] = result
            state.done += 1
            state.simulations += int(result[2])
            if index >= offset:
                state.candidates_done += 1
            while next_index in finished:
                push, cost, _ = finished.pop(next_index)
                if next_index < offset:
                    state.baseline_push = push
                else:
                    cand = Candidate(np.asarray(points[next_index - offset]), push, cost)
                    if cand.max_push > 0 and cand.better_than(best, s.resolution):
                        set_best(cand)
                    history.append(best.max_push if best else 0.0)
                    state.history = list(history)
                    state.candidate_pushes.append(push)
                next_index += 1
            message = (f"{label}: tested {state.candidates_done} / {s.candidates} candidate controllers"
                       if state.candidates_done else "Measuring the current controller…")
            if not emit(phase, message):
                pool.cancel()
                return False
        return True

    z0 = np.log10([DEFAULT_WEIGHTS.cart_position, DEFAULT_WEIGHTS.angle, DEFAULT_WEIGHTS.cart_velocity,
                   DEFAULT_WEIGHTS.angular_velocity, DEFAULT_WEIGHTS.effort])
    lo, hi = WEIGHT_BOUNDS[:, 0], WEIGHT_BOUNDS[:, 1]
    stage1 = [z0] + [np.clip(z0 + rng.normal(0.0, 1.2, 5), lo, hi) for _ in range(s.samples)]

    cancelled = False
    previous_token, _active_token = _active_token, token
    try:
        emit("baseline", f"Measuring the current controller on {pool.describe()}…")
        if not run_batch(stage1, "explore", "Exploring", with_baseline=True):
            cancelled = True
        sigma = 0.6
        for r in range(s.refine_rounds):
            if cancelled or best is None:
                break
            batch = [np.clip(best.log_weights + rng.normal(0.0, sigma, 5), lo, hi) for _ in range(s.refine_samples)]
            if not run_batch(batch, "refine", f"Refining ({r + 1}/{s.refine_rounds})"):
                cancelled = True
            sigma *= 0.6
    except TuningCancelled:
        cancelled = True
    finally:
        _active_token = previous_token
        if own_pool:
            pool.close()

    if best is None:
        if cancelled or token.cancelled:
            raise TuningCancelled("tuning was cancelled before a candidate was evaluated")
        raise ValueError("no stabilising controller found for this system "
                         "(check the actuator limit and the physical parameters)")
    weights = _weights(best.log_weights)
    controller = normalised_controller(cfg, weights)
    state.done = total if not cancelled else state.done
    if progress is not None:
        progress(1.0 if not cancelled else state.fraction, "Done" if not cancelled else "Cancelled")
    state.phase, state.message = "done", "Done" if not cancelled else "Cancelled – best result so far"
    state.elapsed = time.perf_counter() - started
    state.eta = 0.0
    if on_update is not None:
        on_update(state)
    baseline = state.baseline_push
    if baseline is None:      # cancelled before the baseline finished: measure it now (serially)
        baseline = max_recoverable_push(cfg, s)
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


def estimate_seconds(settings: TuningSettings, push_test_s: float, workers: int) -> float:
    """Rough duration of a search from the time of one full-horizon push test.

    Heuristic averages (measured on the built-in scenarios): a fully evaluated candidate
    costs ≈ 16 push tests of ≈ 45 % of the horizon each (falls end early, recoveries stop
    early); with pruning about 60 % of the refinement candidates are rejected after one
    test. Parallel speed-up is taken as 0.8 × workers (uneven batch tails).
    """
    full = 16 * 0.45 * push_test_s
    explore = (2 + settings.samples) * full
    refine_n = settings.refine_rounds * settings.refine_samples
    refine = refine_n * (0.4 * full + 0.6 * push_test_s * 0.6) if settings.prune else refine_n * full
    speedup = max(1.0, 0.8 * workers) if workers > 1 else 1.0
    return (explore + refine) / speedup


def calibrate_push_test_seconds(config: AppConfig, settings: TuningSettings | None = None) -> float:
    """Wall time of one full-horizon push test on this device (for :func:`estimate_seconds`)."""
    s = settings or TuningSettings()
    cfg = _upright_config(config)
    started = time.perf_counter()
    _push_test(cfg, 0.0, s, allow_early_stop=False)
    return time.perf_counter() - started


def compare_responses(config: AppConfig, old: ControllerConfig, new: ControllerConfig,
                      push: float, settings: TuningSettings | None = None, duration: float = 5.0):
    """Experiments with the old and the new controller under the same tip push.

    Both start upright at rest; at 1 s the tip is pushed with ``push`` N for the tuner's
    pulse length. Returns ``(old_result, new_result)`` (:class:`.experiment.ExperimentResult`
    with settling time, overshoot, peak force, control energy …).
    """
    from .experiment import run_experiment

    s = settings or TuningSettings()
    results = []
    for name, ctrl in (("before tuning", old), ("after tuning", new)):
        cfg = _upright_config(config)
        cfg.controller = ctrl
        cfg.simulation.telemetry_rate_hz = 100.0
        cfg.experiment.duration = duration
        d = cfg.experiment.disturbance
        d.enabled, d.time, d.force, d.duration = True, 1.0, push, s.push_duration
        results.append(run_experiment(cfg, name))
    return results[0], results[1]


def round_nice(value: float) -> float:
    """Round down to 2 significant digits (for a friendly default push force)."""
    if value <= 0:
        return 0.0
    exponent = math.floor(math.log10(value)) - 1
    return round(math.floor(value / 10**exponent) * 10**exponent, max(0, -exponent))
