"""Physical model configuration panel."""

from __future__ import annotations

import copy
import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLabel, QTabWidget, QVBoxLayout, QWidget

from ..physics import MAX_SEGMENTS, ActuatorParams, BaseType, PlantParams, SegmentParams
from .widgets import check, group, hint, int_spin, set_silently, spin


class SegmentEditor(QWidget):
    changed = Signal()

    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index
        layout = QVBoxLayout(self)
        box, form = group(f"Segment {index + 1}")
        self.length = spin(0.02, 5.0, 0.5, 3, 0.05, "m", "Distance between the segment's joints")
        self.mass = spin(0.001, 50.0, 0.2, 3, 0.05, "kg")
        self.com = spin(0.0, 1.0, 0.5, 3, 0.05, "", "Centre-of-mass position as a fraction of the length, "
                                                    "measured from the lower joint (0.5 = uniform rod)")
        self.inertia = spin(0.0, 10.0, 0.004, 6, 0.0005, "kg·m²", "Moment of inertia about the centre of mass")
        self.auto_inertia = check("Uniform rod  (I = m·L²/12)", True)
        self.viscous = spin(0.0, 5.0, 0.0005, 5, 0.0005, "N·m·s/rad", "Viscous friction of the joint below this segment")
        self.coulomb = spin(0.0, 5.0, 0.0, 4, 0.001, "N·m", "Dry (Coulomb) friction torque of the joint below")
        self.angle = spin(-180.0, 180.0, 0.0, 2, 1.0, "°", "Initial absolute angle from the upward vertical "
                                                        "(positive leans towards +x / clockwise)")
        self.velocity = spin(-2000.0, 2000.0, 0.0, 1, 5.0, "°/s", "Initial angular velocity")
        form.addRow("Length", self.length)
        form.addRow("Mass", self.mass)
        form.addRow("Centre of mass", self.com)
        form.addRow("Inertia (COM)", self.inertia)
        form.addRow("", self.auto_inertia)
        form.addRow("Joint viscous friction", self.viscous)
        form.addRow("Joint Coulomb friction", self.coulomb)
        layout.addWidget(box)
        ibox, iform = group("Initial conditions (applied on reset)")
        iform.addRow("Angle θ₀", self.angle)
        iform.addRow("Angular velocity ω₀", self.velocity)
        layout.addWidget(ibox)
        layout.addStretch(1)
        for w in (self.length, self.mass, self.com, self.inertia, self.viscous, self.coulomb,
                  self.angle, self.velocity):
            w.valueChanged.connect(self._on_change)
        self.auto_inertia.toggled.connect(self._on_change)
        self._update_inertia_state()

    def _update_inertia_state(self) -> None:
        auto = self.auto_inertia.isChecked()
        self.inertia.setEnabled(not auto)
        if auto:
            set_silently(self.inertia, SegmentParams.rod_inertia(self.mass.value(), self.length.value()))

    def _on_change(self) -> None:
        self._update_inertia_state()
        self.changed.emit()

    def set_segment(self, s: SegmentParams) -> None:
        set_silently(self.length, s.length)
        set_silently(self.mass, s.mass)
        set_silently(self.com, s.com_ratio)
        set_silently(self.inertia, s.inertia)
        rod = SegmentParams.rod_inertia(s.mass, s.length)
        set_silently(self.auto_inertia, math.isclose(s.inertia, rod, rel_tol=1e-6, abs_tol=1e-12))
        set_silently(self.viscous, s.joint_viscous_friction)
        set_silently(self.coulomb, s.joint_coulomb_friction)
        set_silently(self.angle, math.degrees(s.initial_angle))
        set_silently(self.velocity, math.degrees(s.initial_velocity))
        self._update_inertia_state()

    def segment(self) -> SegmentParams:
        return SegmentParams(
            length=self.length.value(),
            mass=self.mass.value(),
            com_ratio=self.com.value(),
            inertia=self.inertia.value(),
            joint_viscous_friction=self.viscous.value(),
            joint_coulomb_friction=self.coulomb.value(),
            initial_angle=math.radians(self.angle.value()),
            initial_velocity=math.radians(self.velocity.value()),
        )


