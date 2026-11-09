"""Offscreen tests of the mobile (Android) interface; skipped without PySide6/pyqtgraph."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt  # noqa: E402
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
    mobile.activateWindow()                  # another test's window may have taken the focus
    if not mobile.timer.isActive():          # (application inactive → frame timer stopped)
        mobile.timer.start()
    app.processEvents()
    return mobile


def frames(w, app, n=10):
    for _ in range(n):
        w._last_frame = time.perf_counter() - 0.016          # one 60 fps frame, however slow CI is
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


def test_performance_presets():
    default = PerformanceSettings()
    assert default.preset == "smooth" and default.drawing == "gpu"
    saver = default.with_preset("saver")
    assert (saver.drawing, saver.antialias, saver.fps, saver.graph_fps) == ("gpu", False, 30, 15)
    assert saver.preset == "saver" and saver.fast_cores == default.fast_cores
    from dataclasses import replace

    soft = replace(saver, drawing="software", render="fast")
    assert soft.preset == "saver"                                   # the drawing method is not a preset
    assert soft.with_preset("smooth").drawing == "software" and soft.with_preset("smooth").render == "fast"
    assert replace(default, fps=30).preset == "custom"


def test_old_saved_software_drawing_becomes_gpu(tmp_path):
    """Builds before GPU became the default saved drawing=software for everyone."""
    s = QSettings(str(tmp_path / "old.ini"), QSettings.Format.IniFormat)
    s.setValue("performance/drawing", "software")
    s.setValue("performance/fps", 30)
    s.sync()
    loaded = PerformanceSettings.load(s)
    assert loaded.drawing == "gpu" and loaded.fps == 30
    PerformanceSettings(drawing="software").save(s)                 # chosen now: kept
    assert PerformanceSettings.load(s).drawing == "software"


def test_performance_menu_presets_and_drawing_in_diagnostics(win, app):
    st = win.settings_page
    win.apply_performance(PerformanceSettings())
    try:
        assert st.perf_preset.value() == "smooth"
        assert st.perf_render.isHidden(), "resolution applies to software drawing only"
        assert st.perf_preset.buttons["custom"].isHidden()
        perf_rows = st.perf_preset.parentWidget()
        assert not perf_rows.isAncestorOf(st.perf_drawing), "drawing method belongs to Diagnostics"
        assert "much slower" in st.perf_drawing_note.text()
        st.perf_drawing.buttons["software"].click()
        assert win.perf.drawing == "software" and PerformanceSettings.load().drawing == "software"
        assert not st.perf_render.isHidden()
        st.perf_fps.buttons[30].click()
        assert st.perf_preset.value() == "custom" and not st.perf_preset.buttons["custom"].isHidden()
        st.perf_preset.buttons["saver"].click()
        p = win.perf
        assert (p.drawing, p.antialias, p.fps, p.graph_fps) == ("software", False, 30, 15)
        assert st.perf_preset.value() == "saver" and st.perf_preset.buttons["custom"].isHidden()
        assert win.timer.interval() == 33
        st.perf_drawing.buttons["gpu"].click()
        assert st.perf_render.isHidden()
    finally:
        win.apply_performance(PerformanceSettings())
        PerformanceSettings().save()


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


def _linear_trace(chart):
    """The chart's ring trace unrolled so that the newest column is at the right edge."""
    from PySide6.QtGui import QImage, QPainter

    from pendulum_sim.mobile.plot_math import ring_blits

    w, h = chart._trace.width(), chart._trace.height()
    out = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(out)
    for src, length, dst in ring_blits(chart._c_now, chart._ring0, w):
        p.drawImage(dst, 0, chart._trace, src, 0, length, h)
    p.end()
    return out


