r"""Multibody dynamics of an n-link (n = 1..3) inverted pendulum.

Coordinates
-----------
Generalised coordinates ``q = [x, θ1, …, θn]``:

* ``x``  – horizontal cart position [m] (identically 0 for a fixed base),
* ``θi`` – *absolute* angle of segment *i* measured from the upward vertical,
  positive when the segment leans towards +x (clockwise on screen).

The full state is ``s = [q, q̇]`` of size ``2(n+1)``.

Kinematics
----------
For segment *i* define the lever coefficients

.. math::  a_{ij} = L_j \;(j<i),\qquad a_{ii} = c_i,\qquad a_{ij}=0 \;(j>i)

where ``L_j`` is the length of segment *j* and ``c_i`` the distance from the
lower joint of segment *i* to its centre of mass. The centre of mass is then

.. math::  x_{ci} = x + \sum_j a_{ij}\sin\theta_j,\qquad y_{ci} = \sum_j a_{ij}\cos\theta_j

Equations of motion (Lagrange / Kane)
-------------------------------------
With constant matrices ``W = Aᵀ diag(m) A`` and ``h = Aᵀ m`` the kinetic energy
``T = ½ q̇ᵀ M(q) q̇`` has the mass matrix

.. math::

    M_{xx} = m_{cart} + \sum_i m_i,\quad
    M_{x\theta_j} = h_j\cos\theta_j,\quad
    M_{\theta_j\theta_k} = W_{jk}\cos(\theta_j-\theta_k) + \delta_{jk} I_j

and the velocity-product (Coriolis/centrifugal) vector

.. math::

    C_x = -\sum_k h_k \sin\theta_k\,\dot\theta_k^2,\qquad
    C_{\theta_j} = \sum_k W_{jk}\sin(\theta_j-\theta_k)\,\dot\theta_k^2 .

The potential energy is ``V = g Σ_j h_j cos θ_j`` so the gravity generalised
force is ``G_{θj} = g h_j sin θ_j`` (destabilising about the upright).

The motion obeys ``M(q) q̈ + C(q, q̇) = G(q) + Q`` where ``Q`` gathers the
actuator, joint friction, cart friction, track end-stop and disturbance
generalised forces. Joint friction acts on *relative* joint velocities
``φ̇_k = θ̇_k − θ̇_{k−1}`` (``θ̇_0 = 0``) and is applied as equal and opposite
torques on the two connected bodies.

For a fixed base the cart row/column is removed and ``x ≡ 0``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .params import BaseType, PlantParams

# Velocity scale for the smooth Coulomb friction approximation tanh(φ̇/ε) [rad/s].
COULOMB_SMOOTHING = 0.02


@dataclass(frozen=True)
class Kinematics:
    """Positions of the cart, joints, centres of mass and tip (world frame)."""

    cart: np.ndarray        # (2,)  cart / base pivot position
    joints: np.ndarray      # (n+1, 2) lower joints of each segment + tip
    coms: np.ndarray        # (n, 2) centres of mass

    @property
    def tip(self) -> np.ndarray:
        return self.joints[-1]


class MultiLinkPendulum:
    """Rigid-body model of a cart- or fixed-base pendulum with 1–3 segments.

    The class is stateless with respect to the simulation state: every method
    receives the state vector. Parameters can be swapped at any time with
    :meth:`set_params` (the number of segments determines the state size).
    """

    def __init__(self, params: PlantParams) -> None:
        self.params = params
        self.set_params(params)

    # ------------------------------------------------------------------ setup
    def set_params(self, params: PlantParams) -> None:
        params.validate()
        self.params = params
        n = params.n_segments
        self.n = n
        self.lengths = np.array([s.length for s in params.segments], dtype=float)
        self.masses = np.array([s.mass for s in params.segments], dtype=float)
        self.com = np.array([s.com_distance for s in params.segments], dtype=float)
        self.inertias = np.array([s.inertia for s in params.segments], dtype=float)
        self.viscous = np.array([s.joint_viscous_friction for s in params.segments], dtype=float)
        self.coulomb = np.array([s.joint_coulomb_friction for s in params.segments], dtype=float)

        a = np.zeros((n, n))
        for i in range(n):
            a[i, :i] = self.lengths[:i]
            a[i, i] = self.com[i]
        self.lever = a
        self.W = a.T @ np.diag(self.masses) @ a
        self.h = a.T @ self.masses
        self._inertia_diag = np.diag(self.inertias)
        self.total_mass = params.cart_mass + float(self.masses.sum())

        # Track end stops: stiff spring–damper contact (penalty method).
        self.stop_stiffness = 2000.0 * self.total_mass
        self.stop_damping = 2.0 * 0.7 * np.sqrt(self.stop_stiffness * self.total_mass)

    @property
    def has_cart(self) -> bool:
        return self.params.base_type == BaseType.CART

    @property
    def state_size(self) -> int:
        return 2 * (self.n + 1)

    def initial_state(self) -> np.ndarray:
        s = np.zeros(self.state_size)
        s[1 : self.n + 1] = [seg.initial_angle for seg in self.params.segments]
        s[self.n + 2 :] = [seg.initial_velocity for seg in self.params.segments]
        return s

    # -------------------------------------------------------------- dynamics
    def mass_matrix(self, q: np.ndarray) -> np.ndarray:
        n = self.n
        th = q[1:]
        c = np.cos(th)
        m = np.empty((n + 1, n + 1))
        m[0, 0] = self.total_mass
        m[0, 1:] = self.h * c
        m[1:, 0] = m[0, 1:]
        m[1:, 1:] = self.W * np.cos(th[:, None] - th[None, :]) + self._inertia_diag
        return m

    def bias_forces(self, q: np.ndarray, qd: np.ndarray) -> np.ndarray:
        """Return ``C(q, q̇) − G(q)`` (velocity products minus gravity)."""
        th = q[1:]
        w2 = qd[1:] ** 2
        s = np.sin(th)
        b = np.empty(self.n + 1)
        b[0] = -np.dot(self.h * s, w2)
        b[1:] = (self.W * np.sin(th[:, None] - th[None, :])) @ w2
        b[1:] -= self.params.gravity * self.h * s
        return b

    def joint_friction_torques(self, qd: np.ndarray) -> np.ndarray:
        """Generalised friction forces on the angle coordinates (length n)."""
        w = qd[1:]
        rel = w - np.concatenate(([0.0], w[:-1]))
        tau = -self.viscous * rel - self.coulomb * np.tanh(rel / COULOMB_SMOOTHING)
        gen = tau.copy()
        gen[:-1] -= tau[1:]          # reaction on the segment below
        return gen

    def generalized_forces(
        self, q: np.ndarray, qd: np.ndarray, actuator: float, tip_force: float
    ) -> np.ndarray:
        """Sum of non-conservative generalised forces ``Q``."""
        p = self.params
        Q = np.zeros(self.n + 1)
        Q[1:] += self.joint_friction_torques(qd)
        if self.has_cart:
            Q[0] += actuator - p.cart_viscous_friction * qd[0]
            Q[0] += self._end_stop_force(q[0], qd[0])
        else:
            Q[1] += actuator            # motor torque between ground and segment 1
        if tip_force != 0.0:
            # Horizontal force at the tip: Q = Jᵀ F with ∂x_tip/∂θj = L_j cos θj.
            Q[0] += tip_force
            Q[1:] += tip_force * self.lengths * np.cos(q[1:])
        return Q

    def _end_stop_force(self, x: float, xd: float) -> float:
        limit = 0.5 * self.params.track_length
        if x > limit:
            return -self.stop_stiffness * (x - limit) - self.stop_damping * max(xd, 0.0)
        if x < -limit:
            return -self.stop_stiffness * (x + limit) - self.stop_damping * min(xd, 0.0)
        return 0.0

    def accelerations(
        self, state: np.ndarray, actuator: float = 0.0, tip_force: float = 0.0
    ) -> np.ndarray:
        n1 = self.n + 1
        q, qd = state[:n1], state[n1:]
        rhs = self.generalized_forces(q, qd, actuator, tip_force) - self.bias_forces(q, qd)
        M = self.mass_matrix(q)
        if self.has_cart:
            return np.linalg.solve(M, rhs)
        qdd = np.zeros(n1)
        qdd[1:] = np.linalg.solve(M[1:, 1:], rhs[1:])
        return qdd

    def derivative(
        self, state: np.ndarray, actuator: float = 0.0, tip_force: float = 0.0
    ) -> np.ndarray:
        """State derivative ``ṡ = [q̇, q̈]``."""
        n1 = self.n + 1
        ds = np.empty_like(state)
        ds[:n1] = state[n1:]
        ds[n1:] = self.accelerations(state, actuator, tip_force)
        if not self.has_cart:
            ds[0] = 0.0
        return ds

    # ------------------------------------------------------------ energetics
    def kinetic_energy(self, state: np.ndarray) -> float:
        n1 = self.n + 1
        q, qd = state[:n1], state[n1:]
        return float(0.5 * qd @ self.mass_matrix(q) @ qd)

    def potential_energy(self, state: np.ndarray) -> float:
        """Gravitational potential energy relative to the base height [J]."""
        th = state[1 : self.n + 1]
        return float(self.params.gravity * np.dot(self.h, np.cos(th)))

    def total_energy(self, state: np.ndarray) -> float:
        return self.kinetic_energy(state) + self.potential_energy(state)

    def horizontal_momentum(self, state: np.ndarray) -> float:
        n1 = self.n + 1
        return float(self.mass_matrix(state[:n1])[0] @ state[n1:])

    # ------------------------------------------------------------ kinematics
    def kinematics(self, state: np.ndarray) -> Kinematics:
        x = state[0] if self.has_cart else 0.0
        th = state[1 : self.n + 1]
        s, c = np.sin(th), np.cos(th)
        joints = np.zeros((self.n + 1, 2))
        joints[0] = (x, 0.0)
        joints[1:, 0] = x + np.cumsum(self.lengths * s)
        joints[1:, 1] = np.cumsum(self.lengths * c)
        coms = joints[:-1] + np.column_stack((self.com * s, self.com * c))
        return Kinematics(cart=np.array([x, 0.0]), joints=joints, coms=coms)
