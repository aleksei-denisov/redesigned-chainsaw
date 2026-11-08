"""Allow ``python -m pendulum_sim.mobile`` (desktop preview of the mobile interface)."""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
