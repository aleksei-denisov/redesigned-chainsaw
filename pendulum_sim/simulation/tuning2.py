"""Auto-tune with options: integral gains, gain limits, selected loops, methods (quick-core-v2).

The robustness metric, the push test and the parallel evaluation are those of
:mod:`.tuning` (largest recoverable tip push; among equally robust controllers the one
with the smallest recovery cost). What is new is *what* is searched:

``mode = "full"`` – every loop at once, as before, through the five LQR weights (each
candidate a stabilising design mapped onto the loops' P and D gains), then **integral
gains**: with the best design fixed, a second search over each loop's integral rate
``Ki / Kp`` (1/s, log scale, "off" included). The result keeps integral action only
where it makes the controller more robust (``tests/test_tuning2.py``; the research
script ``scripts/research_pid_vs_pd.py`` compares PD and PID on the built-in scenarios).

``mode = "loops"`` – only the **selected loops** and **selected terms** (P, I, D); every
other loop keeps its values. A direct search over ``log10 |K|`` around the current gains
(sign kept; a gain that is zero starts from the LQR design's value for that loop), first
wide, then refined. Searching raw gains is only reasonable near a working controller –
which is what tuning a few loops of one means.

**Gain limits** – per loop and term (``GainLimits.per_loop``), or general ones for the
loops without their own, or none. In the direct search they bound the search box; in
the LQR search a design that breaks a limit is rejected (clipping an LQR design can
make it unstable) – if no design within the limits recovers from any push, the search
says so.

**Hold position** (optional, off by default) – a second test per candidate: a steady
tip force (``HOLD_FORCE`` × the candidate's recoverable push) for ``HOLD_SECONDS``. The
*held quantity* is the cart position, or without a cart segment 1's angle (on a cart the
pendulum must lean against a steady force, so the cart is what can be brought back). Its
residual ``r`` = mean |offset| over the last second / peak |offset| (0: back at the
setpoint, 1: a PD controller's steady offset, or it fell). Candidates are then compared by
``push · (1 − HOLD_WEIGHT · r)`` instead of the push alone: a controller that holds its
position wins against one that does not while it recovers from at least half the push.
With the integral search, the cart (or segment 1) integral alone at ``HOLD_SEEDS`` is
tried as well – the research found exactly that one useful
(``docs/QUICK_CORE_V2.md``).

**Methods** (optional) – after the gains: the alternative derivative source and
anti-windup strategy are tried for the tuned loops, one change at a time, and kept if
they help.

Desktop and Widgets apps keep :func:`.tuning.robust_tune` unchanged.
"""

from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..control import AntiWindup, ControllerConfig, DerivativeMode, LQRWeights, lqr_controller
from . import parallel
from .config import AppConfig, config_from_dict, config_to_dict
from .engine import SimulationDiverged, Simulator
from .tuning import (
    DEFAULT_WEIGHTS,
    WEIGHT_BOUNDS,
    CancelToken,
    TuneProgress,
    TuningCancelled,
    TuningResult,
    TuningSettings,
    _push_test,
    _upright_config,
    evaluate_baseline,
    max_recoverable_push,
    round_nice,
)
from . import tuning as _tuning

TERMS = ("kp", "ki", "kd")
KI_RATIO_BOUNDS = (-2.0, 1.5)        # log10(Ki/Kp) [1/s]: integral time 30 s … 0.03 s
OFF = -99.0                          # a search coordinate meaning "this term off"
HOLD_FORCE = 0.05                    # steady tip force of the hold test, × the candidate's push
HOLD_SECONDS = 12.0                  # s, length of the hold test
HOLD_WEIGHT = 0.5                    # score = push · (1 − HOLD_WEIGHT · residual)
HOLD_SEEDS = (-1.0, -0.7, -0.5)      # log10(Ki/Kp) of the held loop's integral tried with "hold"
HOLD_COST = 2.4                      # rough factor on the search time with the hold test


