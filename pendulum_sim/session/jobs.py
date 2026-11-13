"""Background jobs of a session: auto-tune and experiment runs, replay data (Qt-free).

Each job runs in a plain Python thread; the front end polls its progress and outcome
on a timer, so no Qt object ever crosses a thread boundary (queued signals carrying
Python objects, and a ``QThread`` running Python next to a repainting progress bar,
crashed PySide6 with Python 3.10 on Windows). Moved unchanged from the Widgets mobile
screens (``mobile/autotune_screen.py``, ``mobile/experiments_page.py``) so the Qt Quick
interface uses the same code.
"""

from __future__ import annotations

import copy
import threading

import numpy as np

from ..physics import MultiLinkPendulum
from ..simulation import AppConfig, ExperimentResult, run_experiment
from ..simulation.tuning import (
    CancelToken,
    TuneProgress,
    TuningCancelled,
    TuningResult,
    TuningSettings,
    calibrate_push_test_seconds,
    compare_responses,
    robust_tune,
)


class TuneWorker:
    """Runs calibration or the search in a plain Python thread (no Qt in it).

    Nothing crosses the thread boundary through Qt: the worker keeps its latest
    progress snapshot and its outcome in attributes guarded by a lock, and the UI
    polls them (and :meth:`done`) on a timer. Queued signals carrying Python objects,
    and later a ``QThread`` running Python code next to a repainting progress bar,
    crashed PySide6 with Python 3.10 on Windows.
    """

    def __init__(self, config: AppConfig, settings: TuningSettings | None, calibrate_only: bool = False,
                 options=None) -> None:
        """``options``: a ``tuning2.TuneOptions`` for the search with options (Qt Quick app,
        quick-core-v2); ``None`` runs the original search (desktop, Widgets)."""
        self._thread = threading.Thread(target=self.run, name="autotune", daemon=True)
        self.config = config.copy()
        self.settings = settings
        self.calibrate_only = calibrate_only
        self.options = options
        self.token = CancelToken()
        self._lock = threading.Lock()
        self._latest: TuneProgress | None = None
        self.calibration: float | None = None
        self.outcome: tuple[TuningResult, object] | None = None
        self.error: str | None = None

    def cancel(self) -> None:
        self.token.cancel()

    def start(self) -> None:
        self._thread.start()

    def done(self) -> bool:
        return self._thread.ident is not None and not self._thread.is_alive()

    def wait(self, timeout_ms: int) -> bool:
        if self._thread.ident is not None:
            self._thread.join(timeout_ms / 1000.0)
        return not self._thread.is_alive()

    def take_progress(self) -> TuneProgress | None:
        with self._lock:
            u, self._latest = self._latest, None
        return u

    def run(self) -> None:
        try:
            if self.calibrate_only:
                self.calibration = calibrate_push_test_seconds(self.config)
                return

            def update(u: TuneProgress) -> bool:
                snap = copy.copy(u)
                snap.history = list(u.history)
                snap.candidate_pushes = list(u.candidate_pushes)
                with self._lock:
                    self._latest = snap
                return not self.token.cancelled

            if self.options is not None:
                from ..simulation.tuning2 import tune

                result = tune(self.config, self.settings, self.options, on_update=update, cancel=self.token)
            else:
                result = robust_tune(self.config, self.settings, on_update=update, cancel=self.token)
            comparison = None
            try:
                comparison = compare_responses(self.config, self.config.controller, result.controller,
                                               max(result.recommended_push, 1e-3), self.settings)
            except Exception:  # noqa: BLE001 - the summary works without the preview
                comparison = None
            self.outcome = (result, comparison)
        except TuningCancelled:
            self.error = "Cancelled before any candidate was finished – nothing to apply."
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self.error = str(exc)


class ExperimentWorker:
    """One experiment in a plain Python thread; the UI polls :meth:`done` and ``progress``."""

    def __init__(self, config: AppConfig, name: str) -> None:
        self.config = config
        self.name = name
        self.progress = 0.0
        self.result: ExperimentResult | None = None
        self.error: str | None = None
        self._cancel = False
        self._thread = threading.Thread(target=self._run, name="experiment", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._cancel = True

    def done(self) -> bool:
        return self._thread.ident is not None and not self._thread.is_alive()

    def wait(self, timeout_s: float) -> bool:
        if self._thread.ident is not None:
            self._thread.join(timeout_s)
        return not self._thread.is_alive()

    def _run(self) -> None:
        def report(fraction: float) -> bool:
            self.progress = fraction
            return not self._cancel

        try:
            self.result = run_experiment(self.config, self.name, report)
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self.error = str(exc)


class ReplayData:
    """Recorded states of a run (telemetry columns → model state), for playing it back."""

    def __init__(self, result: ExperimentResult) -> None:
        self.result = result
        self.cfg = result.config
        self.model = MultiLinkPendulum(self.cfg.plant)
        d = result.data
        self.t = np.nan_to_num(np.asarray(d["t"], dtype=float))
        n = self.model.n
        self.n = n

        def col(name: str) -> np.ndarray:
            return np.nan_to_num(np.asarray(d.get(name, np.zeros_like(self.t)), dtype=float))

        self.x, self.xd = col("x"), col("xd")
        self.th = np.stack([col(f"theta{i + 1}") for i in range(n)], axis=1) if n else np.zeros((self.t.size, 0))
        self.om = np.stack([col(f"omega{i + 1}") for i in range(n)], axis=1) if n else np.zeros((self.t.size, 0))
        self.actuator, self.tip = col("actuator"), col("tip_force")

    @property
    def duration(self) -> float:
        return float(self.t[-1]) if self.t.size else 0.0

    def index(self, t: float) -> int:
        if self.t.size == 0:
            return 0
        return int(min(max(np.searchsorted(self.t, t, side="right") - 1, 0), self.t.size - 1))

    def state(self, i: int) -> np.ndarray:
        """Model state vector ``[x, θ…, ẋ, ω…]`` of sample ``i``."""
        return np.concatenate(([self.x[i]], self.th[i], [self.xd[i]], self.om[i]))
