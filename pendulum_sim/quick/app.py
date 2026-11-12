"""Entry point of the Qt Quick interface (``pendulum_sim/quick``).

Start-up order, built for a fast first frame:

1. ``QGuiApplication``, the bundled fonts, ``Settings`` (theme) and ``App``;
2. ``Main.qml`` – only the loading screen – is shown;
3. after its first frame: NumPy and the simulation core, the physics process and the
   backend objects, exposed to QML as context objects (``sim``, ``model``,
   ``controller``, ``graphs``, ``panel``, ``autotune``, ``scenarios``, ``frames``, ``bench``);
4. the app shell and the Simulate screen are built and fade in; other screens are
   built when first opened;
5. once the Simulate screen is on screen: the frame loop's helpers start (Android
   performance hints, render-thread measurement) and the garbage collector switches to
   idle-time collection.

    python -m pendulum_sim.quick                          # desktop preview
    python -m pendulum_sim.quick --benchmark              # performance test, report printed, exit
    python -m pendulum_sim.quick --smoke-test 3           # CI: visit every screen, run, exit
    python -m pendulum_sim.quick --screenshot out.png --screen graphs --theme dark

On Android it is started by ``android/main_quick.py`` (``scripts/build_android.py --ui quick``).
If the QML cannot be loaded, the reason is drawn on screen with plain QtGui, so a
packaging problem is visible on the device without ``adb``.
"""

from __future__ import annotations

import argparse
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
GUI_SWITCH_INTERVAL_S = 0.001      # with the physics thread fallback: the GUI waits ≤ 1 ms for the interpreter


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pendulum-sim-quick",
                                     description="Inverted Pendulum Simulator – Qt Quick interface")
    parser.add_argument("--physics", choices=["thread", "process"], default=None,
                        help="where the physics runs (default: the saved setting, normally a process)")
    parser.add_argument("--scenario", default=SCENARIO)
    parser.add_argument("--theme", choices=["system", "light", "dark"], default=None,
                        help="theme for this run (not saved)")
    parser.add_argument("--screen", choices=["simulate", "graphs", "settings"], default="simulate")
    parser.add_argument("--panel", choices=["edit", "value", "graph"], default=None,
                        help="screenshots: side panel in edit mode, or the editor of its first value/graph tile")
    parser.add_argument("--panel-stress", type=int, metavar="STEPS", default=None,
                        help="CI: edit the side panel STEPS times through the tile editor's buttons, then exit")
    parser.add_argument("--stress-gc", action="store_true", help="--panel-stress: collect garbage after each edit")
    parser.add_argument("--smoke-test", type=float, metavar="SECONDS", default=None,
                        help="visit every screen, run the simulation for SECONDS, then exit (CI)")
    parser.add_argument("--screenshot", metavar="PNG", default=None, help="save the window after 2.5 s and exit")
    parser.add_argument("--benchmark", action="store_true", help="run the performance test, print the report, exit")
    parser.add_argument("--strict", action="store_true", help="exit with an error on QML warnings (CI)")
    parser.add_argument("--safe-mode-preview", action="store_true",
                        help="CI: show the safe-mode screen after an unfinished start even in a test run")
    args, _unknown = parser.parse_known_args(argv)  # Android may pass launcher arguments
    return args


