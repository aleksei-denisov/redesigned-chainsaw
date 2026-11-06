"""Fixed-step simulation engine (physics + sensors + controller + actuator).

One call to :meth:`Simulator.step` advances the system by exactly
``SimulationSettings.dt``. The GUI calls :meth:`Simulator.advance` with the
amount of simulated time it wants per frame; the number of physics steps is
derived from that, so the physics timestep never depends on the frame rate.

Order of operations in a physics step::

    sensors (+noise) → controller (runs at its own rate, ZOH)
                     → actuator dynamics → RK4 integration of the plant
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..control import ControllerConfig, ControllerOutput, MultiLoopController
from ..physics import Actuator, MultiLinkPendulum, PlantParams
from .config import AppConfig
from .telemetry import INDEX, MAX_LOOPS, COLUMNS, TelemetryRecorder

# Velocities beyond this are treated as numerical divergence.
DIVERGENCE_LIMIT = 1e4


class SimulationDiverged(RuntimeError):
    """Raised when the state becomes non-finite or unphysically large."""


@dataclass
class Disturbance:
    start: float
    end: float
    force: float


class Simulator:
    def __init__(self, config: AppConfig, telemetry_window_s: float | None = 30.0) -> None:
        config.validate()
        self.config = config
        rate = config.simulation.telemetry_rate_hz
        max_samples = None if telemetry_window_s is None else int(telemetry_window_s * rate)
        self.telemetry = TelemetryRecorder(max_samples)
        self.model = MultiLinkPendulum(config.plant)
        self.actuator = Actuator(config.plant.actuator)
        self.controller = MultiLoopController(config.controller)
        self.manual_force = 0.0
        self.reset()

    # ------------------------------------------------------------- lifecycle
    def reset(self) -> None:
        """Return to the configured initial conditions (time 0, clean telemetry)."""
        self.model.set_params(self.config.plant)
        self.actuator.params = self.config.plant.actuator
        self.actuator.reset()
        self.controller.set_config(self.config.controller)
        self.controller.reset()
        self.state = self.model.initial_state()
        self.time = 0.0
        self.steps = 0
        self.disturbances: list[Disturbance] = []
        self.rng = np.random.default_rng(self.config.simulation.noise_seed)
        self.last_control = ControllerOutput(loops=[])
        self.applied = 0.0
        self.tip_force = 0.0
        self._next_record = 0.0
        self.telemetry.clear()
        self.telemetry.loop_names = [loop.name for loop in self.config.controller.loops]
        self._record()

    # ------------------------------------------------------ live parameters
    def update_plant(self, plant: PlantParams) -> bool:
        """Apply new physical parameters while running.

        Returns ``False`` (and changes nothing) when the number of segments
        or the base type changed – those need :meth:`reset` because the state
        vector changes meaning.
        """
        plant.validate()
        current = self.model.params
        if plant.n_segments != self.model.n or plant.base_type != current.base_type:
            return False
        self.config.plant = plant
        self.model.set_params(plant)
        self.actuator.params = plant.actuator
        return True

    def update_controller(self, controller: ControllerConfig) -> None:
        controller.validate()
        self.config.controller = controller
        self.controller.set_config(controller)
        self.telemetry.loop_names = [loop.name for loop in controller.loops]

    def apply_disturbance(self, force: float, duration: float = 0.1, start: float | None = None) -> None:
        """Schedule a horizontal force pulse [N] at the pendulum tip."""
        t0 = self.time if start is None else start
        self.disturbances.append(Disturbance(t0, t0 + duration, force))

    # ------------------------------------------------------------- stepping
    def _measure(self) -> tuple[np.ndarray, np.ndarray]:
        n1 = self.model.n + 1
        q = self.state[:n1].copy()
        qd = self.state[n1:].copy()
        s = self.config.simulation
        if s.sensor_noise_angle_deg > 0:
            q[1:] += self.rng.normal(0.0, math.radians(s.sensor_noise_angle_deg), n1 - 1)
        if s.sensor_noise_rate_dps > 0:
            qd[1:] += self.rng.normal(0.0, math.radians(s.sensor_noise_rate_dps), n1 - 1)
        if s.sensor_noise_position > 0 and self.model.has_cart:
            q[0] += self.rng.normal(0.0, s.sensor_noise_position)
        return q, qd

    def _tip_force(self) -> float:
        if not self.disturbances:
            return 0.0
        force = 0.0
        for d in self.disturbances:
            if d.start <= self.time < d.end:
                force += d.force
        self.disturbances = [d for d in self.disturbances if d.end > self.time]
        return force

    def step(self) -> None:
        dt = self.config.simulation.dt
        if self.controller.sample_due(self.time):
            # Sensors are only read when the controller samples (saves work between samples).
            q, qd = self._measure()
            self.last_control = self.controller.step(self.time, q, qd)
        command = self.last_control.command + self.manual_force
        self.applied = self.actuator.step(command, dt)
        self.tip_force = self._tip_force()
        u, f = self.applied, self.tip_force
        self.state = self.model.rk4_step(self.state, u, f, dt)
        self.steps += 1
        self.time += dt  # dt may be changed by the user between steps
        # "not <=" is also true for NaN, so this one comparison catches non-finite states.
        if not np.abs(self.state).max() <= DIVERGENCE_LIMIT:
            raise SimulationDiverged(
                f"simulation diverged at t = {self.time:.3f} s – reduce the time step or the gains"
            )
        if self.time + 1e-12 >= self._next_record:
            self._record()

    def advance(self, duration: float, max_steps: int | None = None) -> int:
        """Advance by ``duration`` seconds of simulated time. Returns steps taken."""
        dt = self.config.simulation.dt
        n = int(round(duration / dt))
        if max_steps is not None:
            n = min(n, max_steps)
        for _ in range(n):
            self.step()
        return n

    # ------------------------------------------------------------- telemetry
    def _record(self) -> None:
        self._next_record += 1.0 / self.config.simulation.telemetry_rate_hz
        n = self.model.n
        n1 = n + 1
        row = np.full(len(COLUMNS), np.nan)
        row[INDEX["t"]] = self.time
        row[INDEX["x"]] = self.state[0]
        row[INDEX["xd"]] = self.state[n1]
        for i in range(n):
            row[INDEX[f"theta{i + 1}"]] = self.state[1 + i]  # continuous (unwrapped)
            row[INDEX[f"omega{i + 1}"]] = self.state[n1 + 1 + i]
        row[INDEX["command"]] = self.last_control.command
        row[INDEX["unsaturated"]] = self.last_control.unsaturated
        row[INDEX["actuator"]] = self.applied
        row[INDEX["tip_force"]] = self.tip_force
        ke = self.model.kinetic_energy(self.state)
        pe = self.model.potential_energy(self.state)
        row[INDEX["ke"]], row[INDEX["pe"]], row[INDEX["energy"]] = ke, pe, ke + pe
        for k, loop in enumerate(self.controller.loops[:MAX_LOOPS]):
            out = loop.last
            row[INDEX[f"error{k}"]] = out.error
            row[INDEX[f"p{k}"]] = out.p
            row[INDEX[f"i{k}"]] = out.i
            row[INDEX[f"d{k}"]] = out.d
        self.telemetry.append(row)

    # ------------------------------------------------------------- helpers
    @property
    def angles(self) -> np.ndarray:
        return self.state[1 : self.model.n + 1]

    @property
    def angular_velocities(self) -> np.ndarray:
        return self.state[self.model.n + 2 :]

    def energy(self) -> float:
        return self.model.total_energy(self.state)
