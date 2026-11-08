"""Mobile (touch-first) main window.

Phase 1 of the Android port: a deliberately small shell that proves the full
stack on a device – the shared simulator and controller, the unchanged
QPainter pendulum view, a live pyqtgraph telemetry plot, touch disturbances,
LQR seeding (NumPy Riccati solver when SciPy is absent) and correct pausing
when the app goes to the background. The full parameter UI follows once the
APK is confirmed on real hardware.

Layout: control bar at the bottom (thumb reach), pendulum view above it and
the graphs either below the view (portrait) or beside it (landscape).
"""

from __future__ import annotations

import copy
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..control import lqr_controller
from ..simulation import SCENARIOS, AppConfig, SimulationDiverged, Simulator, scenario
from ..simulation.realtime import RealtimeStepper
from ..ui.controller_panel import ControllerPanel
from ..ui.pendulum_view import FrameData
from ..ui.telemetry_panel import TelemetryPanel
from .touch_view import TouchPendulumView

FRAME_INTERVAL_MS = 16          # ~60 fps
PLOT_INTERVAL_S = 0.1           # graphs are the expensive part of a frame on phones
MOBILE_GRAPHS = {"angles", "control"}
SPEEDS = [0.25, 0.5, 1.0, 2.0]
DRAG_GAIN = 2.0                 # drag force = DRAG_GAIN · push force · (offset / total length)
DRAG_LIMIT = 4.0                # … clamped to ±DRAG_LIMIT · push force
TOUCH_TARGET_PX = 48            # minimum button height (device-independent px ≈ dp)


