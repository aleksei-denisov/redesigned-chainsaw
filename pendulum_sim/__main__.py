"""Allow ``python -m pendulum_sim``."""

import multiprocessing
import sys

from .ui.app import main

if __name__ == "__main__":  # guard: worker processes of the auto-tuner re-import this module
    multiprocessing.freeze_support()
    sys.exit(main())