# ------------------------------------------------------------------ options
@dataclass
class TermLimits:
    """Signed ``(min, max)`` per gain, ``None`` = no limit."""

    kp: tuple[float, float] | None = None
    ki: tuple[float, float] | None = None
    kd: tuple[float, float] | None = None

    def get(self, term: str) -> tuple[float, float] | None:
        return getattr(self, term)

    def allows(self, term: str, value: float) -> bool:
        lim = self.get(term)
        return lim is None or lim[0] - 1e-12 <= value <= lim[1] + 1e-12


@dataclass
class GainLimits:
    general: TermLimits = field(default_factory=TermLimits)
    per_loop: dict[str, TermLimits] = field(default_factory=dict)     # by loop signal

    def for_loop(self, signal: str) -> TermLimits:
        return self.per_loop.get(signal, self.general)

    def allows(self, ctrl: ControllerConfig) -> bool:
        return all(self.for_loop(lp.signal).allows(t, getattr(lp, t)) for lp in ctrl.loops for t in TERMS)


@dataclass
class TuneOptions:
    mode: str = "full"                          # "full" | "loops"
    loops: list[str] = field(default_factory=list)   # "loops" mode: signals to tune (empty: all)
    terms: tuple[str, ...] = TERMS              # "loops" mode: gains to tune …
    off_terms: tuple[str, ...] = ()             # … and gains to switch off (0) in the selected loops;
                                                # the others keep their values
    integral: bool = True                       # "full" mode: search integral gains after the design
    methods: bool = False                       # try derivative source / anti-windup
    limits: GainLimits = field(default_factory=GainLimits)
    hold: bool = False                          # also score holding position under a steady push


@dataclass
class TuningResult2(TuningResult):
    """:class:`.tuning.TuningResult` plus the hold test (``nan`` when it was not run)."""

    hold: bool = False
    hold_residual: float = math.nan             # of the result
    baseline_hold_residual: float = math.nan    # of the controller before tuning
    held: str = ""                              # "cart position" / "segment 1 angle"


# ------------------------------------------------------------- controllers
def full_controller(cfg: AppConfig, weights: LQRWeights, ki_ratios: list[float] | None = None) -> ControllerConfig:
    """LQR design for every loop with the tuner's settings; ``ki_ratios[i]`` = log10(Ki/Kp) or OFF."""
    p = cfg.plant
    ctrl = lqr_controller(p, weights, base=cfg.controller)
    limit = p.actuator.max_output
    ctrl.enabled = True
    ctrl.output_min, ctrl.output_max = -limit, limit
    for i, loop in enumerate(ctrl.loops):
        loop.enabled = True
        loop.p_enabled = loop.i_enabled = loop.d_enabled = True
        r = ki_ratios[i] if ki_ratios is not None and i < len(ki_ratios) else OFF
        loop.ki = 0.0 if r <= OFF / 2 else loop.kp * 10.0 ** r
        loop.setpoint = 0.0
        loop.integral_limit = 0.0
        loop.anti_windup = AntiWindup.CLAMP
        loop.derivative_mode = DerivativeMode.MEASURED_RATE
    return ctrl


def _weights(z) -> LQRWeights:
    return LQRWeights(*(float(v) for v in 10.0 ** np.asarray(z)))


def _magnitude_bounds(sign: float, lim: tuple[float, float] | None, start: float) -> tuple[float, float] | None:
    """log10 |K| range for a gain of ``sign`` within ``lim`` and ±3 decades of ``start``."""
    lo, hi = math.log10(start) - 3.0, math.log10(start) + 3.0
    if lim is not None:
        a, b = (lim[0], lim[1]) if sign > 0 else (-lim[1], -lim[0])
        if b <= 0:
            return None                         # this sign is not allowed at all
        lo = max(lo, math.log10(max(a, 1e-9))) if a > 0 else lo
        hi = min(hi, math.log10(b))
    return (lo, hi) if hi > lo else (hi - 1e-6, hi)


@dataclass
class _Space:
    """A search space: start, bounds, and the controller of a point (``None``: not allowed)."""

    z0: np.ndarray
    lo: np.ndarray
    hi: np.ndarray
    build: Callable[[np.ndarray], ControllerConfig | None]
    sigma: float
    weights: Callable[[np.ndarray], LQRWeights | None] = lambda z: None

    def clip(self, z: np.ndarray) -> np.ndarray:
        return np.clip(z, self.lo, self.hi)


