"""Screenshots and a drawing benchmark of the mobile interface (offscreen, no device needed).

    QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 python scripts/mobile_preview.py out/

``QT_SCALE_FACTOR=2`` reproduces a high-density tablet such as the Xiaomi Pad 6
(2880×1800 pixels = 1440×900 logical at a pixel ratio of 2). Writes PNG screenshots
of the main layouts and pages plus ``benchmark.txt`` with the time to paint the
pendulum view at each render resolution, the graphs and the whole window. The CI
runner is faster than a tablet; compare the settings with each other, not absolutely.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SIZES = {  # logical sizes
    "tablet_landscape": (1440, 900),
    "tablet_portrait": (900, 1440),
    "phone_portrait": (412, 915),
    "phone_landscape": (915, 412),
}


def timed(fn, repeat: int) -> float:
    fn()  # warm-up (caches, buffers)
    t = time.perf_counter()
    for _ in range(repeat):
        fn()
    return (time.perf_counter() - t) / repeat * 1000.0


def main(out_dir: str) -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    app.setOrganizationName("pendulum-sim-preview")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(out / "settings"))

    import pyqtgraph as pg

    from pendulum_sim.mobile.performance import PerformanceSettings
    from pendulum_sim.mobile.touch_style import apply_touch_style
    from pendulum_sim.mobile.window import MobileWindow
    from pendulum_sim.simulation import scenario

    apply_touch_style(app)
    pal = app.palette()
    pg.setConfigOptions(antialias=True, background=pal.base().color(), foreground=pal.text().color())

    def settle(win, seconds: float = 1.5) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.005)

    report = [f"device pixel ratio {app.devicePixelRatio():g}", ""]
    win = MobileWindow(scenario("triple_cart_lqr"))
    win.show()
    win.apply_performance(PerformanceSettings(render="native"))

    for name, (w, h) in SIZES.items():
        win.resize(w, h)
        win.set_panel_visible(name.startswith("tablet"))
        win.tabs.setCurrentIndex(0)
        win.reset()
        win.start()
        settle(win)
        win.push(+1)
        settle(win, 0.6)
        win.grab().save(str(out / f"{name}.png"))

    # Configuration pages on the tablet in landscape
    win.resize(*SIZES["tablet_landscape"])
    win.set_panel_visible(True)
    for i, page in enumerate(("pendulum", "pid", "simulation")):
        win.side.setCurrentIndex(i)
        settle(win, 0.3)
        win.grab().save(str(out / f"tablet_panel_{page}.png"))
    win.side.setCurrentIndex(0)

    # Experiments page with two runs
    win.pause()
    win.tabs.setCurrentIndex(1)
    for _ in range(2):
        win.experiments.duration.setValue(3.0)
        win.experiments.run()
        deadline = time.perf_counter() + 60
        n = len(win.experiments.results)
        while len(win.experiments.results) == n and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.01)
    settle(win, 0.3)
    win.grab().save(str(out / "tablet_experiments.png"))
    win.resize(*SIZES["phone_portrait"])
    win.set_panel_visible(False)
    settle(win, 0.3)
    win.grab().save(str(out / "phone_experiments.png"))

    # Auto-tune screen: running and result pages (small search so CI stays quick)
    from pendulum_sim.mobile import autotune_screen
    from pendulum_sim.simulation.tuning import TuningSettings

    small = TuningSettings(horizon=2.5, samples=6, refine_rounds=1, refine_samples=4, resolution=0.08)
    autotune_screen.TuningSettings.preset = staticmethod(lambda name: TuningSettings(**small.__dict__))
    win.tabs.setCurrentIndex(0)
    win.load(scenario("single_cart_full"))
    win.resize(*SIZES["tablet_landscape"])
    win.controller_panel.robustTuneRequested.emit()
    screen = win.autotune
    deadline = time.perf_counter() + 60
    while screen.push_test_s is None and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)
    settle(win, 0.2)
    win.grab().save(str(out / "tablet_autotune_setup.png"))
    screen.start()
    deadline = time.perf_counter() + 120
    while (screen.last is None or screen.last.candidates_done < 3) and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)
    settle(win, 0.3)
    win.grab().save(str(out / "tablet_autotune_running.png"))
    deadline = time.perf_counter() + 300
    while screen.busy and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)
    settle(win, 0.3)
    win.grab().save(str(out / "tablet_autotune_result.png"))
    win.resize(*SIZES["phone_portrait"])
    settle(win, 0.3)
    win.grab().save(str(out / "phone_autotune_result.png"))
    screen.close_screen()
    win.load(scenario("triple_cart_lqr"))

    # ------------------------------------------------------------ benchmark
    win.tabs.setCurrentIndex(0)
    win.resize(*SIZES["tablet_landscape"])
    win.set_panel_visible(True)
    win.start()
    settle(win, 1.0)
    view_px = win.view.width(), win.view.height()
    report.append(f"Tablet landscape 1440×900 logical, view {view_px[0]}×{view_px[1]} logical, "
                  f"graphs {win.telemetry.width()}×{win.telemetry.height()} logical")
    report.append("Pendulum view: paint time inside paintEvent (excludes Qt's window flush)")
    report.append(f"{'setting':44s} {'ms':>7s}")

    class Collect:
        def __init__(self):
            self.samples = []

        def add_view(self, ms):
            self.samples.append(ms)

    def view_paint_ms(repeat: int = 40) -> float:
        c = Collect()
        win.view.stats = c
        win.view.repaint()
        c.samples.clear()
        for _ in range(repeat):
            win.view.repaint()
        win.view.stats = win.stats
        return sum(c.samples) / len(c.samples)

    for cache in (False, True):
        win.view.cache_grid = cache
        for render in ("native", "high", "balanced", "fast"):
            for aa in (True, False):
                win.apply_performance(PerformanceSettings(render=render, antialias=aa))
                ms = view_paint_ms()
                report.append(f"{render:9s} antialias={str(aa):5s} grid cache={str(cache):5s}        {ms:7.2f}")

    # Breakdown of the scene at native resolution with antialiasing, grid cache on
    win.apply_performance(PerformanceSettings(render="native", antialias=True))
    win.view.cache_grid = True
    parts = {}
    originals = {}
    for name in ("_draw_grid", "_draw_base", "_draw_equilibrium", "_draw_trails", "_draw_links",
                 "_draw_force", "_draw_hud", "_draw_scale_bar", "_paint_overlay"):
        fn = getattr(win.view, name)
        originals[name] = fn

        def wrapped(*a, _fn=fn, _name=name):
            t = time.perf_counter()
            try:
                return _fn(*a)
            finally:
                parts[_name] = parts.get(_name, 0.0) + (time.perf_counter() - t) * 1000.0
        setattr(win.view, name, wrapped)
    n = 40
    for _ in range(n):
        win.view.repaint()
    for name in originals:
        delattr(win.view, name)
    report.append("breakdown (native, antialias, grid cache):  " +
                  "  ".join(f"{k[1:]} {v / n:.2f}" for k, v in parts.items()))
    report.append(f"{'repaint() incl. window flush, native+AA+cache':44s} {timed(win.view.repaint, 20):7.2f}")
    win.apply_performance(PerformanceSettings())
    # Graphs: new QPainter strip charts vs the desktop pyqtgraph panel, same area and selection
    from pendulum_sim.ui.telemetry_panel import TelemetryPanel

    graphs = win.telemetry
    gw, gh = graphs.width(), graphs.height()
    old = TelemetryPanel()
    old.resize(gw, gh)
    old.configure(win.config.plant.n_segments, win.config.plant.has_cart,
                  ["cart" if lp.signal == "cart" else f"θ{lp.signal[5:]}" for lp in win.config.controller.loops])
    old.show()
    selections = {
        2: (["angle", "control"], {"angles", "control"}),
        4: (["angle", "angular_velocity", "cart_position", "control"], {"angles", "velocities", "cart", "control"}),
    }
    report.append(f"Graphs in {gw}×{gh} logical: one refresh = update data + paint (ms)")
    for n, (new_keys, old_keys) in selections.items():
        graphs.set_selection(new_keys)
        for k, a in old.toggles.items():
            a.setChecked(k in old_keys)
        settle(win, 0.3)

        def new_refresh():
            graphs.refresh(win.sim.telemetry, force=True)
            graphs.repaint()

        def old_refresh():
            old.refresh(win.sim.telemetry, force=True)
            old.repaint()

        line = f"  {n} graphs: pyqtgraph (before) {timed(old_refresh, 15):7.2f}"
        for mode in ("smooth", "fast"):
            graphs.pen_mode = mode
            builds0 = sum(c.static_builds for c in graphs.visible_charts())
            ms = timed(new_refresh, 30)
            charts = graphs.visible_charts()
            builds = sum(c.static_builds for c in charts) - builds0
            line += (f"   strip charts {mode}: {ms:6.2f} (data {sum(c.update_ms for c in charts):.2f} · "
                     f"paint {sum(c.paint_ms for c in charts):.2f} · static rebuilds {builds}/31)")
        graphs.pen_mode = "fast"
        report.append(line)
        if n == 4:
            win.grab().save(str(out / "tablet_four_graphs.png"))
    old.close()
    old.deleteLater()
    graphs.set_selection(["angle", "control"])
    settle(win, 0.3)
    whole = timed(win.repaint, 10)
    report.append(f"{'whole window repaint':34s} {whole:8.2f}")
    sim = win.sim
    step = timed(sim.step, 2000)
    report.append(f"{'one physics step (triple pendulum)':34s} {step:8.3f}  "
                  f"(×{int(round(0.016 / sim.config.simulation.dt))} per 60 fps frame)")
    text = "\n".join(report)
    (out / "benchmark.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    win.close()
    win.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1] if len(sys.argv) > 1 else "build/mobile-preview")
    sys.stdout.flush()
    sys.stderr.flush()
    # Skip interpreter teardown: pyqtgraph objects outliving the QApplication can crash at
    # exit, which would fail CI after all results have been written.
    os._exit(code)
