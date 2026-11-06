"""Application entry point (``python main.py`` or the ``pendulum-sim`` command)."""

from __future__ import annotations

import argparse
import sys
import time


def parse_args(argv: list[str]) -> argparse.Namespace:
    from ..simulation import DEFAULT_SCENARIO, SCENARIOS

    parser = argparse.ArgumentParser(prog="pendulum-sim", description="Interactive inverted pendulum simulator")
    parser.add_argument("--config", metavar="FILE", help="open a configuration JSON file at start-up")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default=DEFAULT_SCENARIO,
                        help=f"built-in scenario to start with (default: {DEFAULT_SCENARIO})")
    parser.add_argument("--list-scenarios", action="store_true", help="print the built-in scenarios and exit")
    parser.add_argument("--smoke-test", type=float, metavar="SECONDS", default=None,
                        help="open the window, run the simulation and one experiment, then exit "
                             "(used by CI to verify the installation)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    from ..simulation import SCENARIOS, load_config, scenario

    if args.list_scenarios:
        for key, factory in SCENARIOS.items():
            print(f"{key:20s} {factory().name}")
        return 0

    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Inverted Pendulum Simulator")
    app.setOrganizationName("pendulum-sim")
    app.setStyle("Fusion")
    pal = app.palette()
    pg.setConfigOptions(antialias=True, background=pal.base().color(), foreground=pal.text().color())

    config = scenario(args.scenario)
    error = None
    if args.config:
        try:
            config = load_config(args.config)
        except (OSError, ValueError) as exc:
            error = f"Could not load {args.config}:\n{exc}\n\nStarting with the default scenario."

    window = MainWindow(config)
    window.show()
    if error:
        if args.smoke_test is not None:
            print(error, file=sys.stderr)
            return 1
        QMessageBox.warning(window, "Configuration error", error)

    if args.smoke_test is not None:
        return _smoke_test(app, window, args.smoke_test)
    return app.exec()


def _smoke_test(app, window, seconds: float) -> int:
    """Run the live simulation for ``seconds`` and one experiment, then quit."""
    from PySide6.QtCore import QTimer

    status = {"code": 1}
    started = time.perf_counter()

    def start_experiment() -> None:
        window.experiments.duration.setValue(1.0)
        window.experiments.run()

    def check() -> None:
        if window.experiments.results:
            res = window.experiments.results[-1]
            print(f"smoke test: live sim t={window.sim.time:.2f}s, experiment '{res.name}' "
                  f"rmse={res.metrics.rmse:.3f} deg, fell={res.metrics.fell}")
            status["code"] = 0 if window.sim.time > 0 else 1
            window.close()
            app.quit()
        elif time.perf_counter() - started > seconds + 120:
            print("smoke test: timeout waiting for experiment", file=sys.stderr)
            window.close()
            app.quit()

    window.start()
    QTimer.singleShot(int(seconds * 1000), start_experiment)
    poll = QTimer()
    poll.timeout.connect(check)
    poll.start(200)
    app.exec()
    return status["code"]


if __name__ == "__main__":
    sys.exit(main())
