"""What an edit does to a running :class:`~pendulum_sim.simulation.Simulator` (Qt-free).

The desktop and Widgets windows apply edits to a simulator that shares its
configuration object with the window. The Qt Quick app runs the simulator in another
process, which has its own copy, so every edit is replayed there by one of these
functions – the same calls, in the same order, as the window makes
(``tests/test_session_equivalence.py``).
"""

from __future__ import annotations

import time

from ..control import ControllerConfig
from ..physics import PlantParams
from ..simulation import SimulationDiverged, SimulationSettings, Simulator
from .rules import single_step_count


def apply_structure(sim: Simulator, plant: PlantParams, controller: ControllerConfig) -> None:
    """New number of segments or base type: new plant and controller, back to the initial state."""
    sim.config.plant = plant
    sim.config.controller = controller
    sim.update_controller(controller)
    sim.reset()


def apply_plant(sim: Simulator, plant: PlantParams) -> bool:
    """New physical parameters while running (same structure); ``False`` if refused."""
    return sim.update_plant(plant)


def apply_controller(sim: Simulator, controller: ControllerConfig) -> None:
    sim.update_controller(controller)


def apply_simulation_settings(sim: Simulator, settings: SimulationSettings) -> None:
    """Time step, telemetry rate, sensor noise: used from the next step on."""
    sim.config.simulation = settings


def reset_integrators(sim: Simulator) -> None:
    sim.controller.reset_integrators()


def run_steps(sim: Simulator, n: int, deadline: float | None = None) -> tuple[int, SimulationDiverged | None]:
    """Up to ``n`` steps in batches of 25 (stopping early after ``deadline``, a
    ``time.perf_counter()`` value). Returns the steps done and the divergence, if any."""
    done = 0
    try:
        while done < n:
            k = min(25, n - done)
            for _ in range(k):
                sim.step()
            done += k
            if deadline is not None and time.perf_counter() > deadline:
                break
    except SimulationDiverged as exc:
        return done, exc
    return done, None


def single_step(sim: Simulator) -> tuple[int, SimulationDiverged | None]:
    """*Step*: one controller period."""
    return run_steps(sim, single_step_count(sim.controller.period, sim.config.simulation.dt))
