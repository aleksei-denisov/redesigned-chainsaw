"""Undo / redo of parameter edits and restoring snapshots through the session."""

from __future__ import annotations

import copy

from pendulum_sim.session.history import EditHistory
from pendulum_sim.session.session import Session
from pendulum_sim.simulation.scenarios import scenario


def _config(name: str = "double_cart_lqr"):
    return scenario(name)


def test_history_undo_redo_and_new_edit_clears_redo():
    h = EditHistory(limit=3)
    assert not h.can_undo and h.undo("x") is None
    h.record(1)
    h.record(2)
    assert h.undo(3) == 2 and h.can_redo
    assert h.redo(2) == 3 and not h.can_redo
    h.undo(3)
    h.record(9)                                   # a new edit
    assert not h.can_redo
    for k in range(10):
        h.record(k)
    assert len(h._undo) == 3                      # limited


def test_restore_model_brings_back_structure_and_controller():
    s = Session(_config())
    before = (copy.deepcopy(s.config.plant), copy.deepcopy(s.config.simulation), copy.deepcopy(s.config.controller))
    plant = copy.deepcopy(s.config.plant)
    plant.segments = plant.segments[:1]
    change = s.change_plant(plant, structural=True)
    assert change.structural
    back = s.restore_model(*before)
    assert back.structural
    assert s.config.plant == before[0]
    assert [lp.signal for lp in s.config.controller.loops] == [lp.signal for lp in before[2].loops]
    assert [lp.kp for lp in s.config.controller.loops] == [lp.kp for lp in before[2].loops]


def test_restore_model_plain_edit_and_reset_values_kept_at_load():
    s = Session(_config())
    plant = copy.deepcopy(s.config.plant)
    plant.cart_mass *= 2
    s.change_plant(plant, structural=False)
    change = s.restore_model(s.original_plant, s.original_simulation, s.original_controller)
    assert not change.structural and change.operations[-1][0] == "plant"
    assert s.config.plant == s.original_plant


def test_restore_controller_fits_the_current_structure():
    s = Session(_config())
    ctrl = copy.deepcopy(s.config.controller)
    ctrl.loops[0].kp += 1.0
    s.change_controller(ctrl)
    s.restore_controller(s.original_controller)
    assert s.config.controller.loops[0].kp == s.original_controller.loops[0].kp
