"""Touch-first main window – the desktop :class:`~pendulum_sim.ui.main_window.MainWindow`
with a different layout and touch input.

Everything the desktop window *does* is inherited: run control, scenarios,
configuration files, the Pendulum / PID controller / Simulation & view panels,
LQR seeding, robust auto-tuning, integrator reset, restoring gains, experiments
and their comparison plots, CSV/JSON export, divergence handling and graph-rate
adaptation. This class only overrides the *builders* and adds what a touch
device needs:

* layout – the live view and graphs (tab *Live*) and the experiment page (tab
  *Experiments*) share the screen with a collapsible configuration panel that sits
  to the right in landscape and below in portrait (``⚙`` toggles it);
* a touch control bar with large buttons instead of the tool bar, and a ``☰`` menu
  instead of the menu bar (open/save configuration, fit view, about…);
* tap / drag / pinch on the pendulum view (:mod:`.touch_view`);
* pausing in the background and resuming without a time jump;
* rendering-performance settings with an on-screen timing read-out
  (:mod:`.performance`).
"""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMenu,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..control import LQRWeights
from ..simulation import AppConfig
from ..ui.main_window import MainWindow
from ..ui.widgets import hint, scrollable
from . import cpu
from .autotune_screen import AutotuneScreen
from .graphs import GraphsPanel
from .performance import FRAME_RATES, GRAPH_RATES_HZ, FrameStats, PerformanceSettings, instrument_graphs
from .touch_view import TouchPendulumView

DRAG_GAIN = 2.0          # drag force = DRAG_GAIN · push force · (offset / total length)
DRAG_LIMIT = 4.0         # … clamped to ±DRAG_LIMIT · push force
WIDE_BAR_PX = 900        # control bar in one row from this width on
VIEW_SHARE = 0.6         # share of the live page height (portrait: width too) for the pendulum view
EXPERIMENT_CONTROLS_PX = 440
SIDE_PANEL_MIN_SHORT_EDGE = 600   # show the configuration panel at start on tablets

RENDER_LABELS = {"native": "Native (sharpest)", "high": "High (1.5×)",
                 "balanced": "Balanced (1.25×)", "fast": "Fast (1×)"}


def _tool_button(text: str = "", action: QAction | None = None, tip: str = "") -> QToolButton:
    b = QToolButton()
    if action is not None:
        b.setDefaultAction(action)
    else:
        b.setText(text)
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if tip:
        b.setToolTip(tip)
    return b


def _shrinkable(widget: QWidget) -> QWidget:
    """Ignore the widget's width hint so it can never force the window wider than a phone."""
    policy = widget.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    widget.setSizePolicy(policy)
    return widget


