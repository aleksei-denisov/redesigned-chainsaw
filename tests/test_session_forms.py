"""Editor models of the parameter screens (pendulum_sim/session/forms.py), Qt-free."""

from __future__ import annotations

import math
from dataclasses import asdict

import pytest

from pendulum_sim.physics import BaseType, SegmentParams
from pendulum_sim.session.forms import ControllerForm, PlantForm, SimulationForm
from pendulum_sim.simulation import SCENARIOS, scenario


@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_round_trips(key):
    cfg = scenario(key)
    plant_form, sim_form, ctrl_form = PlantForm(), SimulationForm(), ControllerForm()
    plant_form.set_plant(cfg.plant)
    sim_form.set_simulation(cfg.simulation)
    ctrl_form.set_config(cfg.controller)
    got = plant_form.plant()
    for a, b in zip(got.segments, cfg.plant.segments):
        # a uniform rod's inertia is recomputed; angles go through degrees (as in the Widgets editors)
        for name in ("inertia", "initial_angle", "initial_velocity"):
            assert getattr(a, name) == pytest.approx(getattr(b, name), rel=1e-6, abs=1e-12)
            setattr(a, name, getattr(b, name))
    assert asdict(got) == asdict(cfg.plant)
    assert asdict(sim_form.simulation()) == pytest.approx(asdict(cfg.simulation))
    assert asdict(ctrl_form.config()) == asdict(cfg.controller)


def test_segment_count_keeps_spare_segments():
    form = PlantForm()
    form.set_plant(scenario("triple_cart_lqr").plant)
    form.set("seg2.mass", 0.42)
    plant, structural = form.set("segments", 2)
    assert structural and plant.n_segments == 2
    assert not form.visible("seg2") and form.visible("seg1")
    plant, structural = form.set("gravity", 9.0)
    assert not structural
    plant, structural = form.set("segments", 3)
    assert structural and plant.segments[2].mass == 0.42      # comes back as it was


def test_unused_segment_editors_start_as_the_last_segment_at_rest():
    form = PlantForm()
    single = scenario("single_cart_pid").plant
    form.set_plant(single)
    plant, _ = form.set("segments", 3)
    for seg in plant.segments[1:]:
        assert seg.length == single.segments[0].length and seg.initial_angle == 0.0


def test_uniform_rod_computes_the_inertia():
    form = PlantForm()
    form.set_plant(scenario("single_cart_full").plant)
    assert form.get("seg0.rod") and not form.editable("seg0.inertia")
    plant, _ = form.set("seg0.mass", 2.0)
    assert plant.segments[0].inertia == pytest.approx(SegmentParams.rod_inertia(2.0, plant.segments[0].length))
    form.set("seg0.rod", False)
    plant, _ = form.set("seg0.inertia", 0.5)
    assert form.editable("seg0.inertia") and plant.segments[0].inertia == 0.5
    plant, _ = form.set("seg0.rod", True)
    assert plant.segments[0].inertia == pytest.approx(SegmentParams.rod_inertia(2.0, plant.segments[0].length))


def test_values_are_clamped_and_converted():
    form = PlantForm()
    form.set_plant(scenario("single_cart_full").plant)
    plant, _ = form.set("gravity", 99.0)
    assert plant.gravity == 30.0
    plant, _ = form.set("seg0.angle", 10.0)
    assert plant.segments[0].initial_angle == pytest.approx(math.radians(10.0))
    plant, structural = form.set("base", BaseType.FIXED.value)
    assert structural and not form.visible("cart") and form.actuator_note().startswith("Torque")
    sim = SimulationForm()
    sim.set_simulation(scenario("single_cart_full").simulation)
    assert sim.set("dt", 0.5).dt == pytest.approx(0.0005)
    assert sim.set("seed", 12.0).noise_seed == 12


def test_controller_form_edits_and_rebuilds():
    form = ControllerForm()
    triple = scenario("triple_cart_lqr").controller
    assert form.set_config(triple)
    kp = form.get("loop1.kp")
    ctrl = form.set("loop1.kp", kp * 2)
    assert ctrl.loops[1].kp == kp * 2 and ctrl.loops[0] == triple.loops[0]
    ctrl = form.set("loop1.setpoint", 5.0)
    assert ctrl.loops[1].setpoint == pytest.approx(math.radians(5.0))
    assert form.set("lqr.angle", 250.0) is None and form.weights().angle == 250.0
    assert not form.set_config(ctrl)                           # same loops: sections kept
    double = scenario("double_cart_lqr").controller
    assert form.set_config(double)                             # other loops: rebuilt
    assert "loop3.kp" not in form.values and form.loop_signals() == [lp.signal for lp in double.loops]