def loops_space(cfg: AppConfig, options: TuneOptions) -> _Space:
    """Direct search over the selected loops' gains (log10 |K|, sign kept)."""
    base = cfg.controller
    try:
        design = lqr_controller(cfg.plant, DEFAULT_WEIGHTS, base=base)
        ref = {lp.signal: lp for lp in design.loops}
    except Exception:  # noqa: BLE001 - a reference for zero gains only
        ref = {}
    chosen = set(options.loops) or {lp.signal for lp in base.loops}
    dims: list[tuple[int, str, float]] = []                # (loop index, term, sign)
    off_loops: list[int] = []                              # selected loops whose off_terms become 0
    z0, lo, hi = [], [], []
    for i, lp in enumerate(base.loops):
        if lp.signal not in chosen or not lp.enabled:
            continue
        lim = options.limits.for_loop(lp.signal)
        off_loops.append(i)
        for term in options.terms:
            if term in options.off_terms:
                continue
            cur = getattr(lp, term)
            if term == "ki":
                kp = abs(lp.kp) or abs(getattr(ref.get(lp.signal), "kp", 1.0)) or 1.0
                start = abs(cur) if cur else 0.1 * kp
                sign = math.copysign(1.0, cur) if cur else math.copysign(1.0, lp.kp or getattr(ref.get(lp.signal), "kp", 1.0))
            else:
                r = getattr(ref.get(lp.signal), term, 0.0) if ref else 0.0
                start = abs(cur) if cur else (abs(r) or 1.0)
                sign = math.copysign(1.0, cur) if cur else math.copysign(1.0, r or 1.0)
            b = _magnitude_bounds(sign, lim.get(term), start)
            if b is None:
                continue
            # a gain chosen for tuning stays on: the search looks for its best non-zero value
            dims.append((i, term, sign))
            z0.append(min(max(math.log10(start), b[0]), b[1]))
            lo.append(b[0])
            hi.append(b[1])
    if not dims and not (off_loops and options.off_terms):
        raise ValueError("nothing to tune: choose at least one loop and one gain (and check the gain limits)")
    lo_a = np.array(lo)

    def build(z: np.ndarray) -> ControllerConfig | None:
        ctrl = copy.deepcopy(base)
        for i in off_loops:
            for term in options.off_terms:
                setattr(ctrl.loops[i], term, 0.0)
                setattr(ctrl.loops[i], term[1] + "_enabled", False)
        for k, (i, term, sign) in enumerate(dims):
            setattr(ctrl.loops[i], term, sign * 10.0 ** float(z[k]))
            setattr(ctrl.loops[i], term[1] + "_enabled", True)       # p_enabled / i_enabled / d_enabled
        return ctrl if options.limits.allows(ctrl) else None

    return _Space(np.array(z0), lo_a, np.array(hi), build, sigma=0.5)


def weights_space(cfg: AppConfig, options: TuneOptions) -> _Space:
    z0 = np.log10([DEFAULT_WEIGHTS.cart_position, DEFAULT_WEIGHTS.angle, DEFAULT_WEIGHTS.cart_velocity,
                   DEFAULT_WEIGHTS.angular_velocity, DEFAULT_WEIGHTS.effort])

    def build(z: np.ndarray) -> ControllerConfig | None:
        try:
            ctrl = full_controller(cfg, _weights(z))
        except (ValueError, np.linalg.LinAlgError, ArithmeticError):
            return None
        return ctrl if options.limits.allows(ctrl) else None

    return _Space(z0, WEIGHT_BOUNDS[:, 0].copy(), WEIGHT_BOUNDS[:, 1].copy(), build, sigma=1.2, weights=_weights)


