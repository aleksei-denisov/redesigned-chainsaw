"""Offscreen tests of the mobile (Android) interface; skipped without PySide6/pyqtgraph."""

from __future__ import annotations

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QScrollArea, QWidget  # noqa: E402

from pendulum_sim.mobile import icons, theme  # noqa: E402
from pendulum_sim.mobile.performance import PerformanceSettings  # noqa: E402
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
    old_palette, old_sheet, old_font = app.palette(), app.styleSheet(), app.font()
    theme.apply(app)
    yield app
    app.setPalette(old_palette)
    app.setStyleSheet(old_sheet)
    app.setFont(old_font)


@pytest.fixture(scope="module")
def mobile(app):
    w = MobileWindow(scenario("single_cart_full"))
    w.resize(1280, 800)
    w.show()
    app.processEvents()
    yield w
    if w.autotune.worker is not None:
        w.autotune.cancel()
        w.autotune.worker.wait(20000)
    w.close()
    w.deleteLater()
    app.processEvents()


@pytest.fixture
def win(mobile, app):
    mobile.pause()
    mobile.resize(1280, 800)
    mobile.navigate("simulate", remember=False)
    mobile._history.clear()
    mobile.load(scenario("single_cart_full"))
    app.processEvents()
    return mobile


def frames(w, app, n=10):
    for _ in range(n):
        w._last_frame -= 0.016
        w._on_frame()
        app.processEvents()


def widest(root, limit):
    """Visible widgets under ``root`` whose minimum width exceeds ``limit`` (diagnostics)."""
    out = []
    for w in root.findChildren(QWidget):
        if w.isVisibleTo(root):
            m = max(w.minimumWidth(), w.minimumSizeHint().width())
            if m > limit:
                text = getattr(w, "text", lambda: "")()
                out.append((m, type(w).__name__, w.objectName(), str(text)[:40]))
    return sorted(out, reverse=True)[:8]