def _button(text: str, slot, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setMinimumHeight(TOUCH_TARGET_PX)
    b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if tip:
        b.setToolTip(tip)
    b.clicked.connect(slot)
    return b


class MobileWindow(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.setWindowTitle("Inverted Pendulum Simulator")
        self.running = False
        self.speed = 1.0
        self.stepper = RealtimeStepper()
        self._last_frame = time.perf_counter()
        self._last_plot = 0.0
        self._frame_period = FRAME_INTERVAL_MS / 1000.0
        self._drag_offset = 0.0
        self._resume_on_activate = False

        # ------------------------------------------------------- top bar
        self.scenario_combo = QComboBox()
        self.scenario_combo.setMinimumHeight(TOUCH_TARGET_PX)
        for key, factory in SCENARIOS.items():
            self.scenario_combo.addItem(factory().name, key)
        self.scenario_combo.activated.connect(self._on_scenario_chosen)
        self.speed_combo = QComboBox()
        self.speed_combo.setMinimumHeight(TOUCH_TARGET_PX)
        for s in SPEEDS:
            self.speed_combo.addItem(f"{s:g}×", s)
        self.speed_combo.setCurrentIndex(SPEEDS.index(1.0))
        self.speed_combo.currentIndexChanged.connect(
            lambda _: setattr(self, "speed", self.speed_combo.currentData()))
        self.perf = QLabel()             # fps / physics time (Android has no title bar)
        self.perf.setToolTip("Display frame rate · physics time per frame · speed vs real time")
        top = QHBoxLayout()
        top.addWidget(self.scenario_combo, 1)
        top.addWidget(self.speed_combo)

        self.status = QLabel()
        self.status.setWordWrap(True)

        # ------------------------------------------------- view + graphs
        self.view = TouchPendulumView()
        self.view.tapPush.connect(self.push)
        self.view.dragOffset.connect(self._on_drag)
        self.telemetry = TelemetryPanel()
        for key, act in self.telemetry.toggles.items():
            act.setChecked(key in MOBILE_GRAPHS)
        self.content = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.content.addWidget(self.view, 3)
        self.content.addWidget(self.telemetry, 2)

        # --------------------------------------------------- control bar
        self.run_btn = _button("▶ Start", self.toggle_run)
        self.reset_btn = _button("⟲ Reset", self.reset)
        self.step_btn = _button("Step", self.single_step, "Advance by one controller period")
        self.push_left_btn = _button("◀ Push", lambda: self.push(-1))
        self.push_right_btn = _button("Push ▶", lambda: self.push(+1))
        self.fit_btn = _button("Fit", self.view.fit, "Fit the pendulum into the view")
        self.lqr_btn = _button("LQR gains", self.seed_lqr, "Seed the PID gains from an LQR design")
        bar = QGridLayout()
        bar.setSpacing(6)
        for col, b in enumerate((self.run_btn, self.reset_btn, self.step_btn, self.lqr_btn)):
            bar.addWidget(b, 0, col)
        bar.addWidget(self.push_left_btn, 1, 0)
        bar.addWidget(self.fit_btn, 1, 1, 1, 2)
        bar.addWidget(self.push_right_btn, 1, 3)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        root.addLayout(top)
        root.addLayout(self.content, 1)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.perf)
        root.addLayout(status_row)
        root.addLayout(bar)

        self.load(config)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._on_frame)
        self.timer.start(FRAME_INTERVAL_MS)
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self.on_application_state)

    # ------------------------------------------------------------ loading
    def load(self, config: AppConfig) -> None:
        self.running = False
        try:
            config.controller = ControllerPanel.complete_loops(config.controller, config.plant)
            config.validate()
            sim = Simulator(config)
        except ValueError as exc:
            self.show_message(f"⚠ Invalid configuration: {exc}")
            return
        self.sim = sim
        self.config = config
        self._original_controller = copy.deepcopy(config.controller)
        idx = self.scenario_combo.findText(config.name)
        if idx >= 0:
            self.scenario_combo.setCurrentIndex(idx)
        self._configure_telemetry()
        self.view.clear_trails()
        self._update_view()
        QTimer.singleShot(0, self.view.fit)
        self.show_message(config.description or config.name)
        self._update_buttons()

    def _on_scenario_chosen(self, index: int) -> None:
        key = self.scenario_combo.itemData(index)
        if key:
            self.load(scenario(key))

    def _configure_telemetry(self) -> None:
        labels = ["cart" if loop.signal == "cart" else f"θ{loop.signal[5:]}"
                  for loop in self.config.controller.loops]
        self.telemetry.configure(self.config.plant.n_segments, self.config.plant.has_cart, labels)
        c = self.config.controller
        self.telemetry.set_output_limit(max(self.config.plant.actuator.max_output,
                                            abs(c.output_min), abs(c.output_max)))
        self.telemetry.refresh(self.sim.telemetry, force=True)

    # -------------------------------------------------------- run control
    def start(self) -> None:
        self.running = True
        self.stepper.reset_clock()
        self._last_frame = time.perf_counter()
        self._update_buttons()

    def pause(self) -> None:
        self.running = False
        self._update_buttons()

    def toggle_run(self) -> None:
        self.pause() if self.running else self.start()

    def reset(self) -> None:
        self.running = False
        self.sim.reset()
        self.view.clear_trails()
        self._configure_telemetry()
        self._update_view()
        self._update_buttons()

    def single_step(self) -> None:
        self.pause()
        steps = max(1, int(round(self.sim.controller.period / self.config.simulation.dt)))
        try:
            for _ in range(steps):
                self.sim.step()
        except SimulationDiverged as exc:
            self._diverged(exc)
        self._update_view()
        self.telemetry.refresh(self.sim.telemetry)

    def push(self, direction: int) -> None:
        force = direction * self.config.push_force
        self.sim.apply_disturbance(force, 0.1)
        self.show_message(f"Tip push {force:+.2f} N for 0.1 s")

    def _on_drag(self, offset: float) -> None:
        self._drag_offset = offset

    def drag_force(self) -> float:
        f = DRAG_GAIN * self.config.push_force * self._drag_offset
        limit = DRAG_LIMIT * self.config.push_force
        return float(np.clip(f, -limit, limit))

    def seed_lqr(self) -> None:
        try:
            ctrl = lqr_controller(self.config.plant, base=self.config.controller)
            self.sim.update_controller(ctrl)
        except (ValueError, np.linalg.LinAlgError) as exc:
            self.show_message(f"⚠ LQR design failed: {exc}")
            return
        self._configure_telemetry()
        self.show_message("Gains seeded from LQR. Reset to try them from the initial conditions.")

    def _diverged(self, exc: Exception) -> None:
        self.pause()
        self.show_message(f"⚠ {exc}. Paused – press Reset.")

    def _update_buttons(self) -> None:
        paused = (not self.running) and self.sim.time > 0
        self.run_btn.setText("⏸ Pause" if self.running else "▶ Resume" if paused else "▶ Start")

    def show_message(self, text: str) -> None:
        self.status.setText(text)

    # ---------------------------------------------------------- lifecycle
    def on_application_state(self, state: Qt.ApplicationState) -> None:
        """Pause in the background (no physics, no timer); resume cleanly on return."""
        if state == Qt.ApplicationState.ApplicationActive:
            self._last_frame = time.perf_counter()
            self.stepper.reset_clock()
            if not self.timer.isActive():
                self.timer.start(FRAME_INTERVAL_MS)
            if self._resume_on_activate:
                self._resume_on_activate = False
                self.start()
        else:
            if self.running:
                self._resume_on_activate = True
                self.pause()
            self._drag_offset = 0.0
            self.timer.stop()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.timer.stop()
        super().closeEvent(event)

    # ------------------------------------------------------------ layout
    def resizeEvent(self, event) -> None:  # noqa: N802
        landscape = self.width() > self.height()
        direction = QBoxLayout.Direction.LeftToRight if landscape else QBoxLayout.Direction.TopToBottom
        if self.content.direction() != direction:
            self.content.setDirection(direction)
            self.content.setStretch(0, 3 if not landscape else 1)
            self.content.setStretch(1, 2 if not landscape else 1)
            QTimer.singleShot(0, self.view.fit)
        super().resizeEvent(event)

    # ------------------------------------------------------------- frame
    def _on_frame(self) -> None:
        now = time.perf_counter()
        elapsed = now - self._last_frame
        self._last_frame = now
        self._frame_period = 0.9 * self._frame_period + 0.1 * elapsed
        if self.running:
            if self._drag_offset != 0.0:
                duration = max(min(elapsed, self.stepper.max_frame_s) * self.speed, self.config.simulation.dt)
                self.sim.apply_disturbance(self.drag_force(), duration)
            try:
                self.stepper.tick(self.sim, elapsed, self.speed)
            except SimulationDiverged as exc:
                self._diverged(exc)
        self._update_view()
        if now - self._last_plot > PLOT_INTERVAL_S:
            self._last_plot = now
            self.telemetry.refresh(self.sim.telemetry)
            fps = 1.0 / max(self._frame_period, 1e-3)
            self.perf.setText(f"{fps:.0f} fps · {self.stepper.physics_ms:.1f} ms · "
                              f"{self.stepper.realtime_factor:.2f}×" if self.running else f"{fps:.0f} fps")
            self._update_buttons()

    def _update_view(self) -> None:
        sim = self.sim
        self.view.set_frame(FrameData(
            kin=sim.model.kinematics(sim.state),
            angles=sim.angles.copy(),
            velocities=sim.angular_velocities.copy(),
            cart_velocity=float(sim.state[sim.model.n + 1]),
            time=sim.time,
            applied=sim.applied,
            max_output=self.config.plant.actuator.max_output,
            tip_force=sim.tip_force,
            has_cart=sim.model.has_cart,
            track_length=self.config.plant.track_length,
            lengths=sim.model.lengths.copy(),
        ))
