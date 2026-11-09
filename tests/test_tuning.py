"""Tests for the robust auto-tuner and its push-robustness metric."""

from __future__ import annotations

import math

import numpy as np

from pendulum_sim.simulation import Simulator, scenario
from pendulum_sim.simulation.tuning import (
    TuningSettings,
    max_recoverable_push,
    robust_tune,
    round_nice,
)

FAST = TuningSettings(horizon=2.5, samples=3, refine_rounds=1, refine_samples=2, resolution=0.08, workers=1)


def test_no_controller_means_no_push_tolerance():
    cfg = scenario("single_fixed_pid")
    cfg.controller.enabled = False
    assert max_recoverable_push(cfg, FAST) == 0.0


def test_push_metric_is_consistent_with_simulation():
    cfg = scenario("single_fixed_pid")
    limit = max_recoverable_push(cfg, FAST)
    assert limit > 0.0
    for force, expect_ok in ((0.8 * limit, True), (1.5 * limit + 0.05, False)):
        c = cfg.copy()
        c.plant.segments[0].initial_angle = math.radians(0.2)
        sim = Simulator(c, telemetry_window_s=None)
        sim.apply_disturbance(force, FAST.push_duration, start=FAST.push_time)
        sim.advance(FAST.horizon)
        peak = np.max(np.abs(sim.telemetry.column("theta1")))
        recovered = peak < math.radians(FAST.fall_deg) and abs(sim.angles[0]) < math.radians(FAST.settle_deg)
        assert recovered == expect_ok, (force, math.degrees(peak))


def test_tuning_does_not_get_worse_and_is_stabilising():
    cfg = scenario("single_fixed_pid")
    cfg.controller.loops[0].kp = 1.5       # a badly detuned controller
    result = robust_tune(cfg, FAST)
    assert result.max_push >= result.baseline_max_push
    assert result.max_push > 0
    assert 0 < result.recommended_push <= result.max_push
    tuned = cfg.copy()
    tuned.controller = result.controller
    sim = Simulator(tuned)
    sim.advance(4.0)
    assert abs(sim.angles[0]) < math.radians(0.5)
    loop = result.controller.loops[0]
    assert loop.ki == 0.0 and loop.enabled and loop.p_enabled and loop.d_enabled


def test_tuning_with_worker_processes():
    settings = TuningSettings(horizon=2.0, samples=2, refine_rounds=0, resolution=0.1, workers=2)
    result = robust_tune(scenario("single_fixed_pid"), settings)
    assert result.max_push > 0


def test_tuning_can_be_cancelled():
    calls = []

    def progress(fraction: float, message: str) -> bool:
        calls.append(fraction)
        return len(calls) < 3

    result = robust_tune(scenario("single_fixed_pid"), FAST, progress)
    assert result.cancelled


def test_round_nice():
    assert round_nice(0.2649) == 0.26
    assert round_nice(3.88) == 3.8
    assert np.isclose(round_nice(0.0612), 0.061)
    assert round_nice(0.0) == 0.0


def test_no_worker_processes_on_android(monkeypatch):
    import sys

    from pendulum_sim.simulation import tuning

    monkeypatch.setattr(sys, "getandroidapilevel", lambda: 34, raising=False)
    assert not tuning.multiprocessing_supported()
    assert tuning.default_workers() == 1
