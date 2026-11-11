"""Frame pacing and idle-time garbage collection for the Qt Quick app (Qt-free).

**Pacing.** The display refreshes at ``R`` Hz (120 on the Pad 6). The app shows a new
frame on every ``k``-th vsync, so it runs at ``R / k`` fps with even frame spacing:
120, 60, 40 and 30 fps on a 120 Hz display, 60 and 30 on a 60 Hz one. In *auto* mode
the :class:`Pacer` keeps the highest rate whose recent frame cost fits:

* **step down** when, over the last second, the 95th percentile of the frame cost
  exceeds ``DOWN_FRACTION`` of the frame interval, or frames were missed (the gap between
  shown frames exceeded 1.5 intervals in more than ``MISS_FRACTION`` of them);
* **step up** after ``UP_HOLD_S`` seconds in which the p95 cost stayed below
  ``UP_FRACTION`` of the *faster* rate's interval and nothing was missed.

Skipped vsyncs change nothing in the scene, so Qt Quick does not render them: the GPU
and the render thread rest as well. The simulation itself never depends on the frame
rate (it runs at its fixed time step in the physics process).

**Garbage collection.** :class:`IdleCollector` disables the automatic collector after
start-up (start-up objects are frozen out of its view) and runs the young generation
only when a frame left enough slack, or when the app is paused or in the background,
so a collection never lands in a busy frame.
"""

from __future__ import annotations

import gc
from collections import deque

WINDOW_S = 1.0               # cost statistics over the last second
DOWN_FRACTION = 0.85         # p95 cost above this share of the interval → slower rate
UP_FRACTION = 0.55           # p95 cost below this share of the faster interval …
UP_HOLD_S = 3.0              # … for this long → faster rate
MISS_FRACTION = 0.05         # more missed frames than this → slower rate
MIN_SAMPLES = 20


def supported_rates(refresh_hz: float) -> list[int]:
    """``refresh / k`` for k = 1…4 down to 30 fps, fastest first (120, 60, 40, 30 at 120 Hz)."""
    rates = [int(round(refresh_hz / k)) for k in range(1, 5) if refresh_hz / k >= 29.0]
    return rates or [int(round(refresh_hz))]


def p95(values) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1) + 0.5))]


class Pacer:
    """Chooses on which vsyncs the app shows a new frame.

    ``mode`` is ``"auto"`` or a fixed rate (``"120"``, ``"60"`` …). Call :meth:`vsync`
    on every vsync tick: it returns ``True`` when this tick shows a frame. After a shown
    frame, report its cost with :meth:`frame_done` (milliseconds of work for that frame).
    """

    def __init__(self, refresh_hz: float = 60.0, mode: str = "auto") -> None:
        self.refresh_hz = float(refresh_hz) if refresh_hz and refresh_hz > 1 else 60.0
        self.rates = supported_rates(self.refresh_hz)
        self.mode = "auto"
        self.rate = self.rates[0]
        self._phase = 0
        self._samples: deque[tuple[float, float, bool]] = deque()   # (time, cost ms, missed)
        self._good_since: float | None = None
        self._last_shown: float | None = None
        self.changes = 0
        self.set_mode(mode)

    # ------------------------------------------------------------- settings
    def set_mode(self, mode: str) -> None:
        mode = str(mode)
        if mode != "auto":
            try:
                wanted = int(mode)
            except ValueError:
                wanted = None
            if wanted is None:
                mode = "auto"
            else:
                # the fastest supported rate not above the wanted one
                self._set_rate(next((r for r in self.rates if r <= wanted), self.rates[-1]))
        if mode == "auto" and self.mode != "auto":
            self._set_rate(self.rates[0])
        self.mode = mode

    def set_refresh(self, refresh_hz: float) -> None:
        if refresh_hz and abs(refresh_hz - self.refresh_hz) > 0.5:
            self.refresh_hz = float(refresh_hz)
            self.rates = supported_rates(self.refresh_hz)
            mode, self.mode = self.mode, "auto"
            self._set_rate(self.rates[0])
            self.set_mode(mode)

    @property
    def divisor(self) -> int:
        return max(1, int(round(self.refresh_hz / self.rate)))

    @property
    def interval_ms(self) -> float:
        return 1000.0 / self.rate

    def _set_rate(self, rate: int) -> None:
        if rate != self.rate:
            self.rate = rate
            self.changes += 1
        self._samples.clear()
        self._good_since = None
        self._phase = 0

    # ------------------------------------------------------------ per frame
    def vsync(self) -> bool:
        """``True`` on the vsyncs that show a frame (every ``divisor``-th)."""
        show = self._phase == 0
        self._phase = (self._phase + 1) % self.divisor
        return show

    def frame_done(self, now: float, cost_ms: float) -> None:
        """A shown frame took ``cost_ms`` of work; ``now`` in seconds (monotonic)."""
        missed = False
        if self._last_shown is not None:
            missed = (now - self._last_shown) * 1000.0 > 1.5 * self.interval_ms
        self._last_shown = now
        if self.mode != "auto":
            return
        samples = self._samples
        samples.append((now, cost_ms, missed))
        while samples and now - samples[0][0] > WINDOW_S:
            samples.popleft()
        if len(samples) < MIN_SAMPLES:
            return
        costs = [c for _t, c, _m in samples]
        cost95 = p95(costs)
        misses = sum(1 for _t, _c, m in samples if m) / len(samples)
        index = self.rates.index(self.rate)
        if (cost95 > DOWN_FRACTION * self.interval_ms or misses > MISS_FRACTION) and index + 1 < len(self.rates):
            self._set_rate(self.rates[index + 1])
            return
        if index > 0 and cost95 < UP_FRACTION * 1000.0 / self.rates[index - 1] and misses == 0:
            if self._good_since is None:
                self._good_since = now
            elif now - self._good_since >= UP_HOLD_S:
                self._set_rate(self.rates[index - 1])
        else:
            self._good_since = None

    def paused(self) -> None:
        """Frames stopped for a while (app in the background): no false 'missed' frame."""
        self._last_shown = None


class IdleCollector:
    """Young-generation garbage collection only in idle moments.

    :meth:`start` (after start-up) freezes everything allocated so far and disables
    automatic collection. :meth:`frame_slack` is called after each frame with the time
    left until the next one; when enough is left and a collection is due, the young
    generation is collected there. :meth:`idle` (paused, background) collects fully.
    """

    INTERVAL_S = 1.0          # at most one young collection per second
    MIN_SLACK_MS = 4.0        # … and only with this much time to spare

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.active = False
        self.collections = 0
        self.last_ms = 0.0
        self._due = 0.0

    def start(self, now: float) -> None:
        if not self.enabled:
            return
        gc.collect()
        gc.freeze()
        gc.disable()
        self.active = True
        self._due = now + self.INTERVAL_S

    def frame_slack(self, now: float, slack_ms: float, clock=None) -> bool:
        """Collect the young generation if due and ``slack_ms`` allows; ``True`` if it ran."""
        if not self.active or now < self._due or slack_ms < self.MIN_SLACK_MS:
            return False
        self._run(0, clock)
        self._due = now + self.INTERVAL_S
        return True

    def idle(self, clock=None) -> None:
        if self.active:
            self._run(2, clock)

    def _run(self, generation: int, clock) -> None:
        started = clock() if clock else 0.0
        gc.collect(generation)
        self.collections += 1
        if clock:
            self.last_ms = (clock() - started) * 1000.0

    def stop(self) -> None:
        if self.active:
            gc.enable()
            self.active = False
