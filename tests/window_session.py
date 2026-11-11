"""Drive a real window (desktop or Widgets mobile) and the shared Session side by side.

Used by the offscreen GUI tests: the window's handlers make their decisions through
``pendulum_sim.session`` now; this checks, inside the real windows, that the window and a
:class:`~pendulum_sim.session.Session` with its own simulator (what the Qt Quick app does)
end up with the same configuration and bit-identical states. The window keeps unapplied
edits in its editors, so the Session is given the editors' controller where the window
uses it (``edited`` / ``base``).
"""

from __future__ import annotations

import copy
from dataclasses import asdict

import numpy as np

from pendulum_sim.control import LQRWeights
from pendulum_sim.session import Session, apply, operations
from pendulum_sim.simulation import scenario


def drive(window, process, apply_plant) -> list[str]:
    """Run the edit script; returns the checkpoints passed. ``apply_plant(plant, structural)``
    is the window's plant handler (the mobile one without its busy overlay)."""
    key = "triple_cart_lqr"
    window.pause()
    window.load(scenario(key))
    process()
    session = Session(scenario(key))
    state = {"sim": apply(None, ("config", session.config))}
    passed = []

    def do(ops):
        for op in ops:
            state["sim"] = apply(state["sim"], op)

    def same(where):
        a, b = window.sim, state["sim"]
        assert (a.time, a.steps) == (b.time, b.steps), where
        assert np.array_equal(a.state, b.state), where
        assert asdict(window.config.controller) == asdict(session.config.controller), where
        assert asdict(window.config.plant) == asdict(session.config.plant), where
        assert asdict(window.config.simulation) == asdict(session.config.simulation), where
        passed.append(where)

    def steps(n):
        window._run_steps(n, None)
        operations.run_steps(state["sim"], n)

    same("load")
    steps(300)
    same("steps")

    weights = LQRWeights(angle=150.0)
    base = window._ui_controller()
    window._on_auto_tune(weights)
    _ctrl, ops = session.seed_lqr(weights, base=base)
    do(ops)
    process()
    same("seed LQR")
    steps(200)
    same("steps after LQR")

    plant = copy.deepcopy(window.config.plant)
    plant.segments[1].mass *= 1.1
    apply_plant(copy.deepcopy(plant), False)
    do(session.change_plant(copy.deepcopy(plant), False).operations)
    process()
    same("mass edit")
    steps(200)
    same("steps after mass edit")

    settings = copy.deepcopy(window.config.simulation)
    settings.dt = 0.0005
    window._on_simulation_settings(copy.deepcopy(settings))
    do(session.change_simulation(copy.deepcopy(settings)))
    steps(200)
    same("time step")

    structure = copy.deepcopy(window.config.plant)
    structure.segments.pop()
    edited = window._ui_controller()
    apply_plant(copy.deepcopy(structure), True)
    do(session.change_plant(copy.deepcopy(structure), True, edited_controller=edited).operations)
    process()
    same("one segment less")
    steps(200)
    same("steps after the structure change")

    window._on_restore_gains()
    _ctrl, ops = session.restore_gains()
    do(ops)
    process()
    same("restore gains")
    steps(200)
    same("steps after restoring")

    window.single_step()
    operations.single_step(state["sim"])
    same("single step")
    return passed
