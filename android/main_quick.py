"""Android entry point of the Qt Quick interface (``scripts/build_android.py --ui quick``).

Staged as the APK's ``main.py`` (pyside6-android-deploy requires that name), next to the
shared core and ``pendulum_sim/quick``. The Widgets app keeps ``android/main.py``.
"""

import sys

from pendulum_sim.mobile import startup  # noqa: F401  - first: start-up timing reference
from pendulum_sim.quick.app import main

if __name__ == "__main__":
    sys.exit(main())
