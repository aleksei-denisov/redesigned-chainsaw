"""Touch-first main window: the desktop :class:`~pendulum_sim.ui.main_window.MainWindow`
with screens, navigation and touch editors.

All behaviour is inherited from the desktop window – run control, scenarios,
configuration files, parameter changes, LQR seeding, restoring gains, experiments,
divergence handling, graph-rate adaptation. This class replaces the *builders* and
the four UI ↔ configuration hooks (``_show_config``, ``_ui_controller``,
``_show_controller``, ``_show_controller_output``) and adds:

* screens – **Simulate** (large view, essential controls), **Graphs**, **Controller**,
  **System** (pendulum + simulation), **Auto-tune**, **Experiments**, **Settings** –
  behind a navigation rail (tablet landscape) or a bottom bar (portrait / phones,
  where the last three sit behind *More*);
* the Android back button: back through visited screens, cancel a running
  auto-tune, and on *Simulate* ask before leaving the app;
* touch gestures on the pendulum (:mod:`.touch_view`), toasts instead of a status
  bar, pausing in the background, and the rendering-performance settings.

The simulation keeps running while other screens are shown; only what is visible
is drawn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sys
import time

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QDialog,
    QDialogButtonBox,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..control import ControllerConfig, LQRWeights
from ..simulation import AppConfig
from ..ui.main_window import SPEEDS, MainWindow
from . import cpu, startup, theme
from .controls import NumberRow, make_kinetic, scroll_area, tool_button
from .graphs import GraphsPanel
from .icons import icon
from .loading import BusyOverlay
from .pages import ControllerPage, GraphsPage, NavBar, SettingsPage, SimulatePage, SystemPage
from .performance import FrameStats, PerformanceSettings, instrument_graphs
from .touch_view import TouchPendulumView

if TYPE_CHECKING:
    from ..ui.experiment_panel import ExperimentPanel
    from .autotune_screen import AutotuneScreen

DRAG_GAIN = 2.0          # drag force = DRAG_GAIN · push force · (offset / total length)
DRAG_LIMIT = 4.0         # … clamped to ±DRAG_LIMIT · push force
TIME_UPDATES = sys.version_info >= (3, 11)
HITCH_FRAMES = 3.0       # a frame gap this many intervals long skips the graph step


class _NoExperiments:
    """Stand-in when the Experiments screen was never opened (closing the window)."""

    def shutdown(self) -> None:
        pass


RAIL_MIN_WIDTH = 900     # navigation rail from this window width on (landscape) …
RAIL_MIN_HEIGHT = 560    # … when its seven items fit vertically
PRIMARY = ["simulate", "graphs", "controller", "system"]
SECONDARY = ["autotune", "experiments", "settings"]
NAV_ITEMS = [("simulate", "Simulate", "simulate"), ("graphs", "Graphs", "graphs"),
             ("controller", "Controller", "controller"), ("system", "System", "system"),
             ("autotune", "Auto-tune", "autotune"), ("experiments", "Experiments", "experiments"),
             ("settings", "Settings", "settings"), ("more", "More", "more")]


class ReportDialog(QDialog):
    """Monospace report with Copy and Close (fills most of the window)."""

    def __init__(self, title: str, text: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.text = text
        v = QVBoxLayout(self)
        body = QLabel(text)
        mono = body.font()
        mono.setFamily("monospace")
        mono.setStyleHint(mono.StyleHint.Monospace)
        mono.setPixelSize(theme.SMALL_PX - 1)
        body.setFont(mono)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.addWidget(body)
        hl.addStretch(1)
        area = scroll_area(holder)
        area.widget().setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        v.addWidget(area, 1)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy report")
        self.copy_btn.setProperty("primary", True)
        close = QPushButton("Close")
        row.addWidget(close)
        row.addWidget(self.copy_btn, 1)
        v.addLayout(row)
        self.copy_btn.clicked.connect(self.copy)
        close.clicked.connect(self.accept)
        r = parent.rect()
        self.resize(max(320, int(r.width() * 0.92)), max(300, int(r.height() * 0.85)))

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text)
        self.copy_btn.setText("Copied")


class Toast(QLabel):
    """Short message near the bottom of the window (replaces the desktop status bar)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"QLabel {{ background: {theme.SURFACE_2}; color: {theme.TEXT}; border: 1px solid "
                           f"{theme.OUTLINE}; border-radius: 12px; padding: 10px 16px; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_text(self, text: str, ms: int) -> None:
        self.setText(text)
        self.place()
        self.show()
        self.raise_()
        self._timer.start(max(ms, 1500))

    def place(self) -> None:
        """Centred just above the controls of the current screen, never over them."""
        parent = self.parentWidget()
        self.setFixedWidth(max(min(parent.width() - 32, 640), 120))
        self.adjustSize()
        bottom = getattr(parent, "toast_bottom", lambda: parent.height() - 16)()
        self.move((parent.width() - self.width()) // 2, max(8, bottom - self.height() - 12))


class MobileWindow(MainWindow):
    def __init__(self, config: AppConfig) -> None:
        self._drag_offset = 0.0
        self._resume_on_activate = False
        super().__init__(config)
        startup.TIMER.mark("  scenario loaded into the screens")
        self.setWindowTitle("Inverted Pendulum Simulator")
        self.apply_performance(self.perf)
        startup.TIMER.mark("  performance settings applied")
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

    def _create_widgets(self) -> None:
        """Touch editors instead of the desktop panels (no desktop panel is created)."""
        self.view = self._create_view()
        self.telemetry = self._create_telemetry()
        self._experiments = None               # Experiments and Auto-tune are built on first use
        self._autotune = None                  # (pyqtgraph and the tuner are not needed at start-up)
        startup.TIMER.mark("  view and graphs created")
        self.controller_page = ControllerPage()
        self.system_page = SystemPage()
        self.settings_page = SettingsPage()
        startup.TIMER.mark("  editor screens created")

    def _build_layout(self) -> None:
        self.perf = PerformanceSettings.load()
        self.stats = FrameStats()
        self.stats.on_second = self._on_stats_second
        self.stats.physics_source = lambda: self.stepper.last_ms
        self._graph_due = 0.0
        self.view.stats = self.stats
        self.telemetry.stats = self.stats
        self._last_drawn = None
        self._cpu_text = ""
        self._pinned: set[int] = set()
        self._history: list[str] = []
        self._tune_was_running = False

        self.simulate_page = SimulatePage(self.view)
        # compact run controls on the Graphs screen (connected in _build_toolbar_and_menu)
        self.g_run_btn = tool_button("play", tip="Start / pause")
        self.g_reset_btn = tool_button("reset", tip="Back to the initial state (stopped)")
        self.g_push_left_btn = tool_button("push_left", tip="Push the tip to the left")
        self.g_push_right_btn = tool_button("push_right", tip="Push the tip to the right")
        self.graphs_page = GraphsPage(self.view, self.telemetry, [self.g_run_btn, self.g_reset_btn,
                                                                  self.g_push_left_btn, self.g_push_right_btn])
        inner = QWidget()
        self._exp_layout = QVBoxLayout(inner)
        self._exp_layout.setContentsMargins(8, 8, 8, 8)
        self._exp_loading = QLabel("Loading experiments…")
        self._exp_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._exp_layout.addWidget(self._exp_loading, 1)
        experiments_page = scroll_area(inner)  # scrolls only when the screen is too low (phone landscape)
        self.autotune_host = QWidget()
        self._tune_layout = QVBoxLayout(self.autotune_host)
        self._tune_layout.setContentsMargins(0, 0, 0, 0)
        self.pages: dict[str, QWidget] = {
            "simulate": self.simulate_page, "graphs": self.graphs_page, "controller": self.controller_page,
            "system": self.system_page, "autotune": self.autotune_host, "experiments": experiments_page,
            "settings": self.settings_page,
        }
        self.stack = QStackedWidget()
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.nav = NavBar(NAV_ITEMS)
        self.nav.selected.connect(self._on_nav)
        self.more_menu = QMenu(self)
        for key in SECONDARY:
            text = next(t for k, t, _ in NAV_ITEMS if k == key)
            act = self.more_menu.addAction(icon(key), text)
            act.triggered.connect(lambda _=False, k=key: self.navigate(k))

        central = QWidget()
        self.body = QVBoxLayout(central)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        self.setCentralWidget(central)
        self.toast = Toast(self)
        self.busy = BusyOverlay(self)          # "Loading …" cover for scenario / file loads
        self._rail: bool | None = None
        self._arrange(rail=False)
        self.current = "simulate"
        self.nav.set_current("simulate")
        startup.TIMER.mark("  layout and navigation built")

    # ------------------------------------------------ screens built on first use
    @property
    def experiments(self) -> ExperimentPanel:
        if self._experiments is None:
            self._build_experiments()
        return self._experiments

    @property
    def autotune(self) -> AutotuneScreen:
        if self._autotune is None:
            self._build_autotune()
        return self._autotune

    def _tune_busy(self) -> bool:
        return self._autotune is not None and self._autotune.busy

    def _build_experiments(self) -> None:
        import pyqtgraph as pg

        from ..ui.experiment_panel import ExperimentPanel

        started = time.perf_counter()
        pal = self.palette()
        pg.setConfigOptions(antialias=True, background=pal.base().color(), foreground=pal.text().color())
        panel = ExperimentPanel(lambda: self.config)
        self._experiments = panel
        self._exp_loading.hide()
        self._exp_layout.addWidget(panel)
        panel.set_settings(self.config.experiment)
        panel.statusMessage.connect(self.show_message)
        panel.apply_btn.clicked.connect(self._apply_experiment_gains)
        # finger scrolling for the settings column, lists and table (not for the plots:
        # QGraphicsView is a scroll area too, but pans/zooms itself)
        for area in panel.findChildren(QAbstractScrollArea):
            if not isinstance(area, QGraphicsView):
                make_kinetic(area)
        instrument_graphs(self.stats)          # time the pyqtgraph plots of this page
        self._orient_experiments()
        startup.TIMER.note("experiments screen built", time.perf_counter() - started)

    def _build_autotune(self) -> None:
        from .autotune_screen import AutotuneScreen

        started = time.perf_counter()
        screen = AutotuneScreen(embedded=True)
        self._autotune = screen
        self._tune_layout.addWidget(screen)
        screen.applyRequested.connect(self._apply_tuning_result)
        screen.closed.connect(self._on_autotune_closed)
        screen.runningChanged.connect(self._on_autotune_running)
        startup.TIMER.note("auto-tune screen built", time.perf_counter() - started)

    def _arrange(self, rail: bool) -> None:
        """Navigation rail at the left (tablet landscape) or bar at the bottom."""
        if rail == self._rail:
            return
        self._rail = rail
        while self.body.count():
            item = self.body.takeAt(0)
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    lay.takeAt(0)
                lay.deleteLater()
        if rail:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            row.addWidget(self.nav)
            row.addWidget(self.stack, 1)
            self.body.addLayout(row)
            self.nav.set_vertical(True, [k for k, _, _ in NAV_ITEMS if k != "more"])
        else:
            self.body.addWidget(self.stack, 1)
            self.body.addWidget(self.nav)
            self.nav.set_vertical(False, PRIMARY + ["more"])

    def _build_toolbar_and_menu(self) -> None:
        """Run controls on the Simulate screen (no menu bar / tool bar on mobile)."""
        self.run_btn = QPushButton()
        self.run_btn.setProperty("primary", True)
        self.run_btn.setIconSize(QSize(26, 26))
        self.run_btn.setMinimumSize(132, 52)
        self.run_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.run_btn.clicked.connect(self.toggle_run)
        self.reset_btn = tool_button("reset", "Reset", "Back to the initial state (stopped)")
        self.restart_btn = tool_button("restart", "Restart", "Back to the initial state and run")
        self.step_btn = tool_button("step", tip="One controller period")
        self.push_left_btn = tool_button("push_left", tip="Push the tip to the left")
        self.push_right_btn = tool_button("push_right", tip="Push the tip to the right")
        self.force_btn = QToolButton()
        self.force_btn.setMinimumSize(76, theme.TOUCH_PX)
        self.force_btn.setToolTip("Push force – tap to change")
        self.force_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed_btn = tool_button("speed", "1×", "Simulation speed")
        self.speed_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        speed_menu = QMenu(self)
        for s in SPEEDS:
            act = speed_menu.addAction(f"{s:g}×")
            act.triggered.connect(lambda _=False, v=s: self.set_speed(v))
        self.speed_btn.setMenu(speed_menu)
        self.scenario_btn = tool_button("scenarios", "Scenario", "Load a demonstration scenario")
        self.scenario_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        scen_menu = QMenu(self)
        for i in range(1, self.scenario_combo.count()):
            act = scen_menu.addAction(self.scenario_combo.itemText(i))
            act.triggered.connect(lambda _=False, idx=i: self._on_scenario_chosen(idx))
        self.scenario_btn.setMenu(scen_menu)
        self.fit_btn = tool_button("fit", tip="Fit the pendulum into the view")
        self.reset_btn.clicked.connect(self.reset)
        self.restart_btn.clicked.connect(self.restart)
        self.step_btn.clicked.connect(self.single_step)
        self.push_left_btn.clicked.connect(lambda: self.push(-1))
        self.push_right_btn.clicked.connect(lambda: self.push(+1))
        self.g_run_btn.clicked.connect(self.toggle_run)
        self.g_reset_btn.clicked.connect(self.reset)
        self.g_push_left_btn.clicked.connect(lambda: self.push(-1))
        self.g_push_right_btn.clicked.connect(lambda: self.push(+1))
        self.force_btn.clicked.connect(self._edit_push_force)
        self.fit_btn.clicked.connect(self.view.fit)
        self.simulate_page.add_controls([self.run_btn, self.reset_btn, self.restart_btn, self.step_btn,
                                         self.push_left_btn, self.force_btn, self.push_right_btn,
                                         self.speed_btn, self.scenario_btn, self.fit_btn])
        self.push_force.valueChanged.connect(lambda v: self.force_btn.setText(f"{v:g} N"))
        self.act_clear_trails = QAction("Clear trajectories", self)
        self.act_clear_trails.triggered.connect(self.view.clear_trails)

    def _build_status_bar(self) -> None:
        super()._build_status_bar()          # labels used by the inherited status updates
        self.statusBar().hide()

    def _connect_signals(self) -> None:
        cp, sp, st = self.controller_page, self.system_page, self.settings_page
        cp.changed.connect(self._on_controller_changed)
        cp.seedLqr.connect(self._on_auto_tune)
        cp.restoreRequested.connect(self._on_restore_gains)
        cp.resetIntegratorsRequested.connect(self._on_reset_integrators)
        cp.autotuneRequested.connect(self._on_robust_tune)
        sp.plantChanged.connect(self._on_plant_changed)
        sp.simulationChanged.connect(self._on_simulation_settings)
        st.viewChanged.connect(self._on_view_settings)
        st.performanceChanged.connect(self._on_performance_edited)
        st.openRequested.connect(self.open_config)
        st.saveRequested.connect(self.save_config)
        st.saveAsRequested.connect(self.save_config_as)
        st.clearTrailsRequested.connect(self.view.clear_trails)
        st.perfTestRequested.connect(self.run_performance_test)

    # ========================================== UI <-> configuration hooks
    def _show_config(self, config: AppConfig) -> None:
        self.push_force.blockSignals(True)
        self.push_force.setValue(config.push_force)
        self.push_force.blockSignals(False)
        self.force_btn.setText(f"{config.push_force:g} N")
        self.system_page.set_plant(config.plant)
        self.system_page.set_simulation(config.simulation)
        self.controller_page.set_config(config.controller)
        if self._experiments is not None:
            self._experiments.set_settings(config.experiment)

    def _ui_controller(self) -> ControllerConfig:
        return self.controller_page.config()

    def _show_controller(self, ctrl: ControllerConfig) -> None:
        self.controller_page.set_config(ctrl)

    def _show_controller_output(self, out) -> None:
        self.controller_page.show_output(out)

    def load(self, config) -> None:
        """Desktop ``load`` behind a "Loading …" cover (rebuilding the editors takes a moment)."""
        if not self.isVisible() or not hasattr(self, "busy"):
            super().load(config)
            return
        self.busy.run(f"Loading {config.name}…", lambda: MainWindow.load(self, config))

    def _on_plant_changed(self, plant, structural: bool) -> None:
        if structural and self.isVisible():  # segments / base changed: editors are rebuilt
            self.busy.run("Rebuilding the model…", lambda: self._apply_plant(plant, structural))
        else:
            self._apply_plant(plant, structural)

    def _apply_plant(self, plant, structural: bool) -> None:
        super()._on_plant_changed(plant, structural)
        if structural:                       # show the plant as accepted
            self.system_page.set_plant(self.config.plant)

    # ============================================================ navigation
    def _on_nav(self, key: str) -> None:
        if key == "more":
            self.nav.set_current("more" if self.current in SECONDARY else self.current)
            btn = self.nav.buttons["more"]
            self.more_menu.popup(btn.mapToGlobal(QPoint(0, -self.more_menu.sizeHint().height())))
            return
        self.navigate(key)

    def navigate(self, key: str, remember: bool = True) -> None:
        if key not in self.pages or (self._tune_busy() and key != "autotune"):
            self.nav.set_current(self._nav_key(self.current))
            return
        if remember and key != self.current:
            self._history.append(self.current)
            del self._history[:-20]
        self.current = key
        if key == "graphs":                  # one shared pendulum view
            self.graphs_page.attach()
        elif key == "simulate":
            self.simulate_page.attach()
        self.stack.setCurrentWidget(self.pages[key])
        self.nav.set_current(self._nav_key(key))
        if self.toast.isVisible():
            QTimer.singleShot(0, self.toast.place)
        if key == "experiments" and self._experiments is None:
            QTimer.singleShot(30, lambda: self.experiments)   # show the screen first, then build
        if key == "autotune" and not self._tune_busy() and self.autotune.config is None:
            self.config.controller = self._ui_controller()
            self.autotune.open(self.config)

    def _nav_key(self, key: str) -> str:
        return key if (self._rail or key in PRIMARY) else "more"

    def go_back(self) -> bool:
        """Android back; returns False when the app should ask before exiting."""
        if self.current == "autotune" and self._tune_busy():
            self.autotune.cancel()
            return True
        if self._history:
            self.navigate(self._history.pop(), remember=False)
            return True
        if self.current != "simulate":
            self.navigate("simulate", remember=False)
            return True
        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Back:
            event.accept()
            if not self.go_back():
                self.confirm_exit()
            return
        super().keyPressEvent(event)

    def confirm_exit(self) -> None:
        box = QMessageBox(QMessageBox.Icon.Question, "Leave the simulator?",
                          "Close the Inverted Pendulum Simulator?", parent=self)
        leave = box.addButton("Leave", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Stay", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is leave:
            app = QGuiApplication.instance()
            if app is not None:
                app.quit()

    # ============================================================ auto-tune
    def _on_robust_tune(self) -> None:
        """Controller → 'Auto-tune': its own screen (the desktop opens a dialog)."""
        self.config.controller = self._ui_controller()
        if not self._tune_busy():
            self.autotune.open(self.config)
        self.navigate("autotune")

    def _on_autotune_running(self, running: bool) -> None:
        if running:
            self._tune_was_running = self.running
            self.pause()                       # all CPU cores for the search
        self.nav.setEnabled(not running)       # the rest of the app is locked meanwhile
        self.simulate_page.bar.setEnabled(not running)
        self.graphs_page.bar.setEnabled(not running)

    def _on_autotune_closed(self) -> None:
        self.autotune.config = None            # re-read the controller next time
        if self._tune_was_running:
            self._tune_was_running = False
            self.start()
        if self.current == "autotune" and not self.go_back():
            self.navigate("controller", remember=False)

    # ============================================================== controls
    def set_speed(self, value: float) -> None:
        self.speed = value
        self.speed_combo.setCurrentIndex(SPEEDS.index(value))
        self.speed_btn.setText(f"{value:g}×")

    def _edit_push_force(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Push force")
        lay = QVBoxLayout(dlg)
        row = NumberRow("Push force (0.1 s pulse at the tip)", 0.01, 1000.0, self.push_force.value(), 2, "N", "log")
        lay.addWidget(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)
        row.valueChanged.connect(self.push_force.setValue)
        dlg.resize(min(self.width() - 32, 560), dlg.sizeHint().height())
        dlg.exec()

    def _update_actions(self) -> None:
        super()._update_actions()
        paused = (not self.running) and self.sim.time > 0
        self.run_btn.setIcon(icon("pause" if self.running else "play", theme.ON_ACCENT))
        self.run_btn.setText(" Pause" if self.running else " Resume" if paused else " Start")
        self.g_run_btn.setIcon(icon("pause" if self.running else "play"))

    def _on_drag(self, offset: float) -> None:
        self._drag_offset = offset

    def drag_force(self) -> float:
        f = DRAG_GAIN * self.config.push_force * self._drag_offset
        limit = DRAG_LIMIT * self.config.push_force
        return float(np.clip(f, -limit, limit))

    def seed_lqr(self) -> None:
        self._on_auto_tune(LQRWeights())

    def toast_bottom(self) -> int:
        """y (window coordinates) the toast must stay above: the current screen's control bar."""
        bar = {"simulate": self.simulate_page.bar, "graphs": self.graphs_page.bar}.get(self.current)
        if self.current == "autotune" and self._autotune is not None:
            bar = getattr(self._autotune.pages.currentWidget(), "bar", None)
        if bar is not None and bar.isVisible():
            return bar.mapTo(self, QPoint(0, 0)).y()
        return self.stack.mapTo(self, QPoint(0, self.stack.height())).y()

    def show_message(self, text: str, timeout_ms: int = 4000) -> None:
        self.toast.show_text(text, min(timeout_ms, 6000))

    # =========================================================== performance
    def apply_performance(self, perf: PerformanceSettings) -> None:
        self.perf = perf
        self.settings_page.set_performance(perf)
        self.view.set_render_scale(perf.render_scale)
        self.view.antialias = perf.antialias
        self.plot_interval_min = perf.plot_interval_s
        self.plot_interval_max = max(0.3, 2.5 * perf.plot_interval_s)
        self.target_frame_s = 1.25 * perf.frame_interval_ms / 1000.0
        self._plot_interval = perf.plot_interval_s
        self.timer.setInterval(perf.frame_interval_ms)
        if perf.fast_cores and not self._pinned:
            self._pinned = cpu.pin_to_performance_cores()
        elif not perf.fast_cores and self._pinned:
            cpu.unpin()
            self._pinned = set()
        if not perf.debug_overlay:
            self.view.overlay_text = ""
        self.set_drawing(perf.drawing, perf.gpu_samples)
        self.view.update()

    def set_drawing(self, mode: str, samples: int = 0) -> str:
        """Software or GPU drawing for the pendulum view and the graphs; returns the mode in use."""
        gpu = mode == "gpu"
        self.view.gpu_fallback = self._gpu_unavailable
        on = self.view.set_gpu(gpu, samples)
        self.telemetry.set_gpu(on, samples)
        return "gpu" if on else "software"

    def _gpu_unavailable(self) -> None:
        self.telemetry.set_gpu(False)
        self.show_message("GPU drawing is not available on this device – using software drawing", 6000)

    def run_performance_test(self) -> None:
        """Settings → Debug: ~30 s scripted measurement, then a copyable report."""
        from .perftest import PerformanceTest

        if getattr(self, "_perf_test", None) is not None and self._perf_test.running:
            return
        self._perf_test = PerformanceTest(self)
        self._perf_test.progress.connect(lambda text: self.show_message(f"Performance test {text}", 6000))
        self._perf_test.finished.connect(self._show_report)
        self.nav.setEnabled(False)
        self._perf_test.start()

    def _show_report(self, text: str) -> None:
        self.nav.setEnabled(not self._tune_busy())
        dialog = ReportDialog("Performance test", text, self)
        dialog.open()
        self._report_dialog = dialog

    def _on_performance_edited(self, perf: PerformanceSettings) -> None:
        self.apply_performance(perf)
        perf.save()

    # ================================================================ layout
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_orientation()

    def _apply_orientation(self) -> None:
        if getattr(self, "_orienting", False):     # re-arranging can resize the window again
            return
        self._orienting = True
        try:
            self._apply_orientation_now()
        finally:
            self._orienting = False

    def _apply_orientation_now(self) -> None:
        landscape = self.width() > self.height()
        rail = landscape and self.width() >= RAIL_MIN_WIDTH and self.height() >= RAIL_MIN_HEIGHT
        if rail != self._rail:
            self._arrange(rail)
            self.nav.set_current(self._nav_key(self.current))
        self.graphs_page.set_landscape(landscape)
        self.telemetry.set_compact(self.height() < 600 or self.width() < 600)
        self.nav.set_compact(not rail and self.height() < 500)
        compact = self.width() < 600 or self.height() < 500   # phones: icon-only secondary buttons
        for b in (getattr(self, n, None) for n in ("reset_btn", "restart_btn", "scenario_btn")):
            if b is not None:
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if compact
                                     else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if self.toast.isVisible():
            QTimer.singleShot(0, self.toast.place)
        self._orient_experiments()
        QTimer.singleShot(0, self.view.fit)

    def _orient_experiments(self) -> None:
        """Experiment controls beside the plots in landscape, above them in portrait."""
        if self._experiments is None:
            return
        splitters = self._experiments.findChildren(QSplitter)
        if splitters:
            landscape = self.width() > self.height()
            splitters[0].setOrientation(Qt.Orientation.Horizontal if landscape else Qt.Orientation.Vertical)

    def fit_to_screen(self) -> None:
        self.showMaximized()

    # ============================================================= lifecycle
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
            if self._tune_busy():
                self.autotune.cancel()        # auto-tune is a foreground operation
            if self.running:
                self._resume_on_activate = True
                self.pause()
            self._drag_offset = 0.0
            self.timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Embedded in the start-up shell the window may miss the "application active"
        # change (it happened while the loading screen was shown): run the frame timer.
        app = QGuiApplication.instance()
        if app is not None and app.applicationState() == Qt.ApplicationState.ApplicationActive \
                and not self.timer.isActive():
            self._last_frame = time.perf_counter()
            self.timer.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._tune_busy():
            self.autotune.cancel()
            if self.autotune.worker is not None:
                self.autotune.worker.wait(10000)
        if self._experiments is None:          # MainWindow.closeEvent shuts it down: nothing to do
            self._experiments = _NoExperiments()
        super().closeEvent(event)

    # ================================================================= frame
    def _on_frame(self) -> None:
        now = time.perf_counter()
        gap = now - self._last_frame
        if self.running and self._drag_offset != 0.0:
            duration = max(min(gap, self.stepper.max_frame_s) * self.speed, self.config.simulation.dt)
            self.sim.apply_disturbance(self.drag_force(), duration)
        super()._on_frame()
        # Graphs: per-frame scroll (only new samples are drawn), at the chosen animation rate;
        # skipped only after a real hitch (≥ 3 frame intervals) so the pendulum and the physics
        # recover first (the next step catches up – the charts are time-based). A device that
        # simply runs below the target rate (e.g. 40 fps) still steps the graphs every frame.
        overran = gap > HITCH_FRAMES * self.perf.frame_interval_ms / 1000.0
        if self.current == "graphs" and not overran:
            # time-based: at most graph_fps steps per second, but every frame when the device
            # draws fewer frames than that (a frame-count divider halved the rate at 21 fps)
            self._graph_due -= gap
            if self._graph_due <= 0.25 * gap:          # tolerance against timer jitter
                self._graph_due = max(self._graph_due + self.perf.graph_interval_s, 0.0)
                self.telemetry.advance(self.sim.telemetry)
        self.stats.frame(gap)

    def _update_view(self) -> None:
        # While paused the timer keeps running; redraw only when something changed
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
                f"view {s.view_ms.value:.1f} · graphs {s.graph_data_ms:.1f}+{s.graph_paint_ms:.1f} · "
                f"screen {s.update_ms:.1f} ms/frame │ {self._cpu_text}")

    def _timed_event(self, event) -> bool:
        """``event()`` override (installed below): times each screen update.

        Painting + composing + hand-over to the display. Not installed on Python 3.10:
        there a Python ``event()`` that calls back into Qt, which then calls the Python
        ``paintEvent`` s, crashed PySide6 while a worker thread was running Python (seen
        on Windows CI; the APK runs Python 3.14).
        """
        if event.type() == QEvent.Type.UpdateRequest:
            started = time.perf_counter()
            result = super().event(event)
            self.stats.add_update((time.perf_counter() - started) * 1000.0)
            return result
        return super().event(event)

    def _on_stats_second(self, _stats: FrameStats) -> None:
        if not (self.perf.debug_overlay or self.perf.log_stats or self.current == "settings"):
            return
        self._cpu_text = cpu.describe()     # one small /proc read per second, only when shown
        self.settings_page.cpu_label.setText(f"Simulation thread: {self._cpu_text}")
        if self.perf.log_stats:
            print(f"pendulum-perf: {self.timing_text()} · page={self.current} · running={self.running}",
                  flush=True)

    def _update_status(self) -> None:
        super()._update_status()
        if self.perf.debug_overlay:
            self.view.overlay_text = self.timing_text()


if TIME_UPDATES:
    MobileWindow.event = MobileWindow._timed_event
