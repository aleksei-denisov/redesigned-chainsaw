"""The shared session logic (pendulum_sim/session) gives exactly the results of the code it replaced.

``Old*`` below is a verbatim copy of the previous code at commit ``bda1ff4`` (the
handlers of ``ui/main_window.py`` and the Widgets mobile window, and
``ControllerPanel.complete_loops`` of ``ui/controller_panel.py``), with only the widget
and message calls removed. Every built-in scenario is driven through the same sequence
of edits twice – once by the old code on its own simulator, once through
:class:`~pendulum_sim.session.Session` and the simulator operations the Qt Quick app's
physics process performs – and after every edit the configurations must be equal and
the simulated states and telemetry bit-identical.
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from pendulum_sim.control import LQRWeights, PIDLoopConfig, default_action, lqr_controller
from pendulum_sim.physics import BaseType
from pendulum_sim.session import IncompatibleRun, Session, apply, operations, rules
from pendulum_sim.simulation import SCENARIOS, ExperimentSettings, SimulationDiverged, Simulator, scenario


# ============================================================ old code (bda1ff4)
def old_complete_loops(config, plant):
    """ui/controller_panel.py: ControllerPanel.complete_loops."""
    signals = (["cart"] if plant.has_cart else []) + [f"theta{i + 1}" for i in range(plant.n_segments)]
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


def old_drag_force(push_force, drag_offset):
    """mobile/window.py: MobileWindow.drag_force (DRAG_GAIN 2, DRAG_LIMIT 4)."""
    f = 2.0 * push_force * drag_offset
    limit = 4.0 * push_force
    return float(np.clip(f, -limit, limit))


def old_build_config(config, settings, reuse_results):
    """ui/experiment_panel.py and mobile/experiments_page.py: build_config."""
    cfg = config.copy()
    cfg.experiment = settings
    if reuse_results:
        base = reuse_results[0].config
        cfg.plant = base.copy().plant
        cfg.simulation = base.copy().simulation
        cfg.controller = old_complete_loops(cfg.controller, cfg.plant)
    return cfg


class OldWindow:
    """ui/main_window.py MainWindow handlers. ``_ui_controller`` is the controller panel's
    content, which the desktop keeps equal to the configuration's controller (a copy)."""

    def __init__(self, config):
        self.running = False
        self.load(config)

    def load(self, config):
        self.running = False
        config.controller = old_complete_loops(config.controller, config.plant)
        config.validate()
        self.sim = Simulator(config)
        self.config = config
        self._original_controller = copy.deepcopy(config.controller)

    def _ui_controller(self):
        return copy.deepcopy(self.config.controller)

    def _reset_sim(self):
        self.sim.reset()

    def reset(self):
        self.running = False
        self._reset_sim()

    def single_step(self):
        self.running = False
        steps = max(1, int(round(self.sim.controller.period / self.config.simulation.dt)))
        self._run_steps(steps, deadline=None)

    def push(self, direction):
        self.sim.apply_disturbance(direction * self.config.push_force, 0.1)

    def _run_steps(self, n, deadline):
        done = 0
        try:
            while done < n:
                k = min(25, n - done)
                for _ in range(k):
                    self.sim.step()
                done += k
        except SimulationDiverged:
            self.running = False
        return done

    def _on_plant_changed(self, plant, structural):
        plant.validate()
        if structural:
            base_changed = plant.base_type != self.config.plant.base_type
            ctrl = self._ui_controller()
            if base_changed:
                ctrl.action = default_action(plant)
                ctrl.output_min, ctrl.output_max = -plant.actuator.max_output, plant.actuator.max_output
                for loop in ctrl.loops:
                    loop.kp, loop.ki, loop.kd, loop.enabled = 0.0, 0.0, 0.0, False
            ctrl = old_complete_loops(ctrl, plant)
            self.config.plant = plant
            self.config.controller = ctrl
            self.sim.update_controller(ctrl)
            self._reset_sim()
            return
        initial_changed = any(
            a.initial_angle != b.initial_angle or a.initial_velocity != b.initial_velocity
            for a, b in zip(plant.segments, self.config.plant.segments)
        )
        self.sim.update_plant(plant)
        if initial_changed and not self.running and self.sim.time == 0.0:
            self._reset_sim()

    def _on_controller_changed(self, controller):
        self.sim.update_controller(controller)

    def _on_auto_tune(self, weights):
        ctrl = lqr_controller(self.config.plant, weights, base=self._ui_controller())
        self.sim.update_controller(ctrl)

    def _apply_tuning_result(self, result):
        self.sim.update_controller(result.controller)
        self.config.push_force = result.recommended_push

    def _on_restore_gains(self):
        plant = self.config.plant
        ctrl = old_complete_loops(copy.deepcopy(self._original_controller), plant)
        ctrl.validate()
        self.sim.update_controller(ctrl)

    def _on_reset_integrators(self):
        self.sim.controller.reset_integrators()

    def _on_simulation_settings(self, settings):
        settings.validate()
        self.config.simulation = settings

    def _apply_experiment_gains(self, result):
        plant = self.config.plant
        if (result.config.plant.n_segments, result.config.plant.base_type) != (plant.n_segments, plant.base_type):
            raise IncompatibleRun("different structure")
        ctrl = result.config.copy().controller
        self._on_controller_changed(ctrl)

    def steps(self, n):
        return self._run_steps(n, None)


