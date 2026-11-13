"""QML context object ``experiments``: run the current set-up, compare runs (*Experiments* screen).

The same flow as the Widgets app's Experiments screen, on the shared pieces: the
settings form (``session.forms.ExperimentForm``), the run configuration
(``session.rules.experiment_config`` – with *Same start* every run begins from the first
run's plant, initial state and noise, so only the controller differs), the worker
(``session.jobs.ExperimentWorker``, a thread computing at full speed) and the result
texts (``session.texts``). Each run gets a colour index; the comparison plots (angle,
control error with the settling band, actuator output, cart position) are prepared here,
thinned to a few hundred points per run.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ...session import rules, texts
from ...session.forms import ExperimentForm
from ...session.jobs import ExperimentWorker
from ...simulation.experiment import ExperimentResult
from .formmodel import FormModel
from .simulation import SimulationBridge

POLL_MS = 50
PLOT_POINTS = 600
PLOTS = [("angle", "Angle", "°"), ("error", "Control error", "°"), ("effort", "Actuator output", "N · N·m"),
         ("cart", "Cart position", "m")]


def thin(t: np.ndarray, y: np.ndarray, points: int = PLOT_POINTS) -> tuple[list, list]:
    """At most ``points`` samples (min and max of each bucket, so peaks survive)."""
    n = t.size
    if n <= points:
        return t.tolist(), y.tolist()
    buckets = points // 2
    edges = np.linspace(0, n, buckets + 1).astype(int)
    ts, ys = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        seg = y[a:b]
        i, j = a + int(np.nanargmin(seg)), a + int(np.nanargmax(seg))
        for k in sorted((i, j)) if i != j else (i,):
            ts.append(float(t[k]))
            ys.append(float(y[k]))
    return ts, ys


class ExperimentsBridge(QObject):
    runsChanged = Signal()
    progressChanged = Signal()
    runningChanged = Signal()
    nameChanged = Signal()
    segmentChanged = Signal()

    def __init__(self, sim: SimulationBridge, controller, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self.controller = controller
        self.form = ExperimentForm()
        self.form.set_settings(sim.session.config.experiment)
        self._model = FormModel(self.form, self._edit, editable=self.form.editable,
                                expanded={"disturbance": True}, parent=self)
        self.results: list[ExperimentResult] = []
        self.colors: list[int] = []                      # colour index per run (kept when runs are removed)
        self.worker: ExperimentWorker | None = None
        self._counter = 1
        self._next_color = 0
        self._name = ""
        self._segment = 0
        self._plots: list[dict] = []
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)
        sim.configChanged.connect(self._on_config)

    # ------------------------------------------------------------ properties
    model = Property(QObject, lambda self: self._model, constant=True)
    running = Property(bool, lambda self: self.worker is not None, notify=runningChanged)
    progress = Property(float, lambda self: self.worker.progress if self.worker else 0.0, notify=progressChanged)

    def _set_name(self, name: str) -> None:
        if name != self._name:
            self._name = name
            self.nameChanged.emit()

    name = Property(str, lambda self: self._name, _set_name, notify=nameChanged)
    placeholder = Property(str, lambda self: f"Run {self._counter}", notify=runsChanged)

    def _set_segment(self, k: int) -> None:
        k = max(0, min(int(k), self.segments - 1))
        if k != self._segment:
            self._segment = k
            self._plots = self._make_plots()
            self.segmentChanged.emit()
            self.runsChanged.emit()

    segment = Property(int, lambda self: self._segment, _set_segment, notify=segmentChanged)

    @property
    def segments(self) -> int:
        return max((r.n_segments for r in self.results), default=1)

    segmentCount = Property(int, lambda self: self.segments, notify=runsChanged)
    plots = Property("QVariantList", lambda self: self._plots, notify=runsChanged)

    def _runs(self) -> list[dict]:
        out = []
        for i, r in enumerate(self.results):
            status = texts.status_text(r)
            out.append({"index": i, "name": r.name, "color": self.colors[i], "status": status,
                        "stable": status == "stable", "visible": r.visible,
                        "metrics": [{"name": k, "value": v} for k, v in texts.metric_pairs(r)],
                        "gains": r.gains_summary()})
        return out

    runs = Property("QVariantList", _runs, notify=runsChanged)

    # ---------------------------------------------------------------- edits
    def _edit(self, key: str, value) -> None:
        self.form.set(key, value)
        self._model.update_values()
        if key == "tolerance":
            self._plots = self._make_plots()
            self.runsChanged.emit()

    def _on_config(self) -> None:
        """A scenario was loaded: its experiment settings (the runs stay for comparison)."""
        self.form.set_settings(self.sim.session.config.experiment)
        self._model.update_values()

    # ------------------------------------------------------------------ run
    @Slot()
    def run(self) -> None:
        if self.worker is not None:
            return
        reuse = self.results[0].config if self.form.get("reuse") and self.results else None
        try:
            cfg = rules.experiment_config(self.sim.session.config, self.form.settings(), reuse)
            cfg.validate()
        except ValueError as exc:
            self.sim.message.emit("error", f"Invalid configuration: {exc}")
            return
        name = self._name.strip() or f"Run {self._counter}"
        self._counter += 1
        self.worker = ExperimentWorker(cfg, name)
        self.worker.start()
        self.timer.start()
        self.sim.message.emit("info", f"Running experiment '{name}'…")
        self.runningChanged.emit()
        self.progressChanged.emit()

    @Slot()
    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    def _poll(self) -> None:
        w = self.worker
        if w is None:
            self.timer.stop()
            return
        self.progressChanged.emit()
        if not w.done():
            return
        self.timer.stop()
        self.worker = None
        self.runningChanged.emit()
        self.progressChanged.emit()
        if w.error is not None:
            self.sim.message.emit("error", f"Experiment failed: {w.error}")
            return
        r = w.result
        if r is None:
            return
        if r.message == "cancelled":
            self.sim.message.emit("info", f"Experiment '{r.name}' cancelled")
            return
        self.results.append(r)
        self.colors.append(self._next_color)
        self._next_color += 1
        self._set_name("")
        self._changed()
        self.sim.message.emit("info", f"Experiment '{r.name}' finished ({texts.status_text(r)})")

    # -------------------------------------------------------------- results
    def _changed(self) -> None:
        if self._segment >= self.segments:
            self._segment = 0
            self.segmentChanged.emit()
        self._plots = self._make_plots()
        self.runsChanged.emit()

    @Slot(int)
    def toggleVisible(self, i: int) -> None:  # noqa: N802 - QML API
        if 0 <= i < len(self.results):
            self.results[i].visible = not self.results[i].visible
            self._changed()

    @Slot(int)
    def remove(self, i: int) -> None:
        if 0 <= i < len(self.results):
            del self.results[i]
            del self.colors[i]
            self._changed()

    @Slot()
    def clear(self) -> None:
        self.results.clear()
        self.colors.clear()
        self._counter = 1
        self._next_color = 0
        self._changed()

    @Slot(int)
    def useGains(self, i: int) -> None:  # noqa: N802 - QML API
        if 0 <= i < len(self.results) and self.controller.use_run_gains(self.results[i].config):
            self.sim.message.emit("info", f"Gains of '{self.results[i].name}' applied (undo on Controller)")

    # ---------------------------------------------------------------- plots
    def _curve(self, key: str, r: ExperimentResult) -> np.ndarray | None:
        seg = self._segment
        if key == "angle" and seg < r.n_segments:
            return r.angle_deg(seg)
        if key == "error" and seg < r.n_segments:
            return r.error_deg(seg)
        if key == "effort":
            return np.asarray(r.data["actuator"], dtype=float)
        if key == "cart" and r.config.plant.has_cart:
            return np.asarray(r.data["x"], dtype=float)
        return None

    def _make_plots(self) -> list[dict]:
        if not self.results:
            return []
        tol = float(self.form.get("tolerance"))
        has_cart = any(r.config.plant.has_cart for r in self.results)
        out = []
        for key, title, unit in PLOTS:
            if key == "cart" and not has_cart:
                continue
            series = []
            for i, r in enumerate(self.results):
                if not r.visible:
                    continue
                y = self._curve(key, r)
                if y is None:
                    continue
                ts, ys = thin(np.asarray(r.data["t"], dtype=float), np.asarray(y, dtype=float))
                series.append({"xs": ts, "ys": ys, "color": self.colors[i]})
            suffix = f" θ{self._segment + 1}" if key in ("angle", "error") else ""
            out.append({"key": key, "title": title + suffix, "unit": unit, "series": series,
                        "hlines": [tol, -tol] if key == "error" else []})
        return out

    def shutdown(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5.0)
