"""Entry point of the mobile (Android) interface.

On Android this is started by ``android/main.py`` (packaged as the APK's
``main.py``). On the desktop the same interface can be previewed with::

    python -m pendulum_sim.mobile                 # phone-sized window
    python -m pendulum_sim.mobile --landscape
    python -m pendulum_sim.mobile --tablet --landscape   # 1280×800 tablet
    python -m pendulum_sim.mobile --smoke-test 2  # CI: run briefly and exit

If start-up fails, the traceback is shown on screen (and printed, which ends up
in ``adb logcat``) instead of the app silently closing – the most useful
diagnostic for a first run on a new device.
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
import traceback

IS_ANDROID = hasattr(sys, "getandroidapilevel") or "ANDROID_ARGUMENT" in os.environ


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pendulum-sim-mobile",
                                     description="Inverted Pendulum Simulator – mobile interface")
    parser.add_argument("--scenario", default=None, help="built-in scenario to start with")
    parser.add_argument("--landscape", action="store_true", help="desktop preview in landscape")
    parser.add_argument("--tablet", action="store_true", help="desktop preview at tablet size")
    parser.add_argument("--smoke-test", type=float, metavar="SECONDS", default=None,
                        help="run the simulation for SECONDS, then exit (CI)")
    args, _unknown = parser.parse_known_args(argv)  # Android may pass launcher arguments
    return args


def _error_screen(app, text: str):
    from PySide6.QtWidgets import QPlainTextEdit

    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setPlainText("The simulator could not start.\n\n" + text)
    view.setWindowTitle("Inverted Pendulum Simulator – error")
    return view


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if IS_ANDROID:
        # Qt's accessibility bridge can dead-lock popups while an accessibility service
        # (e.g. a password manager) is active; the simulator does not rely on it.
        os.environ.setdefault("QT_ANDROID_DISABLE_ACCESSIBILITY", "1")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Inverted Pendulum Simulator")
    app.setOrganizationName("pendulum-sim")
    app.setStyle("Fusion")
    from . import theme

    theme.apply(app)        # dark palette, 16 dp text, 48 dp touch targets (no Python style hooks)

    try:
        import pyqtgraph as pg

        from ..simulation import DEFAULT_SCENARIO, SCENARIOS, scenario
        from .window import MobileWindow

        pal = app.palette()
        pg.setConfigOptions(antialias=True, background=pal.base().color(), foreground=pal.text().color())
        key = args.scenario if args.scenario in SCENARIOS else DEFAULT_SCENARIO
        window = MobileWindow(scenario(key))
        # Move everything created so far (Qt wrappers, pyqtgraph items, scenarios) out of the
        # garbage collector's view so full collections stay short while the app runs.
        gc.collect()
        gc.freeze()
    except Exception:  # noqa: BLE001 - show any start-up failure on screen
        text = traceback.format_exc()
        print(text, file=sys.stderr, flush=True)
        if args.smoke_test is not None:
            return 1
        window = _error_screen(app, text)

    if IS_ANDROID:
        window.showMaximized()
    else:
        long_edge, short_edge = (1280, 800) if args.tablet else (860, 400)
        if args.landscape:
            window.resize(long_edge, short_edge)
        else:
            window.resize(short_edge, long_edge)
        window.show()

    if args.smoke_test is not None:
        code = _smoke_test(app, window, args.smoke_test)
    else:
        code = app.exec()
    _shutdown(app, window)
    return code


def _shutdown(app, window) -> None:
    """Destroy the window while the QApplication still exists.

    Leaving the order to interpreter shutdown can delete Qt objects after the
    application object (seen as a segfault on macOS).
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _smoke_test(app, window, seconds: float) -> int:
    """Seed LQR gains, run the live simulation for ``seconds`` and one experiment, then quit."""
    from PySide6.QtCore import QTimer

    window.seed_lqr()
    window.start()
    started = time.perf_counter()
    result = {"code": 1}

    def start_experiment() -> None:
        window.experiments.duration.setValue(1.0)
        window.experiments.run()

    def check() -> None:
        if window.experiments.results:
            res = window.experiments.results[-1]
            print(f"mobile smoke test: live t={window.sim.time:.2f}s after "
                  f"{time.perf_counter() - started:.1f}s wall, angles="
                  f"{[round(float(a), 4) for a in window.sim.angles]}, experiment '{res.name}' "
                  f"rmse={res.metrics.rmse:.3f} deg, {window.stats.fps:.0f} fps", flush=True)
            result["code"] = 0 if window.sim.time > 0 else 1
            app.quit()
        elif time.perf_counter() - started > seconds + 120:
            print("mobile smoke test: timeout waiting for experiment", file=sys.stderr, flush=True)
            app.quit()

    QTimer.singleShot(int(seconds * 1000), start_experiment)
    poll = QTimer()
    poll.timeout.connect(check)
    poll.start(200)
    app.exec()
    poll.stop()
    return result["code"]


if __name__ == "__main__":
    sys.exit(main())
