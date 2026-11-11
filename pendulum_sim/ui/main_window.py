"""Main application window: wires the simulator to the view, panels and graphs.

The window is assembled by small builder methods (``_create_view``, ``_create_widgets``,
``_build_layout``, ``_create_actions``, ``_build_toolbar_and_menu``, ``_build_status_bar``)
so that the touch interface (:mod:`pendulum_sim.mobile.window`) can subclass it and
replace only the layout and input handling while reusing every handler below.
"""

from __future__ import annotations

import copy
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QToolBar,
)

from ..control import ControllerConfig, lqr_controller
from ..physics import PlantParams
from ..simulation import (
    SCENARIOS,
    AppConfig,
    SimulationDiverged,
    SimulationSettings,
    Simulator,
    load_config,
    save_config,
    scenario,
)
from ..session import IncompatibleRun, rules, texts
from ..session.rules import SPEEDS
from ..simulation.realtime import RealtimeStepper
from . import file_io
from .controller_panel import ControllerPanel
from .pendulum_view import FrameData, PendulumView
from .plant_panel import PlantPanel
from .settings_panel import SettingsPanel
from .widgets import scrollable, spin

FRAME_INTERVAL_MS = 16            # ~60 fps GUI refresh
PHYSICS_BUDGET_S = 0.010          # max physics compute per frame (keeps the GUI responsive)
PLOT_INTERVAL_S = 0.066           # ~15 Hz graph refresh (raised automatically if frames get slow)
MAX_PLOT_INTERVAL_S = 0.3
TARGET_FRAME_S = 0.020            # if frames take longer than this, graphs refresh less often


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.resize(1500, 920)           # adjusted to the screen in fit_to_screen()
        self.config_path: Path | str | None = None   # str: Android content:// URI
        self.running = False
        self.stepper = RealtimeStepper(budget_s=PHYSICS_BUDGET_S)   # fixed-step pacing (Qt-free)
        self._last_frame = time.perf_counter()
        self._last_plot = 0.0
        self.plot_interval_min = PLOT_INTERVAL_S     # graph refresh limits (adaptive in between)
        self.plot_interval_max = MAX_PLOT_INTERVAL_S
        self.target_frame_s = TARGET_FRAME_S
        self._plot_interval = PLOT_INTERVAL_S
        self._frame_period = FRAME_INTERVAL_MS / 1000.0
        self.speed = 1.0

        self._create_widgets()
        self._build_layout()
        self._create_actions()
        self._build_toolbar_and_menu()
        self._build_status_bar()
        self._connect_signals()

        self.load(config)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._on_frame)
        self.timer.start(FRAME_INTERVAL_MS)

    # ------------------------------------------------------------- builders
    def _create_view(self) -> PendulumView:
        return PendulumView()

    def _create_telemetry(self):
        """Live graphs; must offer configure / set_output_limit / refresh like TelemetryPanel."""
        from .telemetry_panel import TelemetryPanel   # pyqtgraph: imported when first needed

        return TelemetryPanel()

    def _create_widgets(self) -> None:
        """Create the view, graphs, experiment page and configuration panels (no layout)."""
        self.view = self._create_view()
        self.telemetry = self._create_telemetry()
        from .experiment_panel import ExperimentPanel  # pyqtgraph: imported when first needed

        self.experiments = ExperimentPanel(lambda: self.config)
        self.plant_panel = PlantPanel()
        self.controller_panel = ControllerPanel()
        self.settings_panel = SettingsPanel()

    def _build_layout(self) -> None:
        live = QSplitter(Qt.Orientation.Vertical)
        live.addWidget(self.view)
        live.addWidget(self.telemetry)
        live.setSizes([520, 400])
        self.tabs = QTabWidget()
        self.tabs.addTab(live, "Live simulation")
        self.tabs.addTab(self.experiments, "Experiments && comparison")
        self.setCentralWidget(self.tabs)

        side = QTabWidget()
        side.addTab(scrollable(self.plant_panel), "Pendulum")
        side.addTab(scrollable(self.controller_panel), "PID controller")
        side.addTab(scrollable(self.settings_panel), "Simulation && view")
        dock = QDockWidget("Configuration", self)
        dock.setObjectName("configuration")
        dock.setWidget(side)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        dock.setMinimumWidth(330)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _connect_signals(self) -> None:
        self.plant_panel.changed.connect(self._on_plant_changed)
        self.controller_panel.changed.connect(self._on_controller_changed)
        self.controller_panel.autoTuneRequested.connect(self._on_auto_tune)
        self.controller_panel.resetIntegratorsRequested.connect(self._on_reset_integrators)
        self.controller_panel.robustTuneRequested.connect(self._on_robust_tune)
        self.controller_panel.restoreRequested.connect(self._on_restore_gains)
        self.settings_panel.simulationChanged.connect(self._on_simulation_settings)
        self.settings_panel.viewChanged.connect(self._on_view_settings)
        self.settings_panel.zoomChanged.connect(self.view.set_zoom)
        self.settings_panel.fitRequested.connect(self.view.fit)
        self.settings_panel.clearTrailsRequested.connect(self.view.clear_trails)
        self.view.zoomChanged.connect(self.settings_panel.set_zoom)
        self.experiments.statusMessage.connect(self.show_message)
        self.experiments.apply_btn.clicked.connect(self._apply_experiment_gains)

    # ------------------------------------------------------------- actions
    def _action(self, text: str, slot, shortcut: str | None = None, tip: str = "") -> QAction:
        act = QAction(text, self)
        act.triggered.connect(slot)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        if tip:
            act.setToolTip(f"{tip} ({shortcut})" if shortcut else tip)
        self.addAction(act)
        return act

    def _create_actions(self) -> None:
        """Actions and the speed / push-force / scenario controls (placed by the toolbar builder)."""
        self.act_start = self._action("▶ Start", self.start, "F5", "Start / resume the simulation")
        self.act_pause = self._action("⏸ Pause", self.pause, "F6", "Pause the simulation")
        self.act_toggle = self._action("Start/Pause", self.toggle_run, "Space")
        self.act_reset = self._action("⟲ Reset", self.reset, "Ctrl+R", "Return to the initial conditions (stopped)")
        self.act_restart = self._action("⟳ Restart", self.restart, "Ctrl+Shift+R",
                                        "Return to the initial conditions and run")
        self.act_step = self._action("Step", self.single_step, "F7", "Advance by one controller period")
        self.act_push_left = self._action("◀ Push", lambda: self.push(-1), "Ctrl+Left", "Push the tip towards −x")
        self.act_push_right = self._action("Push ▶", lambda: self.push(+1), "Ctrl+Right", "Push the tip towards +x")
        self.act_open = self._action("Open configuration…", self.open_config, "Ctrl+O")
        self.act_save = self._action("Save configuration", self.save_config, "Ctrl+S")
        self.act_save_as = self._action("Save configuration as…", self.save_config_as, "Ctrl+Shift+S")
        self.act_quit = self._action("Quit", self.close, "Ctrl+Q")
        self.act_fit = self._action("Fit view", self.view.fit, "Ctrl+0")
        self.act_about = QAction("About", self)
        self.act_about.triggered.connect(self._about)

        self.speed_combo = QComboBox()
        for s in SPEEDS:
            self.speed_combo.addItem(f"{s:g}×", s)
        self.speed_combo.setCurrentIndex(SPEEDS.index(1.0))
        self.speed_combo.currentIndexChanged.connect(lambda _: setattr(self, "speed", self.speed_combo.currentData()))
        self.push_force = spin(0.01, 1000.0, 1.0, 2, 0.5, "N", "Tip push force (0.1 s pulse)")
        self.push_force.setMinimumWidth(90)
        self.push_force.valueChanged.connect(self._on_push_force_changed)
        self.scenario_combo = QComboBox()
        self.scenario_combo.addItem("— choose a demonstration —", None)
        for key, factory in SCENARIOS.items():
            self.scenario_combo.addItem(factory().name, key)
        self.scenario_combo.activated.connect(self._on_scenario_chosen)

    def _build_toolbar_and_menu(self) -> None:
        tb = QToolBar("Simulation")
        tb.setObjectName("simulation_toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)
        for a in (self.act_start, self.act_pause, self.act_reset, self.act_restart, self.act_step):
            tb.addAction(a)
        tb.addSeparator()
        tb.addWidget(QLabel(" Speed "))
        tb.addWidget(self.speed_combo)
        tb.addSeparator()
        tb.addAction(self.act_push_left)
        tb.addWidget(self.push_force)
        tb.addAction(self.act_push_right)
        tb.addSeparator()
        tb.addWidget(QLabel(" Scenario "))
        tb.addWidget(self.scenario_combo)

        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        scen_menu = file_menu.addMenu("New from scenario")
        for key, factory in SCENARIOS.items():
            act = QAction(factory().name, self)
            act.triggered.connect(lambda _=False, k=key: self.load(scenario(k)))
            scen_menu.addAction(act)
        for a in (self.act_open, self.act_save, self.act_save_as):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)
        sim_menu = menu.addMenu("&Simulation")
        for a in (self.act_start, self.act_pause, self.act_toggle, self.act_reset, self.act_restart,
                  self.act_step, self.act_push_left, self.act_push_right):
            sim_menu.addAction(a)
        view_menu = menu.addMenu("&View")
        view_menu.addAction(self.act_fit)
        help_menu = menu.addMenu("&Help")
        help_menu.addAction(self.act_about)

    def _build_status_bar(self) -> None:
        self.status_state = QLabel()
        self.status_time = QLabel()
        self.status_rtf = QLabel()
        self.status_energy = QLabel()
        self.status_perf = QLabel()
        self.status_perf.setToolTip("Display frame rate and physics compute time per frame")
        sb = self.statusBar()
        for w in (self.status_state, self.status_time, self.status_rtf, self.status_energy, self.status_perf):
            w.setMinimumWidth(120)
            sb.addPermanentWidget(w)

    def show_message(self, text: str, timeout_ms: int = 6000) -> None:
        self.statusBar().showMessage(text, timeout_ms)

    # --------------------------------------------------------- loading
    def load(self, config: AppConfig) -> None:
        """Replace the whole configuration (scenario, file)."""
        self.running = False
        try:
            rules.prepare_config(config)
            self.sim = Simulator(config)
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))
            return
        self.config = config
        self._original_controller = copy.deepcopy(config.controller)
        self._show_config(config)
        self._configure_telemetry()
        self.view.clear_trails()
        self._update_view()
        QTimer.singleShot(0, self.view.fit)
        self.setWindowTitle(f"Inverted Pendulum Simulator — {config.name}")
        if config.description:
            self.show_message(config.description, 15000)
        self._update_actions()

    # ------------------------------------------- UI <-> configuration hooks
    # The desktop keeps the editable state in its panels; the touch interface edits the
    # configuration directly and overrides these four methods.
    def _show_config(self, config: AppConfig) -> None:
        """Put a freshly loaded configuration into the editors."""
        self.push_force.blockSignals(True)
        self.push_force.setValue(config.push_force)
        self.push_force.blockSignals(False)
        self.plant_panel.set_plant(config.plant)
        self.controller_panel.set_config(config.controller)
        self.settings_panel.set_simulation(config.simulation)
        self.experiments.set_settings(config.experiment)

    def _ui_controller(self) -> ControllerConfig:
        """The controller configuration as currently edited."""
        return self.controller_panel.config()

    def _show_controller(self, ctrl: ControllerConfig) -> None:
        self.controller_panel.set_config(ctrl)

    def _show_controller_output(self, out) -> None:
        self.controller_panel.show_output(out)

    def _configure_telemetry(self) -> None:
        labels = rules.loop_labels(self.config.controller)
        self.telemetry.configure(self.config.plant.n_segments, self.config.plant.has_cart, labels)
        self.telemetry.set_output_limit(rules.output_limit(self.config))
        self.telemetry.refresh(self.sim.telemetry, force=True)

    def _on_scenario_chosen(self, index: int) -> None:
        key = self.scenario_combo.itemData(index)
        if key:
            self.load(scenario(key))
        self.scenario_combo.setCurrentIndex(0)

    def open_config(self) -> None:
        start = str(Path.cwd() / "configs")
        path = file_io.open_file_name(self, "Open configuration", start, "Configuration (*.json)")
        if not path:
            return
        try:
            cfg = file_io.read_with(path, load_config)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not open configuration", str(exc))
            return
        self.load(cfg)
        self.config_path = path

    def save_config(self) -> None:
        if self.config_path is None:
            self.save_config_as()
            return
        self._write_config(self.config_path)

    def save_config_as(self) -> None:
        path = file_io.save_file_name(self, "Save configuration", "my_configuration.json",
                                      "Configuration (*.json)", ".json")
        if path:
            self._write_config(path)

    def _write_config(self, path) -> None:
        self.config.experiment = self.experiments.settings()
        try:
            file_io.write_with(path, lambda local: save_config(self.config, local))
        except OSError as exc:
            QMessageBox.critical(self, "Could not save configuration", str(exc))
            return
        self.config_path = path
        self.show_message(f"Configuration saved to {file_io.display_name(path)}")

    # ---------------------------------------------------- run control
    def start(self) -> None:
        self.running = True
        self.stepper.reset_clock()
        self._last_frame = time.perf_counter()
        self._update_actions()

    def pause(self) -> None:
        self.running = False
        self._update_actions()

    def toggle_run(self) -> None:
        self.pause() if self.running else self.start()

    def reset(self) -> None:
        self.running = False
        self._reset_sim()
        self._update_actions()

    def restart(self) -> None:
        self._reset_sim()
        self.start()

    def single_step(self) -> None:
        self.pause()
        steps = rules.single_step_count(self.sim.controller.period, self.config.simulation.dt)
        self._run_steps(steps, deadline=None)
        self._update_view()
        self.telemetry.refresh(self.sim.telemetry)

    def push(self, direction: int) -> None:
        self.sim.apply_disturbance(direction * self.push_force.value(), 0.1)
        self.show_message(f"Tip push {direction * self.push_force.value():+.2f} N for 0.1 s", 2000)

    def _reset_sim(self) -> None:
        try:
            self.sim.reset()
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))
        self.view.clear_trails()
        self._configure_telemetry()
        self._update_view()

    def _update_actions(self) -> None:
        paused = (not self.running) and self.sim.time > 0
        self.act_start.setText("▶ Resume" if paused else "▶ Start")
        self.act_start.setEnabled(not self.running)
        self.act_pause.setEnabled(self.running)
        state = "Running" if self.running else "Paused" if paused else "Stopped (initial state)"
        self.status_state.setText(state)

    # ------------------------------------------------- parameter edits
    def _on_plant_changed(self, plant: PlantParams, structural: bool) -> None:
        try:
            plant.validate()
        except ValueError as exc:
            self.show_message(f"⚠ {exc}")
            return
        if structural:
            ctrl = rules.controller_for_structure(self._ui_controller(), plant, self.config.plant)
            self.config.plant = plant
            self.config.controller = ctrl
            self._show_controller(ctrl)
            self.sim.update_controller(ctrl)
            self._reset_sim()
            QTimer.singleShot(0, self.view.fit)
            self.show_message(texts.STRUCTURE_CHANGED, 10000)
            return
        initial_changed = rules.initial_conditions_changed(plant, self.config.plant)
        self.sim.update_plant(plant)
        if initial_changed and not self.running and self.sim.time == 0.0:
            self._reset_sim()   # show the new initial pose right away
        self._update_view()

    def _on_controller_changed(self, controller: ControllerConfig) -> None:
        try:
            self.sim.update_controller(controller)
        except ValueError as exc:
            self.show_message(f"⚠ {exc}")
            return
        self._configure_telemetry()

    def _on_auto_tune(self, weights) -> None:
        try:
            ctrl = lqr_controller(self.config.plant, weights, base=self._ui_controller())
        except (ValueError, np.linalg.LinAlgError) as exc:
            QMessageBox.warning(self, "LQR design failed",
                                f"The LQR design could not be computed for this plant:\n{exc}")
            return
        self._show_controller(ctrl)
        self.sim.update_controller(ctrl)
        self._configure_telemetry()
        self.show_message(texts.LQR_SEEDED)

    def _apply_controller(self, ctrl: ControllerConfig, message: str) -> None:
        self._show_controller(ctrl)
        self.sim.update_controller(ctrl)
        self._configure_telemetry()
        self.show_message(message)

    def _on_robust_tune(self) -> None:
        was_running = self.running
        self.pause()                       # give the tuner all the CPU
        self.config.controller = self._ui_controller()
        from .tuning_dialog import RobustTuneDialog

        dialog = RobustTuneDialog(self.config, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        result = dialog.result_value
        if accepted and result is not None:
            self._apply_tuning_result(result)
        if was_running:
            self.start()

    def _apply_tuning_result(self, result) -> None:
        """Use the gains of a :class:`TuningResult` and its recommended test push."""
        self._apply_controller(result.controller, texts.tuning_applied(result))
        self.push_force.setValue(result.recommended_push)

    def _on_restore_gains(self) -> None:
        try:
            ctrl = rules.restored_controller(self._original_controller, self.config.plant)
        except ValueError as exc:
            self.show_message(f"⚠ {exc}")
            return
        self._apply_controller(ctrl, texts.GAINS_RESTORED)

    def _on_push_force_changed(self, value: float) -> None:
        self.config.push_force = value

    def _on_reset_integrators(self) -> None:
        self.sim.controller.reset_integrators()
        self.show_message("Integrators reset", 2000)

    def _on_simulation_settings(self, settings: SimulationSettings) -> None:
        try:
            settings.validate()
        except ValueError as exc:
            self.show_message(f"⚠ {exc}")
            return
        self.config.simulation = settings

    def _on_view_settings(self, settings) -> None:
        self.view.settings = settings
        self.view.update()

    def _apply_experiment_gains(self) -> None:
        result = self.experiments.selected_result()
        if result is None:
            QMessageBox.information(self, "Load gains", "Select an experiment in the table first.")
            return
        try:
            ctrl = rules.controller_from_run(result.config, self.config.plant)
        except IncompatibleRun:
            QMessageBox.warning(self, "Load gains", "That experiment used a different pendulum structure.")
            return
        self._show_controller(ctrl)
        self._on_controller_changed(ctrl)
        self.show_message(f"Controller gains loaded from '{result.name}'")

    # ------------------------------------------------------------ frame
    def _run_steps(self, n: int, deadline: float | None) -> int:
        done = 0
        try:
            while done < n:
                k = min(25, n - done)
                for _ in range(k):
                    self.sim.step()
                done += k
                if deadline is not None and time.perf_counter() > deadline:
                    break
        except SimulationDiverged as exc:
            self._on_diverged(exc)
        return done

    def _on_diverged(self, exc: SimulationDiverged) -> None:
        self.pause()
        QMessageBox.warning(self, "Simulation diverged", f"{exc}\n\nThe simulation was paused. "
                            "Reset it, or reduce the time step / gains.")

    def _on_frame(self) -> None:
        now = time.perf_counter()
        elapsed = now - self._last_frame
        self._last_frame = now
        self._frame_period = 0.9 * self._frame_period + 0.1 * elapsed
        if self.running:
            # Whole fixed-dt steps for the elapsed wall time; backlog dropped beyond the budget.
            try:
                self.stepper.tick(self.sim, elapsed, self.speed)
            except SimulationDiverged as exc:
                self._on_diverged(exc)
        self._update_view()
        # Graphs are the most expensive thing to paint; refresh them less often when frames are slow
        # so the pendulum animation and the physics stay smooth.
        if self._frame_period > self.target_frame_s:
            self._plot_interval = min(self.plot_interval_max, self._plot_interval * 1.05)
        else:
            self._plot_interval = max(self.plot_interval_min, self._plot_interval * 0.98)
        if now - self._last_plot > self._plot_interval:
            self._last_plot = now
            self.telemetry.refresh(self.sim.telemetry)
            self._show_controller_output(self.sim.last_control)
            self._update_status()

    def _update_view(self) -> None:
        sim = self.sim
        frame = FrameData(
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
        )
        self.view.set_frame(frame)

    def _update_status(self) -> None:
        self.status_time.setText(f"t = {self.sim.time:.2f} s")
        if self.running:
            rtf = self.stepper.realtime_factor
            lag = " (slower than real time)" if rtf < 0.95 * self.speed else ""
            self.status_rtf.setText(f"speed {rtf:.2f}×{lag}")
        else:
            self.status_rtf.setText("")
        self.status_energy.setText(f"E = {self.sim.energy():.4f} J")
        fps = 1.0 / max(self._frame_period, 1e-3)
        self.status_perf.setText(f"{fps:.0f} fps · physics {self.stepper.physics_ms:.1f} ms")
        self._update_actions()

    def fit_to_screen(self) -> None:
        """Size and centre the window inside the available area of its screen."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        width = min(1500, int(avail.width() * 0.96))
        height = min(940, int(avail.height() * 0.94))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        self.move(frame.topLeft())
        if avail.width() < 1100 or avail.height() < 700:
            self.showMaximized()

    # ------------------------------------------------------------- misc
    def _about(self) -> None:
        QMessageBox.about(self, "About", "<b>Interactive Inverted Pendulum Simulator</b><br>"
                          "Multibody 1–3 segment pendulum on a cart or fixed pivot, multi-loop PID "
                          "control with LQR seeding, experiment comparison.<br><br>"
                          "See README.md, RUNNING.md and docs/ in the repository.")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.timer.stop()
        self.experiments.shutdown()
        super().closeEvent(event)

