"""Experiments screen of the mobile app: run, watch, compare and export controller runs.

Same functions as the desktop experiment panel (``ui/experiment_panel.py``, unchanged),
laid out like the other mobile screens:

* **Experiment** and **Scheduled disturbance** cards with the usual touch rows; *Run* /
  *Cancel* and the progress bar stay in a bar at the bottom of the screen;
* runs are computed by :func:`~pendulum_sim.simulation.run_experiment` in a plain Python
  thread (the UI polls it – no Qt objects cross threads, see ``autotune_screen.py``),
  as fast as the processor allows;
* **Watch** (optional): a run is played back in a pendulum view at real speed (0.25–4×,
  pause, scrub) with a time marker moving through the comparison plots – from the
  recorded states, so watching never changes or slows the computation;
* **Comparison**: angle, control error (with the settling band), actuator output and cart
  position for every shown run, as :class:`~.plot_view.PlotView` thumbnails; a tap opens
  one full-window to zoom and read values;
* **Results**: one card per run (colour, status, the metrics of the desktop table, gains)
  with *Show*, *Watch*, *Use gains*, *CSV* and *Remove*;
* **Files**: full set-up (configuration + app settings, JSON), results (JSON, load and
  save), the comparison graphs as an image (PNG) and their data (CSV) – each separately.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..physics import MultiLinkPendulum
from ..simulation import (
    AppConfig,
    DisturbanceSettings,
    ExperimentResult,
    ExperimentSettings,
    load_experiments,
    run_experiment,
    save_experiments,
)
from ..simulation import export_csv as export_result_csv
from ..ui.pendulum_view import FrameData
from . import theme
from .controls import (
    ActionBar,
    ChoiceRow,
    FlowLayout,
    NumberRow,
    ProgressBar,
    Section,
    SwitchRow,
    label,
    push_button,
    scroll_area,
    tool_button,
)
from .dialogs import alert
from .plot_math import Curve
from .plot_view import PlotView, save_plot_data, slug
from .touch_view import TouchPendulumView

# bright on the dark theme (the desktop colours include dark blue-greys)
RUN_COLORS = ["#4FA3FF", "#FF6B6B", "#4CD07D", "#C792EA", "#FFA14A", "#7FDBCA", "#F78C6C", "#E0E0E0",
              "#FFCB6B", "#89DDFF"]
SPEEDS = [0.25, 0.5, 1.0, 2.0, 4.0]
WIDE_MIN_WIDTH = 900           # two plot columns from this width on
PLOTS = [  # key, title, unit
    ("angle", "Angle", "deg"),
    ("error", "Control error", "deg"),
    ("effort", "Actuator output", "N / N·m"),
    ("cart", "Cart position", "m"),
]


def run_color(index: int) -> str:
    return RUN_COLORS[index % len(RUN_COLORS)]


def status_text(r: ExperimentResult) -> str:
    return "diverged" if r.diverged else "fell" if r.metrics.fell else "stable"


def metric_pairs(r: ExperimentResult) -> list[tuple[str, str]]:
    """The desktop table's columns as (name, value) pairs."""
    m = r.metrics
    settle = "–" if m.settling_time is None else f"{m.settling_time:.3f} s"
    pairs = [("Settling", settle), ("Max dev", f"{m.max_deviation:.2f}°"), ("Overshoot", f"{m.overshoot_pct:.1f} %"),
             ("RMSE", f"{m.rmse:.3f}°"), ("u RMS", f"{m.control_rms:.3f}"), ("u peak", f"{m.control_peak:.3f}"),
             ("∫u² dt", f"{m.control_energy:.3f}")]
    if r.config.plant.has_cart:
        pairs.append(("Max |x|", f"{m.max_cart_displacement:.3f} m"))
    return pairs


# ------------------------------------------------------------------ worker
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


