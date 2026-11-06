"""Validation of the multibody pendulum model against independent derivations."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from pendulum_sim.physics import (
    Actuator,
    ActuatorParams,
    BaseType,
    MultiLinkPendulum,
    PlantParams,
    SegmentParams,
    rk4_step,
)


def make_plant(n: int, base: BaseType = BaseType.CART, friction: bool = False) -> PlantParams:
    segments = []
    for i in range(n):
        length = [0.6, 0.45, 0.3][i]
        mass = [0.3, 0.2, 0.15][i]
        segments.append(
            SegmentParams(
                length=length,
                mass=mass,
                com_ratio=[0.5, 0.42, 0.6][i],
                inertia=SegmentParams.rod_inertia(mass, length) * [1.0, 0.8, 1.3][i],
                joint_viscous_friction=0.002 if friction else 0.0,
                joint_coulomb_friction=0.0,
            )
        )
    return PlantParams(
        base_type=base,
        gravity=9.81,
        cart_mass=0.8,
        cart_viscous_friction=0.05 if friction else 0.0,
        track_length=100.0,
        segments=segments,
    )


# --------------------------------------------------------------------------
# Independent Lagrangian built only from body kinematics (no shared formulas)
# --------------------------------------------------------------------------
def com_velocities(model: MultiLinkPendulum, q: np.ndarray, qd: np.ndarray) -> np.ndarray:
    """Numerically differentiate the COM positions along the motion."""
    eps = 1e-6
    n1 = model.n + 1
    sp = np.concatenate((q + eps * qd, np.zeros(n1)))
    sm = np.concatenate((q - eps * qd, np.zeros(n1)))
    return (model.kinematics(sp).coms - model.kinematics(sm).coms) / (2 * eps)


def kinetic_energy_independent(model: MultiLinkPendulum, q, qd) -> float:
    v = com_velocities(model, q, qd)
    t = 0.5 * model.params.cart_mass * qd[0] ** 2 if model.has_cart else 0.0
    t += 0.5 * np.sum(model.masses * np.sum(v**2, axis=1))
    t += 0.5 * np.sum(model.inertias * qd[1:] ** 2)
    return float(t)


def potential_energy_independent(model: MultiLinkPendulum, q) -> float:
    s = np.concatenate((q, np.zeros_like(q)))
    return float(model.params.gravity * np.sum(model.masses * model.kinematics(s).coms[:, 1]))


def lagrange_accelerations(model: MultiLinkPendulum, q, qd) -> np.ndarray:
    """q̈ from Euler–Lagrange equations using finite differences of T and V."""
    n1 = model.n + 1
    eps = 1e-5
    E = np.eye(n1)

    def T(qq, vv):
        return kinetic_energy_independent(model, qq, vv)

    # Mass matrix as the Hessian of T w.r.t. velocities (T is quadratic in q̇).
    M = np.zeros((n1, n1))
    for i in range(n1):
        for j in range(n1):
            M[i, j] = (
                T(q, E[i] + E[j]) - T(q, E[i] - E[j]) - T(q, -E[i] + E[j]) + T(q, -E[i] - E[j])
            ) / 4.0
    # p = M q̇ ;  d/dt p = M q̈ + Ṁ q̇,  Ṁ = Σ ∂M/∂q_k q̇_k
    def mass(qq):
        out = np.zeros((n1, n1))
        for i in range(n1):
            for j in range(n1):
                out[i, j] = (
                    T(qq, E[i] + E[j]) - T(qq, E[i] - E[j]) - T(qq, -E[i] + E[j]) + T(qq, -E[i] - E[j])
                ) / 4.0
        return out

    Mdot = (mass(q + eps * qd) - mass(q - eps * qd)) / (2 * eps)
    dT_dq = np.array([(T(q + eps * E[k], qd) - T(q - eps * E[k], qd)) / (2 * eps) for k in range(n1)])
    dV_dq = np.array(
        [
            (potential_energy_independent(model, q + eps * E[k]) - potential_energy_independent(model, q - eps * E[k]))
            / (2 * eps)
            for k in range(n1)
        ]
    )
    rhs = -Mdot @ qd + dT_dq - dV_dq
    if model.has_cart:
        return np.linalg.solve(M, rhs)
    out = np.zeros(n1)
    out[1:] = np.linalg.solve(M[1:, 1:], rhs[1:])
    return out


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("base", [BaseType.CART, BaseType.FIXED])
def test_equations_of_motion_match_independent_lagrangian(n, base):
    model = MultiLinkPendulum(make_plant(n, base))
    rng = np.random.default_rng(42 + n)
    for _ in range(5):
        q = np.concatenate(([rng.uniform(-0.5, 0.5)], rng.uniform(-math.pi, math.pi, n)))
        qd = rng.uniform(-3.0, 3.0, n + 1)
        if base == BaseType.FIXED:
            q[0] = qd[0] = 0.0
        state = np.concatenate((q, qd))
        expected = lagrange_accelerations(model, q, qd)
        actual = model.accelerations(state)
        np.testing.assert_allclose(actual, expected, rtol=1e-4, atol=1e-4)


@pytest.mark.parametrize("n", [1, 2, 3])
def test_kinetic_energy_matches_body_velocities(n):
    model = MultiLinkPendulum(make_plant(n))
    q = np.array([0.1, 0.3, -0.7, 1.1][: n + 1])
    qd = np.array([0.4, -1.2, 2.0, 0.5][: n + 1])
    state = np.concatenate((q, qd))
    assert model.kinetic_energy(state) == pytest.approx(kinetic_energy_independent(model, q, qd), rel=1e-6)


def test_single_cart_pole_matches_textbook_equations():
    """Compare with the closed-form cart-pole equations (point mass + inertia)."""
    plant = make_plant(1)
    model = MultiLinkPendulum(plant)
    seg = plant.segments[0]
    M, m, l, I, g = plant.cart_mass, seg.mass, seg.com_distance, seg.inertia, plant.gravity
    th, thd, F = 0.4, 1.3, 2.5
    # (M+m) ẍ + m l cosθ θ̈ − m l sinθ θ̇² = F ;  m l cosθ ẍ + (I+ml²) θ̈ − m g l sinθ = 0
    A = np.array([[M + m, m * l * math.cos(th)], [m * l * math.cos(th), I + m * l**2]])
    b = np.array([F + m * l * math.sin(th) * thd**2, m * g * l * math.sin(th)])
    expected = np.linalg.solve(A, b)
    actual = model.accelerations(np.array([0.0, th, 0.0, thd]), actuator=F)
    np.testing.assert_allclose(actual, expected, rtol=1e-12)


def test_upright_equilibrium_is_stationary():
    for n in (1, 2, 3):
        model = MultiLinkPendulum(make_plant(n))
        np.testing.assert_allclose(model.accelerations(np.zeros(2 * (n + 1))), 0.0, atol=1e-14)


def test_small_oscillation_period_of_hanging_pendulum():
    """Fixed-base compound pendulum: T = 2π √((I + m c²) / (m g c))."""
    plant = make_plant(1, BaseType.FIXED)
    seg = plant.segments[0]
    seg.initial_angle = math.pi - 0.01
    model = MultiLinkPendulum(plant)
    expected = 2 * math.pi * math.sqrt((seg.inertia + seg.mass * seg.com_distance**2) /
                                       (seg.mass * plant.gravity * seg.com_distance))
    state, dt, t = model.initial_state(), 1e-3, 0.0
    crossings = []
    prev = state[1] - math.pi
    while len(crossings) < 3 and t < 10:
        state = rk4_step(model.derivative, state, dt)
        t += dt
        cur = state[1] - math.pi
        if prev < 0 <= cur:
            crossings.append(t - dt * cur / (cur - prev))
        prev = cur
    period = crossings[2] - crossings[1]
    assert period == pytest.approx(expected, rel=1e-3)


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("base", [BaseType.CART, BaseType.FIXED])
def test_energy_conserved_without_friction(n, base):
    plant = make_plant(n, base)
    for i, seg in enumerate(plant.segments):
        seg.initial_angle = [2.0, -1.0, 0.5][i]
        seg.initial_velocity = [0.0, 1.0, -2.0][i]
    model = MultiLinkPendulum(plant)
    state = model.initial_state()
    e0 = model.total_energy(state)
    scale = model.total_mass * plant.gravity * 1.0
    for _ in range(3000):
        state = rk4_step(model.derivative, state, 5e-4)
    assert abs(model.total_energy(state) - e0) / scale < 1e-5


@pytest.mark.parametrize("n", [1, 2, 3])
def test_horizontal_momentum_conserved_on_free_cart(n):
    plant = make_plant(n)
    plant.segments[0].initial_angle = 1.0
    model = MultiLinkPendulum(plant)
    state = model.initial_state()
    p0 = model.horizontal_momentum(state)
    for _ in range(2000):
        state = rk4_step(model.derivative, state, 1e-3)
    # Momentum is non-linear in q, so RK4 conserves it only to truncation error.
    assert model.horizontal_momentum(state) == pytest.approx(p0, abs=1e-5)


def test_friction_dissipates_energy():
    plant = make_plant(3, friction=True)
    for seg in plant.segments:
        seg.joint_coulomb_friction = 0.002
    plant.segments[0].initial_angle = 2.5
    model = MultiLinkPendulum(plant)
    state = model.initial_state()
    energies = [model.total_energy(state)]
    for _ in range(10):
        for _ in range(200):
            state = rk4_step(model.derivative, state, 1e-3)
        energies.append(model.total_energy(state))
    assert all(b <= a + 1e-9 for a, b in zip(energies, energies[1:]))
    assert energies[-1] < energies[0]


def test_rk4_agrees_with_scipy_adaptive_solver():
    plant = make_plant(2, friction=True)
    plant.segments[0].initial_angle = 0.8
    plant.segments[1].initial_angle = -0.4
    model = MultiLinkPendulum(plant)
    s0 = model.initial_state()
    ref = solve_ivp(lambda t, s: model.derivative(s, 0.5), (0, 2.0), s0, rtol=1e-10, atol=1e-12)
    errors = []
    for dt in (2e-3, 1e-3):
        state = s0
        for _ in range(round(2.0 / dt)):
            state = rk4_step(lambda s: model.derivative(s, 0.5), state, dt)
        errors.append(np.max(np.abs(state - ref.y[:, -1])))
    assert errors[1] < 1e-4
    assert errors[0] / errors[1] > 10  # 4th-order convergence (ideal ratio 16)


def test_track_end_stop_keeps_cart_near_limit():
    plant = make_plant(1)
    plant.track_length = 1.0
    model = MultiLinkPendulum(plant)
    state = model.initial_state()
    state[2] = 3.0  # cart moving right at 3 m/s
    max_x = 0.0
    for _ in range(3000):
        state = rk4_step(model.derivative, state, 1e-3)
        max_x = max(max_x, state[0])
    assert max_x < 0.5 + 0.1


def test_tip_force_generalised_force_matches_virtual_work():
    model = MultiLinkPendulum(make_plant(3))
    state = np.array([0.0, 0.3, -0.2, 0.9, 0, 0, 0, 0], dtype=float)
    q = state[:4]
    Q = model.generalized_forces(q, np.zeros(4), 0.0, 2.0) - model.generalized_forces(q, np.zeros(4), 0.0, 0.0)
    eps = 1e-7
    for k in range(4):
        dq = np.zeros(8)
        dq[k] = eps
        dx_tip = (model.kinematics(state + dq).tip[0] - model.kinematics(state - dq).tip[0]) / (2 * eps)
        assert Q[k] == pytest.approx(2.0 * dx_tip, rel=1e-6, abs=1e-9)


def test_actuator_saturation_lag_rate_and_deadband():
    act = Actuator(ActuatorParams(max_output=10.0, rate_limit=0.0, time_constant=0.0, deadband=0.5))
    assert act.step(0.3, 1e-3) == 0.0
    assert act.step(50.0, 1e-3) == 10.0
    assert act.step(-50.0, 1e-3) == -10.0

    lag = Actuator(ActuatorParams(max_output=100.0, time_constant=0.1))
    for _ in range(100):  # 0.1 s = one time constant
        lag.step(1.0, 1e-3)
    assert lag.output == pytest.approx(1 - math.exp(-1), rel=1e-9)

    rate = Actuator(ActuatorParams(max_output=100.0, rate_limit=10.0, time_constant=0.0))
    rate.step(5.0, 0.1)
    assert rate.output == pytest.approx(1.0)


def test_invalid_parameters_are_rejected():
    plant = make_plant(1)
    plant.segments[0].mass = -1
    with pytest.raises(ValueError):
        MultiLinkPendulum(plant)
    plant = make_plant(1)
    plant.segments = []
    with pytest.raises(ValueError):
        plant.validate()
