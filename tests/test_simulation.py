"""Tests for the engine, configuration IO, metrics and experiments."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from pendulum_sim.control import AntiWindup
from pendulum_sim.simulation import (
    SCENARIOS,
    Simulator,
    config_from_dict,
    config_to_dict,
    export_csv,
    load_config,
    load_experiments,
    run_experiment,
    save_config,
    save_experiments,
    scenario,
)
from pendulum_sim.simulation.metrics import overshoot_pct, settling_time

ROOT = Path(__file__).resolve().parents[1]


def assert_nested_close(a, b):
    if isinstance(a, dict):
        assert a.keys() == b.keys()
        for k in a:
            assert_nested_close(a[k], b[k])
    elif isinstance(a, list):
        assert len(a) == len(b)
        for x, y in zip(a, b):
            assert_nested_close(x, y)
    elif isinstance(a, float):
        assert a == pytest.approx(b, rel=1e-12, abs=1e-12)
    else:
        assert a == b


def test_fixed_timestep_is_independent_of_advance_chunking():
    a, b = Simulator(scenario("double_cart_lqr")), Simulator(scenario("double_cart_lqr"))
    a.advance(1.0)
    for _ in range(100):
        b.advance(0.01)
    np.testing.assert_array_equal(a.state, b.state)
    assert a.time == pytest.approx(1.0)


def test_live_parameter_change_keeps_state():
    sim = Simulator(scenario("single_cart_full"))
    sim.advance(0.5)
    state = sim.state.copy()
    plant = sim.config.plant
    plant.segments[0].mass = 0.3
    assert sim.update_plant(plant)
    np.testing.assert_array_equal(sim.state, state)
    sim.advance(0.5)
    assert sim.model.masses[0] == 0.3


def test_segment_count_change_requires_reset():
    sim = Simulator(scenario("single_cart_full"))
    plant = scenario("double_cart_lqr").plant
    assert not sim.update_plant(plant)


def test_disturbance_is_applied_for_its_duration():
    sim = Simulator(scenario("single_cart_full"))
    sim.apply_disturbance(5.0, 0.05)
    sim.advance(0.02)
    assert sim.tip_force == 5.0
    sim.advance(0.05)
    assert sim.tip_force == 0.0


def test_sensor_noise_is_reproducible():
    cfg = scenario("single_cart_full")
    cfg.simulation.sensor_noise_angle_deg = 0.2
    a, b = Simulator(cfg.copy()), Simulator(cfg.copy())
    a.advance(1.0)
    b.advance(1.0)
    np.testing.assert_array_equal(a.state, b.state)


def test_telemetry_window_is_bounded():
    sim = Simulator(scenario("single_cart_full"), telemetry_window_s=1.0)
    sim.advance(5.0)
    assert len(sim.telemetry) <= 2 * 200 + 1
    t = sim.telemetry.column("t")
    assert t[-1] == pytest.approx(5.0, abs=0.01)


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_config_json_roundtrip(key, tmp_path):
    cfg = scenario(key)
    path = tmp_path / "cfg.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert_nested_close(config_to_dict(loaded), config_to_dict(cfg))
    assert loaded.plant.segments[0].initial_angle == pytest.approx(cfg.plant.segments[0].initial_angle)


def test_example_configs_load():
    files = sorted((ROOT / "configs").glob("*.json"))
    assert len(files) >= len(SCENARIOS)
    for f in files:
        cfg = load_config(f)
        assert cfg.plant.n_segments in (1, 2, 3)


def test_bad_config_reports_error(tmp_path):
    with pytest.raises(ValueError):
        config_from_dict({"plant": {"segments": [{"length": -1}]}})
    with pytest.raises(ValueError):
        config_from_dict({"plant": {"base_type": "rocket"}})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(bad)


def test_settling_time_and_overshoot_definitions():
    t = np.linspace(0, 1, 11)
    e = np.array([10, 5, -3, 2, 0.5, 0.2, 0.1, 0.0, 0.0, 0.0, 0.0])
    assert settling_time(t, e, 1.0) == pytest.approx(0.4)
    assert overshoot_pct(e) == pytest.approx(30.0)
    assert settling_time(t, np.ones(11) * 5, 1.0) is None


def test_experiment_is_deterministic_and_identical_initial_conditions():
    cfg = scenario("single_fixed_pid")
    r1, r2 = run_experiment(cfg, "a"), run_experiment(cfg, "b")
    np.testing.assert_array_equal(r1.data["theta1"], r2.data["theta1"])
    assert r1.metrics.rmse == r2.metrics.rmse


def test_experiment_comparison_shows_anti_windup_effect():
    cfg = scenario("windup_demo")
    base = run_experiment(cfg, "no anti-windup")
    cfg.controller.loops[0].anti_windup = AntiWindup.CLAMP
    clamped = run_experiment(cfg, "clamp")
    assert clamped.metrics.overshoot_pct < base.metrics.overshoot_pct
    assert clamped.data["theta1"][0] == base.data["theta1"][0]


def test_experiment_detects_fall():
    cfg = scenario("single_cart_pid")
    cfg.controller.enabled = False
    assert run_experiment(cfg).metrics.fell


def test_experiment_save_load_and_csv(tmp_path):
    results = [run_experiment(scenario("single_fixed_pid"), "one")]
    path = tmp_path / "exp.json"
    save_experiments(results, path)
    loaded = load_experiments(path)
    assert loaded[0].name == "one"
    np.testing.assert_allclose(loaded[0].data["theta1"], results[0].data["theta1"])
    assert loaded[0].metrics.rmse == results[0].metrics.rmse
    csv_path = tmp_path / "exp.csv"
    export_csv(results[0], csv_path)
    lines = csv_path.read_text().splitlines()
    assert lines[0].startswith("t,x,xd,theta1_deg")
    assert len(lines) == len(results[0].data["t"]) + 1


def test_energy_flat_in_free_swing_scenario():
    r = run_experiment(scenario("double_free_swing"))
    e = r.data["energy"]
    assert np.ptp(e) < 1e-4


def test_angle_telemetry_is_continuous():
    r = run_experiment(scenario("hanging_damped"))
    assert np.max(np.abs(np.diff(r.data["theta1"]))) < math.radians(10)