# ------------------------------------------------------------------ replay
class ReplayView(TouchPendulumView):
    """Pendulum view of a recorded run: one finger pans, pinch zooms, no pushes."""

    def one_down(self, pos: QPointF) -> None:
        self._pan = (QPointF(pos), self.camera.copy())

    def one_move(self, pos: QPointF) -> None:
        start = getattr(self, "_pan", None)
        if start is None:
            return
        d = pos - start[0]
        self.camera = start[1] + np.array([-d.x() / self.scale, d.y() / self.scale])
        self.update()

    def one_up(self, pos: QPointF) -> None:
        self._pan = None

    def one_cancel(self) -> None:
        self._pan = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.fit()


class Replay:
    """Frames of a recorded run (telemetry columns → model state → :class:`FrameData`)."""

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

    def frame(self, i: int) -> FrameData:
        n = self.n
        state = np.concatenate(([self.x[i]], self.th[i], [self.xd[i]], self.om[i]))
        plant = self.cfg.plant
        return FrameData(
            kin=self.model.kinematics(state), angles=self.th[i].copy(), velocities=self.om[i].copy(),
            cart_velocity=float(self.xd[i]), time=float(self.t[i]), applied=float(self.actuator[i]),
            max_output=plant.actuator.max_output, tip_force=float(self.tip[i]), has_cart=plant.has_cart,
            track_length=plant.track_length, lengths=self.model.lengths.copy())


class ReplayCard(QFrame):
    """Pendulum playback with play/pause, a time slider, speed chips and close."""

    timeChanged = Signal(float)     # replay time (for the plot markers)
    closed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        self.replay: Replay | None = None
        self.t = 0.0
        self.speed = 1.0
        self.playing = False
        self._last = 0.0
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        self.title = label("", "title")
        head.addWidget(self.title, 1)
        self.close_btn = tool_button("close", tip="Stop watching")
        head.addWidget(self.close_btn)
        v.addLayout(head)
        self.view = ReplayView()
        self.view.setMinimumHeight(240)
        v.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.play_btn = tool_button("pause", tip="Play / pause")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.time_label = QLabel("0.00 s")
        self.time_label.setMinimumWidth(80)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.play_btn)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time_label)
        v.addLayout(row)
        self.speed_row = ChoiceRow("", [(s, f"{s:g}×") for s in SPEEDS], 1.0)
        v.addWidget(self.speed_row)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.play_btn.clicked.connect(self.toggle)
        self.close_btn.clicked.connect(self.stop)
        self.slider.sliderMoved.connect(self._scrub)
        self.slider.sliderPressed.connect(self._pause_for_scrub)
        self.speed_row.changed.connect(self._set_speed)

    def watch(self, result: ExperimentResult, color: str) -> None:
        self.replay = Replay(result)
        self.title.setText(f"Watching: {result.name}")
        self.title.setStyleSheet(f"QLabel {{ color: {color}; }}")
        self.view.clear_trails()
        self.t = 0.0
        self._show(0)
        self.view.fit()
        self.play()

    def play(self) -> None:
        if self.replay is None:
            return
        if self.t >= self.replay.duration:
            self.t = 0.0
            self.view.clear_trails()
        self.playing = True
        self._last = time.perf_counter()
        self.play_btn.setIcon(_icon("pause"))
        self.timer.start()

    def pause(self) -> None:
        self.playing = False
        self.play_btn.setIcon(_icon("play"))
        self.timer.stop()

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def stop(self) -> None:
        self.pause()
        self.replay = None
        self.hide()
        self.closed.emit()

    def _set_speed(self, value) -> None:
        self.speed = float(value)

    def _pause_for_scrub(self) -> None:
        self.pause()

    def _scrub(self, pos: int) -> None:
        if self.replay is None:
            return
        self.t = self.replay.duration * pos / 1000.0
        self.view.clear_trails()
        self._show(self.replay.index(self.t))

    def _tick(self) -> None:
        if self.replay is None:
            self.pause()
            return
        now = time.perf_counter()
        self.t += min(now - self._last, 0.1) * self.speed
        self._last = now
        if self.t >= self.replay.duration:
            self.t = self.replay.duration
            self.pause()
        self._show(self.replay.index(self.t))

    def _show(self, i: int) -> None:
        r = self.replay
        if r is None or r.t.size == 0:
            return
        self.view.set_frame(r.frame(i))
        self.time_label.setText(f"{self.t:.2f} s")
        if r.duration > 0 and not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(round(1000 * self.t / r.duration)))
            self.slider.blockSignals(False)
        self.timeChanged.emit(self.t)


