"""Linearisation and LQR-based seeding of the multi-loop PID gains.

For the 2- and 3-link pendulums, hand-tuning a set of coupled PID loops is very
hard. This module linearises the plant about the upright equilibrium,
``ṡ ≈ A s + B u``, solves the continuous algebraic Riccati equation (SciPy
when installed, otherwise the NumPy solver in :mod:`.riccati`) for
``u = −K s`` and maps the state-feedback gains onto the PD terms of the
parallel loops. With the controller-action sign ``σ`` (+1 direct, −1 reverse)
the loop error is ``e = σ·y`` so ``Kp = −σ·K_position`` and
``Kd = −σ·K_velocity``. Integral gains are left to the user (default 0).

The actuator lag and the controller sample rate are not part of the design
model, so the result is a starting point that the user can refine.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..physics import MultiLinkPendulum, PlantParams
from .multiloop import ControllerConfig
from .riccati import solve_care
from .pid import AntiWindup, ControlAction, DerivativeMode, PIDLoopConfig


@dataclass
class LQRWeights:
    """Diagonal LQR weights."""

    cart_position: float = 10.0
    angle: float = 100.0
    cart_velocity: float = 1.0
    angular_velocity: float = 1.0
    effort: float = 0.1


def linearize(plant: PlantParams, eps: float = 1e-6) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Return ``(A, B, kept_indices)`` of the model linearised at the upright.

    For a fixed base the cart states are removed; ``kept_indices`` maps the
    rows of ``A`` back to the full state vector.
    """
    model = MultiLinkPendulum(plant)
    n1 = model.n + 1
    size = 2 * n1
    s0 = np.zeros(size)
    A = np.zeros((size, size))
    for k in range(size):
        d = np.zeros(size)
        d[k] = eps
        A[:, k] = (model.derivative(s0 + d) - model.derivative(s0 - d)) / (2 * eps)
    B = ((model.derivative(s0, eps) - model.derivative(s0, -eps)) / (2 * eps)).reshape(-1, 1)
    kept = list(range(size)) if model.has_cart else [i for i in range(size) if i not in (0, n1)]
    return A[np.ix_(kept, kept)], B[kept], kept


def lqr_gain(plant: PlantParams, weights: LQRWeights | None = None) -> tuple[np.ndarray, list[int]]:
    """Continuous-time LQR gain ``K`` (1 × len(kept)) and the kept state indices."""
    w = weights or LQRWeights()
    A, B, kept = linearize(plant)
    n1 = plant.n_segments + 1
    diag = []
    for idx in kept:
        if idx == 0:
            diag.append(w.cart_position)
        elif idx < n1:
            diag.append(w.angle)
        elif idx == n1:
            diag.append(w.cart_velocity)
        else:
            diag.append(w.angular_velocity)
    Q = np.diag(diag)
    R = np.array([[w.effort]])
    P = solve_care(A, B, Q, R)
    K = np.linalg.solve(R, B.T @ P)
    return K.ravel(), kept


def lqr_controller(
    plant: PlantParams,
    weights: LQRWeights | None = None,
    base: ControllerConfig | None = None,
) -> ControllerConfig:
    """Build a multi-loop controller whose PD gains reproduce the LQR design.

    Settings that are not gains (frequency, output limits, anti-windup, …) are
    taken from ``base`` when given.
    """
    K, kept = lqr_gain(plant, weights)
    action = base.action if base is not None else default_action(plant)
    sigma = 1.0 if action == ControlAction.DIRECT else -1.0
    n1 = plant.n_segments + 1
    gains = {idx: k for idx, k in zip(kept, K)}
    old = {loop.signal: loop for loop in (base.loops if base else [])}
    loops: list[PIDLoopConfig] = []
    signals = (["cart"] if plant.has_cart else []) + [f"theta{i + 1}" for i in range(plant.n_segments)]
    for sig in signals:
        pos = 0 if sig == "cart" else int(sig[5:])
        prev = old.get(sig)
        loop = PIDLoopConfig(
            name="Cart position" if sig == "cart" else f"Segment {pos} angle",
            signal=sig,
            kp=-sigma * float(gains[pos]),
            ki=prev.ki if prev else 0.0,
            kd=-sigma * float(gains[pos + n1]),
            anti_windup=prev.anti_windup if prev else AntiWindup.CLAMP,
            derivative_mode=prev.derivative_mode if prev else DerivativeMode.MEASURED_RATE,
            integral_limit=prev.integral_limit if prev else 0.0,
            setpoint=prev.setpoint if prev else 0.0,
        )
        loops.append(loop)
    limit = plant.actuator.max_output
    if base is not None:
        return ControllerConfig(
            enabled=base.enabled,
            frequency_hz=base.frequency_hz,
            output_min=base.output_min,
            output_max=base.output_max,
            action=action,
            loops=loops,
        )
    return ControllerConfig(output_min=-limit, output_max=limit, action=action, loops=loops)


def default_action(plant: PlantParams) -> ControlAction:
    """Direct action for a cart (drive towards the lean), reverse for a base motor."""
    return ControlAction.DIRECT if plant.has_cart else ControlAction.REVERSE
