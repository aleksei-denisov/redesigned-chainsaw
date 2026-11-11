"""Editor models of the parameter screens: fields, ranges, units, and the editing rules (Qt-free).

The Widgets mobile app's *System* and *Controller* screens (``mobile/pages.py``) keep
editor state of their own: three segment editors whatever the current number of
segments (going 3 → 2 → 3 brings the third segment back as it was), the *uniform rod*
switch that computes the inertia from mass and length, angles and the time step shown
in degrees and milliseconds, the controller's loops rebuilt when the structure changes.
The forms below are that state and those rules without widgets, so the Qt Quick screens
behave the same; each field carries the label, range, decimals, unit and slider mapping
of the corresponding Widgets row (``tests/test_mobile_smoke.py`` compares them with the
real Widgets editors).

A form holds a value per field key (``"gravity"``, ``"seg1.mass"``, ``"loop0.kp"`` …),
fills them from a configuration object (``set_*``) and builds a new one from them.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace

from ..control import AntiWindup, ControlAction, ControllerConfig, DerivativeMode, LQRWeights, PIDLoopConfig
from ..physics import MAX_SEGMENTS, ActuatorParams, BaseType, PlantParams, SegmentParams
from ..simulation import SimulationSettings

SEED_MAX = 9999                   # slider range of the noise seed (typing allows the same)
GAIN_RANGE = 1e6


@dataclass(frozen=True)
class Field:
    """One editable value. ``kind``: ``number`` | ``choice`` | ``switch`` | ``stepper``."""

    key: str
    label: str
    kind: str = "number"
    minimum: float = 0.0
    maximum: float = 1.0
    decimals: int = 3
    unit: str = ""
    mapping: str = "linear"          # slider mapping: linear | log | symlog
    step: float | None = None
    hint: str = ""
    options: tuple = ()              # choice: ((value, text), …)


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    fields: tuple[Field, ...]
    expanded: bool = True
    note: str = ""


def number(key, label, minimum, maximum, decimals=3, unit="", mapping="linear", step=None, hint="") -> Field:
    return Field(key, label, "number", minimum, maximum, decimals, unit, mapping, step, hint)


# =================================================================== system
BASE_OPTIONS = ((BaseType.CART.value, "Cart on a track"), (BaseType.FIXED.value, "Fixed pivot"))


def segment_fields(i: int) -> tuple[Field, ...]:
    p = f"seg{i}."
    return (
        number(p + "length", "Length", 0.02, 5.0, 3, "m", "log"),
        number(p + "mass", "Mass", 0.001, 50.0, 3, "kg", "log"),
        number(p + "com", "Centre of mass", 0.0, 1.0, 3, "× length"),
        Field(p + "rod", "Uniform rod", "switch", hint="inertia I = m·L²/12"),
        number(p + "inertia", "Inertia about the COM", 0.0, 10.0, 6, "kg·m²", "symlog"),
        number(p + "viscous", "Joint viscous friction", 0.0, 5.0, 5, "N·m·s/rad", "symlog"),
        number(p + "coulomb", "Joint dry friction", 0.0, 5.0, 4, "N·m", "symlog"),
        number(p + "angle", "Initial angle θ₀", -180.0, 180.0, 2, "°"),
        number(p + "velocity", "Initial angular velocity ω₀", -2000.0, 2000.0, 1, "°/s", "symlog"),
    )


PLANT_SECTIONS = (
    Section("pendulum", "Pendulum", (
        Field("base", "Base", "choice", options=BASE_OPTIONS),
        Field("segments", "Segments", "stepper", 1, MAX_SEGMENTS, 0),
        number("gravity", "Gravity", 0.0, 30.0, 3, "m/s²"),
    )),
    Section("cart", "Cart", (
        number("cart_mass", "Cart mass", 0.01, 100.0, 3, "kg", "log"),
        number("cart_friction", "Cart friction", 0.0, 50.0, 3, "N·s/m", "symlog"),
        number("track", "Track length", 0.2, 50.0, 2, "m", "log"),
    )),
    Section("actuator", "Actuator", (
        number("max_out", "Max output", 0.001, 10000.0, 3, "", "log"),
        number("rate", "Rate limit", 0.0, 1e6, 1, "/s", "symlog", hint="0 = unlimited"),
        number("tau", "Time constant", 0.0, 1.0, 4, "s", "symlog", hint="0 = ideal"),
        number("deadband", "Deadband", 0.0, 1000.0, 4, "", "symlog"),
    ), expanded=False),
    *(Section(f"seg{i}", f"Segment {i + 1}", segment_fields(i), expanded=(i == 0),
              note="Initial conditions (used on reset)") for i in range(MAX_SEGMENTS)),
)

SIMULATION_SECTION = Section("simulation", "Simulation", (
    number("dt", "Physics time step", 0.01, 10.0, 3, "ms", "log", hint="smaller = more accurate, slower"),
    number("telemetry", "Telemetry rate", 1.0, 2000.0, 0, "Hz", "log"),
    number("noise_angle", "Angle noise", 0.0, 10.0, 3, "°", "symlog"),
    number("noise_rate", "Angular-rate noise", 0.0, 100.0, 2, "°/s", "symlog"),
    number("noise_pos", "Cart-position noise", 0.0, 0.1, 4, "m", "symlog"),
    number("seed", "Noise seed", 0, SEED_MAX, 0, "", step=1, hint="any whole number; the same seed gives the same noise"),
), expanded=False, note="Sensor noise (Gaussian, seeded – runs stay repeatable)")


def _clamp(field: Field, value):
    if field.kind in ("number", "stepper"):
        return min(max(value, field.minimum), field.maximum)
    return value


class Form:
    """Values per field key, clamped to the fields' ranges as the Widgets rows do."""

    sections: tuple[Section, ...] = ()

    def __init__(self) -> None:
        self.fields: dict[str, Field] = {f.key: f for s in self.sections for f in s.fields}
        self.values: dict[str, object] = {}

    def get(self, key: str):
        return self.values[key]

    def _put(self, key: str, value) -> None:
        field = self.fields[key]
        if field.kind == "switch":
            value = bool(value)
        elif field.kind == "stepper":
            value = int(round(_clamp(field, value)))
        elif field.kind == "number":
            value = float(_clamp(field, float(value)))
        self.values[key] = value


