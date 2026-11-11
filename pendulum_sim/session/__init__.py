"""Application logic shared by all interfaces (desktop, Widgets mobile, Qt Quick) – Qt-free.

* :mod:`.rules` – how configuration edits are decided (loops, structure changes, restoring
  gains, gains from a run, single step, drag force);
* :mod:`.operations` – what an edit does to a running simulator;
* :mod:`.session` – :class:`Session`, the edited configuration plus the rules, for front
  ends whose simulator runs elsewhere (the Qt Quick app's physics process);
* :mod:`.jobs` – auto-tune and experiment workers, replay data;
* :mod:`.texts` – texts shown the same way everywhere.

May import ``physics``, ``control`` and ``simulation`` – never Qt.
"""

from .rules import IncompatibleRun
from .session import PlantChange, Session, apply

__all__ = ["IncompatibleRun", "PlantChange", "Session", "apply"]