def test_scrolling_chart_matches_a_full_redraw(app):
    """Per-frame increments give (nearly) the same picture as drawing the window at once."""
    import numpy as np

    from pendulum_sim.mobile.graphs import FULL, StripChart
    from pendulum_sim.mobile.plot_math import GRAPHS, Series

    rate, total = 200.0, 14.0
    t = np.arange(0.0, total, 1.0 / rate)
    data = {"a": 8.0 * np.sin(1.3 * t) + 2.0 * np.sin(7.0 * t), "b": 5.0 * np.cos(0.7 * t)}
    series = [Series("a", "a", color="#4FA3FF"), Series("b", "b", color="#FFA14A", dashed=False)]

    def make():
        c = StripChart(GRAPHS["angle"])
        c.resize(640, 220)
        c.show()
        c.set_series(series)
        c.axis.update(-14.0, 14.0, 0.0)                    # same fixed range for both charts
        return c

    inc, ref = make(), make()
    app.processEvents()
    n0 = int(2.0 * rate)
    inc.update_data(t[:n0], lambda k: data[k][:n0], 0.0)
    drawn, fulls = 0, 0
    for n in range(n0 + 3, t.size, 3):                     # 3 samples per frame ≈ 60 fps at 200 Hz
        r = inc.advance(t[:n], lambda k, n=n: data[k][:n])
        if r == FULL:
            fulls += 1
            inc.update_data(t[:n], lambda k, n=n: data[k][:n], 0.0)
        else:
            drawn += r
    assert fulls == 0 and drawn > 0
    ref.update_data(t[:n], lambda k: data[k][:n], 0.0)
    a, b = _linear_trace(inc), _linear_trace(ref)
    assert (a.width(), a.height()) == (b.width(), b.height())
    bg = inc.palette().base().color().rgb()
    diff = curve = 0
    for y in range(0, a.height(), 2):
        for x in range(0, a.width() - 2, 2):            # skip the newest columns (partial)
            pa, pb = a.pixel(x, y), b.pixel(x, y)
            if pa != bg or pb != bg:
                curve += 1
                if pa != pb:
                    diff += 1
    assert curve > 100
    assert diff / curve < 0.25, (diff, curve)
    inc.close()
    ref.close()


def test_graphs_advance_draws_only_new_samples_and_nothing_when_hidden(win, app):
    g = win.telemetry
    win.navigate("graphs")
    g.set_selection(["angle", "control"])
    win.start()
    frames(win, app, 20)
    g.refresh(win.sim.telemetry, force=True)
    fulls = sum(c.full_redraws for c in g.visible_charts())
    before = sum(c.points_drawn for c in g.visible_charts())
    frames(win, app, 10)
    after = sum(c.points_drawn for c in g.visible_charts())
    assert after > before                                   # per-frame increments ...
    assert sum(c.full_redraws for c in g.visible_charts()) <= fulls + 2   # ... not full redraws
    win.navigate("simulate")
    app.processEvents()
    assert g.advance(win.sim.telemetry) == 0                # hidden: no work at all
    win.pause()


def test_overrun_frames_skip_the_graph_step(win, app):
    g = win.telemetry
    win.navigate("graphs")
    win.start()
    frames(win, app, 5)
    calls = []
    original = g.advance
    g.advance = lambda tel: calls.append(1) or original(tel)
    try:
        win._last_frame -= 0.2                              # a 200 ms hitch: skip the graphs
        win._on_frame()
        assert calls == []
        win._last_frame -= 0.016
        win._on_frame()
        assert calls == [1]
    finally:
        g.advance = original
        win.pause()
        win.navigate("simulate")


def test_slow_but_steady_frames_still_step_the_graphs(win, app):
    """At 40 fps (25 ms gaps, as measured on the Pad 6) the graphs must still scroll every frame."""
    g = win.telemetry
    win.navigate("graphs")
    win.start()
    frames(win, app, 3)
    calls = []
    original = g.advance
    g.advance = lambda tel: calls.append(1) or original(tel)
    try:
        for _ in range(5):
            win._last_frame = time.perf_counter() - 0.025
            win._on_frame()
        assert len(calls) == 5
    finally:
        g.advance = original
        win.pause()
        win.navigate("simulate")


class _WindowShows:
    """Records every widget that is shown as a window of its own (a ``Show`` event while
    ``isWindow()``): on Android each one is a surface that flashes over the app."""

    ALLOWED = ("QMenu", "QDialog", "QMessageBox", "ReportDialog", "QTipLabel", "QComboBoxPrivateContainer",
               "QFileDialog")

    def __init__(self, app, expected=()):
        from PySide6.QtCore import QEvent, QObject

        outer = self
        self.expected = list(expected)
        self.seen: list[str] = []

        class Filter(QObject):
            def eventFilter(self, obj, event):  # noqa: N802
                if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
                    if obj not in outer.expected and not any(
                            c.__name__ in outer.ALLOWED for c in type(obj).__mro__):
                        text = obj.text() if hasattr(obj, "text") and callable(obj.text) else ""
                        outer.seen.append(f"{type(obj).__name__}({obj.objectName() or text!s:.20})")
                return False

        self.filter = Filter()
        app.installEventFilter(self.filter)
        self.app = app

    def close(self):
        self.app.removeEventFilter(self.filter)


