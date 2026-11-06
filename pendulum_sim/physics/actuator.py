"""Actuator dynamics: deadband, saturation, slew-rate limit and first-order lag."""

from __future__ import annotations

import math

from .params import ActuatorParams


class Actuator:
    """Converts a command into the physical force/torque applied to the plant.

    The processing chain per physics step ``dt`` is::

        command → deadband → saturation → first-order lag → slew-rate limit → output

    The output is held constant during one physics step (zero-order hold).
    """

    def __init__(self, params: ActuatorParams) -> None:
        self.params = params
        self.output = 0.0

    def reset(self) -> None:
        self.output = 0.0

    def target(self, command: float) -> float:
        p = self.params
        if abs(command) < p.deadband:
            command = 0.0
        return max(-p.max_output, min(p.max_output, command))

    def step(self, command: float, dt: float) -> float:
        p = self.params
        target = self.target(command)
        if p.time_constant > 0.0:
            # Exact discretisation of  τ·u̇ = target − u  over one step.
            alpha = 1.0 - math.exp(-dt / p.time_constant)
            new = self.output + alpha * (target - self.output)
        else:
            new = target
        if p.rate_limit > 0.0:
            max_delta = p.rate_limit * dt
            new = self.output + max(-max_delta, min(max_delta, new - self.output))
        self.output = max(-p.max_output, min(p.max_output, new))
        return self.output
