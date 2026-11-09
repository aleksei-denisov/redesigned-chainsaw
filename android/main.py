"""Android entry point.

pyside6-android-deploy requires the application script to be called ``main.py``.
The build script (scripts/build_android.py) stages this file as ``main.py`` next to
the ``pendulum_sim`` package, so the desktop ``main.py`` in the repository root is
left untouched.
"""

import sys

from pendulum_sim.mobile import startup  # noqa: F401  - first: start-up timing reference
from pendulum_sim.mobile.app import main

if __name__ == "__main__":
    sys.exit(main())
