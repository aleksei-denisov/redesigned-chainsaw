"""Physical parameter definitions for the multi-link inverted pendulum.

All values are SI units (m, kg, kg·m², s, N, N·m) and angles are radians.
The GUI and the JSON configuration files use degrees for angles; the
conversion happens in :mod:`pendulum_sim.simulation.config`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

MAX_SEGMENTS = 3


class BaseType(str, Enum):
    """How the first joint is supported and actuated.

    ``CART``  – the first joint sits on a cart that moves along a horizontal
                track; the actuator applies a horizontal force to the cart
                (classic cart-pole, n-link version).
    ``FIXED`` – the first joint is fixed in space; the actuator is a motor
                applying torque between the ground and the first segment.
    """

    CART = "cart"
    FIXED = "fixed"


@dataclass
class SegmentParams:
    """One rigid pendulum segment (link).

    Attributes:
        length: Distance from this segment's lower joint to its upper joint [m].
        mass: Segment mass [kg].
        com_ratio: Centre-of-mass position as a fraction of ``length`` measured
            from the lower joint (0.5 = uniform rod).
        inertia: Moment of inertia about the centre of mass [kg·m²].
        joint_viscous_friction: Viscous friction coefficient of the joint below
            this segment [N·m·s/rad].
        joint_coulomb_friction: Coulomb (dry) friction torque of that joint [N·m].
        initial_angle: Initial absolute angle from the upward vertical [rad].
        initial_velocity: Initial absolute angular velocity [rad/s].
    """

    length: float = 0.5
    mass: float = 0.2
    com_ratio: float = 0.5
    inertia: float = 0.2 * 0.5**2 / 12.0
    joint_viscous_friction: float = 0.0005
    joint_coulomb_friction: float = 0.0
    initial_angle: float = 0.0
    initial_velocity: float = 0.0

    @property
    def com_distance(self) -> float:
        """Distance from the lower joint to the centre of mass [m]."""
        return self.com_ratio * self.length

    @staticmethod
    def rod_inertia(mass: float, length: float) -> float:
        """Inertia of a uniform slender rod about its centre: m·L²/12."""
        return mass * length**2 / 12.0

    def validate(self, index: int = 0) -> None:
        name = f"segment {index + 1}"
        if self.length <= 0:
            raise ValueError(f"{name}: length must be positive")
        if self.mass <= 0:
            raise ValueError(f"{name}: mass must be positive")
        if not 0.0 <= self.com_ratio <= 1.0:
            raise ValueError(f"{name}: centre of mass ratio must be within [0, 1]")
        if self.inertia < 0:
            raise ValueError(f"{name}: inertia must not be negative")
        if self.joint_viscous_friction < 0 or self.joint_coulomb_friction < 0:
            raise ValueError(f"{name}: friction coefficients must not be negative")


@dataclass
class ActuatorParams:
    """Actuator (motor / linear drive) characteristics.

    The actuator output is a force [N] for a cart base or a torque [N·m] for a
    fixed base.

    Attributes:
        max_output: Saturation limit, |u| <= max_output.
        rate_limit: Maximum slew rate of the output [units/s]; 0 disables it.
        time_constant: First-order lag time constant [s]; 0 = ideal actuator.
        deadband: Commands with |u| below this value produce no output.
    """

    max_output: float = 30.0
    rate_limit: float = 0.0
    time_constant: float = 0.005
    deadband: float = 0.0

    def validate(self) -> None:
        if self.max_output <= 0:
            raise ValueError("actuator: max output must be positive")
        if self.rate_limit < 0 or self.time_constant < 0 or self.deadband < 0:
            raise ValueError("actuator: rate limit, time constant and deadband must not be negative")


@dataclass
class PlantParams:
    """Complete physical description of the pendulum system."""

    base_type: BaseType = BaseType.CART
    gravity: float = 9.81
    cart_mass: float = 1.0
    cart_viscous_friction: float = 0.1
    track_length: float = 4.0
    segments: list[SegmentParams] = field(default_factory=lambda: [SegmentParams()])
    actuator: ActuatorParams = field(default_factory=ActuatorParams)

    @property
    def n_segments(self) -> int:
        return len(self.segments)

    @property
    def has_cart(self) -> bool:
        return self.base_type == BaseType.CART

    def validate(self) -> None:
        if not 1 <= self.n_segments <= MAX_SEGMENTS:
            raise ValueError(f"number of segments must be between 1 and {MAX_SEGMENTS}")
        if self.gravity < 0:
            raise ValueError("gravity must not be negative")
        if self.has_cart:
            if self.cart_mass <= 0:
                raise ValueError("cart mass must be positive")
            if self.track_length <= 0:
                raise ValueError("track length must be positive")
            if self.cart_viscous_friction < 0:
                raise ValueError("cart friction must not be negative")
        for i, seg in enumerate(self.segments):
            seg.validate(i)
        self.actuator.validate()