def integral_space(cfg: AppConfig, options: TuneOptions, weights: LQRWeights) -> _Space:
    """Integral rates log10(Ki/Kp) of every loop, the best design fixed (OFF = no integral)."""
    n = len(full_controller(cfg, weights).loops)
    lo, hi = KI_RATIO_BOUNDS

    def build(z: np.ndarray) -> ControllerConfig | None:
        ratios = [OFF if v < lo + 0.5 else float(v) for v in z]
        try:
            ctrl = full_controller(cfg, weights, ratios)
        except (ValueError, np.linalg.LinAlgError, ArithmeticError):
            return None
        return ctrl if options.limits.allows(ctrl) else None

    return _Space(np.full(n, lo - 0.25), np.full(n, lo - 0.5), np.full(n, hi), build, sigma=1.0,
                  weights=lambda z: weights)


def hold_seeds(space: _Space) -> list[np.ndarray]:
    """Integral rates with only the held loop's integral on (loop 0: the cart, or segment 1)."""
    out = []
    for r in HOLD_SEEDS:
        z = space.z0.copy()
        z[0] = r
        out.append(space.clip(z))
    return out


def method_variants(ctrl: ControllerConfig, signals: set[str]) -> list[ControllerConfig]:
    """One change at a time: the other derivative source, the other anti-windup strategies."""
    out = []
    for i, lp in enumerate(ctrl.loops):
        if lp.signal not in signals:
            continue
        for mode in DerivativeMode:
            if mode != lp.derivative_mode:
                c = copy.deepcopy(ctrl)
                c.loops[i].derivative_mode = mode
                out.append(c)
        if lp.ki != 0.0:
            for aw in AntiWindup:
                if aw != lp.anti_windup:
                    c = copy.deepcopy(ctrl)
                    c.loops[i].anti_windup = aw
                    out.append(c)
    return out


# ---------------------------------------------------------------- work unit
def held_name(cfg: AppConfig) -> str:
    return "cart position" if cfg.plant.has_cart else "segment 1 angle"


def hold_residual(cfg: AppConfig, force: float, settings: TuningSettings, seconds: float = HOLD_SECONDS) -> float:
    """Residual of the held quantity under a steady tip force (0 = back at the setpoint, 1 = not).

    The force acts from ``settings.push_time`` to the end. Held quantity: the cart
    position, or without a cart segment 1's angle. ``r`` = mean |q| over the last second
    divided by the peak |q|, clipped to [0, 1]; a fall counts as 1.
    """
    c = cfg.copy()
    for seg in c.plant.segments:                 # exactly upright: the offset is the force's
        seg.initial_angle = seg.initial_velocity = 0.0
    c.simulation.telemetry_rate_hz = 20.0
    sim = Simulator(c, telemetry_window_s=0.5)
    sim.apply_disturbance(force, seconds + 1.0, start=settings.push_time)
    dt = c.simulation.dt
    steps = int(round(seconds / dt))
    tail = int(round(1.0 / dt))
    fall = math.radians(settings.fall_deg)
    cart = sim.model.has_cart
    peak, tail_sum, tail_n = 0.0, 0.0, 0
    token = _tuning._active_token
    try:
        for k in range(steps):
            sim.step()
            q = abs(float(sim.state[0])) if cart else abs(float(sim.angles[0]))
            peak = max(peak, q)
            if k >= steps - tail:
                tail_sum += q
                tail_n += 1
            if k % 10 == 0:
                if float(np.max(np.abs(sim.angles))) > fall:
                    return 1.0
                if token is not None and k % 1000 == 0 and token.cancelled:
                    raise TuningCancelled
    except SimulationDiverged:
        return 1.0
    if peak < 1e-9:
        return 0.0
    return float(min(1.0, max(0.0, tail_sum / max(tail_n, 1) / peak)))


def score(push: float, residual: float, hold: bool) -> float:
    """What candidates are compared by: the push, reduced by a held quantity that stays off."""
    if not hold or push <= 0.0 or math.isnan(residual):
        return push
    return push * (1.0 - HOLD_WEIGHT * residual)


