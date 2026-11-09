"""Screens of the mobile app (Simulate, Controller, System, Settings, Experiments).

The editors work on a copy of the configuration dataclasses and emit complete new
objects on every committed change; ``MobileWindow`` forwards them to the inherited
desktop handlers (``_on_controller_changed``, ``_on_plant_changed``,
``_on_simulation_settings``, ``_on_view_settings`` …). No simulation or control
logic lives here.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from ..control import AntiWindup, ControlAction, ControllerConfig, DerivativeMode, LQRWeights, PIDLoopConfig
from ..physics import MAX_SEGMENTS, ActuatorParams, BaseType, PlantParams, SegmentParams
from ..simulation import SimulationSettings
from ..ui.pendulum_view import ViewSettings
from . import theme
from .controls import (
    ChoiceRow,
    FlowLayout,
    NumberRow,
    ScrollPage,
    Section,
    StepperRow,
    SwitchRow,
    ToggleChips,
    card,
    label,
    push_button,
    tool_button,
)
from .icons import icon
from .performance import FRAME_RATES, GRAPH_RATES_HZ, PerformanceSettings

GAIN_RANGE = 1e6


def loop_title(loop: PIDLoopConfig) -> str:
    return "Cart position loop" if loop.signal == "cart" else f"Segment {loop.signal[5:]} angle loop (θ{loop.signal[5:]})"


# ================================================================= simulate
class SimulatePage(QWidget):
    """Large pendulum view plus the essential run controls."""

    def __init__(self, view: QWidget) -> None:
        super().__init__()
        self.view = view
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(view, 1)
        self.bar = QWidget()
        self.bar.setObjectName("navbar")
        self.flow = FlowLayout(self.bar, spacing=8)
        self.flow.setContentsMargins(10, 8, 10, 8)
        outer.addWidget(self.bar)

    def add_controls(self, widgets: list[QWidget]) -> None:
        for w in widgets:
            self.flow.addWidget(w)

    def attach(self) -> None:
        """Take the pendulum view back (it is shared with the Graphs screen)."""
        if self.view.parentWidget() is not self:
            self.layout().insertWidget(0, self.view, 1)


# =================================================================== graphs
class GraphsPage(QWidget):
    """Graphs next to (landscape) or below (portrait) a smaller pendulum view.

    The view widget is shared with :class:`SimulatePage` and moved here while this
    screen is shown, so there is still only one view to draw. A compact bar repeats
    the run and push buttons.
    """

    def __init__(self, view: QWidget, graphs: QWidget, buttons: list[QWidget]) -> None:
        super().__init__()
        self.view = view
        self.graphs = graphs
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(0)
        self.left = QWidget()
        left = QVBoxLayout(self.left)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        self.bar = QWidget()
        self.bar.setObjectName("navbar")
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(8)
        for b in buttons:
            bar.addWidget(b)
        bar.addStretch(1)
        left.addWidget(self.bar)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 10, 10, 10)
        rl.addWidget(graphs)
        self.box.addWidget(self.left, 2)
        self.box.addWidget(right, 3)

    def attach(self) -> None:
        if self.view.parentWidget() is not self.left:
            self.left.layout().insertWidget(0, self.view, 1)

    def set_landscape(self, landscape: bool) -> None:
        self.box.setDirection(QBoxLayout.Direction.LeftToRight if landscape else QBoxLayout.Direction.TopToBottom)
        self.box.setStretch(0, 2 if landscape else 1)
        self.box.setStretch(1, 3 if landscape else 2)


# =============================================================== controller
class LoopSection(Section):
    changed = Signal()

    def __init__(self, loop: PIDLoopConfig, expanded: bool) -> None:
        super().__init__(loop_title(loop), expanded)
        self.loop = loop
        angle = loop.signal.startswith("theta")
        self.angle = angle
        unit = "rad" if angle else "m"
        self.enabled = self.add(SwitchRow("Loop enabled"))
        self.kp = self.add(NumberRow("Kp – proportional", -GAIN_RANGE, GAIN_RANGE, 0, 3, "", "symlog",
                                     hint=f"output per {unit}"))
        self.ki = self.add(NumberRow("Ki – integral", -GAIN_RANGE, GAIN_RANGE, 0, 3, "", "symlog",
                                     hint=f"output per {unit}·s"))
        self.kd = self.add(NumberRow("Kd – derivative", -GAIN_RANGE, GAIN_RANGE, 0, 3, "", "symlog",
                                     hint=f"output per {unit}/s"))
        self.terms = ToggleChips([("p", "P term"), ("i", "I term"), ("d", "D term")])
        self.add(label("Active terms"))
        self.add(self.terms)
        self.setpoint = self.add(NumberRow("Setpoint", -180.0 if angle else -10.0, 180.0 if angle else 10.0, 0,
                                           2 if angle else 3, "°" if angle else "m"))
        adv = Section("Integral & derivative details", expanded=False)
        adv.frame.setStyleSheet("QFrame#card { background: transparent; border: none; }")
        self.anti = adv.add(ChoiceRow("Anti-windup", [(AntiWindup.NONE.value, "None"),
                                                      (AntiWindup.CLAMP.value, "Clamping"),
                                                      (AntiWindup.BACK_CALCULATION.value, "Back-calculation")]))
        self.kt = adv.add(NumberRow("Tracking gain Kt", 0.0, 1e4, 10.0, 2, "1/s", "symlog"))
        self.ilimit = adv.add(NumberRow("Integral limit", 0.0, 1e6, 0.0, 3, "", "symlog",
                                        hint="|integral term| in output units, 0 = none"))
        self.dmode = adv.add(ChoiceRow("Derivative source", [(DerivativeMode.MEASURED_RATE.value, "Measured rate"),
                                                             (DerivativeMode.FILTERED_DIFFERENCE.value,
                                                              "Filtered difference")]))
        self.dfilter = adv.add(NumberRow("Derivative filter", 0.1, 5000.0, 50.0, 1, "Hz", "log"))
        self.add(adv)
        self.readout = self.add(label("", "secondary"))
        for row in (self.enabled, self.kp, self.ki, self.kd, self.setpoint, self.kt, self.ilimit, self.dfilter):
            sig = getattr(row, "valueChanged", None) or row.toggled
            sig.connect(lambda *_: self.changed.emit())
        self.anti.changed.connect(lambda *_: self.changed.emit())
        self.dmode.changed.connect(lambda *_: self.changed.emit())
        self.terms.changed.connect(lambda *_: self.changed.emit())
        self.set_loop(loop)

    def set_loop(self, c: PIDLoopConfig) -> None:
        self.loop = copy.deepcopy(c)
        self.enabled.setChecked(c.enabled)
        self.kp.setValue(c.kp)
        self.ki.setValue(c.ki)
        self.kd.setValue(c.kd)
        for key, on in (("p", c.p_enabled), ("i", c.i_enabled), ("d", c.d_enabled)):
            self.terms.set_checked(key, on)
        self.setpoint.setValue(math.degrees(c.setpoint) if self.angle else c.setpoint)
        self.anti.setValue(c.anti_windup.value)
        self.kt.setValue(c.back_calculation_gain)
        self.ilimit.setValue(c.integral_limit)
        self.dmode.setValue(c.derivative_mode.value)
        self.dfilter.setValue(c.derivative_filter_hz)
        self.set_title(f"{loop_title(c)}{'' if c.enabled else '  (off)'}")

    def config(self) -> PIDLoopConfig:
        sp = self.setpoint.value()
        return replace(
            self.loop,
            enabled=self.enabled.isChecked(),
            kp=self.kp.value(), ki=self.ki.value(), kd=self.kd.value(),
            p_enabled=self.terms.buttons["p"].isChecked(),
            i_enabled=self.terms.buttons["i"].isChecked(),
            d_enabled=self.terms.buttons["d"].isChecked(),
            setpoint=math.radians(sp) if self.angle else sp,
            anti_windup=AntiWindup(self.anti.value()),
            back_calculation_gain=self.kt.value(),
            integral_limit=self.ilimit.value(),
            derivative_mode=DerivativeMode(self.dmode.value()),
            derivative_filter_hz=self.dfilter.value(),
        )

    def show_output(self, error: float, p: float, i: float, d: float) -> None:
        e = f"{math.degrees(error):+.2f}°" if self.angle else f"{error:+.4f} m"
        self.readout.setText(f"live: error {e} · P {p:+.3g} · I {i:+.3g} · D {d:+.3g}")


class ControllerPage(ScrollPage):
    changed = Signal(object)            # ControllerConfig
    seedLqr = Signal(object)            # LQRWeights
    restoreRequested = Signal()
    resetIntegratorsRequested = Signal()
    autotuneRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._ctrl = ControllerConfig()
        self.loop_sections: list[LoopSection] = []

        tools, tl = card()
        tl.addWidget(label("Tuning", "title"))
        self.autotune_btn = push_button("Auto-tune for robustness", "autotune", primary=True)
        self.autotune_btn.clicked.connect(self.autotuneRequested)
        tl.addWidget(self.autotune_btn)
        row = QHBoxLayout()
        self.lqr_btn = push_button("Seed from LQR", "lqr")
        self.restore_btn = push_button("Restore gains", "reset")
        self.reset_i_btn = push_button("Reset integrators", "reset")
        for b in (self.lqr_btn, self.restore_btn):
            row.addWidget(b)
        tl.addLayout(row)
        tl.addWidget(self.reset_i_btn)
        self.add(tools)
        self.lqr_btn.clicked.connect(lambda: self.seedLqr.emit(self.weights()))
        self.restore_btn.clicked.connect(self.restoreRequested)
        self.reset_i_btn.clicked.connect(self.resetIntegratorsRequested)

        lqr = Section("LQR weights for seeding", expanded=False)
        w = LQRWeights()
        self.q = {
            "cart_position": lqr.add(NumberRow("Cart position weight", 1e-2, 1e3, w.cart_position, 3, "", "log")),
            "angle": lqr.add(NumberRow("Angle weight", 1e-1, 1e5, w.angle, 2, "", "log")),
            "cart_velocity": lqr.add(NumberRow("Cart velocity weight", 1e-3, 1e3, w.cart_velocity, 3, "", "log")),
            "angular_velocity": lqr.add(NumberRow("Angular velocity weight", 1e-3, 1e3, w.angular_velocity, 3, "",
                                                  "log")),
            "effort": lqr.add(NumberRow("Effort weight R", 1e-4, 1e1, w.effort, 4, "", "log",
                                        hint="larger = gentler control")),
        }
        self.add(lqr)

        glob = Section("Controller")
        self.enabled = glob.add(SwitchRow("Controller enabled"))
        self.action = glob.add(ChoiceRow("Action", [(ControlAction.DIRECT.value, "Direct  e = y − r"),
                                                    (ControlAction.REVERSE.value, "Reverse  e = r − y")]))
        self.freq = glob.add(NumberRow("Update frequency", 1.0, 10000.0, 500.0, 1, "Hz", "log"))
        self.out_min = glob.add(NumberRow("Output minimum", -1e6, 1e6, -30.0, 3, "", "symlog"))
        self.out_max = glob.add(NumberRow("Output maximum", -1e6, 1e6, 30.0, 3, "", "symlog"))
        self.output = glob.add(label("", "secondary"))
        self.add(glob)
        for row in (self.enabled, self.freq, self.out_min, self.out_max):
            sig = getattr(row, "valueChanged", None) or row.toggled
            sig.connect(lambda *_: self._emit())
        self.action.changed.connect(lambda *_: self._emit())
        self.loops_holder = QWidget()
        self.loops_layout = QVBoxLayout(self.loops_holder)
        self.loops_layout.setContentsMargins(0, 0, 0, 0)
        self.loops_layout.setSpacing(12)
        self.add(self.loops_holder)
        self.finish()

    def weights(self) -> LQRWeights:
        return LQRWeights(**{k: row.value() for k, row in self.q.items()})

    def set_config(self, c: ControllerConfig) -> None:
        self._ctrl = copy.deepcopy(c)
        self.enabled.setChecked(c.enabled)
        self.action.setValue(c.action.value)
        self.freq.setValue(c.frequency_hz)
        self.out_min.setValue(c.output_min)
        self.out_max.setValue(c.output_max)
        signals = [lp.signal for lp in c.loops]
        if signals != [s.loop.signal for s in self.loop_sections]:
            for s in self.loop_sections:
                s.setParent(None)
                s.deleteLater()
            self.loop_sections = []
            for k, lp in enumerate(c.loops):
                sec = LoopSection(lp, expanded=(k == 0 or lp.signal == "theta1"))
                sec.changed.connect(self._emit)
                self.loops_layout.addWidget(sec)
                self.loop_sections.append(sec)
        for sec, lp in zip(self.loop_sections, c.loops):
            sec.set_loop(lp)

    def config(self) -> ControllerConfig:
        c = copy.deepcopy(self._ctrl)
        c.enabled = self.enabled.isChecked()
        c.action = ControlAction(self.action.value())
        c.frequency_hz = self.freq.value()
        c.output_min = self.out_min.value()
        c.output_max = self.out_max.value()
        c.loops = [s.config() for s in self.loop_sections]
        return c

    def _emit(self) -> None:
        cfg = self.config()
        self._ctrl = copy.deepcopy(cfg)
        for sec in self.loop_sections:
            sec.set_title(f"{loop_title(sec.loop)}{'' if sec.enabled.isChecked() else '  (off)'}")
        self.changed.emit(cfg)

    def show_output(self, out) -> None:
        if not self.isVisible():
            return
        self.output.setText(f"Output: command {out.command:+.3f} · unsaturated {out.unsaturated:+.3f}")
        for sec, lo in zip(self.loop_sections, out.loops):
            if sec.body.isVisible():
                sec.show_output(lo.error, lo.p, lo.i, lo.d)


# =================================================================== system
class SegmentSection(Section):
    changed = Signal()

    def __init__(self, index: int, expanded: bool) -> None:
        super().__init__(f"Segment {index + 1}", expanded)
        self.length = self.add(NumberRow("Length", 0.02, 5.0, 0.5, 3, "m", "log"))
        self.mass = self.add(NumberRow("Mass", 0.001, 50.0, 0.2, 3, "kg", "log"))
        self.com = self.add(NumberRow("Centre of mass", 0.0, 1.0, 0.5, 3, "× length"))
        self.rod = self.add(SwitchRow("Uniform rod", "inertia I = m·L²/12", True))
        self.inertia = self.add(NumberRow("Inertia about the COM", 0.0, 10.0, 0.004, 6, "kg·m²", "symlog"))
        self.viscous = self.add(NumberRow("Joint viscous friction", 0.0, 5.0, 0.0005, 5, "N·m·s/rad", "symlog"))
        self.coulomb = self.add(NumberRow("Joint dry friction", 0.0, 5.0, 0.0, 4, "N·m", "symlog"))
        self.add(label("Initial conditions (used on reset)", "secondary"))
        self.angle = self.add(NumberRow("Initial angle θ₀", -180.0, 180.0, 0.0, 2, "°"))
        self.velocity = self.add(NumberRow("Initial angular velocity ω₀", -2000.0, 2000.0, 0.0, 1, "°/s", "symlog"))
        for row in (self.length, self.mass, self.com, self.inertia, self.viscous, self.coulomb,
                    self.angle, self.velocity):
            row.valueChanged.connect(lambda *_: self._changed())
        self.rod.toggled.connect(lambda *_: self._changed())

    def _changed(self) -> None:
        self._update_inertia()
        self.changed.emit()

    def _update_inertia(self) -> None:
        rod = self.rod.isChecked()
        self.inertia.setEnabled(not rod)
        if rod:
            self.inertia.setValue(SegmentParams.rod_inertia(self.mass.value(), self.length.value()))

    def set_segment(self, s: SegmentParams) -> None:
        self.length.setValue(s.length)
        self.mass.setValue(s.mass)
        self.com.setValue(s.com_ratio)
        self.inertia.setValue(s.inertia)
        self.rod.setChecked(math.isclose(s.inertia, SegmentParams.rod_inertia(s.mass, s.length),
                                         rel_tol=1e-6, abs_tol=1e-12))
        self.viscous.setValue(s.joint_viscous_friction)
        self.coulomb.setValue(s.joint_coulomb_friction)
        self.angle.setValue(math.degrees(s.initial_angle))
        self.velocity.setValue(math.degrees(s.initial_velocity))
        self._update_inertia()

    def segment(self) -> SegmentParams:
        return SegmentParams(
            length=self.length.value(), mass=self.mass.value(), com_ratio=self.com.value(),
            inertia=self.inertia.value(), joint_viscous_friction=self.viscous.value(),
            joint_coulomb_friction=self.coulomb.value(),
            initial_angle=math.radians(self.angle.value()), initial_velocity=math.radians(self.velocity.value()))


class SystemPage(ScrollPage):
    plantChanged = Signal(object, bool)      # PlantParams, structural
    simulationChanged = Signal(object)       # SimulationSettings

    def __init__(self) -> None:
        super().__init__()
        sys_sec = Section("Pendulum")
        self.base = sys_sec.add(ChoiceRow("Base", [(BaseType.CART.value, "Cart on a track"),
                                                   (BaseType.FIXED.value, "Fixed pivot")]))
        self.segments = sys_sec.add(StepperRow("Segments", 1, MAX_SEGMENTS, 1))
        self.gravity = sys_sec.add(NumberRow("Gravity", 0.0, 30.0, 9.81, 3, "m/s²"))
        self.add(sys_sec)
        self.cart_sec = Section("Cart")
        self.cart_mass = self.cart_sec.add(NumberRow("Cart mass", 0.01, 100.0, 1.0, 3, "kg", "log"))
        self.cart_friction = self.cart_sec.add(NumberRow("Cart friction", 0.0, 50.0, 0.1, 3, "N·s/m", "symlog"))
        self.track = self.cart_sec.add(NumberRow("Track length", 0.2, 50.0, 4.0, 2, "m", "log"))
        self.add(self.cart_sec)
        act = Section("Actuator", expanded=False)
        self.unit_label = act.add(label("", "secondary"))
        self.max_out = act.add(NumberRow("Max output", 0.001, 10000.0, 30.0, 3, "", "log"))
        self.rate = act.add(NumberRow("Rate limit", 0.0, 1e6, 0.0, 1, "/s", "symlog", hint="0 = unlimited"))
        self.tau = act.add(NumberRow("Time constant", 0.0, 1.0, 0.002, 4, "s", "symlog", hint="0 = ideal"))
        self.deadband = act.add(NumberRow("Deadband", 0.0, 1000.0, 0.0, 4, "", "symlog"))
        self.add(act)
        self.seg_holder = QWidget()
        self.seg_layout = QVBoxLayout(self.seg_holder)
        self.seg_layout.setContentsMargins(0, 0, 0, 0)
        self.seg_layout.setSpacing(12)
        self.seg_sections = [SegmentSection(i, expanded=(i == 0)) for i in range(MAX_SEGMENTS)]
        for sec in self.seg_sections:
            self.seg_layout.addWidget(sec)
            sec.changed.connect(self._emit_plant)
        self.add(self.seg_holder)

        sim = Section("Simulation", expanded=False)
        self.dt = sim.add(NumberRow("Physics time step", 0.01, 10.0, 1.0, 3, "ms", "log",
                                    hint="smaller = more accurate, slower"))
        self.telemetry = sim.add(NumberRow("Telemetry rate", 1.0, 2000.0, 200.0, 0, "Hz", "log"))
        sim.add(label("Sensor noise (Gaussian, seeded – runs stay repeatable)", "secondary"))
        self.noise_angle = sim.add(NumberRow("Angle noise", 0.0, 10.0, 0.0, 3, "°", "symlog"))
        self.noise_rate = sim.add(NumberRow("Angular-rate noise", 0.0, 100.0, 0.0, 2, "°/s", "symlog"))
        self.noise_pos = sim.add(NumberRow("Cart-position noise", 0.0, 0.1, 0.0, 4, "m", "symlog"))
        self.seed = sim.add(NumberRow("Noise seed", 0, 2**31 - 1, 1, 0, "", slider=False))
        self.add(sim)
        self.finish()

        self.base.changed.connect(lambda *_: self._emit_plant())
        self.segments.valueChanged.connect(lambda *_: self._emit_plant())
        for row in (self.gravity, self.cart_mass, self.cart_friction, self.track, self.max_out, self.rate,
                    self.tau, self.deadband):
            row.valueChanged.connect(lambda *_: self._emit_plant())
        for row in (self.dt, self.telemetry, self.noise_angle, self.noise_rate, self.noise_pos, self.seed):
            row.valueChanged.connect(lambda *_: self.simulationChanged.emit(self.simulation()))
        self._structure = (1, BaseType.CART.value)

    def _refresh_visibility(self) -> None:
        n = self.segments.value()
        for i, sec in enumerate(self.seg_sections):
            sec.setVisible(i < n)
        cart = self.base.value() == BaseType.CART.value
        self.cart_sec.setVisible(cart)
        self.unit_label.setText("Force on the cart, N" if cart else "Torque at the pivot, N·m")

    def set_plant(self, plant: PlantParams) -> None:
        self.base.setValue(plant.base_type.value)
        self.segments.setValue(plant.n_segments)
        self.gravity.setValue(plant.gravity)
        self.cart_mass.setValue(plant.cart_mass)
        self.cart_friction.setValue(plant.cart_viscous_friction)
        self.track.setValue(plant.track_length)
        a = plant.actuator
        self.max_out.setValue(a.max_output)
        self.rate.setValue(a.rate_limit)
        self.tau.setValue(a.time_constant)
        self.deadband.setValue(a.deadband)
        for i, seg in enumerate(plant.segments):
            self.seg_sections[i].set_segment(seg)
        for i in range(plant.n_segments, MAX_SEGMENTS):   # unused editors: copy of the last segment at rest
            template = copy.deepcopy(plant.segments[-1])
            template.initial_angle = template.initial_velocity = 0.0
            self.seg_sections[i].set_segment(template)
        self._structure = (plant.n_segments, plant.base_type.value)
        self._refresh_visibility()

    def plant(self) -> PlantParams:
        n = self.segments.value()
        return PlantParams(
            base_type=BaseType(self.base.value()), gravity=self.gravity.value(), cart_mass=self.cart_mass.value(),
            cart_viscous_friction=self.cart_friction.value(), track_length=self.track.value(),
            segments=[self.seg_sections[i].segment() for i in range(n)],
            actuator=ActuatorParams(max_output=self.max_out.value(), rate_limit=self.rate.value(),
                                    time_constant=self.tau.value(), deadband=self.deadband.value()))

    def _emit_plant(self) -> None:
        plant = self.plant()
        structure = (plant.n_segments, plant.base_type.value)
        structural = structure != self._structure
        self._structure = structure
        self._refresh_visibility()
        self.plantChanged.emit(plant, structural)

    def set_simulation(self, s: SimulationSettings) -> None:
        self.dt.setValue(s.dt * 1000.0)
        self.telemetry.setValue(s.telemetry_rate_hz)
        self.noise_angle.setValue(s.sensor_noise_angle_deg)
        self.noise_rate.setValue(s.sensor_noise_rate_dps)
        self.noise_pos.setValue(s.sensor_noise_position)
        self.seed.setValue(s.noise_seed)

    def simulation(self) -> SimulationSettings:
        return SimulationSettings(dt=self.dt.value() / 1000.0, sensor_noise_angle_deg=self.noise_angle.value(),
                                  sensor_noise_rate_dps=self.noise_rate.value(),
                                  sensor_noise_position=self.noise_pos.value(), noise_seed=int(self.seed.value()),
                                  telemetry_rate_hz=self.telemetry.value())


# ================================================================= settings
RENDER_LABELS = {"native": "Native", "high": "1.5×", "balanced": "1.25×", "fast": "1×"}


class SettingsPage(ScrollPage):
    viewChanged = Signal(object)            # ViewSettings
    performanceChanged = Signal(object)     # PerformanceSettings
    openRequested = Signal()
    saveRequested = Signal()
    saveAsRequested = Signal()
    clearTrailsRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        files, fl = card()
        fl.addWidget(label("Configuration file", "title"))
        row = QHBoxLayout()
        self.open_btn = push_button("Open", "open")
        self.save_btn = push_button("Save", "save")
        self.save_as_btn = push_button("Save as…", "save")
        for b in (self.open_btn, self.save_btn, self.save_as_btn):
            row.addWidget(b)
        fl.addLayout(row)
        self.add(files)
        self.open_btn.clicked.connect(self.openRequested)
        self.save_btn.clicked.connect(self.saveRequested)
        self.save_as_btn.clicked.connect(self.saveAsRequested)

        disp = Section("Pendulum view")
        v = ViewSettings()
        self.view_rows = {
            "show_hud": disp.add(SwitchRow("State read-out", "time, angles, rates, force", v.show_hud)),
            "show_trails": disp.add(SwitchRow("Tip trajectory", "", v.show_trails)),
            "trail_all_joints": disp.add(SwitchRow("Trajectories of all joints", "", v.trail_all_joints)),
            "show_labels": disp.add(SwitchRow("Angle arcs and labels", "", v.show_labels)),
            "show_velocity": disp.add(SwitchRow("Angular-velocity arrows", "", v.show_velocity)),
            "show_force": disp.add(SwitchRow("Actuator and push arrows", "", v.show_force)),
            "show_com": disp.add(SwitchRow("Centres of mass", "", v.show_com)),
            "show_equilibrium": disp.add(SwitchRow("Upright equilibrium (ghost)", "", v.show_equilibrium)),
            "show_grid": disp.add(SwitchRow("Grid", "", v.show_grid)),
            "follow_cart": disp.add(SwitchRow("Camera follows the cart", "", v.follow_cart)),
        }
        self.trail_len = disp.add(NumberRow("Trajectory length", 10, 5000, v.trail_length, 0, "frames", "log"))
        self.link_width = disp.add(NumberRow("Segment width", 2.0, 40.0, v.link_width_px, 1, "px"))
        clear = push_button("Clear trajectories", "close")
        clear.clicked.connect(self.clearTrailsRequested)
        disp.add(clear)
        self.add(disp)
        for row in self.view_rows.values():
            row.toggled.connect(lambda *_: self.viewChanged.emit(self.view_settings()))
        for row in (self.trail_len, self.link_width):
            row.valueChanged.connect(lambda *_: self.viewChanged.emit(self.view_settings()))

        perf = Section("Performance")
        self.perf_render = perf.add(ChoiceRow("Pendulum drawing resolution",
                                              list(RENDER_LABELS.items()),
                                              hint="Native is sharpest; lower only if the view stutters"))
        self.perf_graph = perf.add(ChoiceRow("Graph refresh (maximum)", [(hz, f"{hz} Hz") for hz in GRAPH_RATES_HZ]))
        self.perf_fps = perf.add(ChoiceRow("Frame rate", [(f, f"{f} fps") for f in FRAME_RATES]))
        self.perf_aa = perf.add(SwitchRow("Antialiased pendulum view"))
        self.perf_cores = perf.add(SwitchRow("Use performance CPU cores",
                                             "keeps the simulation on the fast cores of the processor"))
        self.add(perf)
        debug = Section("Debug", expanded=False)
        self.perf_stats = debug.add(SwitchRow("Performance overlay", "fps and per-component frame time on the view"))
        self.perf_log = debug.add(SwitchRow("Log timings", "one line per second to the system log (adb logcat)"))
        self.cpu_label = debug.add(label("", "secondary"))
        self.add(debug)
        for row in (self.perf_render, self.perf_graph, self.perf_fps):
            row.changed.connect(lambda *_: self.performanceChanged.emit(self.performance()))
        for row in (self.perf_aa, self.perf_cores, self.perf_stats, self.perf_log):
            row.toggled.connect(lambda *_: self.performanceChanged.emit(self.performance()))

        about, al = card()
        al.addWidget(label("Inverted Pendulum Simulator", "title"))
        al.addWidget(label("Multibody 1–3 segment pendulum on a cart or fixed pivot, multi-loop PID control "
                           "with LQR seeding and robust auto-tuning, experiments and comparison. Same physics "
                           "and controller code as the desktop version.", "secondary"))
        self.add(about)
        self.finish()

    def view_settings(self) -> ViewSettings:
        values = {k: row.isChecked() for k, row in self.view_rows.items()}
        return replace(ViewSettings(), trail_length=int(self.trail_len.value()),
                       link_width_px=self.link_width.value(), **values)

    def set_performance(self, p: PerformanceSettings) -> None:
        self.perf_render.setValue(p.render)
        self.perf_graph.setValue(p.graph_hz)
        self.perf_fps.setValue(p.fps)
        for row, on in ((self.perf_aa, p.antialias), (self.perf_cores, p.fast_cores),
                        (self.perf_stats, p.debug_overlay), (self.perf_log, p.log_stats)):
            row.setChecked(on)

    def performance(self) -> PerformanceSettings:
        return PerformanceSettings(render=self.perf_render.value() or "native",
                                   graph_hz=self.perf_graph.value() or 10, fps=self.perf_fps.value() or 60,
                                   antialias=self.perf_aa.isChecked(), debug_overlay=self.perf_stats.isChecked(),
                                   log_stats=self.perf_log.isChecked(), fast_cores=self.perf_cores.isChecked())


# =============================================================== navigation
class NavBar(QWidget):
    """Navigation rail (tablet landscape) or bottom bar (portrait / phones)."""

    selected = Signal(str)

    def __init__(self, items: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self.setObjectName("navbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.items = items
        self.buttons: dict[str, QToolButton] = {}
        for key, text, icon_name in items:
            b = tool_button(icon_name, text, tip=text, size=26)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setProperty("nav", True)
            b.setIcon(icon(icon_name, theme.TEXT_2, theme.DISABLED, theme.TEXT))
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            b.clicked.connect(lambda _=False, k=key: self.selected.emit(k))
            self.buttons[key] = b
        self.layout_: QHBoxLayout | QVBoxLayout | None = None
        self.vertical = None
        self.set_vertical(False)

    def set_vertical(self, vertical: bool, visible_keys: list[str] | None = None) -> None:
        if self.layout_ is not None and self.vertical == vertical and visible_keys is None:
            return
        self.vertical = vertical
        old = self.layout_
        if old is not None:
            while old.count():
                old.takeAt(0)
            QWidget().setLayout(old)          # discard the old layout
        lay = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        keys = visible_keys or [k for k, _, _ in self.items]
        for key, _t, _i in self.items:
            b = self.buttons[key]
            b.setVisible(key in keys)
            if key in keys:
                lay.addWidget(b)
            b.setMinimumSize(QSize(76, 64) if vertical else QSize(56, 60))
        if vertical:
            lay.addStretch(1)
        self.layout_ = lay

    def set_current(self, key: str) -> None:
        for k, b in self.buttons.items():
            b.setChecked(k == key)