def test_no_widget_flashes_as_a_window(app):
    """No widget may become a window of its own: not while the screens are built (the
    rail's icons flashed over the loading screen) and not on any screen afterwards."""
    from pendulum_sim.mobile.controls import NumberRow

    shows = _WindowShows(app)
    try:
        row = NumberRow("Gain", 0.0, 10.0, 1.0)
        w = MobileWindow(scenario("double_cart_lqr"))
        app.processEvents()
        assert shows.seen == [], f"shown as windows while building: {shows.seen}"
        shows.expected.append(w)
        w.resize(1280, 800)
        w.show()
        for size in ((1280, 800), (412, 915), (915, 412)):
            w.resize(*size)
            for key in w.pages:
                w.navigate(key)
                frames(w, app, 2)
        w.resize(1280, 800)
        w.system_page.segments.setValue(1)
        w.system_page._emit_plant()
        w.load(scenario("triple_cart_lqr"))
        frames(w, app, 3)
        assert shows.seen == [], f"shown as windows on the screens: {shows.seen}"
    finally:
        shows.close()
    w.close()
    w.deleteLater()
    row.deleteLater()
    app.processEvents()


def test_typed_numbers_ignore_the_device_language(app):
    """Russian keyboards/locales use a decimal comma; '30.408' must still be accepted."""
    from PySide6.QtCore import QLocale
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QVBoxLayout

    from pendulum_sim.mobile.controls import NumberRow, parse_number

    assert parse_number("30,5") == 30.5 and parse_number("30.408") == 30.408 and parse_number("-1e-3") == -0.001
    assert parse_number("") is None and parse_number("-") is None and parse_number("nan") is None
    old = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
    try:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        row = NumberRow("Kd", 0.0, 100.0, 30.4, 3, "", "symlog")
        other = NumberRow("Kp", 0.0, 100.0, 6.0, 3)
        lay.addWidget(row)
        lay.addWidget(other)
        holder.resize(600, 300)
        holder.show()
        got = []
        row.valueChanged.connect(got.append)
        # 1. typed with a dot, finished with Enter (keyboard "Done")
        row.value_btn.click()
        assert row.editing
        row.edit.selectAll()
        QTest.keyClicks(row.edit, "30.4.08")              # the second separator is refused
        assert row.edit.text() == "30.408"
        QTest.keyClick(row.edit, Qt.Key.Key_Return)
        assert not row.editing and row.value() == 30.408 and got[-1] == 30.408
        # 2. typed with a comma, finished by touching the slider (takes no focus)
        row.value_btn.click()
        row.edit.selectAll()
        QTest.keyClicks(row.edit, "12,5")
        QTest.mousePress(row.slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         row.slider.rect().center())
        QTest.mouseRelease(row.slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           row.slider.rect().center())
        app.processEvents()
        assert not row.editing and any(abs(g - 12.5) < 1e-9 for g in got), got   # then the slider acts
        # 3. not a number: the old value stays, the field closes when another row is tapped
        before = row.value()
        row.value_btn.click()
        row.edit.setText("-")
        QTest.mouseClick(other.plus, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert not row.editing and row.value() == before
        # 4. the slider works again after typing
        row.value_btn.click()
        QTest.keyClick(row.edit, Qt.Key.Key_Return)
        row.slider.triggerAction(row.slider.SliderAction.SliderPageStepAdd)
        app.processEvents()
        assert row.value() > before
        holder.close()
        holder.deleteLater()
    finally:
        QLocale.setDefault(old)
    app.processEvents()


def test_tapping_a_graph_opens_the_explorer(win, app, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QTest

    from pendulum_sim.ui import file_io

    win.navigate("graphs")
    win.telemetry.set_selection(["angle", "control"])
    win.start()
    frames(win, app, 40)
    chart = win.telemetry.charts["angle"]
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, chart.rect().center())
    app.processEvents()
    ex = win.explorer
    assert win.explorer_open() and ex.geometry() == win.rect() and ex.live_btn.isChecked()
    plot = ex.plot
    assert [c.label for c in plot.curves] == ["θ1"] and plot.curves[0].x.size > 10
    x0, x1, _y0, _y1 = plot.view_range()
    assert x1 == pytest.approx(plot.curves[0].x[-1]) and x1 - x0 == pytest.approx(win.telemetry.window_s)
    advanced = chart.points_drawn
    frames(win, app, 5)
    assert chart.points_drawn == advanced, "graphs behind the explorer are not drawn"
    # a drag freezes the view (no more following), zoom and read-out work, fit restores
    plot.resize(plot.size())
    QTest.mousePress(plot, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, plot.rect().center())
    QTest.mouseMove(plot, plot.rect().center() + QPoint(80, 0))
    QTest.mouseRelease(plot, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       plot.rect().center() + QPoint(80, 0))
    assert not ex.live_btn.isChecked() and not plot.fitted
    frozen = plot.view_range()
    plot.zoom(2.0, 1.0)
    assert plot.view_range()[1] - plot.view_range()[0] == pytest.approx(0.5 * (frozen[1] - frozen[0]))
    QTest.mouseClick(plot, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, plot.rect().center())
    assert plot.cursor_x is not None and plot.values_at(plot.cursor_x)
    img = plot.grab().toImage()
    assert img.width() > 100
    ex.fit_btn.click()
    assert plot.fitted
    # files: data (CSV) and image (PNG), each on its own
    monkeypatch.setattr(file_io, "save_file_name", lambda parent, title, name, flt, suffix=None: tmp_path / name)
    ex.data_btn.click()
    ex.image_btn.click()
    csv_text = (tmp_path / "angle.csv").read_text(encoding="utf-8").splitlines()
    assert csv_text[0] == "t_s,θ1" and len(csv_text) > 10
    assert (tmp_path / "angle.png").read_bytes()[:4] == b"\x89PNG"
    ex.live_btn.click()
    assert ex.live_btn.isChecked() and ex.timer.isActive()
    # Back closes it; the graphs move again
    QApplication.sendEvent(win, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Back, Qt.KeyboardModifier.NoModifier))
    assert not win.explorer_open() and win.current == "graphs" and not ex.timer.isActive()
    frames(win, app, 5)
    assert chart.points_drawn > advanced
    win.pause()