def evaluate_controller(config_dict: dict, settings: TuningSettings, min_push: float = 0.0,
                        hold: bool = False) -> tuple[float, float, int, float]:
    """Picklable work unit: ``(max push, recovery cost, simulations, hold residual)``.

    ``min_push`` (pruning) is compared with the *score*: with ``hold`` a candidate whose
    push is below it cannot win, since its score is at most its push. The residual is
    ``nan`` without ``hold``.
    """
    cfg = config_from_dict(config_dict)
    sims = [0]
    resid = math.nan if not hold else 1.0
    try:
        cfg.validate()
    except ValueError:
        return 0.0, math.inf, 0, resid
    if settings.prune and min_push > 0.0:
        sims[0] += 1
        if not _push_test(_upright_config(cfg), min_push, settings)[0]:
            return 0.0, math.inf, sims[0], resid
    push = max_recoverable_push(cfg, settings, counter=sims)
    if push <= 0.0:
        return 0.0, math.inf, sims[0], resid
    sims[0] += 1
    _, cost = _push_test(_upright_config(cfg), 0.5 * push, settings, allow_early_stop=False)
    if hold:
        sims[0] += 1
        resid = hold_residual(cfg, HOLD_FORCE * push, settings)
    return push, cost, sims[0], resid


def evaluate_start(config_dict: dict, settings: TuningSettings, hold: bool = False) -> tuple[float, float, int, float]:
    """Work unit for the controller before tuning: ``(max push, nan, sims, hold residual)``."""
    push, _, sims = evaluate_baseline(config_dict, settings)
    resid = math.nan
    if hold:
        sims += 1
        resid = hold_residual(config_from_dict(config_dict), HOLD_FORCE * push, settings) if push > 0 else 1.0
    return push, math.nan, sims, resid


@dataclass
class _Best:
    controller: ControllerConfig
    push: float
    cost: float
    weights: LQRWeights | None = None
    z: np.ndarray | None = None
    residual: float = math.nan
    score: float = 0.0

    def beaten_by(self, value: float, cost: float, tol: float) -> bool:
        """``value``: the challenger's score (= its push without the hold test)."""
        if value > self.score * (1.0 + tol):
            return True
        return value >= self.score * (1.0 - tol) and cost < self.cost


def budget(settings: TuningSettings, options: TuneOptions) -> int:
    """Candidates evaluated (without the baseline), for progress and estimates."""
    n = settings.candidates
    if options.mode == "full" and options.integral:
        n += 1 + settings.samples // 2 + settings.refine_rounds * settings.refine_samples // 2
        if options.hold:
            n += len(HOLD_SEEDS)
    if options.methods:
        n += 8
    return n


