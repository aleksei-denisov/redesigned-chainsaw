"""Launch the Interactive Inverted Pendulum Simulator.

Usage::

    python main.py                       # default scenario
    python main.py --scenario triple_cart_lqr
    python main.py --config configs/windup_demo.json
    python main.py --list-scenarios
"""

from __future__ import annotations

import sys


def _check_dependencies() -> None:
    missing = []
    for module, package in (("numpy", "numpy"), ("scipy", "scipy"), ("PySide6", "PySide6"),
                            ("pyqtgraph", "pyqtgraph")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        sys.stderr.write(
            "Missing Python packages: " + ", ".join(missing) + "\n"
            "Install them inside your virtual environment with:\n"
            "    python -m pip install -r requirements.txt\n"
            "See INSTALLATION.md for details.\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()  # needed by the auto-tuner's worker processes in frozen apps
    if "--list-scenarios" not in sys.argv and "-h" not in sys.argv and "--help" not in sys.argv:
        _check_dependencies()
    from pendulum_sim.ui.app import main

    sys.exit(main())
