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

    # ------------------------------------------------------------ benchmark
    win.tabs.setCurrentIndex(0)
    win.resize(*SIZES["tablet_landscape"])
    win.set_panel_visible(True)
    win.start()
    settle(win, 1.0)
    view_px = win.view.width(), win.view.height()
    report.append(f"Tablet landscape 1440×900 logical, view {view_px[0]}×{view_px[1]} logical, "
                  f"graphs {win.telemetry.width()}×{win.telemetry.height()} logical")
    report.append(f"{'setting':34s} {'view ms':>8s}")
    for render in ("native", "high", "balanced", "fast"):
        for aa in (True, False):
            win.apply_performance(PerformanceSettings(render=render, antialias=aa))
            ms = timed(win.view.repaint, 40)
            report.append(f"view {render:9s} antialias={str(aa):5s}        {ms:8.2f}")
    win.apply_performance(PerformanceSettings())
    graphs = timed(lambda: (win.telemetry.refresh(win.sim.telemetry, force=True), win.telemetry.repaint()), 20)
    report.append(f"{'graphs (update + paint)':34s} {graphs:8.2f}")
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
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "build/mobile-preview"))