class PlantPanel(QWidget):
    """Emits ``changed(plant, structural)``; ``structural`` = segment count or base type changed."""

    changed = Signal(object, bool)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        box, form = group("System")
        self.base = QComboBox()
        self.base.addItem("Cart on a track (force actuator)", BaseType.CART.value)
        self.base.addItem("Fixed pivot (base torque motor)", BaseType.FIXED.value)
        self.segments = int_spin(1, MAX_SEGMENTS, 1, "Number of connected segments")
        self.gravity = spin(0.0, 30.0, 9.81, 3, 0.1, "m/s²")
        form.addRow("Base", self.base)
        form.addRow("Segments", self.segments)
        form.addRow("Gravity", self.gravity)
        layout.addWidget(box)

        self.cart_box, cform = group("Cart")
        self.cart_mass = spin(0.01, 100.0, 1.0, 3, 0.1, "kg")
        self.cart_friction = spin(0.0, 50.0, 0.1, 3, 0.05, "N·s/m")
        self.track = spin(0.2, 50.0, 4.0, 2, 0.5, "m", "Usable track length; soft end stops beyond it")
        cform.addRow("Cart mass", self.cart_mass)
        cform.addRow("Cart friction", self.cart_friction)
        cform.addRow("Track length", self.track)
        layout.addWidget(self.cart_box)

        abox, aform = group("Actuator")
        self.unit_label = QLabel("N")
        self.max_out = spin(0.001, 10000.0, 30.0, 3, 1.0, "", "Saturation limit of the actuator")
        self.rate = spin(0.0, 1e6, 0.0, 1, 10.0, "/s", "Slew-rate limit (0 = unlimited)")
        self.tau = spin(0.0, 1.0, 0.002, 4, 0.001, "s", "First-order lag time constant (0 = ideal)")
        self.deadband = spin(0.0, 1000.0, 0.0, 4, 0.01, "", "Commands smaller than this produce no output")
        aform.addRow("Output unit", self.unit_label)
        aform.addRow("Max output", self.max_out)
        aform.addRow("Rate limit", self.rate)
        aform.addRow("Time constant", self.tau)
        aform.addRow("Deadband", self.deadband)
        layout.addWidget(abox)

        self.tabs = QTabWidget()
        self.editors = [SegmentEditor(i) for i in range(MAX_SEGMENTS)]
        layout.addWidget(self.tabs)
        layout.addWidget(hint("Physical parameters apply immediately while running. "
                              "Changing the base type or the number of segments resets the simulation. "
                              "Initial conditions apply on Reset / Restart."))
        layout.addStretch(1)

        self._last_structure: tuple[int, str] | None = None
        self.base.currentIndexChanged.connect(self._emit)
        self.segments.valueChanged.connect(self._emit)
        for w in (self.gravity, self.cart_mass, self.cart_friction, self.track,
                  self.max_out, self.rate, self.tau, self.deadband):
            w.valueChanged.connect(self._emit)
        for e in self.editors:
            e.changed.connect(self._emit)
        self._refresh_tabs()

    def _refresh_tabs(self) -> None:
        n = self.segments.value()
        while self.tabs.count():
            self.tabs.removeTab(0)
        for i in range(n):
            self.tabs.addTab(self.editors[i], f"Segment {i + 1}")
        cart = self.base.currentData() == BaseType.CART.value
        self.cart_box.setVisible(cart)
        self.unit_label.setText("N (force on the cart)" if cart else "N·m (torque at the pivot)")

    def set_plant(self, plant: PlantParams) -> None:
        self.base.blockSignals(True)
        self.base.setCurrentIndex(0 if plant.has_cart else 1)
        self.base.blockSignals(False)
        set_silently(self.segments, plant.n_segments)
        set_silently(self.gravity, plant.gravity)
        set_silently(self.cart_mass, plant.cart_mass)
        set_silently(self.cart_friction, plant.cart_viscous_friction)
        set_silently(self.track, plant.track_length)
        a = plant.actuator
        set_silently(self.max_out, a.max_output)
        set_silently(self.rate, a.rate_limit)
        set_silently(self.tau, a.time_constant)
        set_silently(self.deadband, a.deadband)
        for i, seg in enumerate(plant.segments):
            self.editors[i].set_segment(seg)
        # Unused editors keep a sensible copy of the last segment.
        for i in range(plant.n_segments, MAX_SEGMENTS):
            template = copy.deepcopy(plant.segments[-1])
            template.initial_angle = template.initial_velocity = 0.0
            self.editors[i].set_segment(template)
        self._last_structure = (plant.n_segments, plant.base_type.value)
        self._refresh_tabs()

    def plant(self) -> PlantParams:
        n = self.segments.value()
        return PlantParams(
            base_type=BaseType(self.base.currentData()),
            gravity=self.gravity.value(),
            cart_mass=self.cart_mass.value(),
            cart_viscous_friction=self.cart_friction.value(),
            track_length=self.track.value(),
            segments=[self.editors[i].segment() for i in range(n)],
            actuator=ActuatorParams(
                max_output=self.max_out.value(),
                rate_limit=self.rate.value(),
                time_constant=self.tau.value(),
                deadband=self.deadband.value(),
            ),
        )

    def _emit(self) -> None:
        plant = self.plant()
        structure = (plant.n_segments, plant.base_type.value)
        structural = structure != self._last_structure
        self._last_structure = structure
        if structural:
            current = self.tabs.currentIndex()
            self._refresh_tabs()
            self.tabs.setCurrentIndex(min(current, plant.n_segments - 1))
        self.changed.emit(plant, structural)