def _icon(name: str):
    from .icons import icon

    return icon(name)


# --------------------------------------------------------------- result card
class _Swatch(QWidget):
    def __init__(self, color: str) -> None:
        super().__init__()
        self.color = color
        self.setFixedSize(QSize(14, 32))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(self.rect(), 4, 4)
        p.end()


class ResultCard(QFrame):
    """One run: colour, name, status, metrics, gains and its actions."""

    def __init__(self, result: ExperimentResult, color: str) -> None:
        super().__init__()
        self.result = result
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(_Swatch(color))
        name = label(result.name, "title")
        head.addWidget(name, 1)
        status = status_text(result)
        chip = QLabel(status)
        chip.setStyleSheet(f"QLabel {{ color: {theme.DANGER if status != 'stable' else '#4CD07D'}; "
                           f"font-weight: 600; }}")
        head.addWidget(chip)
        v.addLayout(head)
        metrics = QWidget()
        flow = FlowLayout(metrics, spacing=14)
        for key, value in metric_pairs(result):
            m = QLabel(f"<span style='color:{theme.TEXT_2}'>{key}</span> {value}")
            flow.addWidget(m)
        v.addWidget(metrics)
        v.addWidget(label(result.gains_summary(), "secondary"))
        actions = QWidget()
        af = FlowLayout(actions, spacing=8)
        self.show_btn = push_button("Shown" if result.visible else "Hidden", "eye")
        self.show_btn.setCheckable(True)
        self.show_btn.setChecked(result.visible)
        self.show_btn.setProperty("chip", True)
        self.watch_btn = push_button("Watch", "play")
        self.gains_btn = push_button("Use gains", "controller")
        self.csv_btn = push_button("CSV", "table")
        self.remove_btn = push_button("Remove", "trash")
        for b in (self.show_btn, self.watch_btn, self.gains_btn, self.csv_btn, self.remove_btn):
            b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            af.addWidget(b)
        v.addWidget(actions)