# ------------------------------------------------------------------- search
def tune(config: AppConfig, settings: TuningSettings | None = None, options: TuneOptions | None = None,
         on_update: Callable[[TuneProgress], bool] | None = None, pool: parallel.Pool | None = None,
         cancel: CancelToken | None = None) -> TuningResult:
    """Search with ``options``; same progress and cancel protocol as :func:`.tuning.robust_tune`."""
    s = settings or TuningSettings()
    o = options or TuneOptions()
    cfg = config.copy()
    cfg.validate()
    started = time.perf_counter()
    rng = np.random.default_rng(s.seed)
    total = 1 + budget(s, o)
    token = cancel or CancelToken()
    own_pool = pool is None
    pool = parallel.make_pool(s.workers) if own_pool else pool
    state = TuneProgress(phase="baseline", message="Starting worker processes…", done=0, total=total,
                         elapsed=0.0, eta=None, workers=pool.workers, pool=pool.kind,
                         pool_note=getattr(pool, "reason", ""), candidates_total=total - 1)
    best: _Best | None = None
    history: list[float] = []
    evaluations = 0
    start_residual = [math.nan]

    def emit(phase: str, message: str) -> bool:
        state.phase, state.message = phase, message
        state.elapsed = time.perf_counter() - started
        state.eta = state.elapsed / state.done * (total - state.done) if state.done else None
        keep = not token.cancelled
        if on_update is not None and on_update(state) is False:
            keep = False
        if not keep:
            token.cancel()
        return keep

    def config_with(ctrl: ControllerConfig) -> dict:
        c = cfg.copy()
        c.controller = ctrl
        return config_to_dict(c)

    def run(controllers: list[tuple[ControllerConfig | None, object]], phase: str, label: str,
            with_baseline: bool = False) -> bool:
        """Evaluate in input order (results do not depend on the number of workers)."""
        nonlocal best, evaluations
        threshold = best.score * (1.0 - 2.0 * s.resolution) if (best is not None and s.prune) else 0.0
        tasks: list[parallel.Task] = []
        if with_baseline:
            tasks.append((evaluate_start, (config_to_dict(cfg), s, o.hold)))
        offset = len(tasks)
        index_of: dict[int, int] = {}
        for k, (ctrl, _meta) in enumerate(controllers):
            if ctrl is not None:
                index_of[len(tasks)] = k
                tasks.append((evaluate_controller, (config_with(ctrl), s, threshold, o.hold)))
        results: dict[int, tuple] = {}
        rejected = [k for k, (ctrl, _m) in enumerate(controllers) if ctrl is None]
        for k in rejected:                                   # outside the limits: counted, not run
            results[k] = (0.0, math.inf, 0, math.nan)
        next_k = 0

        def drain() -> None:
            nonlocal best, next_k, evaluations
            while next_k in results:
                push, cost, _, resid = results.pop(next_k)
                ctrl, meta = controllers[next_k]
                evaluations += 1
                value = score(push, resid, o.hold)
                if ctrl is not None and push > 0 and (best is None or best.beaten_by(value, cost, s.resolution)):
                    w, z = meta if isinstance(meta, tuple) else (None, meta)
                    best = _Best(ctrl, push, cost, w, z, resid, value)
                    state.best_push, state.best_cost = push, cost
                    state.best_controller, state.best_weights = ctrl, w
                history.append(best.push if best else 0.0)
                state.history = list(history)
                state.candidate_pushes.append(push)
                next_k += 1

        state.done += len(rejected)
        state.candidates_done += len(rejected)
        drain()
        for index, result in pool.imap_unordered(tasks):
            state.done += 1
            state.simulations += int(result[2])
            if index < offset:
                state.baseline_push = result[0]
                start_residual[0] = result[3]
            else:
                state.candidates_done += 1
                results[index_of[index]] = result
            drain()
            msg = (f"{label}: tested {state.candidates_done} / {total - 1} candidate controllers"
                   if state.candidates_done else "Measuring the current controller…")
            if not emit(phase, msg):
                pool.cancel()
                return False
        drain()
        return True

    def search(space: _Space, samples: int, rounds: int, per_round: int, phase: str, label: str,
               with_baseline: bool = False, include_start: bool = True, seeds: list | None = None) -> bool:
        if space.z0.size == 0:                       # nothing searched (only gains switched off)
            samples, rounds = 0, 0
        pts = ([space.z0] if include_start else []) + list(seeds or []) + \
              [space.clip(space.z0 + rng.normal(0.0, space.sigma, space.z0.size)) for _ in range(samples)]
        if not run([(space.build(z), (space.weights(z), z)) for z in pts], phase, f"{label} – exploring", with_baseline):
            return False
        sigma = 0.5 * space.sigma
        for r in range(rounds):
            if best is None or best.z is None or best.z.size != space.z0.size:
                return True
            pts = [space.clip(best.z + rng.normal(0.0, sigma, space.z0.size)) for _ in range(per_round)]
            if not run([(space.build(z), (space.weights(z), z)) for z in pts], phase,
                       f"{label} – refining {r + 1}/{rounds}"):
                return False
            sigma *= 0.6
        return True

    cancelled = False
    previous, _tuning._active_token = _tuning._active_token, token
    try:
        emit("baseline", f"Measuring the current controller on {pool.describe()}…")
        if o.mode == "loops":
            space = loops_space(cfg, o)
            # the current controller, if within the limits, is the start (and the bar to beat)
            ok = search(space, s.samples, s.refine_rounds, s.refine_samples, "explore", "Gains",
                        with_baseline=True)
        else:
            ok = search(weights_space(cfg, o), s.samples, s.refine_rounds, s.refine_samples, "explore",
                        "LQR design", with_baseline=True)
            if ok and o.integral and best is not None and best.weights is not None:
                space = integral_space(cfg, o, best.weights)
                best.z = space.z0.copy()                    # the PD design is the integral search's start
                ok = search(space, s.samples // 2, s.refine_rounds, s.refine_samples // 2, "refine",
                            "Integral gains", include_start=True, seeds=hold_seeds(space) if o.hold else None)
        if ok and o.methods and best is not None:
            signals = set(o.loops) if o.mode == "loops" and o.loops else {lp.signal for lp in best.controller.loops}
            variants = method_variants(best.controller, signals)[:8]
            keep_w = best.weights
            ok = run([(v if o.limits.allows(v) else None, (keep_w, None)) for v in variants], "refine",
                     "Methods")
        cancelled = not ok
    except TuningCancelled:
        cancelled = True
    finally:
        _tuning._active_token = previous
        if own_pool:
            pool.close()

    if best is None:
        if cancelled or token.cancelled:
            raise TuningCancelled("tuning was cancelled before a candidate was evaluated")
        raise ValueError("no controller within the gain limits recovers from a push – widen the limits, "
                         "or tune other loops or gains")
    state.phase, state.message = "done", "Done" if not cancelled else "Cancelled – best result so far"
    state.elapsed = time.perf_counter() - started
    state.eta = 0.0
    if on_update is not None:
        on_update(state)
    baseline = state.baseline_push
    if baseline is None:
        baseline = max_recoverable_push(cfg, s)
    return TuningResult2(controller=best.controller, weights=best.weights or LQRWeights(), max_push=best.push,
                         baseline_max_push=baseline, recommended_push=round_nice(0.5 * best.push),
                         evaluations=evaluations, elapsed=time.perf_counter() - started, cancelled=cancelled,
                         history=history, hold=o.hold, hold_residual=best.residual,
                         baseline_hold_residual=start_residual[0], held=held_name(cfg))