class MobileWindow(MainWindow):
    def __init__(self, config: AppConfig) -> None:
        self._drag_offset = 0.0
        self._resume_on_activate = False
        super().__init__(config)
        self.setWindowTitle("Inverted Pendulum Simulator")
        self.apply_performance(self.perf)
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self.on_application_state)
        QTimer.singleShot(0, self._apply_orientation)

    # ================================================================ builders
    def _create_view(self) -> TouchPendulumView:
        view = TouchPendulumView()
        view.tapPush.connect(self.push)
        view.dragOffset.connect(self._on_drag)
        return view

    def _create_telemetry(self) -> GraphsPanel:
        return GraphsPanel()

    def _build_layout(self) -> None:
        self.perf = PerformanceSettings.load()
        self.stats = FrameStats()
        self.stats.on_second = self._on_stats_second
        self.view.stats = self.stats
        self.telemetry.stats = self.stats
        instrument_graphs(self.stats)          # pyqtgraph plots on the experiment page
        self._last_drawn = None
        self._cpu_text = ""
        self._pinned: set[int] = set()

        self.live = QSplitter(Qt.Orientation.Vertical)
        self.live.setChildrenCollapsible(False)
        self.live.addWidget(self.view)
        self.live.addWidget(self.telemetry)
        self.live.setStretchFactor(0, 3)
        self.live.setStretchFactor(1, 2)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.live, "Live")
        self.tabs.addTab(self.experiments, "Experiments")
        self.tabs.currentChanged.connect(lambda _: QTimer.singleShot(0, self._size_pages))

        simulation_page = QWidget()
        sv = QVBoxLayout(simulation_page)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(self.settings_panel)
        sv.addWidget(self._build_performance_box())
        self.side = QTabWidget()
        self.side.setDocumentMode(True)
        self.side.addTab(scrollable(self.plant_panel), "Pendulum")
        self.side.addTab(scrollable(self.controller_panel), "PID")
        self.side.addTab(scrollable(simulation_page), "Simulation")
        self.side.setMinimumWidth(0)
        _shrinkable(self.side)

        self.body = QSplitter(Qt.Orientation.Horizontal)
        self.body.setChildrenCollapsible(False)
        self.body.addWidget(self.tabs)
        self.body.addWidget(self.side)
        _shrinkable(self.tabs)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(4, 4, 4, 0)
        outer.setSpacing(4)
        outer.addWidget(self.body, 1)
        self.bar = QGridLayout()
        self.bar.setSpacing(4)
        outer.addLayout(self.bar)
        self.setCentralWidget(central)

        screen = QGuiApplication.primaryScreen()
        short_edge = min(screen.availableGeometry().width(), screen.availableGeometry().height()) \
            if screen is not None else 0
        self.side.setVisible(short_edge >= SIDE_PANEL_MIN_SHORT_EDGE)
        self._orientation: bool | None = None

        # Full-window auto-tune flow (locks the rest of the app while open)
        self.autotune = AutotuneScreen(self)
        self.autotune.applyRequested.connect(self._apply_tuning_result)
        self.autotune.closed.connect(self._on_autotune_closed)
        self._tune_was_running = False

    def _build_toolbar_and_menu(self) -> None:
        """Touch control bar plus a ☰ menu (no menu bar / tool bar on mobile)."""
        self.run_btn = _tool_button("▶ Start")
        self.run_btn.clicked.connect(self.toggle_run)
        self.reset_btn = _tool_button(action=self.act_reset)
        self.restart_btn = _tool_button(action=self.act_restart)
        self.step_btn = _tool_button(action=self.act_step)
        self.push_left_btn = _tool_button(action=self.act_push_left)
        self.push_right_btn = _tool_button(action=self.act_push_right)
        self.panel_btn = _tool_button("⚙", tip="Show / hide the configuration panel")
        self.panel_btn.setCheckable(True)
        self.panel_btn.setChecked(self.side.isVisible())
        self.panel_btn.toggled.connect(self.set_panel_visible)

        self.act_clear_trails = QAction("Clear trajectories", self)
        self.act_clear_trails.triggered.connect(self.view.clear_trails)
        self.act_stats = QAction("Debug: performance overlay", self)
        self.act_stats.setCheckable(True)
        self.act_stats.toggled.connect(self._on_stats_toggled)
        menu = QMenu(self)
        for a in (self.act_open, self.act_save, self.act_save_as):
            menu.addAction(a)
        menu.addSeparator()
        scen = menu.addMenu("New from scenario")
        for i in range(1, self.scenario_combo.count()):
            act = scen.addAction(self.scenario_combo.itemText(i))
            act.triggered.connect(lambda _=False, idx=i: self._on_scenario_chosen(idx))
        menu.addSeparator()
        for a in (self.act_fit, self.act_clear_trails, self.act_stats):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.act_about)
        self.menu_btn = _tool_button("☰", tip="Menu")
        self.menu_btn.setMenu(menu)
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self.scenario_combo.setItemText(0, "Scenario…")
        for combo, chars in ((self.scenario_combo, 8), (self.speed_combo, 4)):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(chars)
            _shrinkable(combo)
        _shrinkable(self.push_force)
        self.push_force.setMinimumWidth(0)
        self.speed_combo.setToolTip("Simulation speed")
        self._bar_wide: bool | None = None
        self._arrange_bar(wide=True)

    def _arrange_bar(self, wide: bool) -> None:
        if wide == self._bar_wide:
            return
        self._bar_wide = wide
        while self.bar.count():
            self.bar.takeAt(0)
        cells = [  # widget, wide (row, col, colspan), narrow (row, col, colspan)
            (self.run_btn, (0, 0, 2), (0, 0, 2)),
            (self.reset_btn, (0, 2, 1), (0, 2, 1)),
            (self.restart_btn, (0, 3, 1), (0, 3, 1)),
            (self.step_btn, (0, 4, 1), (0, 4, 1)),
            (self.push_left_btn, (0, 5, 1), (1, 0, 1)),
            (self.push_force, (0, 6, 2), (1, 1, 2)),
            (self.push_right_btn, (0, 8, 1), (1, 3, 1)),
            (self.speed_combo, (0, 9, 1), (1, 4, 1)),
            (self.scenario_combo, (0, 10, 2), (2, 0, 3)),
            (self.panel_btn, (0, 12, 1), (2, 3, 1)),
            (self.menu_btn, (0, 13, 1), (2, 4, 1)),
        ]
        for col in range(14):
            self.bar.setColumnStretch(col, 1 if (wide or col < 5) else 0)
        for widget, w, n in cells:
            row, col, span = w if wide else n
            self.bar.addWidget(widget, row, col, 1, span)

    def _build_status_bar(self) -> None:
        super()._build_status_bar()
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        for w in (self.status_state, self.status_time, self.status_rtf, self.status_energy, self.status_perf):
            w.setMinimumWidth(1)           # may shrink on phones; Preferred keeps the text width otherwise
        self.status_state.hide()           # shown on the Start/Pause button instead
        self.status_energy.hide()          # the HUD on the view shows the state
        self.status_perf.hide()            # the timing read-out is drawn on the view (see _update_status)

    def _build_performance_box(self) -> QGroupBox:
        box = QGroupBox("Performance (this device)")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.perf_render = QComboBox()
        for key, label in RENDER_LABELS.items():
            self.perf_render.addItem(label, key)
        self.perf_graph = QComboBox()
        for hz in GRAPH_RATES_HZ:
            self.perf_graph.addItem(f"up to {hz} Hz", hz)
        self.perf_fps = QComboBox()
        for fps in FRAME_RATES:
            self.perf_fps.addItem(f"{fps} fps", fps)
        self.perf_aa = QCheckBox("Antialiasing (pendulum and graphs)")
        self.perf_cores = QCheckBox("Use performance CPU cores")
        self.perf_cores.setToolTip("Keeps the simulation on the fast cores of big.LITTLE processors")
        self.perf_stats = QCheckBox("Debug: performance overlay on the view")
        self.perf_log = QCheckBox("Debug: log timings (adb logcat)")
        form.addRow("View resolution", self.perf_render)
        form.addRow("Graph refresh", self.perf_graph)
        form.addRow("Frame rate", self.perf_fps)
        form.addRow("", self.perf_aa)
        form.addRow("", self.perf_cores)
        form.addRow("", self.perf_stats)
        form.addRow("", self.perf_log)
        form.addRow(hint("Drawing, not physics, limits the frame rate on high-resolution screens. "
                         "If the animation stutters, show fewer graphs or lower the graph refresh first. "
                         "Read-out: fps · longest frame gap · physics / view / graphs time per frame."))
        for combo in (self.perf_render, self.perf_graph, self.perf_fps):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)
            combo.currentIndexChanged.connect(self._on_performance_edited)
        for toggle in (self.perf_aa, self.perf_cores, self.perf_stats, self.perf_log):
            toggle.toggled.connect(self._on_performance_edited)
        return box

    # ============================================================ performance
    def apply_performance(self, perf: PerformanceSettings) -> None:
        self.perf = perf
        for combo, value in ((self.perf_render, perf.render), (self.perf_graph, perf.graph_hz),
                             (self.perf_fps, perf.fps)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)
        for box, value in ((self.perf_aa, perf.antialias), (self.perf_stats, perf.debug_overlay),
                           (self.perf_log, perf.log_stats), (self.perf_cores, perf.fast_cores)):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self.act_stats.blockSignals(True)
        self.act_stats.setChecked(perf.debug_overlay)
        self.act_stats.blockSignals(False)
        self.view.set_render_scale(perf.render_scale)
        self.view.antialias = perf.antialias
        self.plot_interval_min = perf.plot_interval_s
        self.plot_interval_max = max(0.3, 2.5 * perf.plot_interval_s)
        self.target_frame_s = 1.25 * perf.frame_interval_ms / 1000.0
        self._plot_interval = perf.plot_interval_s
        self.timer.setInterval(perf.frame_interval_ms)
        self.telemetry.antialias = perf.antialias
        if perf.fast_cores and not self._pinned:
            self._pinned = cpu.pin_to_performance_cores()
        elif not perf.fast_cores and self._pinned:
            cpu.unpin()
            self._pinned = set()
        if not perf.debug_overlay:
            self.view.overlay_text = ""
        self.view.update()

    def _on_performance_edited(self, *_args) -> None:
        perf = PerformanceSettings(
            render=self.perf_render.currentData(),
            graph_hz=self.perf_graph.currentData(),
            fps=self.perf_fps.currentData(),
            antialias=self.perf_aa.isChecked(),
            debug_overlay=self.perf_stats.isChecked(),
            log_stats=self.perf_log.isChecked(),
            fast_cores=self.perf_cores.isChecked(),
        )
        self.apply_performance(perf)
        perf.save()

    def _on_stats_toggled(self, on: bool) -> None:
        self.perf_stats.setChecked(on)   # → _on_performance_edited → saved

    # =============================================================== layout
    def set_panel_visible(self, visible: bool) -> None:
        self.side.setVisible(visible)
        if self.panel_btn.isChecked() != visible:
            self.panel_btn.setChecked(visible)
        if visible:
            self._size_panel()
        QTimer.singleShot(0, self._size_pages)
        QTimer.singleShot(0, self.view.fit)

    def _size_panel(self) -> None:
        horizontal = self.body.orientation() == Qt.Orientation.Horizontal
        total = self.body.width() if horizontal else self.body.height()
        panel = int(total * (0.36 if horizontal else 0.45))
        if horizontal:
            panel = max(min(panel, 480), min(320, total // 2))
        self.body.setSizes([max(total - panel, 1), panel])

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.autotune.isVisible():
            self.autotune.setGeometry(self.rect())
        self._apply_orientation()

    # ============================================================ auto-tune
    def _on_robust_tune(self) -> None:
        """PID panel → 'Auto-tune for robustness': full-screen flow instead of a dialog."""
        self._tune_was_running = self.running
        self.pause()                          # all CPU cores for the search
        self.config.controller = self.controller_panel.config()
        self.autotune.open(self.config)

    def _on_autotune_closed(self) -> None:
        if self._tune_was_running:
            self._tune_was_running = False
            self.start()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Back and self.autotune.isVisible():
            self.autotune.handle_back()
            event.accept()
            return
        super().keyPressEvent(event)

    def _apply_orientation(self) -> None:
        landscape = self.width() > self.height()
        self._arrange_bar(wide=self.width() >= WIDE_BAR_PX)
        if landscape == self._orientation:
            return
        self._orientation = landscape
        self.body.setOrientation(Qt.Orientation.Horizontal if landscape else Qt.Orientation.Vertical)
        # Experiment page: controls beside the plots in landscape, above them in portrait.
        splitters = self.experiments.findChildren(QSplitter)
        if splitters:
            splitters[0].setOrientation(Qt.Orientation.Horizontal if landscape else Qt.Orientation.Vertical)
        if self.side.isVisible():
            self._size_panel()
        QTimer.singleShot(0, self._size_pages)
        QTimer.singleShot(0, self.view.fit)

    def _size_pages(self) -> None:
        """Pendulum view gets most of the live page; experiment controls a usable width."""
        total = self.live.height()
        if total > 0:
            view = int(total * VIEW_SHARE)
            self.live.setSizes([view, total - view])
        splitters = self.experiments.findChildren(QSplitter)
        if splitters and splitters[0].orientation() == Qt.Orientation.Horizontal:
            width = splitters[0].width()
            controls = min(EXPERIMENT_CONTROLS_PX, int(width * 0.45))
            splitters[0].setSizes([controls, max(width - controls, 1)])

    def fit_to_screen(self) -> None:
        self.showMaximized()

    # ========================================================== run control
    def _update_actions(self) -> None:
        super()._update_actions()
        paused = (not self.running) and self.sim.time > 0
        self.run_btn.setText("⏸ Pause" if self.running else "▶ Resume" if paused else "▶ Start")

    def _on_drag(self, offset: float) -> None:
        self._drag_offset = offset

    def drag_force(self) -> float:
        f = DRAG_GAIN * self.config.push_force * self._drag_offset
        limit = DRAG_LIMIT * self.config.push_force
        return float(np.clip(f, -limit, limit))

    def seed_lqr(self) -> None:
        """LQR gains with the default weights (the PID panel's dialog offers custom weights)."""
        self._on_auto_tune(LQRWeights())

    # ============================================================== lifecycle
    def on_application_state(self, state: Qt.ApplicationState) -> None:
        """Pause in the background (no physics, no timer); resume cleanly on return."""
        if state == Qt.ApplicationState.ApplicationActive:
            self._last_frame = time.perf_counter()
            self.stepper.reset_clock()
            if not self.timer.isActive():
                self.timer.start()
            if self._resume_on_activate:
                self._resume_on_activate = False
                self.start()
        else:
            if self.autotune.busy:
                self.autotune.cancel()        # auto-tune is a foreground operation
            if self.running:
                self._resume_on_activate = True
                self.pause()
            self._drag_offset = 0.0
            self.timer.stop()

    # ================================================================= frame
    def _on_frame(self) -> None:
        now = time.perf_counter()
        gap = now - self._last_frame
        if self.running and self._drag_offset != 0.0:
            duration = max(min(gap, self.stepper.max_frame_s) * self.speed, self.config.simulation.dt)
            self.sim.apply_disturbance(self.drag_force(), duration)
        super()._on_frame()
        self.stats.frame(gap)

    def _update_view(self) -> None:
        # While paused the 60 Hz timer keeps running; redraw only when something changed
        # (parameter edits, reset, step). Camera gestures repaint the view themselves.
        sim = self.sim
        key = (id(sim), sim.time, sim.state.tobytes(), sim.model.lengths.tobytes(), sim.applied,
               sim.tip_force, self.running)
        if not self.running and key == self._last_drawn:
            return
        self._last_drawn = key
        super()._update_view()

    def timing_text(self) -> str:
        s = self.stats
        return (f"{s.fps:.0f} fps · max {s.worst_gap_ms:.0f} ms │ physics {self.stepper.physics_ms:.1f} · "
                f"view {s.view_ms.value:.1f} · graphs {s.graph_ms:.1f} ms/frame │ {self._cpu_text}")

    def _on_stats_second(self, _stats: FrameStats) -> None:
        if not (self.perf.debug_overlay or self.perf.log_stats):
            return
        self._cpu_text = cpu.describe()     # one small /proc read per second, only in debug mode
        if self.perf.log_stats:
            print(f"pendulum-perf: {self.timing_text()} · running={self.running}", flush=True)

    def _update_status(self) -> None:
        super()._update_status()
        if self.perf.debug_overlay:
            self.view.overlay_text = self.timing_text()
