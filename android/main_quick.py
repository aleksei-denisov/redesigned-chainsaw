"""Android entry point of the Qt Quick interface (``scripts/build_android.py --ui quick``).

Staged as the APK's ``main.py`` (pyside6-android-deploy requires that name), next to the
shared core and ``pendulum_sim/quick``. The Widgets app keeps ``android/main.py``.

The start-up journal is opened first (it needs only the standard library), so even a
failure while importing the app is recorded – and copied to ``Android/media/<package>/
logs`` where it can be read without adb.
"""

import sys

from pendulum_sim.quick.diagnostics import Journal

journal = Journal()
journal.write("main.py: journal open, importing the app")
try:
    from pendulum_sim.mobile import startup  # noqa: F401  - start-up timing reference
    from pendulum_sim.quick.app import main
except BaseException:
    journal.exception("importing the app")
    raise

if __name__ == "__main__":
    sys.exit(main(journal=journal))
