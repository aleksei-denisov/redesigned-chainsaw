"""Real-time pacing of the fixed-step simulator (no GUI code).

A GUI calls :meth:`RealtimeStepper.tick` once per displayed frame with the
measured wall-clock time since the previous frame. The stepper converts
``wall time × speed`` into a whole number of fixed ``dt`` physics steps,
carrying the fractional remainder to the next frame, so the physics timestep
never depends on the display rate.

Two safeguards keep the GUI responsive on slow devices:

* the wall time of a single frame is clamped (``max_frame_s``), so a stall –
  or the app returning from the background – never triggers a burst of
  catch-up physics;
* physics work per frame is limited to ``budget_s``; when the budget runs out
  the backlog is dropped and the simulation runs slower than real time
  instead of freezing the display (reported through :attr:`realtime_factor`).

This is the same pacing the desktop window uses (see ``ui/main_window.py``).
"""

from __future__ import annotations

import time
from typing import Callable

from .engine import Simulator

STEP_CHUNK = 25  # physics steps between deadline checks


class RealtimeStepper:
    def __init__(self, budget_s: float = 0.010, max_frame_s: float = 0.1,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self.budget_s = budget_s
        self.max_frame_s = max_frame_s
        self.clock = clock
        self.accum = 0.0
        self.realtime_factor = 0.0   # smoothed simulated-time / wall-time ratio
        self.physics_ms = 0.0        # smoothed physics compute time per frame
        self.last_ms = 0.0           # physics compute time of the last frame

    def reset_clock(self) -> None:
        """Forget any accumulated time (call on start/resume)."""
        self.accum = 0.0

    def tick(self, sim: Simulator, wall_elapsed: float, speed: float = 1.0) -> int:
        """Advance ``sim`` for one displayed frame. Returns the number of physics steps.

        :class:`~.engine.SimulationDiverged` propagates to the caller.
        """
        wall = min(max(wall_elapsed, 0.0), self.max_frame_s)
        if wall <= 0.0:
            return 0
        started = self.clock()
        deadline = started + self.budget_s
        dt = sim.config.simulation.dt
        self.accum += wall * speed
        wanted = int(self.accum / dt)
        done = 0
        while done < wanted:
            k = min(STEP_CHUNK, wanted - done)
            for _ in range(k):
                sim.step()
            done += k
            if self.clock() > deadline:
                break
        if done < wanted:
            self.accum = 0.0          # cannot keep up: drop the backlog
        else:
            self.accum -= done * dt
        self.realtime_factor = 0.9 * self.realtime_factor + 0.1 * (done * dt / wall)
        self.last_ms = (self.clock() - started) * 1000.0
        self.physics_ms = 0.9 * self.physics_ms + 0.1 * self.last_ms
        return done