# ===================================================== new path (shared logic)
class NewPath:
    """What the Qt Quick app does: Session decides, the physics process applies the operations."""

    def __init__(self, config):
        self.session = Session(config)
        self.running = False
        self.sim = apply(None, ("config", self.session.config))

    def _do(self, ops):
        for op in ops:
            if op[0] == "reset_if_idle":
                if not self.running and self.sim.time == 0.0:
                    self.sim.reset()
                continue
            self.sim = apply(self.sim, op)

    def reset(self):
        self.running = False
        self.sim.reset()

    def single_step(self):
        self.running = False
        operations.single_step(self.sim)

    def push(self, direction):
        self.sim.apply_disturbance(direction * self.session.config.push_force, 0.1)

    def steps(self, n):
        done, exc = operations.run_steps(self.sim, n)
        if exc is not None:
            self.running = False
        return done

    def _on_plant_changed(self, plant, structural):
        change = self.session.change_plant(plant, structural)
        self._do(change.operations)
        if change.reset_if_idle:
            self._do([("reset_if_idle",)])

    def _on_controller_changed(self, controller):
        self._do(self.session.change_controller(controller))

    def _on_auto_tune(self, weights):
        _ctrl, ops = self.session.seed_lqr(weights)
        self._do(ops)

    def _apply_tuning_result(self, result):
        self._do(self.session.apply_tuning(result))

    def _on_restore_gains(self):
        _ctrl, ops = self.session.restore_gains()
        self._do(ops)

    def _on_reset_integrators(self):
        self._do([("reset_integrators",)])

    def _on_simulation_settings(self, settings):
        self._do(self.session.change_simulation(settings))

    def _apply_experiment_gains(self, result):
        _ctrl, ops = self.session.use_run_gains(result.config)
        self._do(ops)


# ==================================================================== helpers
def assert_same(old: OldWindow, new: NewPath, where: str) -> None:
    a, b = old.sim, new.sim
    assert (a.time, a.steps) == (b.time, b.steps), where
    assert np.array_equal(a.state, b.state), where
    n = len(a.telemetry)
    assert n == len(b.telemetry), where
    assert np.array_equal(a.telemetry._data[:n], b.telemetry._data[:n], equal_nan=True), where
    assert asdict(old.config) == asdict(new.session.config), where
    # the simulator's own copy (another process in the Qt Quick app); the push force is
    # never read by the simulator – pushes carry their force – so it is not forwarded
    assert {**asdict(a.config), "push_force": None} == {**asdict(b.config), "push_force": None}, where


def both(old, new, action, *args, label=None):
    """Run ``action`` on both; they must succeed together or fail with the same exception type."""
    errors = []
    for side in (old, new):
        try:
            getattr(side, action)(*copy.deepcopy(args))
            errors.append(None)
        except Exception as exc:  # noqa: BLE001 - compared below
            errors.append(type(exc))
    assert errors[0] == errors[1], f"{label or action}: {errors}"
    assert_same(old, new, label or action)


def plant_variants(plant):
    """Structural variants of ``plant`` (more / fewer segments, the other base type)."""
    out = []
    if plant.n_segments < 3:
        p = copy.deepcopy(plant)
        extra = copy.deepcopy(p.segments[-1])
        extra.initial_angle = extra.initial_velocity = 0.0
        p.segments.append(extra)
        out.append(("one segment more", p))
    if plant.n_segments > 1:
        p = copy.deepcopy(plant)
        p.segments.pop()
        out.append(("one segment less", p))
    p = copy.deepcopy(plant)
    p.base_type = BaseType.FIXED if plant.has_cart else BaseType.CART
    out.append(("other base", p))
    return out


