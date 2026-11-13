"""A horizontal push at any point of a segment (quick-core-v2 physics extension)."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from pendulum_sim.physics import MultiLinkPendulum
from pendulum_sim.physics.integrators import rk4_step
from pendulum_sim.simulation.engine import Simulator
from pendulum_sim.simulation.scenarios import scenario


@pytest.fixture(params=["triple_cart_lqr", "double_fixed_lqr"])
def model(request):
    return MultiLinkPendulum(scenario(request.param).plant)


def _state(m):
    rng = np.random.default_rng(3)
    s = rng.normal(0.0, 0.4, m.state_size)
    if not m.has_cart:                                 # a fixed pivot has no cart coordinate
        s[0] = s[m.n + 1] = 0.0
    return s


def test_arms_of_the_tip_and_of_joints(model):
    n = model.n
    assert np.allclose(model.push_arms(-1, 1.0), model.lengths)
    assert np.allclose(model.push_arms(n - 1, 1.0), model.lengths)
    if n > 1:                                          # the top of segment 1 = the bottom of segment 2
        assert np.allclose(model.push_arms(0, 1.0), model.push_arms(1, 0.0))
    assert np.allclose(model.push_arms(0, 0.0), 0.0)   # at the pivot: only the cart is pushed


def test_point_force_at_the_tip_equals_the_tip_push(model):
    s = _state(model)
    f = 3.0
    tip = model.derivative(s, 0.5, f)
    point = model.derivative(s, 0.5, f, f * model.push_arms(-1, 1.0))
    assert np.allclose(tip, point, atol=1e-12)


def test_fast_and_reference_dynamics_agree_for_a_point_force(model):
    s = _state(model)
    arms = 2.0 * model.push_arms(model.n - 1, 0.4)
    ref = rk4_step(lambda x: model.derivative(x, 0.3, 2.0, arms), s, 1e-3)
    fast = model.rk4_step(s, 0.3, 2.0, 1e-3, arms)
    assert np.allclose(ref, fast, atol=1e-10)


def test_power_of_a_point_force_is_force_times_point_velocity(model):
    """Q · q̇ (push part) = F · ẋ_point: the generalised force is the right Jᵀ F."""
    s = _state(model)
    n1 = model.n + 1
    q, qd = s[:n1], s[n1:]
    seg, r, F = model.n - 1, 0.35, 4.0
    a = model.push_arms(seg, r)
    q_push = model.generalized_forces(q, qd, 0.0, F, F * a) - model.generalized_forces(q, qd, 0.0, 0.0)
    xdot = (qd[0] if model.has_cart else 0.0) + np.sum(a * np.cos(q[1:]) * qd[1:])
    power = np.dot(q_push, qd) if model.has_cart else np.dot(q_push[1:], qd[1:])
    assert power == pytest.approx(F * xdot, rel=1e-12, abs=1e-12)


def test_engine_tip_push_unchanged_and_point_push_differs():
    cfg = scenario("triple_cart_lqr")
    runs = {}
    for label, kwargs in (("tip", {}), ("tip2", {"segment": 2, "ratio": 1.0}), ("mid", {"segment": 1, "ratio": 0.5})):
        sim = Simulator(copy.deepcopy(cfg))
        sim.apply_disturbance(2.0, 0.1, **kwargs)
        for _ in range(500):
            sim.step()
        runs[label] = sim.state.copy()
    assert np.array_equal(runs["tip"], runs["tip2"])        # the top of the last segment is the tip
    assert not np.allclose(runs["tip"], runs["mid"])
