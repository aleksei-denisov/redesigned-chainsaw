"""Fast scalar implementation of the pendulum dynamics for the real-time loop.

The NumPy implementation in :mod:`.model` is clear and general, but for the
tiny systems simulated here (at most 4×4 matrices) every NumPy call costs far
more in overhead than in arithmetic. This module evaluates *exactly the same
equations* (see the ``model`` module docstring) with plain Python floats and
the ``math`` module, solves the symmetric positive-definite mass-matrix system
with a hand-written Cholesky factorisation and runs the RK4 stages on Python
lists. It is several times faster and is cross-checked against the NumPy
reference in ``tests/test_fast_dynamics.py``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .model import MultiLinkPendulum

from .model import COULOMB_SMOOTHING


class ScalarDynamics:
    """Constants of one :class:`MultiLinkPendulum` unpacked into Python floats."""

    def __init__(self, model: "MultiLinkPendulum") -> None:
        p = model.params
        self.n = model.n
        self.has_cart = model.has_cart
        self.W = [[float(v) for v in row] for row in model.W]
        self.h = [float(v) for v in model.h]
        self.I = [float(v) for v in model.inertias]
        self.L = [float(v) for v in model.lengths]
        self.visc = [float(v) for v in model.viscous]
        self.coul = [float(v) for v in model.coulomb]
        self.g = float(p.gravity)
        self.total_mass = float(model.total_mass)
        self.cart_friction = float(p.cart_viscous_friction)
        self.limit = 0.5 * float(p.track_length)
        self.k_stop = float(model.stop_stiffness)
        self.c_stop = float(model.stop_damping)

    # ------------------------------------------------------------------
    def accelerations(self, s: list[float], u: float, f: float, arms: list[float] | None = None) -> list[float]:
        """q̈ for state ``s = [x, θ1..θn, ẋ, θ̇1..θ̇n]`` (list of floats).

        ``f``: total horizontal push force; ``arms``: ``Σ F_i a_ij`` per angle for pushes at
        points other than the tip (see ``model.push_arms``), ``None`` for a push at the tip.
        """
        n = self.n
        n1 = n + 1
        th = s[1:n1]
        w = s[n1 + 1 :]
        sin_t = [math.sin(t) for t in th]
        cos_t = [math.cos(t) for t in th]
        W, h, g = self.W, self.h, self.g
        w2 = [v * v for v in w]

        # Right-hand side: Q − (C − G), angle rows.
        rhs = [0.0] * n1
        for j in range(n):
            acc = g * h[j] * sin_t[j]
            Wj = W[j]
            sj, cj = sin_t[j], cos_t[j]
            for k in range(n):
                if k != j:
                    # sin(θj − θk) = sj·ck − cj·sk
                    acc -= Wj[k] * (sj * cos_t[k] - cj * sin_t[k]) * w2[k]
            rhs[j + 1] = acc

        # Joint friction on relative velocities (equal and opposite torques).
        prev = 0.0
        for k in range(n):
            rel = w[k] - prev
            prev = w[k]
            tau = -self.visc[k] * rel
            if self.coul[k] != 0.0:
                tau -= self.coul[k] * math.tanh(rel / COULOMB_SMOOTHING)
            rhs[k + 1] += tau
            if k > 0:
                rhs[k] -= tau

        if self.has_cart:
            x, xd = s[0], s[n1]
            q0 = u - self.cart_friction * xd
            if x > self.limit:
                q0 += -self.k_stop * (x - self.limit) - self.c_stop * (xd if xd > 0.0 else 0.0)
            elif x < -self.limit:
                q0 += -self.k_stop * (x + self.limit) - self.c_stop * (xd if xd < 0.0 else 0.0)
            c0 = 0.0
            for k in range(n):
                c0 -= h[k] * sin_t[k] * w2[k]
            rhs[0] = q0 - c0
        else:
            rhs[1] += u
        if f != 0.0 or arms is not None:
            rhs[0] += f
            if arms is None:
                for j in range(n):
                    rhs[j + 1] += f * self.L[j] * cos_t[j]
            else:
                for j in range(n):
                    rhs[j + 1] += arms[j] * cos_t[j]

        # Mass matrix (lower triangle is enough for Cholesky).
        size = n1 if self.has_cart else n
        off = 0 if self.has_cart else 1
        M = [[0.0] * size for _ in range(size)]
        for a in range(size):
            ia = a + off
            for b in range(a + 1):
                ib = b + off
                if ia == 0 and ib == 0:
                    v = self.total_mass
                elif ib == 0:
                    v = h[ia - 1] * cos_t[ia - 1]
                else:
                    j, k = ia - 1, ib - 1
                    # cos(θj − θk) = cj·ck + sj·sk
                    v = W[j][k] * (cos_t[j] * cos_t[k] + sin_t[j] * sin_t[k])
                    if j == k:
                        v += self.I[j]
                M[a][b] = v
        sol = _cholesky_solve(M, rhs[off:])
        if self.has_cart:
            return sol
        return [0.0] + sol

    def rk4_step(self, s: list[float], u: float, f: float, dt: float,
                 arms: list[float] | None = None) -> list[float]:
        n1 = self.n + 1
        acc = self.accelerations

        def deriv(state: list[float]) -> list[float]:
            return state[n1:] + acc(state, u, f, arms)

        k1 = deriv(s)
        s2 = [a + 0.5 * dt * b for a, b in zip(s, k1)]
        k2 = deriv(s2)
        s3 = [a + 0.5 * dt * b for a, b in zip(s, k2)]
        k3 = deriv(s3)
        s4 = [a + dt * b for a, b in zip(s, k3)]
        k4 = deriv(s4)
        sixth = dt / 6.0
        out = [a + sixth * (b + 2.0 * c + 2.0 * d + e) for a, b, c, d, e in zip(s, k1, k2, k3, k4)]
        if not self.has_cart:
            out[0] = 0.0
            out[n1] = 0.0
        return out


def _cholesky_solve(M: list[list[float]], b: list[float]) -> list[float]:
    """Solve ``M x = b`` for symmetric positive-definite ``M`` (lower triangle given)."""
    n = len(b)
    Lc = [[0.0] * n for _ in range(n)]
    for i in range(n):
        Mi, Li = M[i], Lc[i]
        for j in range(i + 1):
            Lj = Lc[j]
            acc = Mi[j]
            for k in range(j):
                acc -= Li[k] * Lj[k]
            if i == j:
                if acc <= 0.0:
                    raise ArithmeticError("mass matrix is not positive definite")
                Li[i] = math.sqrt(acc)
            else:
                Li[j] = acc / Lj[j]
    y = [0.0] * n
    for i in range(n):
        acc = b[i]
        Li = Lc[i]
        for k in range(i):
            acc -= Li[k] * y[k]
        y[i] = acc / Li[i]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        acc = y[i]
        for k in range(i + 1, n):
            acc -= Lc[k][i] * x[k]
        x[i] = acc / Lc[i][i]
    return x
