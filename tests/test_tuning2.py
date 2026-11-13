"""Auto-tune with options (quick-core-v2): limits, selected loops, integral gains, methods."""

from __future__ import annotations

import math

import pytest

from pendulum_sim.simulation.scenarios import scenario
from pendulum_sim.simulation.tuning import TuningSettings
from pendulum_sim.simulation.tuning2 import GainLimits, TermLimits, TuneOptions, full_controller, loops_space, tune

FAST = TuningSettings(samples=3, refine_rounds=1, refine_samples=2, workers=1, horizon=2.0, resolution=0.1)


def test_selected_loops_only_change_those_loops():
    cfg = scenario("double_cart_lqr")
    before = {lp.signal: (lp.kp, lp.ki, lp.kd) for lp in cfg.controller.loops}
    res = tune(cfg, FAST, TuneOptions(mode="loops", loops=["cart"], terms=("kp", "kd")))
    after = {lp.signal: (lp.kp, lp.ki, lp.kd) for lp in res.controller.loops}
    for sig, gains in before.items():
        if sig != "cart":
            assert after[sig] == gains                       # untouched
    assert after["cart"][1] == before["cart"][1]             # Ki not selected: unchanged
    assert res.max_push >= res.baseline_max_push * (1 - 2 * FAST.resolution)


def test_limits_bound_every_gain():
    cfg = scenario("single_cart_pid")
    limits = GainLimits(general=TermLimits(kp=(-80.0, 80.0), ki=(-5.0, 5.0), kd=(-10.0, 10.0)))
    res = tune(cfg, FAST, TuneOptions(mode="loops", limits=limits))
    for lp in res.controller.loops:
        assert -80 <= lp.kp <= 80 and -5 <= lp.ki <= 5 and -10 <= lp.kd <= 10


def test_per_loop_limits_override_the_general_ones():
    lim = GainLimits(general=TermLimits(kp=(-1, 1)), per_loop={"cart": TermLimits()})
    assert lim.for_loop("cart").kp is None and lim.for_loop("theta1").kp == (-1, 1)


def test_full_design_keeps_integral_where_it_helps_and_respects_limits():
    cfg = scenario("single_cart_pid")
    res = tune(cfg, FAST, TuneOptions(mode="full", integral=True))
    assert res.max_push > 0
    assert all(math.isfinite(lp.ki) for lp in res.controller.loops)
    cap = TermLimits(kp=(-1e9, 1e9), kd=(-1e9, 1e9))
    assert GainLimits(general=cap).allows(res.controller)


def test_impossible_limits_are_reported():
    cfg = scenario("single_cart_pid")
    limits = GainLimits(general=TermLimits(kp=(-1e-6, 1e-6), kd=(-1e-6, 1e-6)))
    with pytest.raises(ValueError, match="gain limits"):
        tune(cfg, FAST, TuneOptions(mode="full", integral=False, limits=limits))


def test_full_controller_integral_ratio():
    cfg = scenario("single_cart_pid")
    from pendulum_sim.simulation.tuning import DEFAULT_WEIGHTS

    c = full_controller(cfg, DEFAULT_WEIGHTS, [0.0] * 2)
    assert all(lp.ki == pytest.approx(lp.kp) for lp in c.loops)        # Ki/Kp = 10^0 = 1 / s


def test_loops_space_starts_at_the_current_gains():
    cfg = scenario("single_cart_pid")
    space = loops_space(cfg, TuneOptions(mode="loops", terms=("kp", "kd")))
    ctrl = space.build(space.z0)
    for a, b in zip(ctrl.loops, cfg.controller.loops):
        assert a.kp == pytest.approx(b.kp) and a.kd == pytest.approx(b.kd)