# ====================================================================== tests
@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_scripted_session_matches_old_code(key):
    old, new = OldWindow(scenario(key)), NewPath(scenario(key))
    assert_same(old, new, "load")
    both(old, new, "steps", 300)
    both(old, new, "push", +1)
    both(old, new, "steps", 150)

    ctrl = copy.deepcopy(old.config.controller)
    for loop in ctrl.loops:
        loop.kp *= 1.1
        loop.kd *= 0.9
    both(old, new, "_on_controller_changed", ctrl, label="controller edit")
    both(old, new, "steps", 150)
    both(old, new, "_on_auto_tune", LQRWeights(), label="seed LQR")
    both(old, new, "steps", 150)

    plant = copy.deepcopy(old.config.plant)
    plant.segments[0].mass *= 1.05
    plant.cart_mass *= 0.95
    both(old, new, "_on_plant_changed", plant, False, label="mass edit")
    both(old, new, "steps", 100)

    settings = copy.deepcopy(old.config.simulation)
    settings.dt *= 0.5
    settings.sensor_noise_angle_deg = 0.02
    both(old, new, "_on_simulation_settings", settings, label="time step and noise")
    both(old, new, "steps", 200)
    both(old, new, "_on_reset_integrators")
    both(old, new, "single_step")
    both(old, new, "_on_restore_gains", label="restore gains")
    both(old, new, "steps", 100)
    both(old, new, "_apply_tuning_result",
         SimpleNamespace(controller=copy.deepcopy(old.config.controller), recommended_push=0.37),
         label="tuning result")
    both(old, new, "reset")

    initial = copy.deepcopy(old.config.plant)
    initial.segments[0].initial_angle += 0.01
    both(old, new, "_on_plant_changed", initial, False, label="initial angle while idle")
    both(old, new, "steps", 100)

    for name, variant in plant_variants(old.config.plant):
        both(old, new, "_on_plant_changed", variant, True, label=f"structure: {name}")
        both(old, new, "steps", 120, label=f"steps after {name}")
        both(old, new, "_on_auto_tune", LQRWeights(), label=f"seed LQR after {name}")
        both(old, new, "steps", 120, label=f"steps after LQR, {name}")
        both(old, new, "_on_restore_gains", label=f"restore after {name}")


@pytest.mark.parametrize("key", ["triple_cart_lqr", "single_fixed_pid"])
def test_gains_from_a_run(key):
    old, new = OldWindow(scenario(key)), NewPath(scenario(key))
    run = SimpleNamespace(config=scenario(key))
    for loop in run.config.controller.loops:
        loop.kp += 1.0
    both(old, new, "_apply_experiment_gains", run, label="gains of a compatible run")
    both(old, new, "steps", 200)
    other = SimpleNamespace(config=scenario("single_cart_pid" if key != "single_cart_pid" else "double_cart_lqr"))
    both(old, new, "_apply_experiment_gains", other, label="gains of an incompatible run")


def test_complete_loops_on_every_structure():
    for key in SCENARIOS:
        cfg = scenario(key)
        for _name, plant in [("as is", cfg.plant)] + plant_variants(cfg.plant):
            for drop in range(len(cfg.controller.loops) + 1):
                a, b = copy.deepcopy(cfg.controller), copy.deepcopy(cfg.controller)
                del a.loops[:drop], b.loops[:drop]
                assert asdict(rules.complete_loops(a, plant)) == asdict(old_complete_loops(b, plant))


def test_small_rules():
    for push in (0.06, 1.0, 25.0):
        for offset in (-3.0, -0.4, 0.0, 0.01, 0.7, 5.0):
            assert rules.drag_force(push, offset) == old_drag_force(push, offset)
    for period, dt in ((0.002, 0.001), (0.01, 0.001), (0.0004, 0.001), (0.0025, 0.001)):
        assert rules.single_step_count(period, dt) == max(1, int(round(period / dt)))
    cfg = scenario("triple_cart_lqr")
    assert rules.loop_labels(cfg.controller) == ["cart" if lp.signal == "cart" else f"θ{lp.signal[5:]}"
                                                 for lp in cfg.controller.loops]
    c = cfg.controller
    assert rules.output_limit(cfg) == max(cfg.plant.actuator.max_output, abs(c.output_min), abs(c.output_max))


def test_experiment_config_matches_old_build_config():
    base = scenario("double_cart_lqr")
    first_run = SimpleNamespace(config=scenario("double_cart_lqr"))
    first_run.config.plant.segments[0].mass = 0.33
    first_run.config.simulation.dt = 0.0005
    settings = ExperimentSettings(duration=3.0)
    for reuse in ([], [first_run]):
        old = old_build_config(base, copy.deepcopy(settings), reuse)
        new = rules.experiment_config(base, copy.deepcopy(settings), reuse[0].config if reuse else None)
        assert asdict(old) == asdict(new)


def test_operations_divergence_stops_like_the_window():
    """A diverging simulation: both stop at the same step with the same state."""
    old, new = OldWindow(scenario("single_cart_pid")), NewPath(scenario("single_cart_pid"))
    ctrl = copy.deepcopy(old.config.controller)
    for loop in ctrl.loops:
        loop.kp, loop.kd, loop.enabled = -5000.0, -500.0, True
    both(old, new, "_on_controller_changed", ctrl)
    both(old, new, "steps", 20000, label="until divergence")
