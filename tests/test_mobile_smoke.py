"""Offscreen tests of the mobile (Android) interface; skipped without PySide6/pyqtgraph."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pendulum_sim.mobile.window import MobileWindow  # noqa: E402
from pendulum_sim.simulation import SCENARIOS, scenario  # noqa: E402


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


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


def test_runs_pauses_and_resets(win, app):
    win.toggle_run()
    frames(win, app, 20)
    assert win.running and win.sim.time > 0.2
    win.toggle_run()
    t = win.sim.time
    frames(win, app, 5)
    assert win.sim.time == t
    win.reset()
    assert win.sim.time == 0.0 and win.run_btn.text() == "▶ Start"


def test_every_scenario_loads_and_paints(win, app):
    for key in SCENARIOS:
        win.load(scenario(key))
        win.view.repaint()
        app.processEvents()
        assert win.config.name == scenario(key).name


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


def test_lqr_seeding_and_orientation_switch(win, app):
    win.load(scenario("double_cart_lqr"))
    win.seed_lqr()
    assert "LQR" in win.status.text()
    win.apply_orientation(True)
    assert win.content.direction().name == "LeftToRight"
    win.apply_orientation(False)
    assert win.content.direction().name == "TopToBottom"


def test_fits_a_phone_width(win, app):
    # A 360 dp wide phone in portrait must not be forced wider by any widget.
    from PySide6.QtWidgets import QWidget

    wide = sorted((w.minimumSizeHint().width(), type(w).__name__, w.objectName())
                  for w in win.findChildren(QWidget)
                  if w.isVisibleTo(win) and w.minimumSizeHint().width() > 300)
    assert win.minimumSizeHint().width() <= 360, (win.minimumSizeHint(), wide[-8:])