def wait(app, cond, timeout=120.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    return cond()


# ------------------------------------------------------------------ structure
def test_is_the_desktop_window_with_screens(win):
    assert isinstance(win, MainWindow)
    assert not hasattr(win, "plant_panel") and not hasattr(win, "controller_panel")   # config is the source
    assert set(win.pages) == {"simulate", "graphs", "controller", "system", "autotune", "experiments", "settings"}
    assert win.menuBar().actions() == [] and not win.statusBar().isVisible()
    assert win.nav.vertical                                  # rail on a landscape tablet


def test_navigation_and_back_button(win, app):
    win.navigate("graphs")
    win.navigate("controller")
    assert win.current == "controller" and win.nav.buttons["controller"].isChecked()
    assert win.go_back() and win.current == "graphs"
    assert win.go_back() and win.current == "simulate"
    assert not win.go_back()                                 # on Simulate: ask before leaving
    win.resize(412, 915)                                     # phone portrait: bottom bar + More
    app.processEvents()
    assert not win.nav.vertical and win.nav.buttons["more"].isVisibleTo(win)
    assert not win.nav.buttons["settings"].isVisibleTo(win)
    win.navigate("settings")
    assert win.nav.buttons["more"].isChecked()
    win.navigate("simulate")


def test_simulation_keeps_running_on_other_screens(win, app):
    win.start()
    win.navigate("controller")
    t = win.sim.time
    frames(win, app, 10)
    assert win.sim.time > t
    win.navigate("simulate")
    win.pause()


def test_runs_pauses_and_resets(win, app):
    win.run_btn.click()
    frames(win, app, 20)
    assert win.running and win.sim.time > 0.2 and "Pause" in win.run_btn.text()
    win.run_btn.click()
    t = win.sim.time
    frames(win, app, 5)
    assert win.sim.time == t and "Resume" in win.run_btn.text()
    win.reset_btn.click()
    assert win.sim.time == 0.0 and "Start" in win.run_btn.text()


def test_every_scenario_loads_and_paints(win, app):
    for key in SCENARIOS:
        win.load(scenario(key))
        win.view.repaint()
        app.processEvents()
        assert win.config.name == scenario(key).name
        assert len(win.controller_page.loop_sections) == len(win.config.controller.loops)


# --------------------------------------------------------------------- editors
def test_controller_editor_drives_the_simulation(win, app):
    page = win.controller_page
    sec = page.loop_sections[-1]
    sec.kp._commit(123.0)
    assert win.sim.controller.config.loops[-1].kp == pytest.approx(123.0)
    sec.terms.buttons["d"].setChecked(False)
    assert win.sim.controller.config.loops[-1].d_enabled is False
    page.freq._commit(250.0)
    assert win.sim.controller.config.frequency_hz == pytest.approx(250.0)
    page.lqr_btn.click()
    assert win.toast.isVisible() and "LQR" in win.toast.text()
    page.restore_btn.click()
    original = scenario("single_cart_full").controller.loops[-1].kp
    assert win.sim.controller.config.loops[-1].kp == pytest.approx(original)


def test_system_editor_structural_and_parameter_changes(win, app):
    page = win.system_page
    page.segments.plus.click()
    assert win.config.plant.n_segments == 2 and win.sim.model.n == 2
    assert len(win.controller_page.loop_sections) == 3        # cart + θ1 + θ2 loops completed
    assert page.seg_sections[1].isVisibleTo(page)
    page.gravity._commit(3.71)
    assert win.sim.config.plant.gravity == pytest.approx(3.71)
    page.base.buttons["fixed"].click()
    assert not win.config.plant.has_cart and not page.cart_sec.isVisibleTo(page)
    page.dt._commit(0.5)
    assert win.config.simulation.dt == pytest.approx(0.0005)


def test_settings_page_view_and_performance(win, app):
    st = win.settings_page
    st.view_rows["show_grid"].switch.click()
    assert win.view.settings.show_grid is False
    st.view_rows["show_grid"].switch.click()
    st.perf_fps.buttons[30].click()
    assert win.timer.interval() == 33 and PerformanceSettings.load().fps == 30
    st.perf_stats.switch.click()
    frames(win, app, 3)
    win._update_status()
    assert "fps" in win.view.overlay_text
    win.apply_performance(PerformanceSettings())
    PerformanceSettings().save()
    assert win.view.overlay_text == ""


@pytest.mark.parametrize("render", ["native", "high", "balanced", "fast"])
def test_performance_render_modes(win, app, render):
    win.apply_performance(PerformanceSettings(render=render, antialias=False))
    win.view.repaint()
    assert not win.view.antialias and win.stats.view_ms.value >= 0.0
    win.apply_performance(PerformanceSettings())


# --------------------------------------------------------------------- gestures
def test_background_pauses_and_resumes(win, app):
    win.start()
    frames(win, app, 5)
    win.on_application_state(Qt.ApplicationState.ApplicationSuspended)
    assert not win.running and not win.timer.isActive()
    t = win.sim.time
    win._last_frame -= 60.0
    win.on_application_state(Qt.ApplicationState.ApplicationActive)
    assert win.running and win.timer.isActive()
    frames(win, app, 1)
    assert win.sim.time - t < 0.05
    win.pause()


def test_tap_drag_and_pinch(win, app):
    pushes = []
    win.view.tapPush.connect(pushes.append)
    tip = win.view.to_screen(win.view.tip_world())
    for dx, expected in ((80, 1), (-80, -1)):
        p = QPointF(tip.x() + dx, tip.y())
        win.view.one_down(p)
        win.view.one_up(p)
        assert pushes[-1] == expected
    win.view.tapPush.disconnect(pushes.append)
    win.start()
    win.view.one_down(tip)
    win.view.one_move(QPointF(tip.x() + 100, tip.y()))
    assert win.drag_force() > 0
    frames(win, app, 3)
    win.view.one_up(QPointF(tip.x() + 100, tip.y()))
    assert win.drag_force() == 0.0
    v = win.view
    scale0 = v.scale
    c = QPointF(v.width() / 2, v.height() / 2)
    v.two_begin(c - QPointF(50, 0), c + QPointF(50, 0))
    v.two_move(c - QPointF(100, 0), c + QPointF(100, 0))
    assert v.scale == pytest.approx(2 * scale0)
    v.two_end()
    win.pause()


# ---------------------------------------------------------------------- graphs
def test_graphs_selection_limit_persistence_and_visibility(win, app):
    from pendulum_sim.mobile.graphs import GraphsPanel
    from pendulum_sim.mobile.plot_math import MAX_VISIBLE

    g = win.telemetry
    win.navigate("graphs")
    g.set_selection(["angle", "angular_velocity", "cart_position", "control", "energy"])
    assert len(g.shown_keys()) == MAX_VISIBLE and "energy" not in g.selected
    g.chips.buttons["energy"].click()                         # replaces the oldest
    assert "energy" in g.selected and len(g.selected) == MAX_VISIBLE
    other = GraphsPanel()
    assert other.selected == g.selected                       # remembered
    other.deleteLater()
    win.start()
    frames(win, app, 30)
    g.refresh(win.sim.telemetry, force=True)
    app.processEvents()
    assert all(chart._paths for chart in g.visible_charts())
    for chart in g.visible_charts():
        assert chart.axis.hi - chart.axis.lo >= chart.gdef.min_span
    win.navigate("simulate")                                  # graphs hidden: no data work
    app.processEvents()
    version = g._version
    frames(win, app, 5)
    g.refresh(win.sim.telemetry, force=True)
    assert g._version == version and g._pending is not None
    win.navigate("graphs")
    app.processEvents()
    assert g._pending is None
    g.set_selection(["angle", "control"])
    win.pause()
    win.navigate("simulate")


# ------------------------------------------------------------------- autotune
def test_autotune_page_locks_the_app_and_applies(win, app, monkeypatch):
    from pendulum_sim.mobile import autotune_screen
    from pendulum_sim.simulation import tuning

    fast = tuning.TuningSettings(horizon=2.0, samples=3, refine_rounds=1, refine_samples=2, resolution=0.1)
    monkeypatch.setattr(autotune_screen.TuningSettings, "preset",
                        staticmethod(lambda name: tuning.TuningSettings(**fast.__dict__)))
    win.start()
    win.navigate("controller")
    win.controller_page.autotune_btn.click()
    screen = win.autotune
    assert win.current == "autotune" and screen.pages.currentIndex() == 0
    assert wait(app, lambda: screen.push_test_s is not None, 60)
    screen.workers = 2
    screen.start_btn.click()
    assert screen.busy and not win.running and not win.nav.isEnabled()
    win.navigate("simulate")
    assert win.current == "autotune"                          # locked while running
    assert wait(app, lambda: not screen.busy, 180)
    assert win.nav.isEnabled() and screen.result is not None and screen.pages.currentIndex() == 2
    screen.apply_btn.click()
    app.processEvents()
    assert win.current == "controller" and win.running        # back where we came from, resumed
    assert win.push_force.value() == pytest.approx(screen.result.recommended_push)
    win.pause()


def test_autotune_cancelled_by_back_and_background(win, app, monkeypatch):
    from pendulum_sim.mobile import autotune_screen
    from pendulum_sim.simulation import tuning

    slow = tuning.TuningSettings(horizon=4.0, samples=40, refine_rounds=3, refine_samples=8)
    monkeypatch.setattr(autotune_screen.TuningSettings, "preset",
                        staticmethod(lambda name: tuning.TuningSettings(**slow.__dict__)))
    win.load(scenario("double_cart_lqr"))
    win.navigate("autotune")
    screen = win.autotune
    screen.workers = 2
    screen.start()
    wait(app, lambda: screen.last is not None and screen.last.candidates_done >= 1, 120)
    assert win.go_back()                                      # back = cancel while running
    assert wait(app, lambda: not screen.busy, 30)
    assert screen.pages.currentIndex() == 2
    screen.rerun_btn.click()
    screen.start()
    wait(app, lambda: screen.last is not None, 60)
    win.on_application_state(Qt.ApplicationState.ApplicationSuspended)
    assert wait(app, lambda: not screen.busy, 30)
    win.on_application_state(Qt.ApplicationState.ApplicationActive)
    screen.discard_btn.click()
    app.processEvents()
    assert win.current != "autotune"


# ------------------------------------------------------------ touch & layout
@pytest.mark.parametrize("size", [(360, 760), (760, 360)])
def test_phone_layout_fits_and_targets_are_large(win, app, size):
    win.telemetry.set_selection(["angle", "angular_velocity", "control"])
    for _ in range(2):          # the first resize may be limited by the previous layout's minimum
        win.resize(*size)
        app.processEvents()
    too_big = {k: (p.minimumSizeHint().width(), p.minimumSizeHint().height()) for k, p in win.pages.items()
               if p.minimumSizeHint().width() > size[0] or p.minimumSizeHint().height() > size[1] - 60}
    # Fonts of the Windows offscreen platform are ~1.5× wider than Android's Roboto (Linux and
    # macOS are close to it), so the fit checks run there only; touch sizes are checked everywhere.
    check_fit = sys.platform != "win32"
    assert not check_fit or (win.width(), win.height()) == size, (win.minimumSizeHint(), too_big,
                                                 {k: widest(win.pages[k], size[0] - 40) for k in too_big},
                                                 win.telemetry.minimumSizeHint(), win.view.minimumSizeHint())
    for key in ("simulate", "graphs", "controller", "system", "settings", "autotune"):
        win.navigate(key)
        app.processEvents()
        page = win.pages[key]
        small = [(b.text() or b.toolTip(), b.height()) for b in page.findChildren(QAbstractButton)
                 if b.isVisibleTo(win) and b.height() < 40]
        assert not small, (key, small[:5])
        for area in page.findChildren(QScrollArea) + ([page] if isinstance(page, QScrollArea) else []):
            inner = area.widget()
            if check_fit and inner is not None and area.isVisibleTo(win):   # nothing clipped at the right
                need = inner.layout().minimumSize().width() if inner.layout() else 0
                assert need <= area.viewport().width() + 1, (key, need, area.viewport().width(),
                                                             widest(inner, area.viewport().width() - 80))
    win.navigate("simulate")
    win.telemetry.set_selection(["angle", "control"])
    win.resize(1280, 800)


def test_all_icons_render(app):
    for name in icons.names():
        img = icons.pixmap(name, 24, "#FFFFFF").toImage()
        assert any(img.pixelColor(x, y).alpha() > 0 for x in range(img.width()) for y in range(img.height())), name
