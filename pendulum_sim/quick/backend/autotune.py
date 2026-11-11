"""QML context object ``autotune``: the foreground robustness search (set-up → running → result).

The same flow as the Widgets app's auto-tune screen, on the shared worker
(:class:`~pendulum_sim.session.jobs.TuneWorker`, all CPU cores but one through the
simulation's worker pool) and the shared texts. While a search runs the simulation is
paused and ``busy`` is true – the QML shell locks navigation; leaving the app cancels it.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ...session import texts
from ...session.jobs import TuneWorker
from ...simulation import parallel
from ...simulation.tuning import TuneProgress, TuningResult, TuningSettings
from .simulation import SimulationBridge

POLL_MS = 100


class AutotuneBridge(QObject):
    changed = Signal()            # page, texts, presets
    progressChanged = Signal()    # bar and times (10 Hz while running)
    busyChanged = Signal()
    applied = Signal()

    def __init__(self, sim: SimulationBridge, controller, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self.controller = controller
        self.workers = parallel.default_workers()
        self.config = None
        self.calibration: TuneWorker | None = None
        self.worker: TuneWorker | None = None
        self.push_test_s: float | None = None
        self.result: TuningResult | None = None
        self.comparison = None
        self.last: TuneProgress | None = None
        self._page = "setup"
        self._preset = "normal"
        self._started = 0.0
        self._last_update = 0.0
        self._was_running = False
        self._texts = {"phase": "", "activity": "", "best": "–", "gains": "", "times": "", "headline": "",
                       "notes": "", "old": "", "new": ""}
        self._fraction = 0.0
        self._metrics: list[dict] = []
        self._history: list[float] = []
        self._candidates: list[float] = []
        self._preview: list[dict] = []
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)

    # ------------------------------------------------------------ properties
    page = Property(str, lambda self: self._page, notify=changed)
    busy = Property(bool, lambda self: self.worker is not None, notify=busyChanged)
    preset = Property(str, lambda self: self._preset, notify=changed)
    presets = Property("QVariantList", lambda self: [
        {"key": k, "name": n, "description": d, "estimate": texts.tune_estimate(k, self.push_test_s, self.workers)}
        for k, n, d in texts.TUNE_PRESETS], notify=changed)
    coresText = Property(str, lambda self: texts.tune_cores(self.workers, parallel.cpu_count()), constant=True)
    phaseText = Property(str, lambda self: self._texts["phase"], notify=progressChanged)
    activityText = Property(str, lambda self: self._texts["activity"], notify=progressChanged)
    bestText = Property(str, lambda self: self._texts["best"], notify=progressChanged)
    bestGains = Property(str, lambda self: self._texts["gains"], notify=progressChanged)
    timesText = Property(str, lambda self: self._texts["times"], notify=progressChanged)
    fraction = Property(float, lambda self: self._fraction, notify=progressChanged)
    history = Property("QVariantList", lambda self: self._history, notify=progressChanged)
    candidates = Property("QVariantList", lambda self: self._candidates, notify=progressChanged)
    headline = Property(str, lambda self: self._texts["headline"], notify=changed)
    notes = Property(str, lambda self: self._texts["notes"], notify=changed)
    oldGains = Property(str, lambda self: self._texts["old"], notify=changed)
    newGains = Property(str, lambda self: self._texts["new"], notify=changed)
    metrics = Property("QVariantList", lambda self: self._metrics, notify=changed)
    preview = Property("QVariantList", lambda self: self._preview, notify=changed)
    canApply = Property(bool, lambda self: self.result is not None, notify=changed)

    # --------------------------------------------------------------- set-up
    @Slot()
    def open(self) -> None:
        """Prepare a search for the current configuration (measures this device once)."""
        if self.worker is not None:
            return
        self.config = self.sim.session.config.copy()
        self._page = "setup"
        if self.push_test_s is None and self.calibration is None:
            self.calibration = TuneWorker(self.config, None, calibrate_only=True)
            self.calibration.start()
            self.timer.start()
        self.changed.emit()

    @Slot(str)
    def choose(self, key: str) -> None:
        if key in {k for k, _n, _d in texts.TUNE_PRESETS}:
            self._preset = key
            self.changed.emit()

    @Slot()
    def start(self) -> None:
        if self.worker is not None:
            return
        if self.config is None:
            self.config = self.sim.session.config.copy()
        settings = TuningSettings.preset(self._preset)
        settings.workers = self.workers
        self.result = self.comparison = self.last = None
        self._fraction = 0.0
        self._history, self._candidates = [], []
        self._texts.update(phase="Starting worker processes…", best="–", gains="",
                           activity=f"Working on {self.workers} CPU core{'s' if self.workers != 1 else ''}")
        self._was_running = self.sim.running
        self.sim.pause()                         # all CPU cores for the search
        self._started = time.monotonic()
        self.worker = TuneWorker(self.config, settings)
        self.worker.start()
        self.timer.start()
        self._page = "running"
        self.busyChanged.emit()
        self.changed.emit()
        self.progressChanged.emit()

    @Slot()
    def cancel(self) -> None:
        """Stop the search now (workers are killed); the best result so far is offered."""
        if self.worker is not None:
            self._texts["phase"] = "Stopping the workers…"
            self.worker.cancel()
            self.progressChanged.emit()

    # -------------------------------------------------------------- result
    @Slot()
    def apply(self) -> None:
        if self.result is not None:
            self.controller.apply_tuning(self.result)
            self.applied.emit()
        self.close()

    @Slot()
    def close(self) -> None:
        """Leave the auto-tune screen (cancels a running search first)."""
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(10000)
            self._worker_done()
        self.config = None
        self._page = "setup"
        if self._was_running:
            self._was_running = False
            self.sim.start()
        self.changed.emit()

    @Slot()
    def rerun(self) -> None:
        self.config = self.sim.session.config.copy()
        self.start()

    # ------------------------------------------------------------- polling
    def _poll(self) -> None:
        if self.calibration is not None and self.calibration.done():
            worker, self.calibration = self.calibration, None
            if worker.calibration is not None:
                self.push_test_s = worker.calibration
                self.changed.emit()
        if self.worker is not None:
            u = self.worker.take_progress()
            if u is not None:
                self._on_progress(u)
            self._tick()
            if self.worker.done():
                self._worker_done()
        if self.calibration is None and self.worker is None:
            self.timer.stop()

    def _on_progress(self, u: TuneProgress) -> None:
        self.last = u
        self._last_update = time.monotonic()
        self._texts["phase"] = u.message
        self._texts["activity"] = texts.tune_activity(u)
        if u.best_push > 0 or u.baseline_push is not None:
            self._texts["best"], self._texts["gains"] = texts.tune_best(u)
        self._history = [float(v) for v in u.history]
        self._candidates = [float(v) if v > 0 else math.nan for v in u.candidate_pushes]

    def _tick(self) -> None:
        u = self.last
        since = time.monotonic() - self._last_update
        if u is not None and u.total:
            self._fraction = texts.tune_fraction(u, since)
        self._texts["times"] = texts.tune_times(time.monotonic() - self._started, u, since)
        self.progressChanged.emit()

    def _worker_done(self) -> None:
        worker, self.worker = self.worker, None
        if worker is None:
            return
        last = worker.take_progress()
        if last is not None:
            self._on_progress(last)
        old = texts.gains_text(self.config.controller if self.config else None)
        if worker.outcome is not None:
            self.result, self.comparison = worker.outcome
            self._texts.update(headline=texts.tune_headline(self.result), notes=texts.tune_notes(self.result),
                               old=old, new=texts.gains_text(self.result.controller))
        else:
            self.result, self.comparison = None, None
            self._texts.update(headline="Auto-tune stopped", notes=worker.error or "Auto-tune stopped.",
                               old=old, new="–")
        self._metrics = [] if self.comparison is None else [
            {"name": n, "before": b, "after": a} for n, b, a in texts.tune_metric_rows(self.comparison)]
        self._preview = self._preview_series()
        self._page = "result"
        self.busyChanged.emit()
        self.changed.emit()
        self.progressChanged.emit()

    def _preview_series(self) -> list[dict]:
        """Both responses to the same push: θ1 in degrees over time (for a small plot)."""
        if self.comparison is None:
            return []
        out = []
        for name, response in zip(("before", "after"), self.comparison):
            t = np.asarray(response.data["t"], dtype=float)
            y = np.degrees(np.asarray(response.data["theta1"], dtype=float))
            step = max(1, t.size // 400)
            out.append({"name": name, "t": t[::step].tolist(), "y": y[::step].tolist()})
        return out

    def shutdown(self) -> None:
        for w in (self.worker, self.calibration):
            if w is not None:
                w.cancel()
                w.wait(10000)
