"""The fast scalar dynamics must reproduce the NumPy reference implementation."""

from __future__ import annotations

import numpy as np
import pytest

from pendulum_sim.physics import BaseType, MultiLinkPendulum, rk4_step

from .test_physics import make_plant


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("base", [BaseType.CART, BaseType.FIXED])
def test_scalar_accelerations_match_reference(n, base):
    plant = make_plant(n, base, friction=True)
    for seg in plant.segments:
        seg.joint_coulomb_friction = 0.003
    plant.track_length = 1.0
    model = MultiLinkPendulum(plant)
    rng = np.random.default_rng(7 + n)
    for _ in range(50):
        state = np.concatenate(([rng.uniform(-0.8, 0.8)], rng.uniform(-np.pi, np.pi, n),
                                rng.uniform(-6, 6, n + 1)))
        if base == BaseType.FIXED:
            state[0] = state[n + 1] = 0.0
        u, f = rng.uniform(-5, 5), rng.choice([0.0, rng.uniform(-2, 2)])
        expected = model.accelerations(state, u, f)
        actual = np.array(model._scalar.accelerations(state.tolist(), u, f))
        np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-10)


@pytest.mark.parametrize("n", [1, 2, 3])
def test_fast_rk4_matches_reference_trajectory(n):
    plant = make_plant(n, friction=True)
    plant.segments[0].initial_angle = 1.2
    model = MultiLinkPendulum(plant)
    a = b = model.initial_state()
    for _ in range(1000):
        a = rk4_step(lambda s: model.derivative(s, 0.7, 0.1), a, 1e-3)
        b = model.rk4_step(b, 0.7, 0.1, 1e-3)
    np.testing.assert_allclose(b, a, rtol=1e-9, atol=1e-9)
