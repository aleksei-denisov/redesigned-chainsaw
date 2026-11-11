"""Rules for editing the configuration of an interactive session (Qt-free).

These are the decisions every front end makes the same way – what happens to the
controller when the pendulum's structure changes, which loops a controller must have,
what "restore the original gains" or "use the gains of that run" means. They used to
live in the desktop window and panels (``ui/main_window.py``, ``ui/controller_panel.py``)
and the Widgets mobile window; they are moved here unchanged so the Qt Quick interface
can use them too. ``tests/test_session_equivalence.py`` checks them against a verbatim
copy of the previous code.

Functions that change a configuration object do so in place and return it, exactly as
the code they replace did.
"""

from __future__ import annotations

import copy

import numpy as np

from ..control import ControllerConfig, PIDLoopConfig, default_action
from ..physics import PlantParams
from ..simulation import AppConfig, ExperimentSettings

DRAG_GAIN = 2.0          # drag force = DRAG_GAIN · push force · (offset / total length)
DRAG_LIMIT = 4.0         # … clamped to ±DRAG_LIMIT · push force


class IncompatibleRun(ValueError):
    """A run's gains belong to a pendulum with another structure (segments or base)."""


def loop_signals(plant: PlantParams) -> list[str]:
    """Signals a controller for ``plant`` can have loops on: ``cart`` (if any), ``theta1`` …"""
    return (["cart"] if plant.has_cart else []) + [f"theta{i + 1}" for i in range(plant.n_segments)]


def complete_loops(config: ControllerConfig, plant: PlantParams) -> ControllerConfig:
    """Ensure there is one loop per available signal (disabled zero-gain if new)."""
    signals = loop_signals(plant)
    existing = {loop.signal: loop for loop in config.loops}
    loops = []
    for sig in signals:
        if sig in existing:
            loops.append(existing[sig])
        else:
            name = "Cart position" if sig == "cart" else f"Segment {sig[5:]} angle"
            loops.append(PIDLoopConfig(name=name, signal=sig, enabled=False, kp=0.0, ki=0.0, kd=0.0))
    config.loops = loops
    return config


def prepare_config(config: AppConfig) -> AppConfig:
    """A configuration about to be loaded (scenario, file): complete loops, then validate.

    Raises ``ValueError`` for an invalid configuration.
    """
    config.controller = complete_loops(config.controller, config.plant)
    config.validate()
    return config


def controller_for_structure(controller: ControllerConfig, plant: PlantParams,
                             old_plant: PlantParams) -> ControllerConfig:
    """The controller after the number of segments or the base type changed.

    A new base type (cart ↔ fixed pivot) gets the default control action and output
    limits of the new actuator, and all gains are cleared (they meant something else);
    then the loops are completed for the new structure.
    """
    ctrl = controller
    if plant.base_type != old_plant.base_type:
        ctrl.action = default_action(plant)
        ctrl.output_min, ctrl.output_max = -plant.actuator.max_output, plant.actuator.max_output
        for loop in ctrl.loops:
            loop.kp, loop.ki, loop.kd, loop.enabled = 0.0, 0.0, 0.0, False
    return complete_loops(ctrl, plant)


def is_structural(plant: PlantParams, old_plant: PlantParams) -> bool:
    """Number of segments or base type differ (the state vector changes meaning)."""
    return (plant.n_segments, plant.base_type) != (old_plant.n_segments, old_plant.base_type)


def initial_conditions_changed(plant: PlantParams, old_plant: PlantParams) -> bool:
    """An initial angle or angular velocity was edited (the initial pose is shown at once)."""
    return any(
        a.initial_angle != b.initial_angle or a.initial_velocity != b.initial_velocity
        for a, b in zip(plant.segments, old_plant.segments)
    )


def restored_controller(original: ControllerConfig, plant: PlantParams) -> ControllerConfig:
    """The controller as loaded, with loops completed for the current plant (validated)."""
    ctrl = complete_loops(copy.deepcopy(original), plant)
    ctrl.validate()
    return ctrl


def same_structure(a: PlantParams, b: PlantParams) -> bool:
    return (a.n_segments, a.base_type) == (b.n_segments, b.base_type)


def controller_from_run(run_config: AppConfig, plant: PlantParams) -> ControllerConfig:
    """A copy of an experiment run's controller, if it fits the current plant's structure."""
    if not same_structure(run_config.plant, plant):
        raise IncompatibleRun("That run used a different pendulum structure.")
    return run_config.copy().controller


def single_step_count(controller_period: float, dt: float) -> int:
    """Physics steps of one *Step*: one controller period (at least one step)."""
    return max(1, int(round(controller_period / dt)))


def drag_force(push_force: float, offset: float) -> float:
    """Tip force while the pendulum is dragged; ``offset`` is relative to the total length."""
    f = DRAG_GAIN * push_force * offset
    limit = DRAG_LIMIT * push_force
    return float(np.clip(f, -limit, limit))


def loop_labels(controller: ControllerConfig) -> list[str]:
    """Short loop names for graphs: ``cart``, ``θ1``, ``θ2`` …"""
    return ["cart" if loop.signal == "cart" else f"θ{loop.signal[5:]}" for loop in controller.loops]


def output_limit(config: AppConfig) -> float:
    """Largest output the graphs must show (actuator or controller limit)."""
    c = config.controller
    return max(config.plant.actuator.max_output, abs(c.output_min), abs(c.output_max))


def experiment_config(config: AppConfig, settings: ExperimentSettings,
                      reuse: AppConfig | None = None) -> AppConfig:
    """Configuration of a new experiment run.

    ``reuse``: the first run's configuration when its plant and simulation settings are
    to be kept (only the controller differs between the compared runs).
    """
    cfg = config.copy()
    cfg.experiment = settings
    if reuse is not None:
        cfg.plant = reuse.copy().plant
        cfg.simulation = reuse.copy().simulation
        cfg.controller = complete_loops(cfg.controller, cfg.plant)
    return cfg
