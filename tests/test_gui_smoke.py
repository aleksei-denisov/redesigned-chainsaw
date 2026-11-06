"""Offscreen GUI smoke tests (skipped automatically when PySide6/pyqtgraph are missing).

They exercise the real widgets: building the window, stepping frames, painting
the pendulum view for every scenario, editing parameters live, structural
changes, LQR seeding, experiments and configuration save/load.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pendulum_sim.control import LQRWeights  # noqa: E402
from pendulum_sim.simulation import SCENARIOS, load_config, run_experiment, scenario  # noqa: E402
from pendulum_sim.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture(scope="module")
def main_window(app):
    # One window for the whole module: repeatedly destroying and re-creating Qt/pyqtgraph
    # widgets in one process is fragile on some Linux Qt builds.
    w = MainWindow(scenario("single_cart_full"))
    w.show()
    app.processEvents()
    yield w
    w.close()
    app.processEvents()


@pytest.fixture
def window(main_window, app):
    main_window.pause()
    main_window.experiments.clear()
    main_window.load(scenario("single_cart_full"))
    app.processEvents()
    return main_window


def frames(window, app, n=10):
    for _ in range(n):
        window._last_frame -= 0.016  # pretend 16 ms of wall time passed
        window._on_frame()
        app.processEvents()


def test_window_runs_and_paints(window, app):
    window.start()
    frames(window, app, 30)
    assert window.sim.time > 0.2
    image = window.view.grab()
    assert not image.isNull()
    window.pause()
    t = window.sim.time
    frames(window, app, 5)
    assert window.sim.time == t
    window.reset()
    assert window.sim.time == 0.0
    window.restart()
    frames(window, app, 5)
    assert window.sim.time > 0


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_every_scenario_loads_and_renders(window, app, key):
    window.load(scenario(key))
    window.start()
    frames(window, app, 10)
    window.telemetry.refresh(window.sim.telemetry, force=True)
    assert not window.view.grab().isNull()


def test_live_parameter_edit(window, app):
    window.start()
    frames(window, app, 5)
    editor = window.plant_panel.editors[0]
    editor.mass.setValue(0.35)
    app.processEvents()
    assert window.sim.model.masses[0] == pytest.approx(0.35)
    assert window.sim.time > 0  # not reset


def test_structural_change_and_lqr_seed(window, app):
    window.plant_panel.segments.setValue(3)
    app.processEvents()
    assert window.sim.model.n == 3
    assert len(window.controller_panel.editors) == 4  # cart + 3 segments
    window._on_auto_tune(LQRWeights())
    window.restart()
    frames(window, app, 20)
    assert abs(window.sim.angles).max() < 0.5


def test_pid_gain_edit_applies_live(window, app):
    editor = window.controller_panel.editors[1]
    editor.kp.setValue(123.0)
    app.processEvents()
    assert any(loop.config.kp == 123.0 for loop in window.sim.controller.loops)


def test_experiment_comparison(window, app):
    panel = window.experiments
    panel.duration.setValue(1.0)
    cfg = panel.build_config()
    panel._on_result(run_experiment(cfg, "baseline"))
    window.controller_panel.editors[1].kp.setValue(window.controller_panel.editors[1].kp.value() * 1.5)
    app.processEvents()
    panel._on_result(run_experiment(panel.build_config(), "retuned"))
    assert panel.table.rowCount() == 2
    a, b = panel.results
    assert a.data["theta1"][0] == b.data["theta1"][0]


def test_config_save_and_reload(window, app, tmp_path):
    path = tmp_path / "saved.json"
    window._write_config(path)
    cfg = load_config(path)
    assert cfg.plant.n_segments == window.config.plant.n_segments