class PlantForm(Form):
    """The *System* screen's pendulum, cart, actuator and segment editors."""

    sections = PLANT_SECTIONS

    def __init__(self) -> None:
        super().__init__()
        self._structure = (1, BaseType.CART.value)

    # -------------------------------------------------------------- fill
    def set_plant(self, plant: PlantParams) -> None:
        self._put("base", plant.base_type.value)
        self._put("segments", plant.n_segments)
        self._put("gravity", plant.gravity)
        self._put("cart_mass", plant.cart_mass)
        self._put("cart_friction", plant.cart_viscous_friction)
        self._put("track", plant.track_length)
        a = plant.actuator
        self._put("max_out", a.max_output)
        self._put("rate", a.rate_limit)
        self._put("tau", a.time_constant)
        self._put("deadband", a.deadband)
        for i, seg in enumerate(plant.segments):
            self._set_segment(i, seg)
        for i in range(plant.n_segments, MAX_SEGMENTS):   # unused editors: copy of the last segment at rest
            template = copy.deepcopy(plant.segments[-1])
            template.initial_angle = template.initial_velocity = 0.0
            self._set_segment(i, template)
        self._structure = (plant.n_segments, plant.base_type.value)

    def _set_segment(self, i: int, s: SegmentParams) -> None:
        p = f"seg{i}."
        self._put(p + "length", s.length)
        self._put(p + "mass", s.mass)
        self._put(p + "com", s.com_ratio)
        self._put(p + "inertia", s.inertia)
        self._put(p + "rod", math.isclose(s.inertia, SegmentParams.rod_inertia(s.mass, s.length),
                                          rel_tol=1e-6, abs_tol=1e-12))
        self._put(p + "viscous", s.joint_viscous_friction)
        self._put(p + "coulomb", s.joint_coulomb_friction)
        self._put(p + "angle", math.degrees(s.initial_angle))
        self._put(p + "velocity", math.degrees(s.initial_velocity))
        self._update_inertia(i)

    def _update_inertia(self, i: int) -> None:
        p = f"seg{i}."
        if self.values[p + "rod"]:
            self._put(p + "inertia", SegmentParams.rod_inertia(self.values[p + "mass"], self.values[p + "length"]))

    # -------------------------------------------------------------- edit
    def set(self, key: str, value) -> tuple[PlantParams, bool]:
        """Edit one field; returns the new plant and whether the structure changed."""
        self._put(key, value)
        if key[:3] == "seg" and key[3:4].isdigit():
            self._update_inertia(int(key[3]))
        plant = self.plant()
        structure = (plant.n_segments, plant.base_type.value)
        structural = structure != self._structure
        self._structure = structure
        return plant, structural

    def editable(self, key: str) -> bool:
        """The inertia is computed while *Uniform rod* is on."""
        if key.endswith(".inertia"):
            return not self.values[key[:5] + "rod"]
        return True

    def visible(self, section_key: str) -> bool:
        if section_key == "cart":
            return self.values["base"] == BaseType.CART.value
        if section_key.startswith("seg"):
            return int(section_key[3:]) < self.values["segments"]
        return True

    def actuator_note(self) -> str:
        cart = self.values["base"] == BaseType.CART.value
        return "Force on the cart, N" if cart else "Torque at the pivot, N·m"

    def plant(self) -> PlantParams:
        v = self.values
        n = v["segments"]
        return PlantParams(
            base_type=BaseType(v["base"]), gravity=v["gravity"], cart_mass=v["cart_mass"],
            cart_viscous_friction=v["cart_friction"], track_length=v["track"],
            segments=[self.segment(i) for i in range(n)],
            actuator=ActuatorParams(max_output=v["max_out"], rate_limit=v["rate"],
                                    time_constant=v["tau"], deadband=v["deadband"]))

    def segment(self, i: int) -> SegmentParams:
        v, p = self.values, f"seg{i}."
        return SegmentParams(
            length=v[p + "length"], mass=v[p + "mass"], com_ratio=v[p + "com"],
            inertia=v[p + "inertia"], joint_viscous_friction=v[p + "viscous"],
            joint_coulomb_friction=v[p + "coulomb"],
            initial_angle=math.radians(v[p + "angle"]), initial_velocity=math.radians(v[p + "velocity"]))


