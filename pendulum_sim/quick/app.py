"""Entry point of the Qt Quick spike (Phase 1 of the Qt Quick mobile interface).

One *Simulate* screen in the "Instrument" light theme: the triple pendulum on a cart
with real physics, a two-trace graph, four read-outs, Play/Pause – and a built-in
benchmark that compares the rendering variants (see ``backend/bench.py``).

    python -m pendulum_sim.quick                       # desktop preview
    python -m pendulum_sim.quick --benchmark           # run all variants, print the report, exit
    python -m pendulum_sim.quick --smoke-test 2        # CI: run briefly and exit
    python -m pendulum_sim.quick --screenshot out.png  # CI: save the window after 2 s

On Android it is started by ``android/main_quick.py`` (``scripts/build_android.py --ui quick``).
If the QML scene cannot be loaded, the reason is drawn on screen with plain QtGui, so a
packaging problem is visible on the device without ``adb``.
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
import traceback
from pathlib import Path

IS_ANDROID = hasattr(sys, "getandroidapilevel") or "ANDROID_ARGUMENT" in os.environ
HERE = Path(__file__).resolve().parent
SCENARIO = "triple_cart_lqr"
FONTS = ("SpaceGrotesk-Regular.ttf", "SpaceGrotesk-Medium.ttf", "SpaceGrotesk-Bold.ttf",
         "JetBrainsMono-Regular.ttf", "JetBrainsMono-Medium.ttf")
GUI_SWITCH_INTERVAL_S = 0.001      # the physics thread yields the interpreter within 1 ms


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pendulum-sim-quick",
                                     description="Inverted Pendulum Simulator – Qt Quick spike")
    parser.add_argument("--physics", choices=["thread", "process"], default="thread")
    parser.add_argument("--view", choices=["items", "node"], default="items", help="pendulum variant")
    parser.add_argument("--graph", choices=["off", "shape", "shapegeo", "qtgraphs", "node", "painted"],
                        default="shape", help="graph variant")
    parser.add_argument("--dark", action="store_true", help="dark theme (preview; the spike targets light)")
    parser.add_argument("--smoke-test", type=float, metavar="SECONDS", default=None,
                        help="run the simulation for SECONDS, then exit (CI)")
    parser.add_argument("--screenshot", metavar="PNG", default=None, help="save the window after 2 s and exit")
    parser.add_argument("--benchmark", action="store_true", help="run the benchmark, print the report, exit")
    parser.add_argument("--strict", action="store_true",
                        help="exit with an error on QML warnings or failed variants (CI)")
    args, _unknown = parser.parse_known_args(argv)  # Android may pass launcher arguments
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    if IS_ANDROID:
        os.environ.setdefault("QT_ANDROID_DISABLE_ACCESSIBILITY", "1")   # as in the Widgets app
    sys.setswitchinterval(GUI_SWITCH_INTERVAL_S)

    from ..mobile.startup import TIMER

    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    app.setApplicationName("Pendulum Quick")
    app.setOrganizationName("pendulum-sim")
    TIMER.mark("Qt ready")
    try:
        spike = Spike(app, args)
    except Exception:  # noqa: BLE001 - show any start-up failure on screen
        text = traceback.format_exc()
        print(text, file=sys.stderr, flush=True)
        if args.smoke_test is not None or args.benchmark or args.screenshot:
            return 1
        window = error_window("The Qt Quick spike could not start.\n\n" + text)
        window.showMaximized() if IS_ANDROID else window.show()
        return app.exec()
    code = spike.run()
    spike.shutdown()
    return code


def error_window(text: str):
    """A window that draws ``text`` with QPainter (works without any QML module)."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFontDatabase, QPainter, QRasterWindow

    class ErrorWindow(QRasterWindow):
        def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
            painter = QPainter(self)
            painter.fillRect(0, 0, self.width(), self.height(), QColor("#F4F5F7"))
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            font.setPixelSize(12)
            painter.setFont(font)
            painter.setPen(QColor("#101828"))
            painter.drawText(QRectF(16, 16, self.width() - 32, self.height() - 32),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                                 | Qt.TextFlag.TextWordWrap), text)
            painter.end()

    window = ErrorWindow()
    window.resize(1138, 637)
    window.setTitle("Pendulum Quick – error")
    return window


