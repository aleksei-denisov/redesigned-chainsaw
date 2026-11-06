"""Build a standalone desktop application with PyInstaller.

Usage (from the repository root, inside the virtual environment)::

    python -m pip install pyinstaller
    python scripts/build_app.py

Result:
    Windows: dist/PendulumSimulator/PendulumSimulator.exe
    macOS:   dist/PendulumSimulator.app
    Linux:   dist/PendulumSimulator/PendulumSimulator
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    try:
        import PyInstaller.__main__ as pyinstaller
    except ImportError:
        print("PyInstaller is not installed. Run:  python -m pip install pyinstaller", file=sys.stderr)
        return 1
    os.chdir(ROOT)
    args = [
        "main.py",
        "--name", "PendulumSimulator",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--collect-submodules", "pyqtgraph",
        # Example configurations next to the executable's internal data.
        "--add-data", f"configs{os.pathsep}configs",
    ]
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "io.github.pendulum-sim"]
    pyinstaller.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