def test_options_from_plain_data():
    from pendulum_sim.simulation.tuning2 import options_from_dict

    o = options_from_dict(None, ["cart", "theta1"])
    assert o.mode == "full" and o.integral and not o.methods and o.limits.general.kp is None
    d = {"mode": "loops", "loops": ["cart", "nope"], "terms": ["kd"], "limit_mode": "per_loop",
         "limits": {"general": {"kp": [None, 100]}, "per_loop": {"cart": {"kd": [-5, 5]}}}}
    o = options_from_dict(d, ["cart", "theta1"])
    assert o.loops == ["cart"] and o.terms == ("kd",)
    assert o.limits.for_loop("theta1").kp == (-math.inf, 100.0)
    assert o.limits.for_loop("cart").kd == (-5.0, 5.0) and o.limits.for_loop("cart").kp is None
    d["limit_mode"] = "general"
    assert options_from_dict(d, ["cart"]).limits.per_loop == {}
    d["limit_mode"] = "none"
    assert options_from_dict(d, ["cart"]).limits.general.kp is None


def test_tuned_integral_stays_on_and_switched_off_gains_are_zero():
    cfg = scenario("double_cart_lqr")
    o = TuneOptions(mode="loops", loops=["cart"], terms=("kp", "ki"), off_terms=("kd",))
    space = loops_space(cfg, o)
    for z in (space.z0, space.lo, space.hi):
        ctrl = space.build(z)
        cart = next(lp for lp in ctrl.loops if lp.signal == "cart")
        assert cart.ki != 0.0 and cart.i_enabled                 # a gain chosen for tuning stays on
        assert cart.kd == 0.0 and not cart.d_enabled              # switched off
        other = [lp for lp in ctrl.loops if lp.signal != "cart"]
        assert all(a.kd == b.kd for a, b in zip(other, [lp for lp in cfg.controller.loops if lp.signal != "cart"]))


def test_only_switching_off_is_allowed():
    cfg = scenario("single_cart_pid")
    res = tune(cfg, FAST, TuneOptions(mode="loops", loops=["theta1"], terms=(), off_terms=("ki",)))
    loop = next(lp for lp in res.controller.loops if lp.signal == "theta1")
    assert loop.ki == 0.0 and loop.kp == 40.0


# ------------------------------------------------------------- hold position
def _with_cart_integral(cfg, ratio: float):
    from pendulum_sim.simulation.tuning import DEFAULT_WEIGHTS
    c = cfg.copy()
    c.controller = full_controller(cfg, DEFAULT_WEIGHTS, [ratio] + [-99.0] * (len(cfg.controller.loops) - 1))
    return c


def test_hold_residual_tells_a_cart_integral_from_pd():
    from pendulum_sim.simulation.tuning2 import OFF, hold_residual
    cfg = scenario("single_cart_pid")
    pd = hold_residual(_with_cart_integral(cfg, OFF), 0.2, FAST)
    pid = hold_residual(_with_cart_integral(cfg, math.log10(0.3)), 0.2, FAST)
    assert pd > 0.8                                   # a PD controller keeps the steady offset
    assert pid < 0.2                                  # the cart integral brings the cart back
    assert hold_residual(_with_cart_integral(cfg, OFF), 1e3, FAST) == 1.0     # it falls


def test_hold_score_and_options():
    from pendulum_sim.simulation.tuning2 import DEFAULT_OPTIONS, options_from_dict, score
    assert DEFAULT_OPTIONS["hold"] is False and options_from_dict({}, ["cart"]).hold is False
    assert options_from_dict({"hold": True}, ["cart"]).hold is True
    assert score(2.0, 0.3, False) == 2.0 and score(2.0, math.nan, True) == 2.0
    assert score(2.0, 1.0, True) == pytest.approx(1.0) and score(2.0, 0.0, True) == 2.0


def test_tune_with_hold_reports_the_residuals():
    cfg = scenario("single_cart_pid")
    res = tune(cfg, FAST, TuneOptions(mode="full", integral=True, hold=True))
    assert res.hold and res.held == "cart position"
    assert 0.0 <= res.hold_residual <= 1.0 and 0.0 <= res.baseline_hold_residual <= 1.0
    # the seeded cart-only integral holds position far better than the PD start
    assert res.hold_residual < 0.5
    off = tune(cfg, FAST, TuneOptions(mode="full", integral=True))
    assert not off.hold and math.isnan(off.hold_residual)
