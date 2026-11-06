"""Multi-loop (parallel) PID controller with a single actuator.

Strategy
--------
One PID loop per measured signal – the absolute angle of every segment and,
optionally, the cart position – and the loop outputs are **summed** into one
actuator command::

    u = sat( Σ_k PID_k(e_k) ),   e_k = ±(y_k − r_k)   (direct / reverse action)

With the integral terms disabled this is exactly linear full-state feedback
``u = −K s`` (each loop's P gain multiplies a position, its D gain the
corresponding velocity). That makes it possible to stabilise the 2- and 3-link
pendulums, which no single angle loop can do, while keeping the familiar PID
tuning knobs. :mod:`pendulum_sim.control.lqr_tuning` computes a good starting
point for all gains from an LQR design on the linearised model.

The controller runs at its own sample rate (``frequency_hz``) and holds its
output between samples (zero-order hold), independent of the physics step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .pid import ControlAction, LoopOutput, PIDLoop, PIDLoopConfig


@dataclass
class ControllerConfig:
    enabled: bool = True
    frequency_hz: float = 500.0
    output_min: float = -30.0
    output_max: float = 30.0
    action: ControlAction = ControlAction.DIRECT
    loops: list[PIDLoopConfig] = field(default_factory=lambda: [PIDLoopConfig()])

    def validate(self) -> None:
        if not 1.0 <= self.frequency_hz <= 10000.0:
            raise ValueError("controller frequency must be between 1 Hz and 10 kHz")
        if self.output_min >= self.output_max:
            raise ValueError("controller output minimum must be below the maximum")
        for loop in self.loops:
            if loop.signal != "cart" and not (loop.is_angle and loop.segment_index in (0, 1, 2)):
                raise ValueError(f"loop '{loop.name}': unknown signal '{loop.signal}'")


@dataclass
class ControllerOutput:
    command: float = 0.0
    unsaturated: float = 0.0
    loops: list[LoopOutput] = field(default_factory=list)
    updated: bool = False


class MultiLoopController:
    def __init__(self, config: ControllerConfig) -> None:
        self.config = config
        self.loops: list[PIDLoop] = []
        self.set_config(config)
        self.reset()

    def set_config(self, config: ControllerConfig) -> None:
        """Apply a new configuration while keeping integrator states (bumpless)."""
        config.validate()
        old = {id(loop.config): loop for loop in self.loops}
        old_by_signal = {loop.config.signal: loop for loop in self.loops}
        new_loops = []
        for cfg in config.loops:
            loop = old.get(id(cfg)) or old_by_signal.get(cfg.signal)
            if loop is None:
                loop = PIDLoop(cfg)
            loop.config = cfg
            new_loops.append(loop)
        self.loops = new_loops
        self.config = config

    @property
    def direction(self) -> float:
        return 1.0 if self.config.action == ControlAction.DIRECT else -1.0

    @property
    def period(self) -> float:
        return 1.0 / self.config.frequency_hz

    def reset(self) -> None:
        for loop in self.loops:
            loop.reset()
        self._next_sample = 0.0
        self.output = ControllerOutput(loops=[LoopOutput() for _ in self.loops])

    def reset_integrators(self) -> None:
        for loop in self.loops:
            loop.integral = 0.0

    @staticmethod
    def _signal(loop: PIDLoop, q: np.ndarray, qd: np.ndarray) -> tuple[float, float] | None:
        if loop.config.signal == "cart":
            return float(q[0]), float(qd[0])
        idx = loop.config.segment_index
        if idx is None or idx + 1 >= len(q):
            return None
        return float(q[idx + 1]), float(qd[idx + 1])

    def sample_due(self, t: float) -> bool:
        """True when :meth:`step` would compute a new output at time ``t``."""
        return t + 1e-12 >= self._next_sample

    def step(self, t: float, q: np.ndarray, qd: np.ndarray) -> ControllerOutput:
        """Return the held command; compute a new one when a sample is due."""
        if t + 1e-12 < self._next_sample:
            self.output.updated = False
            return self.output
        ts = self.period
        self._next_sample += ts
        if self._next_sample <= t:                 # e.g. after a frequency change
            self._next_sample = t + ts
        c = self.config
        outputs: list[LoopOutput] = []
        total = 0.0
        for loop in self.loops:
            sig = self._signal(loop, q, qd)
            if sig is None or not loop.config.enabled:
                loop.last = LoopOutput()
                outputs.append(loop.last)
                continue
            out = loop.compute(sig[0], sig[1], ts, self.direction)
            outputs.append(out)
            total += out.total
        if not c.enabled:
            total = 0.0
        saturated = min(c.output_max, max(c.output_min, total))
        if c.enabled:
            for loop in self.loops:
                loop.update_integral(ts, total, saturated)
        self.output = ControllerOutput(command=saturated, unsaturated=total, loops=outputs, updated=True)
        return self.output
