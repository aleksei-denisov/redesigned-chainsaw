"""Offscreen tests of the mobile (Android) interface; skipped without PySide6/pyqtgraph."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QWidget  # noqa: E402

from pendulum_sim.mobile.performance import PerformanceSettings  # noqa: E402
from pendulum_sim.mobile.touch_style import apply_touch_style  # noqa: E402
from pendulum_sim.mobile.window import MobileWindow  # noqa: E402
from pendulum_sim.simulation import SCENARIOS, scenario  # noqa: E402
from pendulum_sim.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(tmp_path_factory.mktemp("settings")))
    app.setOrganizationName("pendulum-sim-tests")
    previous = app.style().name()
    apply_touch_style(app)
    yield app
    app.setStyle(previous)


@pytest.fixture(scope="module")
def mobile(app):
    w = MobileWindow(scenario("single_cart_full"))
    w.resize(400, 800)
    w.show()
    app.processEvents()
    yield w
    w.close()
    app.processEvents()


@pytest.fixture
def win(mobile, app):
    mobile.pause()
    mobile.load(scenario("single_cart_full"))
    app.processEvents()
    return mobile


def frames(w, app, n=10):
    for _ in range(n):
        w._last_frame -= 0.016
        w._on_frame()
        app.processEvents()


def test_is_the_desktop_window_with_a_touch_layout(win):
    assert isinstance(win, MainWindow)
    assert win.menuBar().actions() == []                    # ☰ menu instead of a menu bar
    assert win.tabs.count() == 2 and win.side.count() == 3  # Live/Experiments + 3 config pages


def test_runs_pauses_and_resets(win, app):
    win.run_btn.click()
    frames(win, app, 20)
    assert win.running and win.sim.time > 0.2 and win.run_btn.text() == "⏸ Pause"
    win.run_btn.click()
    t = win.sim.time
    frames(win, app, 5)
    assert win.sim.time == t and win.run_btn.text() == "▶ Resume"
    win.reset_btn.click()
    assert win.sim.time == 0.0 and win.run_btn.text() == "▶ Start"


def test_every_scenario_loads_and_paints(win, app):
    for key in SCENARIOS:
        win.load(scenario(key))
        win.view.repaint()
        app.processEvents()
        assert win.config.name == scenario(key).name


def test_parameter_panels_drive_the_simulation(win, app):
    # Inherited desktop handlers: editing the plant structure reconfigures the controller.
    plant = win.plant_panel.plant()
    plant.segments = plant.segments + [plant.segments[-1].__class__()]
    win.plant_panel.changed.emit(plant, True)
    app.processEvents()
    assert win.config.plant.n_segments == 2 and win.sim.model.n == 2
    win.seed_lqr()
    assert "LQR" in win.statusBar().currentMessage()
    win.push_force.setValue(3.0)
    assert win.config.push_force == pytest.approx(3.0)


def test_background_pauses_and_resumes(win, app):
    win.start()
    frames(win, app, 5)
    win.on_application_state(Qt.ApplicationState.ApplicationSuspended)
    assert not win.running and not win.timer.isActive()
    t = win.sim.time
    win._last_frame -= 60.0             # a minute in the background
    win.on_application_state(Qt.ApplicationState.ApplicationActive)
    assert win.running and win.timer.isActive()
    frames(win, app, 1)
    assert win.sim.time - t < 0.05      # no catch-up burst after resuming


def test_tap_pushes_towards_tapped_side(win, app):
    pushes = []
    win.view.tapPush.connect(pushes.append)
    tip = win.view.to_screen(win.view.tip_world())
    for dx, expected in ((80, 1), (-80, -1)):
        p = QPointF(tip.x() + dx, tip.y())
        win.view.one_down(p)
        win.view.one_up(p)
        assert pushes[-1] == expected
    win.view.tapPush.disconnect(pushes.append)
    win.push(+1)
    assert win.sim.disturbances and win.sim.disturbances[-1].force == pytest.approx(win.config.push_force)


def test_drag_applies_continuous_force_and_releases(win, app):
    win.start()
    tip = win.view.to_screen(win.view.tip_world())
    win.view.one_down(tip)
    win.view.one_move(QPointF(tip.x() + 100, tip.y()))
    assert win._drag_offset > 0 and win.drag_force() > 0
    frames(win, app, 3)
    assert max(abs(r) for r in win.sim.telemetry.column("tip_force")) > 0
    win.view.one_up(QPointF(tip.x() + 100, tip.y()))
    assert win._drag_offset == 0.0 and win.drag_force() == 0.0


def test_pinch_zooms_and_two_finger_pans(win, app):
    v = win.view
    scale0 = v.scale
    c = QPointF(v.width() / 2, v.height() / 2)
    v.two_begin(c - QPointF(50, 0), c + QPointF(50, 0))
    v.two_move(c - QPointF(100, 0), c + QPointF(100, 0))
    assert v.scale == pytest.approx(2 * scale0)
    cam = v.camera.copy()
    v.two_move(c - QPointF(100, 0) + QPointF(30, 0), c + QPointF(100, 0) + QPointF(30, 0))
    assert v.camera[0] < cam[0]          # content follows the fingers
    v.two_end()


def test_orientation_and_panel(win, app):
    win.resize(1280, 800)
    app.processEvents()
    assert win.body.orientation() == Qt.Orientation.Horizontal
    win.set_panel_visible(True)
    app.processEvents()
    assert win.side.isVisible() and win.panel_btn.isChecked()
    win.resize(400, 800)
    app.processEvents()
    assert win.body.orientation() == Qt.Orientation.Vertical
    win.set_panel_visible(False)
    assert not win.side.isVisible()


def test_fits_a_phone_width(win, app):
    wide = sorted((w.minimumSizeHint().width(), type(w).__name__)
                  for w in win.findChildren(QWidget)
                  if w.isVisibleTo(win) and w.minimumSizeHint().width() > 300)
    assert win.minimumSizeHint().width() <= 360, (win.minimumSizeHint(), wide[-8:])


def test_touch_style(win, app):
    boxes = win.findChildren(QAbstractSpinBox)
    assert boxes and all(b.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.PlusMinus for b in boxes)
    assert win.run_btn.sizeHint().height() >= 44


@pytest.mark.parametrize("render", ["native", "high", "balanced", "fast"])
def test_performance_settings_apply_and_persist(win, app, render):
    perf = PerformanceSettings(render=render, graph_hz=5, fps=30, antialias=False, debug_overlay=True)
    win.apply_performance(perf)
    perf.save()
    win.view.repaint()
    assert win.timer.interval() == 33 and win.plot_interval_min == pytest.approx(0.2)
    assert not win.view.antialias
    assert win.stats.view_ms.value >= 0.0
    assert PerformanceSettings.load() == perf
    win.apply_performance(PerformanceSettings())


def test_graphs_selection_limit_persistence_and_visibility(win, app):
    from pendulum_sim.mobile.graphs import GraphsPanel
    from pendulum_sim.mobile.plot_math import MAX_VISIBLE

    g = win.telemetry
    assert isinstance(g, GraphsPanel)
    g.set_selection(["angle", "angular_velocity", "cart_position", "control", "energy"])
    assert len(g.shown_keys()) == MAX_VISIBLE
    g.actions["energy"].setChecked(True)                    # replaces the oldest
    assert "energy" in g.selected and len(g.selected) == MAX_VISIBLE
    assert GraphsPanel().selected == g.selected              # remembered (QSettings)
    win.start()
    frames(win, app, 30)
    g.refresh(win.sim.telemetry, force=True)
    app.processEvents()
    assert all(chart._paths for chart in g.visible_charts())  # curves drawn
    lo, hi = g.charts["angle"].axis.lo, g.charts["angle"].axis.hi
    assert hi - lo >= 2.0                                     # minimum span, no 1e-15 ranges
    # hidden graphs: no data work, refreshed on show
    g.hide()
    version = g._version
    frames(win, app, 5)
    g.refresh(win.sim.telemetry, force=True)
    assert g._version == version and g._pending is not None
    g.show()
    app.processEvents()
    assert g._pending is None and g._version == win.sim.telemetry.version
    g.set_selection(["angle", "control"])


def test_graphs_follow_structure(win, app):
    win.load(scenario("double_fixed_lqr"))
    g = win.telemetry
    g.set_selection(["angle", "cart_position", "pid"])
    assert "cart_position" not in g.shown_keys()              # no cart on a fixed pivot
    assert len(g.charts["angle"].series) == 2
    assert [s.label for s in g.charts["pid"].series] == ["P", "I", "D"]
    g.set_selection(["angle", "control"])