# -------------------------------------------------------------------- page
class ExperimentsPage(QWidget):
    """The Experiments screen (see the module docstring)."""

    statusMessage = Signal(str)
    gainsRequested = Signal(object)          # ExperimentResult whose controller to use
    saveSetupRequested = Signal()            # full set-up file (handled by the window)

    def __init__(self, get_config: Callable[[], AppConfig],
                 open_plot: Callable[..., None] | None = None) -> None:
        super().__init__()
        self.get_config = get_config
        self.open_plot = open_plot
        self.results: list[ExperimentResult] = []
        self.worker: ExperimentWorker | None = None
        self._counter = 1
        self._segment = 0
        self._wide: bool | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(16, 12, 16, 24)
        self.body.setSpacing(12)
        self.area = scroll_area(inner)
        outer.addWidget(self.area, 1)

        # -------------------------------------------------------- experiment
        exp = Section("Experiment")
        exp.add(label("Run the current configuration, change gains on Controller, run again and compare. "
                      "Runs are deterministic: with 'Same start' every run begins from exactly the same "
                      "state, so only the controller differs.", "secondary"))
        name_row = QWidget()
        nr = QHBoxLayout(name_row)
        nr.setContentsMargins(0, 2, 0, 2)
        nr.addWidget(label("Name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Run 1, Run 2, …")
        nr.addWidget(self.name, 1)
        exp.add(name_row)
        self.duration = exp.add(NumberRow("Duration", 0.1, 600.0, 8.0, 1, "s", "log"))
        self.tolerance = exp.add(NumberRow("Settling band ±", 0.01, 45.0, 1.0, 2, "°", "log",
                                           hint="settling time = first time after which every segment "
                                                "stays inside the band"))
        self.reuse = exp.add(SwitchRow("Same start as the first run",
                                       "plant, initial state and noise of the first run", True))
        self.watch_after = exp.add(SwitchRow("Watch the pendulum",
                                             "play each finished run back at real speed (optional – "
                                             "runs are computed at full speed either way)", False))
        self.body.addWidget(exp)

        dist = Section("Scheduled disturbance", expanded=False)
        self.dist_on = dist.add(SwitchRow("Push the tip during the run", "", False))
        self.dist_time = dist.add(NumberRow("Start time", 0.0, 600.0, 2.0, 2, "s"))
        self.dist_force = dist.add(NumberRow("Force", -1000.0, 1000.0, 1.0, 3, "N", "symlog"))
        self.dist_dur = dist.add(NumberRow("Duration", 0.001, 10.0, 0.1, 3, "s", "log"))
        self.body.addWidget(dist)

        # ---------------------------------------------------------- replay
        self.replay = ReplayCard()
        self.body.addWidget(self.replay)
        self.replay.hide()

        # ------------------------------------------------------ comparison
        self.compare = Section("Comparison")
        self.segment_row = self.compare.add(ChoiceRow("Segment", [(i, f"θ{i + 1}") for i in range(3)], 0))
        self.empty_label = self.compare.add(label("Run an experiment to see its curves here. Tap a graph to "
                                                  "open it full-window (zoom, values).", "secondary"))
        grid_holder = QWidget()
        self.grid = QGridLayout(grid_holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.plots: dict[str, PlotView] = {}
        for key, title, unit in PLOTS:
            plot = PlotView(grid_holder, interactive=False, compact=True)
            plot.title, plot.y_label = title, unit
            plot.setMinimumHeight(200)
            plot.clicked.connect(lambda k=key: self._open_plot(k))
            self.plots[key] = plot
        self.compare.add(grid_holder)
        self.body.addWidget(self.compare)

        # --------------------------------------------------------- results
        self.results_section = Section("Results")
        self.results_holder = QWidget()
        self.results_layout = QVBoxLayout(self.results_holder)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(8)
        self.results_section.add(self.results_holder)
        self.clear_btn = self.results_section.add(push_button("Remove all runs", "trash"))
        self.body.addWidget(self.results_section)

        # ----------------------------------------------------------- files
        files = Section("Files", expanded=False)
        files.add(label("Each is saved on its own: the set-up (what to simulate) never contains recorded "
                        "data, graphs and results never contain the app settings.", "secondary"))
        self.setup_btn = files.add(push_button("Save full set-up (JSON)…", "save"))
        files.add(label("System, controller, simulation and experiment settings plus the app's graph and "
                        "view settings – opens with Settings → Open (the desktop version reads it too).",
                        "secondary"))
        self.save_btn = files.add(push_button("Save results (JSON)…", "save"))
        self.load_btn = files.add(push_button("Load results (JSON)…", "open"))
        self.image_btn = files.add(push_button("Save comparison graphs as image (PNG)…", "image"))
        self.data_btn = files.add(push_button("Save comparison graph data (CSV)…", "table"))
        self.body.addWidget(files)
        self.body.addStretch(1)

        # ------------------------------------------------------ action bar
        self.progress = ProgressBar(1000, show_text=True, height=30)
        # short texts: the bar must fit a 360 dp phone
        self.run_btn = push_button("Run", "play", primary=True)
        self.cancel_btn = push_button("Cancel", "close")
        self.cancel_btn.setEnabled(False)
        self.bar = ActionBar([(self.progress, 2), (self.cancel_btn, 1), (self.run_btn, 2)])
        outer.addWidget(self.bar)

        self.poll = QTimer(self)
        self.poll.setInterval(50)
        self.poll.timeout.connect(self._poll)
        self.run_btn.clicked.connect(self.run)
        self.cancel_btn.clicked.connect(self.cancel)
        self.clear_btn.clicked.connect(self.clear)
        self.setup_btn.clicked.connect(self.saveSetupRequested)
        self.save_btn.clicked.connect(self.save)
        self.load_btn.clicked.connect(self.load)
        self.image_btn.clicked.connect(self.save_image)
        self.data_btn.clicked.connect(self.save_data)
        self.segment_row.changed.connect(self._set_segment)
        self.tolerance.valueChanged.connect(lambda *_: self.redraw())
        self.replay.timeChanged.connect(self._on_replay_time)
        self.replay.closed.connect(lambda: self._on_replay_time(None))
        self.set_wide(False)
        self._rebuild()

    # ----------------------------------------------------------- settings
    def set_settings(self, e: ExperimentSettings) -> None:
        for w, v in ((self.duration, e.duration), (self.tolerance, e.settle_tolerance_deg),
                     (self.dist_time, e.disturbance.time), (self.dist_force, e.disturbance.force),
                     (self.dist_dur, e.disturbance.duration)):
            w.setValue(v)
        self.dist_on.setChecked(e.disturbance.enabled)

    def settings(self) -> ExperimentSettings:
        return ExperimentSettings(
            duration=self.duration.value(), settle_tolerance_deg=self.tolerance.value(),
            disturbance=DisturbanceSettings(enabled=self.dist_on.isChecked(), time=self.dist_time.value(),
                                            force=self.dist_force.value(), duration=self.dist_dur.value()))

    def set_drawing(self, gpu: bool, samples: int = 0) -> None:
        """Same drawing method for the replay view as for the main pendulum view."""
        self.replay.view.set_gpu(gpu, samples)

    def set_view_settings(self, settings) -> None:
        from dataclasses import replace

        self.replay.view.settings = replace(settings, follow_cart=True)
        self.replay.view.update()

    # ---------------------------------------------------------------- run
    def build_config(self) -> AppConfig:
        cfg = self.get_config().copy()
        cfg.experiment = self.settings()
        if self.reuse.isChecked() and self.results:
            from ..ui.controller_panel import ControllerPanel

            base = self.results[0].config
            cfg.plant = base.copy().plant
            cfg.simulation = base.copy().simulation
            cfg.controller = ControllerPanel.complete_loops(cfg.controller, cfg.plant)
        return cfg

    @property
    def running(self) -> bool:
        return self.worker is not None

    def run(self) -> None:
        if self.worker is not None:
            return
        try:
            cfg = self.build_config()
            cfg.validate()
        except ValueError as exc:
            alert(self, "Invalid configuration", str(exc))
            return
        name = self.name.text().strip() or f"Run {self._counter}"
        self._counter += 1
        self.worker = ExperimentWorker(cfg, name)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.statusMessage.emit(f"Running experiment '{name}'…")
        self.worker.start()
        self.poll.start()

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    def _poll(self) -> None:
        w = self.worker
        if w is None:
            self.poll.stop()
            return
        self.progress.setValue(int(w.progress * 1000))
        if not w.done():
            return
        self.poll.stop()
        self.worker = None
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if w.error is not None:
            alert(self, "Experiment failed", w.error)
            return
        r = w.result
        if r is None:
            return
        if r.message == "cancelled":
            self.statusMessage.emit(f"Experiment '{r.name}' cancelled")
            self.progress.setValue(0)
            return
        self.progress.setValue(1000)
        self.results.append(r)
        self.name.clear()
        self._rebuild()
        self.statusMessage.emit(f"Experiment '{r.name}' finished ({status_text(r)})")
        if self.watch_after.isChecked():
            self.watch(len(self.results) - 1)

    def shutdown(self) -> None:
        """Stop a running experiment (window closing)."""
        self.replay.pause()
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5.0)

    # -------------------------------------------------------------- watch
    def watch(self, index: int) -> None:
        if not 0 <= index < len(self.results):
            return
        self.replay.show()
        self.replay.watch(self.results[index], run_color(index))
        QTimer.singleShot(0, lambda: self.area.ensureWidgetVisible(self.replay, 0, 0))

    def _on_replay_time(self, t: float | None) -> None:
        for plot in self.plots.values():
            plot.set_marker(t)

    # ------------------------------------------------------------ results
    def selected_result(self) -> ExperimentResult | None:
        """Desktop API: the most recent run (the window's 'Load gains')."""
        return self.results[-1] if self.results else None

    def _rebuild(self) -> None:
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for i, r in enumerate(self.results):
            c = ResultCard(r, run_color(i))
            self.results_layout.addWidget(c)
            c.show_btn.toggled.connect(lambda on, k=i, b=c.show_btn: self._set_visible(k, on, b))
            c.watch_btn.clicked.connect(lambda _=False, k=i: self.watch(k))
            c.gains_btn.clicked.connect(lambda _=False, k=i: self.gainsRequested.emit(self.results[k]))
            c.csv_btn.clicked.connect(lambda _=False, k=i: self.export_csv(k))
            c.remove_btn.clicked.connect(lambda _=False, k=i: self.remove(k))
        n = len(self.results)
        self.results_section.set_title(f"Results ({n})" if n else "Results")
        self.clear_btn.setVisible(n > 0)
        nseg = max((r.n_segments for r in self.results), default=1)
        for i, b in self.segment_row.buttons.items():
            b.setVisible(i < nseg)
        self.segment_row.setVisible(nseg > 1)
        if self._segment >= nseg:
            self._segment = 0
            self.segment_row.setValue(0)
        self.redraw()

    def _set_visible(self, index: int, on: bool, button) -> None:
        if index < len(self.results):
            self.results[index].visible = on
            button.setText("Shown" if on else "Hidden")
            self.redraw()

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.results):
            if self.replay.replay is not None and self.replay.replay.result is self.results[index]:
                self.replay.stop()
            del self.results[index]
            self._rebuild()

    def clear(self) -> None:
        self.replay.stop()
        self.results.clear()
        self._counter = 1
        self._rebuild()

    # -------------------------------------------------------------- plots
    def _set_segment(self, value) -> None:
        self._segment = int(value)
        self.redraw()

    def curves(self, key: str) -> list[Curve]:
        seg = self._segment
        out = []
        for i, r in enumerate(self.results):
            if not r.visible:
                continue
            t = np.asarray(r.data["t"], dtype=float)
            if key == "angle" and seg < r.n_segments:
                y = r.angle_deg(seg)
            elif key == "error" and seg < r.n_segments:
                y = r.error_deg(seg)
            elif key == "effort":
                y = np.asarray(r.data["actuator"], dtype=float)
            elif key == "cart" and r.config.plant.has_cart:
                y = np.asarray(r.data["x"], dtype=float)
            else:
                continue
            out.append(Curve(t, np.asarray(y, dtype=float), run_color(i), r.name))
        return out

    def redraw(self) -> None:
        seg = self._segment
        tol = self.tolerance.value()
        has_cart = any(r.config.plant.has_cart for r in self.results)
        for key, title, unit in PLOTS:
            plot = self.plots[key]
            suffix = f" θ{seg + 1}" if key in ("angle", "error") else ""
            plot.hlines = [tol, -tol] if key == "error" else []
            plot.set_curves(self.curves(key), title=title + suffix, y_label=unit)
            plot.setVisible(bool(self.results) and (key != "cart" or has_cart))
        self.empty_label.setVisible(not self.results)

    def _open_plot(self, key: str) -> None:
        plot = self.plots[key]
        if self.open_plot is not None and plot.curves:
            self.open_plot(plot.title, plot.y_label, plot.curves, plot.hlines, plot.marker_x)

    def set_wide(self, wide: bool) -> None:
        """Two plot columns on wide screens, one otherwise."""
        if wide == self._wide:
            return
        self._wide = wide
        for plot in self.plots.values():
            self.grid.removeWidget(plot)
        cols = 2 if wide else 1
        for i, (key, _t, _u) in enumerate(PLOTS):
            self.grid.addWidget(self.plots[key], i // cols, i % cols)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.set_wide(self.width() >= WIDE_MIN_WIDTH)
        # the pendulum of a replay gets about half of the screen's height
        self.replay.view.setMinimumHeight(max(240, int(0.5 * self.area.viewport().height())))

    # -------------------------------------------------------------- files
    def _file_io(self):
        from ..ui import file_io

        return file_io

    def save(self) -> None:
        if not self.results:
            self.statusMessage.emit("No results to save yet")
            return
        fio = self._file_io()
        path = fio.save_file_name(self, "Save experiment results", "experiments.json", "JSON (*.json)", ".json")
        if not path:
            return
        try:
            fio.write_with(path, lambda local: save_experiments(self.results, local))
        except OSError as exc:
            alert(self, "Save failed", str(exc))
            return
        self.statusMessage.emit(f"Saved {len(self.results)} runs to {fio.display_name(path)}")

    def load(self) -> None:
        fio = self._file_io()
        path = fio.open_file_name(self, "Load experiment results", "", "JSON (*.json)")
        if not path:
            return
        try:
            loaded = fio.read_with(path, load_experiments)
        except (OSError, ValueError) as exc:
            alert(self, "Load failed", str(exc))
            return
        self.results.extend(loaded)
        self._rebuild()
        self.statusMessage.emit(f"Loaded {len(loaded)} runs")

    def export_csv(self, index: int) -> None:
        if not 0 <= index < len(self.results):
            return
        r = self.results[index]
        fio = self._file_io()
        path = fio.save_file_name(self, "Export time series", f"{slug(r.name)}.csv", "CSV (*.csv)", ".csv")
        if not path:
            return
        try:
            fio.write_with(path, lambda local: export_result_csv(r, local))
        except OSError as exc:
            alert(self, "Export failed", str(exc))
            return
        self.statusMessage.emit(f"Time series of '{r.name}' saved to {fio.display_name(path)}")

    def comparison_image(self):
        """The visible comparison plots stacked into one image (what 'Save … image' writes)."""
        from PySide6.QtGui import QImage

        shown = [p for key, p in self.plots.items() if not p.isHidden()]
        dpr = self.devicePixelRatioF()
        width = max(900, max((p.width() for p in shown), default=900))
        height = 320
        img = QImage(int(width * dpr), int(height * len(shown) * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(QColor(theme.SURFACE))
        painter = QPainter(img)
        for i, plot in enumerate(shown):
            tmp = PlotView(interactive=False)              # off-screen copy at the export size
            tmp.curves, tmp.title, tmp.y_label, tmp.hlines = plot.curves, plot.title, plot.y_label, plot.hlines
            tmp.resize(width, height)
            painter.drawPixmap(0, i * height, tmp.grab())
            tmp.deleteLater()
        painter.end()
        return img

    def save_image(self) -> None:
        if not self.results:
            self.statusMessage.emit("Run an experiment first")
            return
        fio = self._file_io()
        path = fio.save_file_name(self, "Save comparison graphs", "experiment_graphs.png", "PNG image (*.png)",
                                  ".png")
        if not path:
            return
        image = self.comparison_image()

        def write(local) -> None:
            if not image.save(str(local), "PNG"):
                raise OSError("could not encode the image")
        try:
            fio.write_with(path, write)
        except OSError as exc:
            alert(self, "Save failed", str(exc))
            return
        self.statusMessage.emit(f"Graphs saved to {fio.display_name(path)}")

    def comparison_curves(self) -> list[Curve]:
        """Every visible run's curves of the four plots, labelled 'run · signal'."""
        out = []
        for key, title, unit in PLOTS:
            for c in self.curves(key):
                out.append(Curve(c.x, c.y, c.color, f"{c.label} · {title} [{unit}]"))
        return out

    def save_data(self) -> None:
        curves = self.comparison_curves()
        if not curves:
            self.statusMessage.emit("Run an experiment first")
            return
        try:
            name = save_plot_data(self, curves, "experiment_graphs")
        except (OSError, ValueError) as exc:
            alert(self, "Save failed", str(exc))
            return
        if name:
            self.statusMessage.emit(f"Graph data saved to {name}")
