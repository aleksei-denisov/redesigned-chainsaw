"""Tests for the PID loop, multi-loop controller and LQR seeding."""

from __future__ import annotations

import math

import numpy as np
import pytest

from pendulum_sim.control import (
    AntiWindup,
    ControlAction,
    ControllerConfig,
    DerivativeMode,
    MultiLoopController,
    PIDLoop,
    PIDLoopConfig,
    linearize,
    lqr_controller,
    lqr_gain,
    wrap_angle,
)
from pendulum_sim.physics import BaseType
from pendulum_sim.simulation import AppConfig, Simulator, scenario
from pendulum_sim.simulation.scenarios import cart_plant, fixed_plant


def controller(loop: PIDLoopConfig, lo=-10.0, hi=10.0, freq=100.0,
               action=ControlAction.DIRECT) -> MultiLoopController:
    return MultiLoopController(ControllerConfig(frequency_hz=freq, output_min=lo, output_max=hi,
                                                action=action, loops=[loop]))


def q(theta: float) -> np.ndarray:
    return np.array([0.0, theta])


def test_wrap_angle():
    assert wrap_angle(0.0) == 0.0
    assert wrap_angle(math.pi) == pytest.approx(math.pi)
    assert wrap_angle(-math.pi) == pytest.approx(math.pi)
    assert wrap_angle(3 * math.pi / 2) == pytest.approx(-math.pi / 2)


def test_proportional_and_derivative_terms():
    c = controller(PIDLoopConfig(kp=2.0, ki=0.0, kd=0.5))
    out = c.step(0.0, q(0.1), np.array([0.0, 0.4]))
    assert out.loops[0].p == pytest.approx(0.2)
    assert out.loops[0].d == pytest.approx(0.2)
    assert out.command == pytest.approx(0.4)


def test_reverse_action_flips_sign():
    c = controller(PIDLoopConfig(kp=2.0, ki=0.0, kd=0.0), action=ControlAction.REVERSE)
    assert c.step(0.0, q(0.1), np.zeros(2)).command == pytest.approx(-0.2)


def test_component_switches():
    cfg = PIDLoopConfig(kp=2.0, ki=1.0, kd=0.5, p_enabled=False, d_enabled=False)
    c = controller(cfg)
    out = c.step(0.0, q(0.1), np.array([0.0, 1.0]))
    assert out.loops[0].p == 0.0 and out.loops[0].d == 0.0
    cfg.i_enabled = False
    c.step(0.01, q(0.1), np.zeros(2))
    assert c.loops[0].integral == 0.0


def test_integral_accumulates_at_controller_rate_and_holds_output():
    c = controller(PIDLoopConfig(kp=0.0, ki=1.0, kd=0.0), freq=100.0)
    t, dt = 0.0, 0.001
    updates = 0
    for _ in range(1000):  # 1 s of physics steps
        out = c.step(t, q(0.5), np.zeros(2))
        updates += out.updated
        t += dt
    assert updates == 100
    assert c.loops[0].integral == pytest.approx(0.5, rel=1e-9)


def test_output_limits():
    c = controller(PIDLoopConfig(kp=100.0, ki=0.0, kd=0.0), lo=-3.0, hi=5.0)
    assert c.step(0.0, q(1.0), np.zeros(2)).command == 5.0
    c.reset()
    assert c.step(0.0, q(-1.0), np.zeros(2)).command == -3.0


@pytest.mark.parametrize("mode", [AntiWindup.CLAMP, AntiWindup.BACK_CALCULATION])
def test_anti_windup_limits_integrator_growth(mode):
    def run(anti):
        c = controller(PIDLoopConfig(kp=10.0, ki=50.0, kd=0.0, anti_windup=anti), lo=-1, hi=1)
        for k in range(200):
            c.step(k * 0.01, q(0.5), np.zeros(2))
        return c.loops[0].integral

    wound = run(AntiWindup.NONE)
    limited = run(mode)
    assert wound == pytest.approx(50 * 0.5 * 2.0, rel=1e-6)
    assert abs(limited) < 0.2 * abs(wound)


def test_integral_limit():
    c = controller(PIDLoopConfig(kp=0.0, ki=10.0, kd=0.0, integral_limit=0.3, anti_windup=AntiWindup.NONE))
    for k in range(100):
        c.step(k * 0.01, q(1.0), np.zeros(2))
    assert c.loops[0].integral == pytest.approx(0.3)


def test_filtered_difference_derivative_tracks_slope():
    cfg = PIDLoopConfig(kp=0.0, ki=0.0, kd=1.0, derivative_mode=DerivativeMode.FILTERED_DIFFERENCE,
                        derivative_filter_hz=20.0)
    c = controller(cfg, lo=-100, hi=100, freq=1000.0)
    slope = 0.7
    out = None
    for k in range(1000):
        t = k * 0.001
        out = c.step(t, q(slope * t), np.zeros(2))
    assert out.loops[0].d == pytest.approx(slope, rel=1e-3)


def test_bumpless_gain_change_keeps_integrator():
    cfg = PIDLoopConfig(kp=0.0, ki=1.0, kd=0.0)
    c = controller(cfg)
    for k in range(10):
        c.step(k * 0.01, q(1.0), np.zeros(2))
    before = c.loops[0].integral
    new = ControllerConfig(frequency_hz=100.0, output_min=-10, output_max=10,
                           loops=[PIDLoopConfig(kp=5.0, ki=3.0, kd=0.0)])
    c.set_config(new)
    assert c.loops[0].integral == before


def test_loop_for_missing_segment_is_ignored():
    c = MultiLoopController(ControllerConfig(loops=[PIDLoopConfig(signal="theta3", kp=10)]))
    assert c.step(0.0, q(0.3), np.zeros(2)).command == 0.0


def test_invalid_controller_config():
    with pytest.raises(ValueError):
        ControllerConfig(output_min=1, output_max=0).validate()
    with pytest.raises(ValueError):
        ControllerConfig(loops=[PIDLoopConfig(signal="theta9")]).validate()


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("base", [BaseType.CART, BaseType.FIXED])
def test_lqr_closed_loop_is_stable_on_linear_model(n, base):
    plant = cart_plant(n, [0] * n) if base == BaseType.CART else fixed_plant(n, [0] * n)
    A, B, _ = linearize(plant)
    K, _ = lqr_gain(plant)
    eig = np.linalg.eigvals(A - B @ K.reshape(1, -1))
    assert np.all(eig.real < 0)
    # Open loop is unstable (it is an inverted pendulum).
    assert np.any(np.linalg.eigvals(A).real > 0)


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("base", [BaseType.CART, BaseType.FIXED])
def test_lqr_seeded_multiloop_balances_nonlinear_plant(n, base):
    angles = [3.0, -2.0, 1.0][:n]
    plant = cart_plant(n, angles, 60.0) if base == BaseType.CART else fixed_plant(n, angles)
    sim = Simulator(AppConfig(plant=plant, controller=lqr_controller(plant)))
    sim.advance(6.0)
    assert np.max(np.abs(sim.angles)) < math.radians(0.1)
    if base == BaseType.CART:
        assert abs(sim.state[0]) < 0.05


def test_single_pid_scenarios_balance():
    for key in ("single_cart_pid", "single_fixed_pid"):
        sim = Simulator(scenario(key))
        sim.advance(5.0)
        assert abs(sim.angles[0]) < math.radians(0.5), key


def test_uncontrolled_inverted_pendulum_falls():
    cfg = scenario("single_cart_pid")
    cfg.controller.enabled = False
    sim = Simulator(cfg)
    sim.advance(3.0)
    assert abs(sim.angles[0]) > math.radians(60)
