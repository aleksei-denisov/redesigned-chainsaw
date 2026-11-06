"""PID controller configuration panel with live component readout."""

from __future__ import annotations

import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..control import (
    AntiWindup,
    ControlAction,
    ControllerConfig,
    ControllerOutput,
    DerivativeMode,
    LQRWeights,
    PIDLoopConfig,
)
from ..physics import PlantParams
from .widgets import check, group, hint, set_silently, spin

GAIN_RANGE = 1e6


class LoopEditor(QWidget):
    changed = Signal()

    def __init__(self, config: PIDLoopConfig) -> None:
        super().__init__()
        self.signal = config.signal
        self._name = config.name
        angle = config.signal.startswith("theta")
        unit = "rad" if angle else "m"
        layout = QVBoxLayout(self)

        box, form = group("Gains")
        self.enabled = check("Loop enabled", True)
        self.kp = spin(-GAIN_RANGE, GAIN_RANGE, 0.0, 3, 1.0, "", f"Proportional gain [output/{unit}]")
        self.ki = spin(-GAIN_RANGE, GAIN_RANGE, 0.0, 3, 1.0, "", f"Integral gain [output/({unit}·s)]")
        self.kd = spin(-GAIN_RANGE, GAIN_RANGE, 0.0, 3, 0.1, "", f"Derivative gain [output/({unit}/s)]")
        self.p_on = check("P", True, "Enable the proportional term")
        self.i_on = check("I", True, "Enable the integral term")
        self.d_on = check("D", True, "Enable the derivative term")
        terms = QHBoxLayout()
        for w in (self.p_on, self.i_on, self.d_on):
            terms.addWidget(w)
        terms.addStretch(1)
        terms_w = QWidget()
        terms_w.setLayout(terms)
        self.setpoint = spin(-180.0 if angle else -10.0, 180.0 if angle else 10.0, 0.0, 3, 1.0 if angle else 0.05,
                             "°" if angle else "m", "Reference value of this loop")
        form.addRow("", self.enabled)
        form.addRow("Kp", self.kp)
        form.addRow("Ki", self.ki)
        form.addRow("Kd", self.kd)
        form.addRow("Components", terms_w)
        form.addRow("Setpoint", self.setpoint)
        layout.addWidget(box)

        abox, aform = group("Integral & derivative")
        self.anti = QComboBox()
        self.anti.addItem("None", AntiWindup.NONE.value)
        self.anti.addItem("Clamping (conditional integration)", AntiWindup.CLAMP.value)
        self.anti.addItem("Back-calculation", AntiWindup.BACK_CALCULATION.value)
        self.kt = spin(0.0, 1e4, 10.0, 2, 1.0, "1/s", "Back-calculation tracking gain")
        self.ilimit = spin(0.0, 1e6, 0.0, 3, 0.5, "", "Limit of |integral term| in output units (0 = none)")
        self.dmode = QComboBox()
        self.dmode.addItem("Measured rate (encoder/gyro)", DerivativeMode.MEASURED_RATE.value)
        self.dmode.addItem("Filtered difference of the error", DerivativeMode.FILTERED_DIFFERENCE.value)
        self.dfilter = spin(0.1, 5000.0, 50.0, 1, 5.0, "Hz", "Low-pass cut-off for the filtered derivative")
        aform.addRow("Anti-windup", self.anti)
        aform.addRow("Tracking gain Kt", self.kt)
        aform.addRow("Integral limit", self.ilimit)
        aform.addRow("Derivative source", self.dmode)
        aform.addRow("Derivative filter", self.dfilter)
        layout.addWidget(abox)

        rbox, rform = group("Live components")
        self.readout: dict[str, QLabel] = {}
        names = [("error", "Error"), ("p", "P term"), ("i", "I term"), ("d", "D term"), ("total", "Sum")]
        for key, label in names:
            value = QLabel("—")
            value.setMinimumWidth(90)
            self.readout[key] = value
            rform.addRow(label, value)
        layout.addWidget(rbox)
        layout.addStretch(1)

        for w in (self.kp, self.ki, self.kd, self.setpoint, self.kt, self.ilimit, self.dfilter):
            w.valueChanged.connect(self.changed)
        for w in (self.enabled, self.p_on, self.i_on, self.d_on):
            w.toggled.connect(self.changed)
        for w in (self.anti, self.dmode):
            w.currentIndexChanged.connect(self.changed)
        self.set_config(config)

    def set_config(self, c: PIDLoopConfig) -> None:
        self._name = c.name
        set_silently(self.enabled, c.enabled)
        set_silently(self.kp, c.kp)
        set_silently(self.ki, c.ki)
        set_silently(self.kd, c.kd)
        set_silently(self.p_on, c.p_enabled)
        set_silently(self.i_on, c.i_enabled)
        set_silently(self.d_on, c.d_enabled)
        set_silently(self.setpoint, math.degrees(c.setpoint) if c.is_angle else c.setpoint)
        set_silently(self.kt, c.back_calculation_gain)
        set_silently(self.ilimit, c.integral_limit)
        set_silently(self.dfilter, c.derivative_filter_hz)
        for combo, value in ((self.anti, c.anti_windup.value), (self.dmode, c.derivative_mode.value)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)

    def config(self) -> PIDLoopConfig:
        angle = self.signal.startswith("theta")
        sp = self.setpoint.value()
        return PIDLoopConfig(
            name=self._name,
            signal=self.signal,
            enabled=self.enabled.isChecked(),
            setpoint=math.radians(sp) if angle else sp,
            kp=self.kp.value(),
            ki=self.ki.value(),
            kd=self.kd.value(),
            p_enabled=self.p_on.isChecked(),
            i_enabled=self.i_on.isChecked(),
            d_enabled=self.d_on.isChecked(),
            integral_limit=self.ilimit.value(),
            anti_windup=AntiWindup(self.anti.currentData()),
            back_calculation_gain=self.kt.value(),
            derivative_mode=DerivativeMode(self.dmode.currentData()),
            derivative_filter_hz=self.dfilter.value(),
        )

    def show_output(self, error: float, p: float, i: float, d: float) -> None:
        angle = self.signal.startswith("theta")
        self.readout["error"].setText(f"{math.degrees(error):+.3f} °" if angle else f"{error:+.4f} m")
        for key, v in (("p", p), ("i", i), ("d", d), ("total", p + i + d)):
            self.readout[key].setText(f"{v:+.4f}")


class LQRDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Seed gains from LQR")
        layout = QVBoxLayout(self)
        layout.addWidget(hint(
            "Computes a linear-quadratic regulator for the plant linearised about the upright "
            "position and writes the resulting state-feedback gains into the P and D terms of "
            "every loop (integral gains are kept). Larger weights penalise that error more."))
        box, form = group("Weights")
        w = LQRWeights()
        self.qx = spin(0.0, 1e6, w.cart_position, 3, 1.0)
        self.qth = spin(0.0, 1e6, w.angle, 3, 10.0)
        self.qxd = spin(0.0, 1e6, w.cart_velocity, 3, 0.5)
        self.qw = spin(0.0, 1e6, w.angular_velocity, 3, 0.5)
        self.r = spin(1e-5, 1e5, w.effort, 5, 0.05)
        form.addRow("Cart position", self.qx)
        form.addRow("Segment angles", self.qth)
        form.addRow("Cart velocity", self.qxd)
        form.addRow("Angular velocities", self.qw)
        form.addRow("Control effort (R)", self.r)
        layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def weights(self) -> LQRWeights:
        return LQRWeights(self.qx.value(), self.qth.value(), self.qxd.value(), self.qw.value(), self.r.value())


class ControllerPanel(QWidget):
    changed = Signal(object)              # ControllerConfig
    autoTuneRequested = Signal(object)    # LQRWeights
    resetIntegratorsRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        box, form = group("Controller")
        self.enabled = check("Controller enabled", True)
        self.action = QComboBox()
        self.action.addItem("Direct  (e = y − r)", ControlAction.DIRECT.value)
        self.action.addItem("Reverse (e = r − y)", ControlAction.REVERSE.value)
        self.action.setToolTip("Direct: output follows the deviation (cart drives towards the lean).\n"
                               "Reverse: output opposes the deviation (motor torque rotates back).")
        self.freq = spin(1.0, 10000.0, 500.0, 1, 50.0, "Hz", "Controller sample rate (zero-order hold)")
        self.out_min = spin(-1e6, 1e6, -30.0, 3, 1.0, "", "Lower controller output limit")
        self.out_max = spin(-1e6, 1e6, 30.0, 3, 1.0, "", "Upper controller output limit")
        form.addRow("", self.enabled)
        form.addRow("Action", self.action)
        form.addRow("Update frequency", self.freq)
        form.addRow("Output min", self.out_min)
        form.addRow("Output max", self.out_max)
        self.command_label = QLabel("—")
        form.addRow("Output (cmd / unsat.)", self.command_label)
        layout.addWidget(box)

        buttons = QHBoxLayout()
        self.tune_btn = QPushButton("Seed gains from LQR…")
        self.tune_btn.setToolTip("Compute P/D gains for all loops from an LQR design")
        self.reset_i_btn = QPushButton("Reset integrators")
        buttons.addWidget(self.tune_btn)
        buttons.addWidget(self.reset_i_btn)
        layout.addLayout(buttons)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        layout.addWidget(hint(
            "One PID loop per signal; the loop outputs are summed into a single actuator command. "
            "Gains apply immediately. Positive angle-loop gains balance a single segment; inner "
            "segments of multi-link pendulums typically need mixed signs – use LQR seeding."))
        layout.addStretch(1)
        self.editors: list[LoopEditor] = []

        self.enabled.toggled.connect(self._emit)
        self.action.currentIndexChanged.connect(self._emit)
        for w in (self.freq, self.out_min, self.out_max):
            w.valueChanged.connect(self._emit)
        self.tune_btn.clicked.connect(self._ask_tune)
        self.reset_i_btn.clicked.connect(self.resetIntegratorsRequested)

    @staticmethod
    def complete_loops(config: ControllerConfig, plant: PlantParams) -> ControllerConfig:
        """Ensure there is one loop per available signal (disabled zero-gain if new)."""
        signals = (["cart"] if plant.has_cart else []) + [f"theta{i + 1}" for i in range(plant.n_segments)]
        existing = {loop.signal: loop for loop in config.loops}
        loops = []
        for sig in signals:
            if sig in existing:
                loops.append(existing[sig])
            else:
                name = "Cart position" if sig == "cart" else f"Segment {sig[5:]} angle"
                loops.append(PIDLoopConfig(name=name, signal=sig, enabled=False, kp=0.0, ki=0.0, kd=0.0))
        config.loops = loops
        return config

    def set_config(self, config: ControllerConfig) -> None:
        set_silently(self.enabled, config.enabled)
        self.action.blockSignals(True)
        self.action.setCurrentIndex(max(0, self.action.findData(config.action.value)))
        self.action.blockSignals(False)
        set_silently(self.freq, config.frequency_hz)
        set_silently(self.out_min, config.output_min)
        set_silently(self.out_max, config.output_max)
        current = self.tabs.currentIndex()
        while self.tabs.count():
            self.tabs.removeTab(0)
        for e in self.editors:
            e.deleteLater()
        self.editors = []
        for loop in config.loops:
            editor = LoopEditor(loop)
            editor.changed.connect(self._emit)
            self.editors.append(editor)
            label = "Cart x" if loop.signal == "cart" else f"θ{loop.signal[5:]}"
            self.tabs.addTab(editor, label)
        if self.editors:
            self.tabs.setCurrentIndex(min(max(current, 0), len(self.editors) - 1))

    def config(self) -> ControllerConfig:
        return ControllerConfig(
            enabled=self.enabled.isChecked(),
            frequency_hz=self.freq.value(),
            output_min=self.out_min.value(),
            output_max=self.out_max.value(),
            action=ControlAction(self.action.currentData()),
            loops=[e.config() for e in self.editors],
        )

    def _emit(self) -> None:
        if self.out_min.value() >= self.out_max.value():
            self.command_label.setText("⚠ output min must be < max")
            return
        self.changed.emit(self.config())

    def _ask_tune(self) -> None:
        dialog = LQRDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.autoTuneRequested.emit(dialog.weights())

    def show_output(self, out: ControllerOutput) -> None:
        self.command_label.setText(f"{out.command:+.3f} / {out.unsaturated:+.3f}")
        for editor, loop_out in zip(self.editors, out.loops):
            editor.show_output(loop_out.error, loop_out.p, loop_out.i, loop_out.d)