# ------------------------------------------------------------ plain data
DEFAULT_OPTIONS = {"mode": "full", "integral": True, "methods": False, "hold": False, "loops": [],
                   "terms": list(TERMS), "off_terms": [], "limit_mode": "none", "limits": {"general": {}, "per_loop": {}}}


def _pair(v) -> tuple[float, float] | None:
    """``[min, max]`` (either may be ``None`` = open) → a closed pair, or ``None``."""
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    lo = -math.inf if v[0] is None else float(v[0])
    hi = math.inf if v[1] is None else float(v[1])
    if lo == -math.inf and hi == math.inf:
        return None
    return (min(lo, hi), max(lo, hi))


def _term_limits(d) -> TermLimits:
    d = d if isinstance(d, dict) else {}
    return TermLimits(*(_pair(d.get(t)) for t in TERMS))


def options_from_dict(d: dict | None, signals: list[str]) -> TuneOptions:
    """Options from the screen's plain data (also what is stored between runs).

    ``limit_mode``: ``none`` (no limits), ``general`` (the same for every loop) or
    ``per_loop`` (a loop's own limits where it has them, the general ones elsewhere).
    """
    d = {**DEFAULT_OPTIONS, **(d or {})}
    mode = d["mode"] if d["mode"] in ("full", "loops") else "full"
    loops = [s for s in d.get("loops") or [] if s in signals]
    off_terms = tuple(t for t in TERMS if t in (d.get("off_terms") or []))
    terms = tuple(t for t in TERMS if t in (d.get("terms") or []) and t not in off_terms)
    lim = d.get("limits") or {}
    limit_mode = d.get("limit_mode", "none")
    if limit_mode == "none":
        limits = GainLimits()
    else:
        per = lim.get("per_loop") or {} if limit_mode == "per_loop" else {}
        limits = GainLimits(_term_limits(lim.get("general")),
                            {s: _term_limits(v) for s, v in per.items() if s in signals and v is not None})
    return TuneOptions(mode=mode, loops=loops, terms=terms, off_terms=off_terms, integral=bool(d.get("integral", True)),
                       methods=bool(d.get("methods", False)), limits=limits, hold=bool(d.get("hold", False)))