class SimulationForm(Form):
    """The *System* screen's simulation settings (time step in milliseconds)."""

    sections = (SIMULATION_SECTION,)

    def set_simulation(self, s: SimulationSettings) -> None:
        self._put("dt", s.dt * 1000.0)
        self._put("telemetry", s.telemetry_rate_hz)
        self._put("noise_angle", s.sensor_noise_angle_deg)
        self._put("noise_rate", s.sensor_noise_rate_dps)
        self._put("noise_pos", s.sensor_noise_position)
        self._put("seed", s.noise_seed)

    def set(self, key: str, value) -> SimulationSettings:
        self._put(key, value)
        return self.simulation()

    def simulation(self) -> SimulationSettings:
        v = self.values
        return SimulationSettings(dt=v["dt"] / 1000.0, sensor_noise_angle_deg=v["noise_angle"],
                                  sensor_noise_rate_dps=v["noise_rate"], sensor_noise_position=v["noise_pos"],
                                  noise_seed=int(v["seed"]), telemetry_rate_hz=v["telemetry"])


# =============================================================== controller
ACTION_OPTIONS = ((ControlAction.DIRECT.value, "Direct  e = y − r"), (ControlAction.REVERSE.value, "Reverse  e = r − y"))
ANTI_WINDUP_OPTIONS = ((AntiWindup.NONE.value, "None"), (AntiWindup.CLAMP.value, "Clamping"),
                       (AntiWindup.BACK_CALCULATION.value, "Back-calculation"))
