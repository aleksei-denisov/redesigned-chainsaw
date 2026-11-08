"""The NumPy CARE solver (used on Android, where SciPy is not bundled) must match SciPy."""

from __future__ import annotations

import numpy as np
import pytest

from pendulum_sim.control import lqr_tuning
from pendulum_sim.control.lqr_tuning import LQRWeights, linearize, lqr_controller, lqr_gain
from pendulum_sim.control.riccati import care_residual, solve_care, solve_care_numpy
from pendulum_sim.physics import BaseType, PlantParams, SegmentParams

scipy_linalg = pytest.importorskip("scipy.linalg")

PLANTS = [(n, base) for n in (1, 2, 3) for base in (BaseType.CART, BaseType.FIXED)]


def _plant(n: int, base: BaseType) -> PlantParams:
    return PlantParams(base_type=base, segments=[SegmentParams() for _ in range(n)])


def _weights_matrices(plant: PlantParams):
    A, B, kept = linearize(plant)
    w = LQRWeights()
    n1 = plant.n_segments + 1
    diag = [w.cart_position if i == 0 else w.angle if i < n1 else w.cart_velocity if i == n1
            else w.angular_velocity for i in kept]
    return A, B, np.diag(diag), np.array([[w.effort]])


@pytest.mark.parametrize("n,base", PLANTS)
def test_numpy_care_matches_scipy(n, base):
    A, B, Q, R = _weights_matrices(_plant(n, base))
    P_ref = scipy_linalg.solve_continuous_are(A, B, Q, R)
    P = solve_care_numpy(A, B, Q, R)
    assert np.allclose(P, P_ref, rtol=1e-7, atol=1e-9 * np.abs(P_ref).max())
    assert care_residual(A, B, Q, R, P) < 1e-9
    assert np.allclose(P, P.T)


@pytest.mark.parametrize("n,base", PLANTS)
def test_lqr_gain_identical_with_numpy_backend(monkeypatch, n, base):
    plant = _plant(n, base)
    K_ref, kept_ref = lqr_gain(plant)
    monkeypatch.setattr(lqr_tuning, "solve_care", lambda A, B, Q, R: solve_care(A, B, Q, R, backend="numpy"))
    K, kept = lqr_gain(plant)
    assert kept == kept_ref
    assert np.allclose(K, K_ref, rtol=1e-6)
    ctrl = lqr_controller(plant)  # the full seeding path works without SciPy
    assert ctrl.loops


def test_random_stabilisable_systems():
    rng = np.random.default_rng(1)
    for size in (2, 4, 6, 8):
        for _ in range(5):
            A = rng.normal(size=(size, size))
            B = rng.normal(size=(size, 1))
            Q = np.eye(size) * rng.uniform(0.1, 10)
            R = np.array([[rng.uniform(0.01, 1)]])
            P_ref = scipy_linalg.solve_continuous_are(A, B, Q, R)
            P = solve_care_numpy(A, B, Q, R)
            assert np.allclose(P, P_ref, rtol=1e-6, atol=1e-8 * np.abs(P_ref).max())


def test_unstabilisable_raises():
    A = np.array([[1.0, 0.0], [0.0, 2.0]])
    B = np.array([[1.0], [0.0]])  # second unstable mode is uncontrollable
    with pytest.raises(np.linalg.LinAlgError):
        solve_care_numpy(A, B, np.eye(2), np.eye(1))


def test_unknown_backend():
    with pytest.raises(ValueError):
        solve_care(np.eye(1), np.eye(1), np.eye(1), np.eye(1), backend="magic")
