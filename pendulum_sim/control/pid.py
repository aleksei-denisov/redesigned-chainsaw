"""Discrete PID loop with anti-windup, derivative filtering and component switches.

Sign convention (controller action)
-----------------------------------
Industrial PID controllers are either *reverse acting* (``e = r − y``: the
output opposes the deviation, e.g. a motor torque that rotates a segment
back) or *direct acting* (``e = y − r``: the output follows the deviation,
e.g. a cart that must drive *towards* the side the pendulum leans to). The
controller-wide :class:`ControlAction` selects the sign so that a single
balancing loop has positive gains on both base types. Angle errors are
wrapped to (−π, π]. Gains may still be negative where the physics requires
it (e.g. the non-minimum-phase cart-position loop, or inner segments of a
multi-link pendulum).

Control law of one loop::

    u = Kp·e  +  I  +  Kd·ė,      I(k+1) = I(k) + Ki·e·Ts   (+ anti-windup)

The integral is stored as the *integral term* (already multiplied by Ki), so
changing Ki at run time does not cause an output jump (bumpless retuning).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class ControlAction(str, Enum):
    DIRECT = "direct"    # e = y − r
    REVERSE = "reverse"  # e = r − y


class AntiWindup(str, Enum):
    NONE = "none"
    CLAMP = "clamp"                    # conditional integration
    BACK_CALCULATION = "back_calculation"


class DerivativeMode(str, Enum):
    MEASURED_RATE = "measured_rate"            # use the rate sensor (encoder speed / gyro)
    FILTERED_DIFFERENCE = "filtered_difference"  # differentiate the error, 1st-order low-pass


@dataclass
class PIDLoopConfig:
    """Tunable parameters of one PID loop.

    Attributes:
        name: Display name.
        signal: Measured signal: ``"cart"`` or ``"theta1"``…``"theta3"``.
        setpoint: Reference value (rad for angles, m for the cart).
        kp, ki, kd: Gains (output units per rad, per rad·s, per rad/s).
        p_enabled, i_enabled, d_enabled: Switch individual components on/off.
        integral_limit: |integral term| limit in output units (0 = unlimited).
        anti_windup: Strategy used when the total controller output saturates.
        back_calculation_gain: Tracking gain Kt [1/s] for back-calculation.
        derivative_mode: Source of the derivative term.
        derivative_filter_hz: Cut-off of the low-pass filter in
            ``filtered_difference`` mode.
    """

    name: str = "Segment 1 angle"
    signal: str = "theta1"
    enabled: bool = True
    setpoint: float = 0.0
    kp: float = 40.0
    ki: float = 0.0
    kd: float = 4.0
    p_enabled: bool = True
    i_enabled: bool = True
    d_enabled: bool = True
    integral_limit: float = 0.0
    anti_windup: AntiWindup = AntiWindup.CLAMP
    back_calculation_gain: float = 10.0
    derivative_mode: DerivativeMode = DerivativeMode.MEASURED_RATE
    derivative_filter_hz: float = 50.0

    @property
    def is_angle(self) -> bool:
        return self.signal.startswith("theta")

    @property
    def segment_index(self) -> int | None:
        return int(self.signal[5:]) - 1 if self.is_angle else None


@dataclass
class LoopOutput:
    error: float = 0.0
    p: float = 0.0
    i: float = 0.0
    d: float = 0.0

    @property
    def total(self) -> float:
        return self.p + self.i + self.d


def wrap_angle(angle: float) -> float:
    """Wrap an angle to the interval (−π, π]."""
    wrapped = math.fmod(angle + math.pi, 2.0 * math.pi)
    if wrapped <= 0.0:
        wrapped += 2.0 * math.pi
    return wrapped - math.pi


@dataclass
class PIDLoop:
    """Runtime state of one loop. ``config`` may be edited at any time."""

    config: PIDLoopConfig
    integral: float = 0.0
    _prev_error: float | None = None
    _filtered_derivative: float = 0.0
    last: LoopOutput = field(default_factory=LoopOutput)

    def reset(self) -> None:
        self.integral = 0.0
        self._prev_error = None
        self._filtered_derivative = 0.0
        self.last = LoopOutput()

    def error(self, measurement: float, direction: float = 1.0) -> float:
        """Control error; ``direction`` is +1 (direct action) or −1 (reverse)."""
        e = measurement - self.config.setpoint
        e = wrap_angle(e) if self.config.is_angle else e
        return direction * e

    def compute(self, measurement: float, rate: float, ts: float, direction: float = 1.0) -> LoopOutput:
        """Proportional, integral (current state) and derivative terms."""
        c = self.config
        e = self.error(measurement, direction)
        if c.derivative_mode == DerivativeMode.MEASURED_RATE:
            de = direction * rate
        else:
            if self._prev_error is None:
                raw = 0.0
            else:
                diff = e - self._prev_error
                raw = (wrap_angle(diff) if c.is_angle else diff) / ts
            tau = 1.0 / (2.0 * math.pi * max(c.derivative_filter_hz, 1e-6))
            alpha = ts / (ts + tau)
            self._filtered_derivative += alpha * (raw - self._filtered_derivative)
            de = self._filtered_derivative
        self._prev_error = e
        if not c.i_enabled:
            self.integral = 0.0
        self._limit_integral()
        out = LoopOutput(
            error=e,
            p=c.kp * e if c.p_enabled else 0.0,
            i=self.integral if c.i_enabled else 0.0,
            d=c.kd * de if c.d_enabled else 0.0,
        )
        self.last = out
        return out

    def update_integral(self, ts: float, unsaturated: float, saturated: float) -> None:
        """Advance the integrator after the total output has been saturated."""
        c = self.config
        if not (c.enabled and c.i_enabled) or c.ki == 0.0:
            return
        e = self.last.error
        increment = c.ki * e * ts
        saturated_now = unsaturated != saturated
        if c.anti_windup == AntiWindup.CLAMP:
            # Stop integrating if this would drive the output further into saturation.
            if saturated_now and increment * unsaturated > 0.0:
                increment = 0.0
        elif c.anti_windup == AntiWindup.BACK_CALCULATION:
            increment += c.back_calculation_gain * (saturated - unsaturated) * ts
        self.integral += increment
        self._limit_integral()

    def _limit_integral(self) -> None:
        lim = self.config.integral_limit
        if lim > 0.0:
            self.integral = max(-lim, min(lim, self.integral))
