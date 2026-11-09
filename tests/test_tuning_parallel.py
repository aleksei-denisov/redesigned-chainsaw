"""Parallel auto-tuning: same result as serial, cheaper evaluations, prompt cancel, progress."""

from __future__ import annotations

import math
import threading
import time

import numpy as np
import pytest

from pendulum_sim.simulation import parallel, scenario
from pendulum_sim.simulation.tuning import (
    CancelToken,
    TuningCancelled,
    TuningSettings,
    estimate_seconds,
    max_recoverable_push,
    robust_tune,
)

FAST = TuningSettings(horizon=2.5, samples=5, refine_rounds=1, refine_samples=4, resolution=0.08, workers=1)


def _signature(result):
    return (round(result.max_push, 9), round(result.baseline_max_push, 9),
            tuple(round(v, 9) for v in (result.weights.cart_position, result.weights.angle,
                                        result.weights.cart_velocity, result.weights.angular_velocity,
                                        result.weights.effort)),
            tuple(round(h, 9) for h in result.history))


@pytest.mark.parametrize("make_pool", [
    lambda: parallel.ForkPool(3),
    lambda: parallel.ProcessPool(2),
], ids=["fork", "processes"])
def test_parallel_result_equals_serial(make_pool):
    cfg = scenario("single_cart_full")
    serial = robust_tune(cfg, FAST, pool=parallel.SerialPool())
    pool = make_pool()
    with pool:
        par = robust_tune(cfg, FAST, pool=pool)
    assert _signature(par) == _signature(serial)


@pytest.mark.parametrize("key", ["single_cart_full", "single_fixed_pid"])
def test_pruning_and_early_stop_pick_the_same_controller(key):
    cfg = scenario(key)
    plain = robust_tune(cfg, TuningSettings(**{**FAST.__dict__, "prune": False, "early_stop": False}))
    fast = robust_tune(cfg, FAST)
    for a, b in zip(_signature(plain)[2], _signature(fast)[2]):
        assert a == pytest.approx(b)
    assert fast.max_push == pytest.approx(plain.max_push, rel=1e-9)


@pytest.mark.parametrize("key", ["single_cart_full", "double_cart_lqr", "single_fixed_pid"])
def test_early_stop_does_not_change_the_push_metric(key):
    cfg = scenario(key)
    s = TuningSettings(resolution=0.05)
    full = TuningSettings(resolution=0.05, early_stop=False)
    assert max_recoverable_push(cfg, s) == pytest.approx(max_recoverable_push(cfg, full), rel=1e-12)


def test_progress_reports_everything_a_progress_screen_needs():
    updates = []
    result = robust_tune(scenario("single_fixed_pid"), FAST, on_update=lambda u: updates.append(
        (u.phase, u.done, u.total, u.eta, u.workers, u.best_push, len(u.history), u.message)) or True)
    dones = [u[1] for u in updates]
    assert dones == sorted(dones) and dones[-1] == updates[-1][2]
    assert updates[-1][0] == "done" and updates[-1][3] == 0.0
    assert any(u[3] is not None and u[3] > 0 for u in updates[1:-1])     # an ETA while running
    assert "candidate" in updates[-2][7]
    assert updates[-1][6] == FAST.candidates and result.evaluations == FAST.candidates


@pytest.mark.parametrize("pool_factory", [lambda: parallel.SerialPool(), lambda: parallel.ForkPool(2)],
                         ids=["serial", "fork"])
def test_cancel_is_prompt(pool_factory):
    s = TuningSettings(horizon=4.0, samples=30, refine_rounds=3, refine_samples=8, workers=1)
    token = CancelToken()
    out = {}

    def run():
        pool = pool_factory()
        try:
            out["result"] = robust_tune(scenario("double_cart_lqr"), s, pool=pool, cancel=token)
        except TuningCancelled as exc:
            out["cancelled"] = exc
        finally:
            pool.close()

    th = threading.Thread(target=run)
    th.start()
    time.sleep(1.5)
    t = time.perf_counter()
    token.cancel()
    th.join(30)
    assert not th.is_alive()
    assert time.perf_counter() - t < 6.0
    assert "cancelled" in out or out["result"].cancelled


def test_estimate_is_positive_and_scales_with_workers():
    s = TuningSettings()
    one = estimate_seconds(s, 0.1, 1)
    four = estimate_seconds(s, 0.1, 4)
    assert one > four > 0 and math.isfinite(one)
    assert estimate_seconds(TuningSettings.preset("quick"), 0.1, 1) < one < \
        estimate_seconds(TuningSettings.preset("thorough"), 0.1, 1)


def test_worker_errors_are_reported():
    with parallel.ForkPool(2) as pool:
        with pytest.raises(parallel.WorkerError, match="ZeroDivisionError"):
            parallel.run_all(pool, [(divmod, (1, 0))])
        assert parallel.run_all(pool, [(divmod, (7, 2))]) == [(3, 1)]
    assert np.isfinite(parallel.default_workers())


def test_compare_responses_and_calibration():
    from pendulum_sim.simulation.tuning import calibrate_push_test_seconds, compare_responses

    cfg = scenario("single_cart_full")
    result = robust_tune(cfg, FAST)
    old, new = compare_responses(cfg, cfg.controller, result.controller, result.recommended_push, duration=3.0)
    assert old.name == "before tuning" and new.name == "after tuning"
    assert not new.metrics.fell and new.metrics.control_peak > 0
    assert new.data["t"][-1] == pytest.approx(3.0, abs=0.02)
    assert 0 < calibrate_push_test_seconds(cfg) < 30
