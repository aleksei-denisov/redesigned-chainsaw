"""Fixed-step numerical integrators."""

from __future__ import annotations

from typing import Callable

import numpy as np

Derivative = Callable[[np.ndarray], np.ndarray]


def rk4_step(f: Derivative, state: np.ndarray, dt: float) -> np.ndarray:
    """Classical 4th-order Runge–Kutta step with inputs held constant (ZOH).

    A fixed explicit step is used on purpose: it makes runs bit-for-bit
    reproducible (needed for experiment comparison) and keeps the physics
    timestep independent of the GUI frame rate. Accuracy is validated against
    SciPy's adaptive ``solve_ivp`` in the test suite.
    """
    k1 = f(state)
    k2 = f(state + 0.5 * dt * k1)
    k3 = f(state + 0.5 * dt * k2)
    k4 = f(state + dt * k3)
    return state + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