class Spike:
    """The loaded application: bridges, QML engine, root window."""

    def __init__(self, app, args: argparse.Namespace) -> None:
        from ..mobile.startup import TIMER

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonType, qmlRegisterType

        self.app, self.args = app, args
        self.warnings: list[str] = []
        missing = [name for name in FONTS
                   if QFontDatabase.addApplicationFont(str(HERE / "fonts" / name)) < 0]
        app.setFont(QFont("Space Grotesk"))
        TIMER.mark("fonts loaded")

        import numpy  # noqa: F401

        from ..simulation import scenario

        TIMER.mark("NumPy, physics, control and simulation loaded")
        from .backend import scene_items
        from .backend.bench import Benchmark
        from .backend.bridge import GraphBridge, SimBridge

        self.sim = SimBridge(scenario(SCENARIO), args.physics)
        self.sim.pendulumMode = args.view
        self.graph = GraphBridge(self.sim)
        self.graph.mode = args.graph
        self.bench = Benchmark(self.sim, self.graph)
        self.bench.warnings = self.warnings
        if missing:
            scene_items.NOTES["fonts"] = "could not load " + ", ".join(missing)
        scene_items.BRIDGES.update(sim=self.sim, graph=self.graph)
        qmlRegisterType(scene_items.PendulumNode, "PendulumSpike", 1, 0, "PendulumNode")
        qmlRegisterType(scene_items.LineNode, "PendulumSpike", 1, 0, "LineNode")
        qmlRegisterType(scene_items.PaintedGraph, "PendulumSpike", 1, 0, "PaintedGraph")
        qmlRegisterSingletonType(QUrl.fromLocalFile(str(HERE / "qml" / "Theme.qml")), "Instrument", 1, 0, "Theme")
        try:  # registers the series types, so graph.setSeries() receives real LineSeries objects
            from PySide6 import QtGraphs  # noqa: F401
        except ImportError as exc:
            scene_items.NOTES["Qt Graphs"] = f"Python module not available ({exc})"
        TIMER.mark("bridges ready (physics running off the GUI thread)")

        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(self._on_warnings)
        context = self.engine.rootContext()
        context.setContextProperty("sim", self.sim)
        context.setContextProperty("graph", self.graph)
        context.setContextProperty("bench", self.bench)
        self.engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
        roots = self.engine.rootObjects()
        if not roots:
            raise RuntimeError("Main.qml could not be loaded:\n" + "\n".join(self.warnings)
                               + "\n\nQML import paths:\n" + "\n".join(self.engine.importPathList()))
        self.window = roots[0]
        self.bench.window = self.window
        if args.dark:
            try:
                self.engine.singletonInstance("Instrument", "Theme").setProperty("dark", True)
            except Exception as exc:  # noqa: BLE001 - preview option only
                print(f"dark theme not applied: {exc}", file=sys.stderr)
        if IS_ANDROID:
            self.window.showMaximized()
        TIMER.mark("QML scene loaded")

        self._paused_in_background = False
        app.applicationStateChanged.connect(self._on_application_state)
        self.sim.running = True
        gc.collect()
        gc.freeze()            # start-up objects out of the collector's view: short collections later

    # ------------------------------------------------------------- callbacks
    def _on_warnings(self, warnings) -> None:
        for warning in warnings:
            text = warning.toString()
            self.warnings.append(text)
            print("QML:", text, file=sys.stderr, flush=True)

    def _on_application_state(self, state) -> None:
        """Background: stop the simulation (and a running benchmark); foreground: resume."""
        from PySide6.QtCore import Qt

        if state == Qt.ApplicationState.ApplicationActive:
            if self._paused_in_background:
                self._paused_in_background = False
                self.sim.running = True
        elif state in (Qt.ApplicationState.ApplicationSuspended, Qt.ApplicationState.ApplicationHidden):
            self.bench.cancel()
            if self.sim.running:
                self._paused_in_background = True
                self.sim.running = False

    # ------------------------------------------------------------------ modes
    def run(self) -> int:
        from PySide6.QtCore import QTimer

        args, app = self.args, self.app
        result = {"code": 0}
        if args.benchmark:
            def done() -> None:
                failed = "no frames recorded" in self.bench.report or "note:" in self.bench.report
                if args.strict and (failed or self.warnings):
                    result["code"] = 1
                app.quit()

            self.bench.finished.connect(done)
            QTimer.singleShot(1000, self.bench.start)
        elif args.screenshot:
            def shoot() -> None:
                image = self.window.grabWindow()
                ok = image.save(args.screenshot)
                print(f"screenshot {args.screenshot}: {image.width()}×{image.height()} "
                      f"{'saved' if ok else 'NOT saved'}", flush=True)
                result["code"] = 0 if ok and not (args.strict and self.warnings) else 1
                app.quit()

            QTimer.singleShot(2000, shoot)
        elif args.smoke_test is not None:
            started = time.perf_counter()

            def check() -> None:
                from .backend import threads

                stats = self.sim.source.stats()
                sample = self.sim.source.sample()
                t = sample.t if sample is not None else 0.0
                print(f"quick smoke test: simulation t={t:.2f} s after {time.perf_counter() - started:.1f} s wall, "
                      f"{stats['steps']:.0f} steps, {self.sim.recorder.fps:.0f} fps, physics '{self.sim.physics}', "
                      f"render loop {threads.render_loop()}, {len(self.warnings)} QML warnings", flush=True)
                ok = t > 0.5 * args.smoke_test and self.bench.first_frame
                result["code"] = 0 if ok and not (args.strict and self.warnings) else 1
                app.quit()

            QTimer.singleShot(int(args.smoke_test * 1000), check)
        app.exec()
        return result["code"]

    def shutdown(self) -> None:
        """Stop the physics source and delete the scene while the application still exists."""
        from PySide6.QtCore import QCoreApplication, QEvent

        self.graph.timer.stop()
        self.sim.shutdown()
        self.window.close()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()


if __name__ == "__main__":
    sys.exit(main())
