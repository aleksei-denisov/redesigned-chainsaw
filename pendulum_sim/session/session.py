"""Configuration state of one interactive session (Qt-free).

:class:`Session` holds the configuration a front end edits and applies the
:mod:`.rules` to every edit. It does not own a simulator: each method returns the
*operations* the simulator must perform – tuples such as ``("controller", ctrl)`` – which
the caller forwards to wherever the simulator runs (the Qt Quick app: another process,
see ``quick/backend/sim_source.py``) and which :func:`apply` performs on a
:class:`~pendulum_sim.simulation.Simulator` with the functions of :mod:`.operations`.

The desktop window makes the same decisions in the same order on its own simulator;
``tests/test_session_equivalence.py`` runs both side by side and requires identical
configurations and identical simulated trajectories.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..control import ControllerConfig, LQRWeights, lqr_controller
from ..physics import PlantParams
from ..simulation import AppConfig, SimulationSettings, Simulator
from . import operations
from .rules import (
    controller_for_structure,
    controller_from_run,
    initial_conditions_changed,
    prepare_config,
    restored_controller,
    same_structure,
)

Operation = tuple


@dataclass
class PlantChange:
    """Outcome of :meth:`Session.change_plant`."""

    operations: list[Operation] = field(default_factory=list)
    structural: bool = False
    controller: ControllerConfig | None = None      # new controller after a structural change
    reset_if_idle: bool = False                     # initial pose edited: reset if stopped at t = 0


class Session:
    """The configuration being edited, the gains it was loaded with, and the edit rules."""

    def __init__(self, config: AppConfig) -> None:
        self.config: AppConfig
        self.original_controller: ControllerConfig
        self.original_plant: PlantParams
        self.original_simulation: SimulationSettings
        self.load(config)

    # ----------------------------------------------------------- loading
    def load(self, config: AppConfig) -> list[Operation]:
        """Replace the whole configuration (scenario, file). Raises ``ValueError``."""
        prepare_config(config)
        self.config = config
        self.original_controller = copy.deepcopy(config.controller)
        self.original_plant = copy.deepcopy(config.plant)
        self.original_simulation = copy.deepcopy(config.simulation)
        return [("config", config)]

    # ------------------------------------------------------------- edits
    def change_plant(self, plant: PlantParams, structural: bool,
                     edited_controller: ControllerConfig | None = None) -> PlantChange:
        """Physical parameters edited. Raises ``ValueError`` for invalid parameters.

        ``edited_controller``: the controller as currently shown, if the front end keeps
        unapplied edits (the desktop panels); otherwise the session's controller.
        """
        plant.validate()
        if structural:
            ctrl = controller_for_structure(edited_controller or self.config.controller, plant, self.config.plant)
            self.config.plant = plant
            self.config.controller = ctrl
            return PlantChange([("structure", plant, ctrl)], structural=True, controller=ctrl)
        initial = initial_conditions_changed(plant, self.config.plant)
        self.config.plant = plant
        return PlantChange([("plant", plant)], reset_if_idle=initial)

    def restore_model(self, plant: PlantParams, simulation: SimulationSettings,
                      controller: ControllerConfig) -> PlantChange:
        """Plant, simulation settings and – where the structure changes back – the controller
        of a snapshot (undo / redo / reset of the *Model* screen). Raises ``ValueError``."""
        plant, simulation = copy.deepcopy(plant), copy.deepcopy(simulation)
        plant.validate()
        simulation.validate()
        ops: list[Operation] = []
        if simulation != self.config.simulation:
            self.config.simulation = simulation
            ops.append(("simulation", simulation))
        if not same_structure(plant, self.config.plant):
            ctrl = restored_controller(controller, plant)
            self.config.plant, self.config.controller = plant, ctrl
            return PlantChange(ops + [("structure", plant, ctrl)], structural=True, controller=ctrl)
        initial = initial_conditions_changed(plant, self.config.plant)
        self.config.plant = plant
        return PlantChange(ops + [("plant", plant)], reset_if_idle=initial)

    def restore_controller(self, controller: ControllerConfig) -> list[Operation]:
        """A controller snapshot (undo / redo), loops completed for the current plant."""
        return self.change_controller(restored_controller(controller, self.config.plant))

    def change_controller(self, controller: ControllerConfig) -> list[Operation]:
        controller.validate()
        self.config.controller = controller
        return [("controller", controller)]

    def seed_lqr(self, weights: LQRWeights | None = None,
                 base: ControllerConfig | None = None) -> tuple[ControllerConfig, list[Operation]]:
        """Gains from the LQR design. Raises ``ValueError`` / ``LinAlgError`` if it fails."""
        ctrl = lqr_controller(self.config.plant, weights, base=base or self.config.controller)
        return ctrl, self.change_controller(ctrl)

    def restore_gains(self) -> tuple[ControllerConfig, list[Operation]]:
        ctrl = restored_controller(self.original_controller, self.config.plant)
        return ctrl, self.change_controller(ctrl)

    def use_run_gains(self, run_config: AppConfig) -> tuple[ControllerConfig, list[Operation]]:
        """Gains of an experiment run. Raises :class:`~.rules.IncompatibleRun`."""
        ctrl = controller_from_run(run_config, self.config.plant)
        return ctrl, self.change_controller(ctrl)

    def apply_tuning(self, result) -> list[Operation]:
        """Auto-tune result: its gains, and its recommended push for testing them."""
        ops = self.change_controller(result.controller)
        self.config.push_force = result.recommended_push
        return ops

    def change_simulation(self, settings: SimulationSettings) -> list[Operation]:
        settings.validate()
        self.config.simulation = settings
        return [("simulation", settings)]

    def set_push_force(self, value: float) -> None:
        self.config.push_force = value


def apply(sim: Simulator, op: Operation) -> Simulator:
    """Perform one operation on ``sim``; returns the simulator to use from now on
    (a new one after ``("config", …)``)."""
    name, args = op[0], op[1:]
    if name == "config":
        return Simulator(args[0].copy(), **({"telemetry_window_s": args[1]} if len(args) > 1 else {}))
    if name == "structure":
        operations.apply_structure(sim, *args)
    elif name == "plant":
        operations.apply_plant(sim, args[0])
    elif name == "controller":
        operations.apply_controller(sim, args[0])
    elif name == "simulation":
        operations.apply_simulation_settings(sim, args[0])
    elif name == "reset_integrators":
        operations.reset_integrators(sim)
    else:
        raise KeyError(f"unknown operation {name!r}")
    return sim