DERIVATIVE_OPTIONS = ((DerivativeMode.MEASURED_RATE.value, "Measured rate"),
                      (DerivativeMode.FILTERED_DIFFERENCE.value, "Filtered difference"))

LQR_SECTION = Section("lqr", "LQR weights for seeding", (
    number("lqr.cart_position", "Cart position weight", 1e-2, 1e3, 3, "", "log"),
    number("lqr.angle", "Angle weight", 1e-1, 1e5, 2, "", "log"),
    number("lqr.cart_velocity", "Cart velocity weight", 1e-3, 1e3, 3, "", "log"),
    number("lqr.angular_velocity", "Angular velocity weight", 1e-3, 1e3, 3, "", "log"),
    number("lqr.effort", "Effort weight R", 1e-4, 1e1, 4, "", "log", hint="larger = gentler control"),
), expanded=False)

CONTROLLER_SECTION = Section("controller", "Controller", (
    Field("enabled", "Controller enabled", "switch"),
    Field("action", "Action", "choice", options=ACTION_OPTIONS),
    number("freq", "Update frequency", 1.0, 10000.0, 1, "Hz", "log"),
    number("out_min", "Output minimum", -1e6, 1e6, 3, "", "symlog"),
    number("out_max", "Output maximum", -1e6, 1e6, 3, "", "symlog"),
))


def loop_title(loop: PIDLoopConfig) -> str:
    return "Cart position loop" if loop.signal == "cart" else f"Segment {loop.signal[5:]} angle loop (θ{loop.signal[5:]})"


def loop_fields(k: int, signal: str) -> tuple[Field, ...]:
    angle = signal.startswith("theta")
    unit = "rad" if angle else "m"
    p = f"loop{k}."
    return (
        Field(p + "enabled", "Loop enabled", "switch"),
        number(p + "kp", "Kp – proportional", -GAIN_RANGE, GAIN_RANGE, 3, "", "symlog", hint=f"output per {unit}"),
        number(p + "ki", "Ki – integral", -GAIN_RANGE, GAIN_RANGE, 3, "", "symlog", hint=f"output per {unit}·s"),
        number(p + "kd", "Kd – derivative", -GAIN_RANGE, GAIN_RANGE, 3, "", "symlog", hint=f"output per {unit}/s"),
        Field(p + "p", "P term", "switch"),
        Field(p + "i", "I term", "switch"),
        Field(p + "d", "D term", "switch"),
        number(p + "setpoint", "Setpoint", -180.0 if angle else -10.0, 180.0 if angle else 10.0,
               2 if angle else 3, "°" if angle else "m"),
        Field(p + "anti", "Anti-windup", "choice", options=ANTI_WINDUP_OPTIONS),
        number(p + "kt", "Tracking gain Kt", 0.0, 1e4, 2, "1/s", "symlog"),
        number(p + "ilimit", "Integral limit", 0.0, 1e6, 3, "", "symlog",
               hint="|integral term| in output units, 0 = none"),
        Field(p + "dmode", "Derivative source", "choice", options=DERIVATIVE_OPTIONS),
        number(p + "dfilter", "Derivative filter", 0.1, 5000.0, 1, "Hz", "log"),
    )


