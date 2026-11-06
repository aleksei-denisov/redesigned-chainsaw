"""Numerical/sensor settings and visualisation settings."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from ..simulation import SimulationSettings
from .pendulum_view import ViewSettings
from .widgets import check, group, hint, int_spin, set_silently, spin


class SettingsPanel(QWidget):
    simulationChanged = Signal(object)  # SimulationSettings
    viewChanged = Signal(object)        # ViewSettings
    zoomChanged = Signal(float)
    fitRequested = Signal()
    clearTrailsRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        box, form = group("Numerics")
        self.dt = spin(0.01, 10.0, 1.0, 3, 0.1, "ms", "Fixed physics integration step (RK4)")
        self.telemetry = spin(1.0, 2000.0, 200.0, 0, 50.0, "Hz", "Telemetry recording rate")
        form.addRow("Physics time step", self.dt)
        form.addRow("Telemetry rate", self.telemetry)
        layout.addWidget(box)

        nbox, nform = group("Sensor noise (Gaussian)")
        self.noise_angle = spin(0.0, 10.0, 0.0, 3, 0.05, "°")
        self.noise_rate = spin(0.0, 100.0, 0.0, 2, 0.5, "°/s")
        self.noise_pos = spin(0.0, 0.1, 0.0, 4, 0.0005, "m")
        self.seed = int_spin(0, 2**31 - 1, 1, "Identical seeds give identical noise sequences")
        nform.addRow("Angle", self.noise_angle)
        nform.addRow("Angular rate", self.noise_rate)
        nform.addRow("Cart position", self.noise_pos)
        nform.addRow("Seed", self.seed)
        layout.addWidget(nbox)

        vbox, vform = group("Visualisation")
        v = ViewSettings()
        self.show_trails = check("Tip trajectory", v.show_trails)
        self.trail_all = check("Trajectories of all joints", v.trail_all_joints)
        self.trail_len = int_spin(10, 5000, v.trail_length, "Number of frames kept in the trajectory")
        self.show_com = check("Centres of mass", v.show_com)
        self.show_labels = check("Angle arcs and labels", v.show_labels)
        self.show_velocity = check("Angular velocity arrows", v.show_velocity)
        self.show_eq = check("Upright equilibrium (ghost)", v.show_equilibrium)
        self.show_force = check("Actuator / disturbance arrows", v.show_force)
        self.show_grid = check("Grid", v.show_grid)
        self.show_hud = check("State read-out (HUD)", v.show_hud)
        self.follow = check("Camera follows the cart", v.follow_cart)
        self.link_width = spin(2.0, 40.0, v.link_width_px, 1, 1.0, "px")
        self.zoom = spin(10.0, 5000.0, 220.0, 0, 20.0, "px/m", "Scale (mouse wheel zooms, drag pans, double-click fits)")
        for w in (self.show_trails, self.trail_all, self.show_com, self.show_labels, self.show_velocity,
                  self.show_eq, self.show_force, self.show_grid, self.show_hud, self.follow):
            vform.addRow("", w)
            w.toggled.connect(self._emit_view)
        vform.addRow("Trajectory length", self.trail_len)
        vform.addRow("Segment width", self.link_width)
        vform.addRow("Zoom", self.zoom)
        self.trail_len.valueChanged.connect(self._emit_view)
        self.link_width.valueChanged.connect(self._emit_view)
        self.zoom.valueChanged.connect(self.zoomChanged)
        fit = QPushButton("Fit view")
        fit.clicked.connect(self.fitRequested)
        clear = QPushButton("Clear trajectories")
        clear.clicked.connect(self.clearTrailsRequested)
        vform.addRow(fit, clear)
        layout.addWidget(vbox)
        layout.addWidget(hint("A smaller time step is more accurate but slower. Sensor noise uses a seeded "
                              "generator so experiments remain repeatable."))
        layout.addStretch(1)

        for w in (self.dt, self.telemetry, self.noise_angle, self.noise_rate, self.noise_pos):
            w.valueChanged.connect(self._emit_sim)
        self.seed.valueChanged.connect(self._emit_sim)

    def set_simulation(self, s: SimulationSettings) -> None:
        set_silently(self.dt, s.dt * 1000.0)
        set_silently(self.telemetry, s.telemetry_rate_hz)
        set_silently(self.noise_angle, s.sensor_noise_angle_deg)
        set_silently(self.noise_rate, s.sensor_noise_rate_dps)
        set_silently(self.noise_pos, s.sensor_noise_position)
        set_silently(self.seed, s.noise_seed)

    def simulation(self) -> SimulationSettings:
        return SimulationSettings(
            dt=self.dt.value() / 1000.0,
            sensor_noise_angle_deg=self.noise_angle.value(),
            sensor_noise_rate_dps=self.noise_rate.value(),
            sensor_noise_position=self.noise_pos.value(),
            noise_seed=self.seed.value(),
            telemetry_rate_hz=self.telemetry.value(),
        )

    def view(self, base: ViewSettings) -> ViewSettings:
        return replace(
            base,
            show_trails=self.show_trails.isChecked(),
            trail_all_joints=self.trail_all.isChecked(),
            trail_length=self.trail_len.value(),
            show_com=self.show_com.isChecked(),
            show_labels=self.show_labels.isChecked(),
            show_velocity=self.show_velocity.isChecked(),
            show_equilibrium=self.show_eq.isChecked(),
            show_force=self.show_force.isChecked(),
            show_grid=self.show_grid.isChecked(),
            show_hud=self.show_hud.isChecked(),
            follow_cart=self.follow.isChecked(),
            link_width_px=self.link_width.value(),
        )

    def set_zoom(self, scale: float) -> None:
        set_silently(self.zoom, scale)

    def _emit_sim(self) -> None:
        self.simulationChanged.emit(self.simulation())

    def _emit_view(self) -> None:
        self.viewChanged.emit(self.view(ViewSettings()))
