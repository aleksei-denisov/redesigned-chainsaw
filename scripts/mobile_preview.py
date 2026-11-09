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

ROOT = Path(__file__).resolve().parents[1]

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

    from pendulum_sim.mobile import theme
    from pendulum_sim.mobile.performance import PerformanceSettings
    from pendulum_sim.mobile.window import MobileWindow
    from pendulum_sim.simulation import scenario

    app.setStyle("Fusion")
    theme.apply(app)
    pal = app.palette()
    pg.setConfigOptions(antialias=True, background=pal.base().color(), foreground=pal.text().color())

    def settle(win, seconds: float = 1.5) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.005)

    def shot(name: str) -> None:
        settle(win, 0.3)
        win.grab().save(str(out / f"{name}.png"))

    report = [f"device pixel ratio {app.devicePixelRatio():g}", ""]

    # Loading screen (what follows the native splash on Android)
    from pendulum_sim.mobile.loading import LoadingScreen

    for size in ("tablet_landscape", "phone_portrait"):
        ls = LoadingScreen(steps=5)
        ls.resize(*SIZES[size])
        ls.show()
        ls.step("Loading NumPy…")
        ls.step("Loading physics engine…")
        ls.grab().save(str(out / f"{size}_loading.png"))
        ls.close()

    # Start-up: the app as launched, in a fresh process (median of 3)
    import subprocess
    import statistics

    runs = []
    for _ in range(3):
        res = subprocess.run([sys.executable, "-m", "pendulum_sim.mobile", "--startup-test"],
                             capture_output=True, text=True, timeout=180,
                             env=dict(os.environ, QT_QPA_PLATFORM="offscreen"), cwd=str(ROOT))
        runs.append(res.stdout)
    def total(text: str, step: str) -> float:
        lines = [ln for ln in text.splitlines() if ln.rstrip().endswith(step)]
        return float(lines[-1].split()[0]) if lines else float("nan")
    report.append("Start-up in a fresh process (s since process start, median of 3)")
    for step in ("Qt ready", "loading screen shown", "NumPy loaded", "physics, control and simulation loaded",
                 "interface modules loaded", "screens built", "main window shown"):
        report.append(f"  {step:42s} {statistics.median(total(r, step) for r in runs):6.2f}")
    t0 = time.perf_counter()
    probe = MobileWindow(scenario("single_cart_full"))
    t1 = time.perf_counter()
    probe.experiments
    t2 = time.perf_counter()
    probe.autotune
    t3 = time.perf_counter()
    report.append(f"  built on first use: Experiments {t2 - t1:.2f} s (pyqtgraph already imported here), "
                  f"Auto-tune {t3 - t2:.2f} s; main window alone {t1 - t0:.2f} s")
    report.append("")
    probe.close()
    probe.deleteLater()

    win = MobileWindow(scenario("triple_cart_lqr"))
    win.show()
    win.apply_performance(PerformanceSettings(render="native"))

    # Simulate screen in every orientation
    for name, (w, h) in SIZES.items():
        win.resize(w, h)
        win.navigate("simulate", remember=False)
        win.reset()
        win.start()
        settle(win)
        win.push(+1)
        settle(win, 0.6)
        win.grab().save(str(out / f"{name}.png"))

    # The other screens on the tablet (landscape) and on a phone (portrait)
    win.telemetry.set_selection(["angle", "control", "cart_position"])
    for size in ("tablet_landscape", "tablet_portrait", "phone_portrait"):
        win.resize(*SIZES[size])
        for key in ("graphs", "controller", "system", "settings"):
            win.navigate(key, remember=False)
            shot(f"{size}_{key}")
    win.resize(*SIZES["phone_portrait"])
    win.navigate("system", remember=False)
    page = win.system_page
    page.verticalScrollBar().setValue(page.verticalScrollBar().maximum() // 2)
    shot("phone_portrait_system_scrolled")
    page.verticalScrollBar().setValue(0)
    # Settings → Performance, with GPU (default) and with Software drawing (resolution row)
    from pendulum_sim.mobile.performance import PerformanceSettings

    st = win.settings_page
    for size in ("tablet_landscape", "phone_portrait"):
        win.resize(*SIZES[size])
        win.navigate("settings", remember=False)
        for drawing in ("gpu", "software"):
            win.apply_performance(PerformanceSettings(drawing=drawing))
            app.processEvents()
            st.ensureWidgetVisible(st.perf_preset, 0, 0)
            bar = st.verticalScrollBar()
            bar.setValue(st.perf_preset.mapTo(st.widget(), st.perf_preset.rect().topLeft()).y() - 60)
            shot(f"{size}_settings_performance_{drawing}")
        st.verticalScrollBar().setValue(0)
    win.apply_performance(PerformanceSettings())
    win.navigate("simulate", remember=False)
    win.telemetry.set_selection(["angle", "control"])

    # Experiments screen with two runs
    win.pause()
    win.resize(*SIZES["tablet_landscape"])
    win.navigate("experiments", remember=False)
    for _ in range(2):
        win.experiments.duration.setValue(3.0)
        win.experiments.run()
        deadline = time.perf_counter() + 60
        n = len(win.experiments.results)
        while len(win.experiments.results) == n and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.01)
    shot("tablet_landscape_experiments")
    win.resize(*SIZES["phone_portrait"])
    shot("phone_portrait_experiments")

    # Auto-tune screen: set-up, running and result pages (small search so CI stays quick)
    from pendulum_sim.mobile import autotune_screen
    from pendulum_sim.simulation.tuning import TuningSettings

    small = TuningSettings(horizon=2.5, samples=6, refine_rounds=1, refine_samples=4, resolution=0.08)
    autotune_screen.TuningSettings.preset = staticmethod(lambda name: TuningSettings(**small.__dict__))
    win.navigate("simulate", remember=False)
    win.load(scenario("single_cart_full"))
    win.resize(*SIZES["tablet_landscape"])
    win.navigate("controller")
    win.controller_page.autotune_btn.click()
    screen = win.autotune
    deadline = time.perf_counter() + 60
    while screen.push_test_s is None and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)
    shot("tablet_landscape_autotune_setup")
    screen.start()
    deadline = time.perf_counter() + 120
    while (screen.last is None or screen.last.candidates_done < 3) and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)
    shot("tablet_landscape_autotune_running")
    deadline = time.perf_counter() + 300
    while screen.busy and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)
    shot("tablet_landscape_autotune_result")
    for size in ("tablet_portrait", "phone_portrait", "phone_landscape"):
        win.resize(*SIZES[size])
        shot(f"{size}_autotune_result")
    screen.discard_btn.click()
    win.navigate("simulate", remember=False)
    win._history.clear()
    win.load(scenario("triple_cart_lqr"))

    # ------------------------------------------------------------ benchmark
    win.resize(*SIZES["tablet_landscape"])
    win.navigate("simulate", remember=False)
    win.start()
    settle(win, 1.0)
    view_px = win.view.width(), win.view.height()
    report.append(f"Tablet landscape 1440×900 logical, view {view_px[0]}×{view_px[1]} logical (Simulate)")
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

    win.navigate("graphs", remember=False)
    settle(win, 0.5)
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
    report.append(f"Graphs in {gw}×{gh} logical (ms). pyqtgraph: one refresh; strip charts: full redraw "
                  "(only on range/size changes) and the per-frame scroll step (new samples + plot-area paint)")
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
            line += (f"   full redraw {mode}: {ms:6.2f} (data {sum(c.update_ms for c in charts):.2f} · "
                     f"paint {sum(c.paint_ms for c in charts):.2f} · static rebuilds {builds}/31)")
        graphs.pen_mode = "fast"
        # per-frame scroll step: 1/60 s of simulation, then advance + repaint of the plot areas
        win.timer.stop()
        win.start()
        steps = []
        for _ in range(90):
            win.stepper.tick(win.sim, 1 / 60, 1.0)
            t0 = time.perf_counter()
            graphs.advance(win.sim.telemetry)
            for c in graphs.visible_charts():
                c.repaint(c._trace_rect.toAlignedRect())
            steps.append((time.perf_counter() - t0) * 1000.0)
        win.timer.start()
        steps.sort()
        line += f"   scroll step: mean {sum(steps) / len(steps):5.2f}, p95 {steps[int(0.95 * len(steps))]:5.2f}"
        report.append(line)
        if n == 4:
            win.grab().save(str(out / "tablet_landscape_four_graphs.png"))
    old.close()
    old.deleteLater()
    graphs.set_selection(["angle", "control"])
    settle(win, 0.3)
    # End to end: one 60 fps frame (physics + view + graphs at their refresh rate + flush)
    report.append("Frames at 60 fps for 3 s (busy time per frame: physics + drawing + flush, ms)")
    for key, sel in (("simulate", None), ("graphs", ["angle", "control"]),
                     ("graphs", ["angle", "control", "cart_position"])):
        win.navigate(key, remember=False)
        if sel:
            graphs.set_selection(sel)
        win.reset()
        win.start()
        settle(win, 0.5)
        win.timer.stop()
        busy = []
        win._last_frame = time.perf_counter()
        for _ in range(180):
            t0 = time.perf_counter()
            win._on_frame()
            app.processEvents()
            dt = time.perf_counter() - t0
            busy.append(dt * 1000.0)
            time.sleep(max(0.0, 1 / 60 - dt))
        win.timer.start()
        busy.sort()
        label = key + (f" ({len(sel)} graphs)" if sel else "")
        report.append(f"  {label:24s} mean {sum(busy) / len(busy):6.2f}   p95 {busy[int(0.95 * len(busy))]:6.2f}"
                      f"   max {busy[-1]:6.2f}")
    # the on-device performance test, shortened (same report as Settings → Debug)
    from pendulum_sim.mobile import perftest

    # software phases only: the offscreen platform has no OpenGL (GPU drawing is tested under Xvfb)
    perftest.PHASES = [ph[:3] + (2.0,) + ph[4:5] + ("software",) for ph in perftest.PHASES
                       if len(ph) < 6 or ph[5] != "gpu" or ph[4]]
    test = perftest.PerformanceTest(win)
    result = {}
    test.finished.connect(lambda text: result.setdefault("text", text))
    test.start()
    deadline = time.perf_counter() + 120
    while "text" not in result and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.002)
    report.append("")
    report.append(result.get("text", "performance test did not finish"))
    report.append("")
    report.append(f"{'whole window repaint, Graphs screen':34s} {timed(win.repaint, 10):8.2f}")
    win.navigate("simulate", remember=False)
    settle(win, 0.3)
    report.append(f"{'whole window repaint, Simulate':34s} {timed(win.repaint, 10):8.2f}")
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