class ControllerForm(Form):
    """The *Controller* screen: global settings, one section per loop, LQR weights."""

    def __init__(self) -> None:
        self.sections = (LQR_SECTION, CONTROLLER_SECTION)
        super().__init__()
        self._ctrl = ControllerConfig()
        self._loops: list[PIDLoopConfig] = []
        w = LQRWeights()
        for name in ("cart_position", "angle", "cart_velocity", "angular_velocity", "effort"):
            self._put(f"lqr.{name}", getattr(w, name))

    def _rebuild(self, loops: list[PIDLoopConfig]) -> None:
        loop_sections = tuple(Section(f"loop{k}", loop_title(lp), loop_fields(k, lp.signal),
                                      expanded=(k == 0 or lp.signal == "theta1"))
                              for k, lp in enumerate(loops))
        self.sections = (LQR_SECTION, CONTROLLER_SECTION) + loop_sections
        self.fields = {f.key: f for s in self.sections for f in s.fields}
        for key in [k for k in self.values if k.startswith("loop")]:
            del self.values[key]

    def set_config(self, c: ControllerConfig) -> bool:
        """Fill from ``c``; returns ``True`` when the loop sections were rebuilt."""
        self._ctrl = copy.deepcopy(c)
        self._put("enabled", c.enabled)
        self._put("action", c.action.value)
        self._put("freq", c.frequency_hz)
        self._put("out_min", c.output_min)
        self._put("out_max", c.output_max)
        rebuilt = [lp.signal for lp in c.loops] != [lp.signal for lp in self._loops]
        if rebuilt:
            self._rebuild(c.loops)
        self._loops = [copy.deepcopy(lp) for lp in c.loops]
        for k, lp in enumerate(c.loops):
            self._set_loop(k, lp)
        return rebuilt

    def _set_loop(self, k: int, c: PIDLoopConfig) -> None:
        p = f"loop{k}."
        self._loops[k] = copy.deepcopy(c)
        angle = c.signal.startswith("theta")
        self._put(p + "enabled", c.enabled)
        self._put(p + "kp", c.kp)
        self._put(p + "ki", c.ki)
        self._put(p + "kd", c.kd)
        self._put(p + "p", c.p_enabled)
        self._put(p + "i", c.i_enabled)
        self._put(p + "d", c.d_enabled)
        self._put(p + "setpoint", math.degrees(c.setpoint) if angle else c.setpoint)
        self._put(p + "anti", c.anti_windup.value)
        self._put(p + "kt", c.back_calculation_gain)
        self._put(p + "ilimit", c.integral_limit)
        self._put(p + "dmode", c.derivative_mode.value)
        self._put(p + "dfilter", c.derivative_filter_hz)

    def set(self, key: str, value) -> ControllerConfig | None:
        """Edit one field; returns the new controller (``None`` for an LQR weight)."""
        self._put(key, value)
        if key.startswith("lqr."):
            return None
        cfg = self.config()
        self._ctrl = copy.deepcopy(cfg)
        return cfg

    def loop_config(self, k: int) -> PIDLoopConfig:
        v, p, base = self.values, f"loop{k}.", self._loops[k]
        sp = v[p + "setpoint"]
        angle = base.signal.startswith("theta")
        return replace(
            base,
            enabled=v[p + "enabled"],
            kp=v[p + "kp"], ki=v[p + "ki"], kd=v[p + "kd"],
            p_enabled=v[p + "p"], i_enabled=v[p + "i"], d_enabled=v[p + "d"],
            setpoint=math.radians(sp) if angle else sp,
            anti_windup=AntiWindup(v[p + "anti"]),
            back_calculation_gain=v[p + "kt"],
            integral_limit=v[p + "ilimit"],
            derivative_mode=DerivativeMode(v[p + "dmode"]),
            derivative_filter_hz=v[p + "dfilter"],
        )

    def config(self) -> ControllerConfig:
        c = copy.deepcopy(self._ctrl)
        v = self.values
        c.enabled = v["enabled"]
        c.action = ControlAction(v["action"])
        c.frequency_hz = v["freq"]
        c.output_min = v["out_min"]
        c.output_max = v["out_max"]
        c.loops = [self.loop_config(k) for k in range(len(self._loops))]
        return c

    def weights(self) -> LQRWeights:
        return LQRWeights(**{name: self.values[f"lqr.{name}"]
                             for name in ("cart_position", "angle", "cart_velocity", "angular_velocity", "effort")})

    def loop_signals(self) -> list[str]:
        return [lp.signal for lp in self._loops]
