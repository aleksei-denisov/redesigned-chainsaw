"""Entry point of the mobile (Android) interface.

On Android this is started by ``android/main.py`` (packaged as the APK's
``main.py``). On the desktop the same interface can be previewed with::

    python -m pendulum_sim.mobile                 # phone-sized window
    python -m pendulum_sim.mobile --landscape
    python -m pendulum_sim.mobile --smoke-test 2  # CI: run briefly and exit

If start-up fails, the traceback is shown on screen (and printed, which ends up
in ``adb logcat``) instead of the app silently closing – the most useful
diagnostic for a first run on a new device.
"""

from __future__ import annotations

import argparse
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

    try:
        import pyqtgraph as pg

        from ..simulation import DEFAULT_SCENARIO, SCENARIOS, scenario
        from .window import MobileWindow

        pal = app.palette()
        pg.setConfigOptions(antialias=True, background=pal.base().color(), foreground=pal.text().color())
        key = args.scenario if args.scenario in SCENARIOS else DEFAULT_SCENARIO
        window = MobileWindow(scenario(key))
    except Exception:  # noqa: BLE001 - show any start-up failure on screen
        text = traceback.format_exc()
        print(text, file=sys.stderr, flush=True)
        if args.smoke_test is not None:
            return 1
        window = _error_screen(app, text)

    if IS_ANDROID:
        window.showMaximized()
    else:
        window.resize(820, 420) if args.landscape else window.resize(420, 820)
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
    from PySide6.QtCore import QTimer

    window.seed_lqr()
    window.start()
    started = time.perf_counter()
    result = {"code": 1}

    def finish() -> None:
        ok = window.sim.time > 0
        print(f"mobile smoke test: t={window.sim.time:.2f}s after {time.perf_counter() - started:.1f}s wall, "
              f"angles={[round(float(a), 4) for a in window.sim.angles]}", flush=True)
        result["code"] = 0 if ok else 1
        app.quit()

    QTimer.singleShot(int(seconds * 1000), finish)
    app.exec()
    return result["code"]


if __name__ == "__main__":
    sys.exit(main())