def main(argv: list[str] | None = None, journal=None) -> int:
    """``journal``: the start-up journal if the caller already opened one (Android entry point)."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if journal is None:
        from .diagnostics import Journal

        journal = Journal()
    journal.install_hooks()
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    if IS_ANDROID:
        os.environ.setdefault("QT_ANDROID_DISABLE_ACCESSIBILITY", "1")   # as in the Widgets app
    sys.setswitchinterval(GUI_SWITCH_INTERVAL_S)
    if args.screenshot:
        # grabWindow() blocks the GUI thread – holding the GIL – until the render thread has
        # drawn; with a threaded loop GraphLines.updatePaintNode (Python, on the render
        # thread) then waits for the GIL: a deadlock. The basic loop grabs on this thread.
        os.environ.setdefault("QSG_RENDER_LOOP", "basic")

    from ..mobile.startup import TIMER

    TIMER.on_mark = lambda step, t: journal.write(f"step {t:6.3f} s  {step}")
    journal.write("importing PySide6.QtGui")
    from PySide6.QtGui import QGuiApplication

    journal.install_qt_handler()
    journal.write("creating QGuiApplication")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    app.setApplicationName("Pendulum Quick")
    app.setOrganizationName("pendulum-sim")
    TIMER.mark("Qt ready")
    try:
        quick = QuickApp(app, args, journal)
    except Exception:  # noqa: BLE001 - show any start-up failure on screen
        journal.exception("start-up")
        text = "The app could not start.\n\n" + traceback.format_exc()
        if journal.previous_failed:
            text += "\n\n" + journal.report()
        return show_error(app, args, text)
    code = quick.run()
    quick.shutdown()
    return code


def show_error(app, args: argparse.Namespace, text: str) -> int:
    print(text, file=sys.stderr, flush=True)
    if args.smoke_test is not None or args.benchmark or args.screenshot:
        return 1
    try:
        app.clipboard().setText(text)
        text = "(This text is on the clipboard: paste it into a message.)\n\n" + text
    except Exception:  # noqa: BLE001 - best effort
        pass
    window = error_window(text)
    window.showMaximized() if IS_ANDROID else window.show()
    return app.exec()


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


class QuickApp:
    """The running application: QML engine, window, backend singletons."""

    def __init__(self, app, args: argparse.Namespace, journal=None) -> None:
        from ..mobile.startup import TIMER

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtQml import (
            QQmlApplicationEngine,
            qmlRegisterSingletonInstance,
            qmlRegisterSingletonType,
            qmlRegisterType,
        )

        from .backend.settings import SettingsBridge
        from .backend.shell import AppBridge

        self.app, self.args = app, args
        if journal is None:
            from .diagnostics import Journal

            journal = Journal()
        self.journal = journal
        # the previous start did not finish: start without the optional native pieces
        self.safe_mode = journal.previous_failed and (not self._unattended or args.safe_mode_preview)
        self.warnings: list[str] = []
        self.result = 0
        self.backend_error = ""
        missing = [name for name in FONTS if QFontDatabase.addApplicationFont(str(HERE / "fonts" / name)) < 0]
        if missing:
            self.warnings.append("fonts not loaded: " + ", ".join(missing))
        app.setFont(QFont("Space Grotesk"))
        self.settings = SettingsBridge()
        if args.theme:
            self.settings.override_theme(args.theme)
        self.shell = AppBridge()
        self.shell._set_screen(args.screen)
        if self.safe_mode:
            self.shell.set_previous_report(journal.report() + "\n\nThis start runs in safe mode: physics in a thread, "
                                           "no Android performance hints.")
        qmlRegisterSingletonInstance(SettingsBridge, "PendulumApp", 1, 0, "Settings", self.settings)
        qmlRegisterSingletonInstance(AppBridge, "PendulumApp", 1, 0, "App", self.shell)
        qmlRegisterSingletonType(QUrl.fromLocalFile(str(HERE / "qml" / "Theme.qml")), "Instrument", 1, 0, "Theme")
        from .backend.lineitem import GraphLines        # registered before the engine starts (types
        qmlRegisterType(GraphLines, "PendulumItems", 1, 0, "GraphLines")   # registered later break QML)
        TIMER.mark("fonts and settings ready")

        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(self._on_warnings)
        self.shell.loadingShown.connect(self._on_loading_shown)
        self.shell.shellLoaded.connect(lambda: TIMER.mark("app shell and Simulate screen built"))
        self.shell.shellShown.connect(self._on_shell_shown)
        self.shell.exitRequested.connect(self._on_exit_requested)
        self.shell.failed.connect(self._on_shell_failed)
        self.shell.continueRequested.connect(self._load_backend)
        self.engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
        roots = self.engine.rootObjects()
        if not roots:
            raise RuntimeError("Main.qml could not be loaded:\n" + "\n".join(self.warnings)
                               + "\n\nQML import paths:\n" + "\n".join(self.engine.importPathList()))
        self.window = roots[0]
        if IS_ANDROID:
            self.window.showMaximized()
        TIMER.mark("loading screen created")
        if self._unattended:                          # CI: never hang
            from PySide6.QtCore import QTimer

            QTimer.singleShot(90_000, self._watchdog)
        self.sim = self.frames = self.graphs = self.panel = self.trail = self.bench = self.autotune = None
        app.applicationStateChanged.connect(self._on_application_state)

    # ------------------------------------------------------------ start-up
    def _on_loading_shown(self) -> None:
        from PySide6.QtCore import QTimer

        from ..mobile.startup import TIMER

        TIMER.mark("loading screen shown (first frame)")
        if self.safe_mode:                            # wait until the report has been read / copied
            self.shell.set_status("The previous start did not finish.", 0.25)
            if self.args.screenshot:
                QTimer.singleShot(1500, self._screenshot)
            return
        self.shell.set_status("Loading the simulation core…", 0.25)
        QTimer.singleShot(0, self._load_backend)      # let the status text reach the screen first

    def _load_backend(self) -> None:
        if self.sim is not None:
            return
        self.shell.set_status("Loading the simulation core…", 0.25)
        try:
            self._create_backend()
        except Exception:  # noqa: BLE001 - shown on the loading screen
            self.journal.exception("loading the backend")
            self.backend_error = traceback.format_exc()
            print(self.backend_error, file=sys.stderr, flush=True)
            self.shell.set_status("The app could not start:\n\n" + self.backend_error, 0.0)
            self.result = 1
            if self._unattended:
                self.app.exit(1)

    def _create_backend(self) -> None:
        from ..mobile.startup import TIMER

        import numpy  # noqa: F401  - timed on its own: the biggest import

        from ..simulation import scenario

        TIMER.mark("NumPy and the simulation core loaded")
        from .backend.autotune import AutotuneBridge
        from .backend.bench import Benchmark
        from .backend.frames import FrameDriver
        from .backend.graphs import GraphsBridge
        from .backend.panel import PanelBridge
        from .backend.trail import TrailBridge
        from .backend.parameters import ControllerBridge, ModelBridge
        from .backend.settings import ScenarioBridge
        from .backend.simulation import SimulationBridge

        physics = self.args.physics or ("thread" if self.safe_mode else self.settings.physics)
        self.journal.write(f"starting the physics ({physics})")
        self.sim = SimulationBridge(scenario(self.args.scenario), physics)
        TIMER.mark(f"physics started ({self.sim.physics})")
        self.model = ModelBridge(self.sim)
        self.controller = ControllerBridge(self.sim)
        self.graphs = GraphsBridge(self.sim)
        self.graphs.rateHz = self.settings.graphRate
        self.panel = PanelBridge(self.sim, self.graphs)
        self.trail = TrailBridge(self.sim)
        self.autotune = AutotuneBridge(self.sim, self.controller)
        self.scenarios = ScenarioBridge(self.sim)
        screen = self.window.screen()
        refresh = screen.refreshRate() if screen is not None else 60.0
        self.frames = FrameDriver(self.sim, refresh, self.settings.frameRate)
        self.frames.hints_allowed = not self.safe_mode
        from PySide6.QtCore import Qt

        self.window.frameSwapped.connect(self.frames.swapped, Qt.ConnectionType.QueuedConnection)
        self.frames.journal = self.journal
        self.frames.attach_graphs(self.graphs)
        self.frames.listeners.append(self.panel)
        self.frames.listeners.append(self.trail)
        self.bench = Benchmark(self.shell, self.sim, self.graphs, self.frames)
        self.bench.window = self.window
        self.bench.warnings = self.warnings
        # Context objects, not singleton types: QML types registered after the engine has
        # started broke type checks in later files ("Cannot assign object of type …").
        context = self.engine.rootContext()
        for name, obj in (("sim", self.sim), ("model", self.model), ("controller", self.controller),
                          ("graphs", self.graphs), ("panel", self.panel), ("trail", self.trail),
                          ("autotune", self.autotune),
                          ("scenarios", self.scenarios),
                          ("frames", self.frames), ("bench", self.bench)):
            context.setContextProperty(name, obj)
        s = self.settings
        s.frameRateChanged.connect(lambda: self.frames.set_mode(s.frameRate))
        s.graphRateChanged.connect(lambda: setattr(self.graphs, "rateHz", s.graphRate))
        s.physicsChanged.connect(lambda: setattr(self.sim, "physics", s.physics))
        if screen is not None:
            screen.refreshRateChanged.connect(self.frames.set_refresh)
        TIMER.mark("backend ready")
        self.shell.set_status("Building the Simulate screen…", 0.6)
        self.shell.set_ready()                         # QML loads the shell asynchronously now
        self.sim.running = True

    def _on_shell_shown(self) -> None:
        from ..mobile.startup import TIMER

        from PySide6.QtCore import QTimer

        TIMER.mark("Simulate screen shown")
        self.frames.start_threads()
        self.journal.write("garbage collector: idle-time mode")
        self.frames.collector.start(time.perf_counter())
        QTimer.singleShot(4000, self.journal.complete)  # still running 4 s later: a complete start
        self._start_mode()

    def _on_shell_failed(self) -> None:
        print("The app shell could not be loaded:\n" + "\n".join(self.warnings), file=sys.stderr, flush=True)
        if self._unattended:
            self._done(False)

    @property
    def _unattended(self) -> bool:
        return self.args.smoke_test is not None or self.args.benchmark or bool(self.args.screenshot)

    def _on_exit_requested(self) -> None:
        self.window.setProperty("askExit", True)

    def _on_warnings(self, warnings) -> None:
        for warning in warnings:
            text = warning.toString()
            self.warnings.append(text)
            print("QML:", text, file=sys.stderr, flush=True)

    def _on_application_state(self, state) -> None:
        """Background: pause everything (a running test or auto-tune stops); foreground: resume."""
        from PySide6.QtCore import Qt

        if self.sim is None:
            return
        if state == Qt.ApplicationState.ApplicationActive:
            self.frames.enabled = True
            self.sim.resume()
        elif state in (Qt.ApplicationState.ApplicationSuspended, Qt.ApplicationState.ApplicationHidden):
            self.bench.cancel()
            self.autotune.cancel()
            self.sim.suspend()
            self.frames.enabled = False

    # ---------------------------------------------------------------- modes
    def _start_mode(self) -> None:
        from PySide6.QtCore import QTimer

        args = self.args
        if args.benchmark:
            self.bench.finished.connect(lambda: self._done(not self.warnings or not args.strict))
            QTimer.singleShot(1000, self.bench.start)
        elif args.screenshot:
            if args.panel:
                self.panel.editing = True
                if args.panel != "edit":
                    tile = next((t for t in self.panel.model.tiles if t["type"] == args.panel), None)
                    self.panel.selected = tile["id"] if tile else ""
            QTimer.singleShot(2500, self._screenshot)
        elif args.panel_stress is not None:
            self._stress_left = args.panel_stress
            QTimer.singleShot(1500, self._stress_step)
        elif args.smoke_test is not None:
            started = time.perf_counter()
            steps = [("graphs", None), ("settings", None), ("settings", "dark"), ("simulate", None),
                     ("simulate", "light")]
            for i, (screen, theme) in enumerate(steps):
                QTimer.singleShot(400 * (i + 1), lambda s=screen, t=theme: self._smoke_step(s, t))
            QTimer.singleShot(int(max(args.smoke_test, 2.5) * 1000), lambda: self._smoke_check(started))

    def _stress_step(self) -> None:
        """One side-panel edit as a user makes it (buttons of the tile editor), then the next."""
        import gc
        import random

        from PySide6.QtCore import QMetaObject, QObject, QTimer
        from PySide6.QtGui import QGuiApplication
        from shiboken6 import Shiboken

        panel = self.panel
        step = self.args.panel_stress - self._stress_left
        if not hasattr(self, "_stress_buttons"):
            self._stress_buttons = {n: self.window.findChild(QObject, n) for n in ("tileMoveUp", "tileMoveDown")}
        if self._stress_left <= 0:
            print(f"panel stress: {self.args.panel_stress} edits done, {len(panel.tiles)} tiles, "
                  f"{len(self.warnings)} QML warnings", flush=True)
            self._done(not self.args.strict or not self.warnings)
            return
        self._stress_left -= 1
        rng = random.Random(step)
        panel.editing = True
        tiles = panel.model.tiles
        action = ("up", "down", "up", "down", "add", "remove", "update")[step % 7]
        detail = ""
        if action == "add":
            kind = rng.choice(["value", "graph", "status"])
            panel.add(kind)
            detail = kind
        elif action == "remove" and len(tiles) > 3:
            tile = tiles[rng.randrange(len(tiles))]
            detail = tile["type"]
            panel.remove(tile["id"])
        elif action == "update":
            graph = next((t for t in tiles if t["type"] == "graph"), None)
            if graph:
                panel.update(graph["id"], {"window": rng.choice([5, 10, 20]), "smoothing": rng.choice(["off", "light"])})
        elif action in ("up", "down"):
            tile = tiles[rng.randrange(len(tiles))]
            panel.selected = tile["id"]
            detail = tile["type"]
            button = self._stress_buttons["tileMoveUp" if action == "up" else "tileMoveDown"]
            if button is None:
                print("panel stress: tile editor buttons not found", flush=True)
                self._done(False)
                return
            QMetaObject.invokeMethod(button, "clicked")
        if self.args.stress_gc:
            gc.collect()
        print(f"panel stress {step}: {action} {detail}; window wrapper valid {Shiboken.isValid(self.window)}, "
              f"{len(QGuiApplication.topLevelWindows())} windows", flush=True)
        QTimer.singleShot(120, self._stress_step)

    def _smoke_step(self, screen: str, theme: str | None) -> None:
        self.shell.screen = screen
        if theme:
            self.settings.override_theme(theme)

    def _smoke_check(self, started: float) -> None:
        from .backend import threads

        stats = self.sim.source.stats()
        wall = time.perf_counter() - started
        print(f"quick smoke test: simulation t={stats['t']:.2f} s after {wall:.1f} s wall, {stats['steps']:.0f} steps, "
              f"{self.sim.clock.fps:.0f} fps, physics '{self.sim.physics}', render loop {threads.render_loop()}, "
              f"{len(self.warnings)} QML warnings; {self.frames.describe()}", flush=True)
        self._done(stats["t"] > 0.4 * wall and (not self.args.strict or not self.warnings))

    def _screenshot(self) -> None:
        grab = getattr(self.window, "grabWindow", None)     # PySide may hand back the root as a plain QWindow
        image = grab() if grab is not None else self.window.screen().grabWindow(self.window.winId()).toImage()
        ok = image.save(self.args.screenshot)
        print(f"screenshot {self.args.screenshot}: {image.width()}×{image.height()} {'saved' if ok else 'NOT saved'}",
              flush=True)
        self._done(ok and (not self.args.strict or not self.warnings))

    def _watchdog(self) -> None:
        print("Watchdog: the run did not finish within 90 s.\n" + "\n".join(self.warnings), file=sys.stderr, flush=True)
        self._done(False)

    def _done(self, ok: bool) -> None:
        self.result = 0 if ok else 1
        self.app.exit(self.result)        # not quit(): that asks the window, which refuses (Back handling)

    def run(self) -> int:
        self.app.exec()
        return self.result

    def shutdown(self) -> None:
        """Stop the physics, the workers and the scene while the application still exists."""
        from PySide6.QtCore import QCoreApplication, QEvent

        self.journal.write("shutting down")
        if self.sim is not None and self.shell.shown:
            self.journal.complete()                    # a normal exit after a normal start
        if self.frames is not None:
            self.frames.shutdown()
        if self.graphs is not None:
            self.graphs.timer.stop()
        if self.autotune is not None:
            self.autotune.shutdown()
        if self.sim is not None:
            self.sim.shutdown()
        self.window.close()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()


if __name__ == "__main__":
    sys.exit(main())