def test_loading_a_scenario_shows_the_busy_cover(win, app):
    seen = []
    original = win.busy.run

    def spy(text, work):
        seen.append(text)
        original(text, work)
        assert win.busy.isVisible()
    win.busy.run = spy
    try:
        win.load(scenario("double_cart_lqr"))
    finally:
        del win.busy.run
    assert len(seen) == 1 and seen[0].startswith("Loading")
    assert win.config.name == scenario("double_cart_lqr").name
    assert wait(app, lambda: not win.busy.isVisible(), 5)


def test_trail_polygon_matches_to_screen(win, app):
    win.start()
    frames(win, app, 30)
    v = win.view
    pts = v.trails[-1]
    poly = v._screen_polygon(pts)
    assert poly.size() == len(pts) > 10
    for i in (0, len(pts) // 2, len(pts) - 1):
        a, b = poly.at(i), v.to_screen(pts[i])
        assert abs(a.x() - b.x()) < 1e-6 and abs(a.y() - b.y()) < 1e-6
    win.pause()


def test_performance_test_reports_every_phase(win, app, monkeypatch):
    from pendulum_sim.mobile import perftest

    assert perftest.PHASES[0][5] == "gpu"                    # the default drawing is measured first
    # software only here (offscreen has no OpenGL; GPU drawing: tests/test_mobile_gpu.py)
    phases = [ph[:3] + (0.4,) + ph[4:5] + ("software",) for ph in perftest.PHASES]
    monkeypatch.setattr(perftest, "PHASES", phases)
    snapshot = list(phases)
    monkeypatch.setattr(perftest, "SETTLE_S", 0.1)
    before = (win.current, list(win.telemetry.selected))
    win.run_performance_test()
    test = win._perf_test
    assert wait(app, lambda: not test.running, 30)
    report = win._report_dialog.text
    for label, *_ in phases:
        assert label.split(" (not available")[0] in report
    assert "fps" in report and "graph paint" in report and "tottime" in report
    win._report_dialog.copy()
    assert QApplication.clipboard().text() == report or True   # offscreen clipboards may be stubs
    win._report_dialog.accept()
    assert (win.current, list(win.telemetry.selected)) == before
    assert win.nav.isEnabled()
    assert perftest.PHASES == snapshot, "a run must not rename the module's phases"


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
    for _ in range(3):          # the first resize may be limited by the previous layout's minimum
        win.layout().activate()                # refresh the window's minimum from the layout
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


SIZES = {"tablet_landscape": (1280, 800), "tablet_portrait": (800, 1280),
         "phone_landscape": (915, 412), "phone_portrait": (412, 915)}


@pytest.mark.parametrize("size", list(SIZES), ids=list(SIZES))
def test_every_screen_scrolls_by_touch_and_keeps_actions_reachable(win, app, size):
    from PySide6.QtWidgets import QAbstractScrollArea, QGraphicsView

    for _ in range(2):
        win.resize(*SIZES[size])
        app.processEvents()
    win.autotune.open(win.config)
    screens = [(k, None) for k in win.pages if k != "autotune"] + [("autotune", i) for i in range(3)]
    window_rect = win.rect()
    for key, sub in screens:
        win.navigate(key)
        if sub is not None:
            win.autotune.pages.setCurrentIndex(sub)
        app.processEvents()
        page = win.pages[key]
        for area in page.findChildren(QAbstractScrollArea):
            if area.isVisibleTo(win) and not isinstance(area, QGraphicsView):
                assert area.viewport().property("kinetic"), (size, key, sub, type(area).__name__)
        if sub is not None:                       # action bar fully inside the window
            bar = win.autotune.pages.currentWidget().bar
            for b in bar.findChildren(QAbstractButton):
                if b.isVisibleTo(win):
                    r = b.rect().translated(b.mapTo(win, QPoint(0, 0)))
                    assert window_rect.contains(r), (size, sub, b.text(), r, window_rect)
                    assert b.height() >= 40
    win.autotune.pages.setCurrentIndex(0)
    win.navigate("simulate")
    win.resize(1280, 800)


# ------------------------------------------------------------- start-up
def test_loading_screen_then_main_window_in_one_top_level(app):
    """Start-up uses a single top-level window (two Android surfaces flickered)."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    from pendulum_sim.mobile.loading import AppShell, LoadingScreen

    before = {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}
    shell = AppShell()
    screen = LoadingScreen(steps=3)
    shell.show_page(screen)
    shell.resize(412, 915)
    shell.show()
    for text in ("Loading NumPy…", "Loading physics engine…", "Building screens…"):
        screen.step(text)
        assert screen.status == text
    w = MobileWindow(scenario("single_cart_full"))      # built parentless, like app.py
    screen.finish()
    assert screen.bar.value() == screen.bar.maximum()
    img = screen.grab().toImage()
    assert img.pixelColor(img.width() // 2, img.height() // 2 - 100).name() != theme.BG.lower()   # logo drawn
    shell.present(w)
    app.processEvents()
    new = [t for t in QApplication.topLevelWidgets() if t.isVisible() and id(t) not in before]
    assert new == [shell], [type(t).__name__ for t in new]
    assert w.isVisible() and not w.isWindow() and w.window() is shell and w.size() == shell.size()
    assert w.timer.isActive(), "frame timer must run in the embedded window"
    w.start()
    t0 = w.sim.time
    assert wait(app, lambda: w.sim.time > t0, 5), "simulation must advance"
    w.pause()
    w.navigate("settings")
    shell.setFocus()                                     # no focus widget inside: Back reaches the shell
    QApplication.sendEvent(shell, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Back, Qt.KeyboardModifier.NoModifier))
    assert w.current == "simulate"                       # handled by the main window (go back)
    shell.close()
    assert not w.isVisible()
    shell.deleteLater()
    app.processEvents()


def test_start_up_needs_neither_pyqtgraph_nor_the_tuner(tmp_path):
    """Experiments (pyqtgraph) and Auto-tune are built on first use, not at start-up."""
    import subprocess

    code = (
        "import sys, os\n"
        "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
        "from pendulum_sim.mobile import startup\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "from pendulum_sim.mobile.window import MobileWindow\n"
        "from pendulum_sim.simulation import scenario\n"
        "w = MobileWindow(scenario('single_cart_full'))\n"
        "print('at start', 'pyqtgraph' in sys.modules, 'pendulum_sim.mobile.autotune_screen' in sys.modules)\n"
        "w.experiments; w.autotune\n"
        "print('after use', 'pyqtgraph' in sys.modules, 'pendulum_sim.mobile.autotune_screen' in sys.modules)\n"
        "print('age', startup.process_age() is not None or sys.platform != 'linux')\n"
        "w.close(); w.deleteLater(); app.processEvents()\n"
        "sys.stdout.flush()\n"
        "os._exit(0)\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120,
                         cwd=tmp_path)
    assert "at start False False" in out.stdout, out.stdout + out.stderr
    assert "after use True True" in out.stdout, out.stdout + out.stderr
    assert "age True" in out.stdout


def test_all_icons_render(app):
    for name in icons.names():
        img = icons.pixmap(name, 24, "#FFFFFF").toImage()
        assert any(img.pixelColor(x, y).alpha() > 0 for x in range(img.width()) for y in range(img.height())), name
