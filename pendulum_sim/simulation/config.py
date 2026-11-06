"""Application configuration and JSON (de)serialisation.

JSON files use **degrees** for angles and angular velocities (more readable)
while the in-memory dataclasses use radians. Gains are always SI
(output per rad, per rad/s, per rad·s; or per m for the cart loop).
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..control import AntiWindup, ControlAction, ControllerConfig, DerivativeMode, PIDLoopConfig
from ..physics import ActuatorParams, BaseType, PlantParams, SegmentParams

CONFIG_VERSION = 1


@dataclass
class SimulationSettings:
    """Numerical and sensor settings.

    Attributes:
        dt: Fixed physics integration step [s].
        sensor_noise_angle_deg: Std-dev of angle sensor noise [deg].
        sensor_noise_rate_dps: Std-dev of angular-rate sensor noise [deg/s].
        sensor_noise_position: Std-dev of cart position noise [m].
        noise_seed: Seed of the noise generator (identical runs are reproducible).
        telemetry_rate_hz: Recording rate of telemetry samples.
    """

    dt: float = 0.001
    sensor_noise_angle_deg: float = 0.0
    sensor_noise_rate_dps: float = 0.0
    sensor_noise_position: float = 0.0
    noise_seed: int = 1
    telemetry_rate_hz: float = 200.0

    def validate(self) -> None:
        if not 1e-5 <= self.dt <= 0.01:
            raise ValueError("physics time step must be between 0.01 ms and 10 ms")
        if min(self.sensor_noise_angle_deg, self.sensor_noise_rate_dps, self.sensor_noise_position) < 0:
            raise ValueError("sensor noise must not be negative")
        if not 1.0 <= self.telemetry_rate_hz <= 2000.0:
            raise ValueError("telemetry rate must be between 1 and 2000 Hz")


@dataclass
class DisturbanceSettings:
    """Horizontal force pulse applied at the pendulum tip during experiments."""

    enabled: bool = False
    time: float = 2.0
    force: float = 2.0
    duration: float = 0.1


@dataclass
class ExperimentSettings:
    duration: float = 8.0
    settle_tolerance_deg: float = 1.0
    disturbance: DisturbanceSettings = field(default_factory=DisturbanceSettings)

    def validate(self) -> None:
        if not 0.1 <= self.duration <= 600.0:
            raise ValueError("experiment duration must be between 0.1 s and 600 s")
        if self.settle_tolerance_deg <= 0:
            raise ValueError("settling tolerance must be positive")


@dataclass
class AppConfig:
    name: str = "Custom configuration"
    description: str = ""
    plant: PlantParams = field(default_factory=PlantParams)
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    simulation: SimulationSettings = field(default_factory=SimulationSettings)
    experiment: ExperimentSettings = field(default_factory=ExperimentSettings)
    push_force: float = 1.0  # N, default tip push of the toolbar Push buttons (0.1 s pulse)

    def validate(self) -> None:
        self.plant.validate()
        self.controller.validate()
        self.simulation.validate()
        self.experiment.validate()
        if not 0.0 < self.push_force <= 10000.0:
            raise ValueError("push force must be positive")

    def copy(self) -> "AppConfig":
        return copy.deepcopy(self)


# ---------------------------------------------------------------- to dict
def _segment_to_dict(s: SegmentParams) -> dict[str, Any]:
    return {
        "length": s.length,
        "mass": s.mass,
        "com_ratio": s.com_ratio,
        "inertia": s.inertia,
        "joint_viscous_friction": s.joint_viscous_friction,
        "joint_coulomb_friction": s.joint_coulomb_friction,
        "initial_angle_deg": math.degrees(s.initial_angle),
        "initial_velocity_dps": math.degrees(s.initial_velocity),
    }


def _loop_to_dict(c: PIDLoopConfig) -> dict[str, Any]:
    return {
        "name": c.name,
        "signal": c.signal,
        "enabled": c.enabled,
        "setpoint": math.degrees(c.setpoint) if c.is_angle else c.setpoint,
        "kp": c.kp,
        "ki": c.ki,
        "kd": c.kd,
        "p_enabled": c.p_enabled,
        "i_enabled": c.i_enabled,
        "d_enabled": c.d_enabled,
        "integral_limit": c.integral_limit,
        "anti_windup": c.anti_windup.value,
        "back_calculation_gain": c.back_calculation_gain,
        "derivative_mode": c.derivative_mode.value,
        "derivative_filter_hz": c.derivative_filter_hz,
    }


def config_to_dict(cfg: AppConfig) -> dict[str, Any]:
    p, c, s, e = cfg.plant, cfg.controller, cfg.simulation, cfg.experiment
    a = p.actuator
    return {
        "version": CONFIG_VERSION,
        "name": cfg.name,
        "description": cfg.description,
        "plant": {
            "base_type": p.base_type.value,
            "gravity": p.gravity,
            "cart_mass": p.cart_mass,
            "cart_viscous_friction": p.cart_viscous_friction,
            "track_length": p.track_length,
            "actuator": {
                "max_output": a.max_output,
                "rate_limit": a.rate_limit,
                "time_constant": a.time_constant,
                "deadband": a.deadband,
            },
            "segments": [_segment_to_dict(seg) for seg in p.segments],
        },
        "controller": {
            "enabled": c.enabled,
            "frequency_hz": c.frequency_hz,
            "output_min": c.output_min,
            "output_max": c.output_max,
            "action": c.action.value,
            "loops": [_loop_to_dict(loop) for loop in c.loops],
        },
        "simulation": {
            "dt": s.dt,
            "sensor_noise_angle_deg": s.sensor_noise_angle_deg,
            "sensor_noise_rate_dps": s.sensor_noise_rate_dps,
            "sensor_noise_position": s.sensor_noise_position,
            "noise_seed": s.noise_seed,
            "telemetry_rate_hz": s.telemetry_rate_hz,
        },
        "interaction": {"push_force": cfg.push_force},
        "experiment": {
            "duration": e.duration,
            "settle_tolerance_deg": e.settle_tolerance_deg,
            "disturbance": {
                "enabled": e.disturbance.enabled,
                "time": e.disturbance.time,
                "force": e.disturbance.force,
                "duration": e.disturbance.duration,
            },
        },
    }


# -------------------------------------------------------------- from dict
def _get(d: dict[str, Any], key: str, default: Any, kind: type) -> Any:
    value = d.get(key, default)
    try:
        if kind is bool:
            if isinstance(value, bool):
                return value
            raise TypeError
        return kind(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid value for '{key}': {value!r}") from exc


def _segment_from_dict(d: dict[str, Any]) -> SegmentParams:
    base = SegmentParams()
    length = _get(d, "length", base.length, float)
    mass = _get(d, "mass", base.mass, float)
    return SegmentParams(
        length=length,
        mass=mass,
        com_ratio=_get(d, "com_ratio", base.com_ratio, float),
        inertia=_get(d, "inertia", SegmentParams.rod_inertia(mass, length), float),
        joint_viscous_friction=_get(d, "joint_viscous_friction", base.joint_viscous_friction, float),
        joint_coulomb_friction=_get(d, "joint_coulomb_friction", base.joint_coulomb_friction, float),
        initial_angle=math.radians(_get(d, "initial_angle_deg", 0.0, float)),
        initial_velocity=math.radians(_get(d, "initial_velocity_dps", 0.0, float)),
    )


def _loop_from_dict(d: dict[str, Any]) -> PIDLoopConfig:
    base = PIDLoopConfig()
    signal = _get(d, "signal", base.signal, str)
    setpoint = _get(d, "setpoint", 0.0, float)
    try:
        anti = AntiWindup(d.get("anti_windup", base.anti_windup.value))
        mode = DerivativeMode(d.get("derivative_mode", base.derivative_mode.value))
    except ValueError as exc:
        raise ValueError(f"loop '{signal}': {exc}") from exc
    return PIDLoopConfig(
        name=_get(d, "name", signal, str),
        signal=signal,
        enabled=_get(d, "enabled", True, bool),
        setpoint=math.radians(setpoint) if signal.startswith("theta") else setpoint,
        kp=_get(d, "kp", 0.0, float),
        ki=_get(d, "ki", 0.0, float),
        kd=_get(d, "kd", 0.0, float),
        p_enabled=_get(d, "p_enabled", True, bool),
        i_enabled=_get(d, "i_enabled", True, bool),
        d_enabled=_get(d, "d_enabled", True, bool),
        integral_limit=_get(d, "integral_limit", 0.0, float),
        anti_windup=anti,
        back_calculation_gain=_get(d, "back_calculation_gain", base.back_calculation_gain, float),
        derivative_mode=mode,
        derivative_filter_hz=_get(d, "derivative_filter_hz", base.derivative_filter_hz, float),
    )


def config_from_dict(data: dict[str, Any]) -> AppConfig:
    """Build and validate an :class:`AppConfig`; raises ``ValueError`` on bad input."""
    if not isinstance(data, dict):
        raise ValueError("configuration must be a JSON object")
    version = data.get("version", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ValueError(f"unsupported configuration version {version}")
    pd = data.get("plant", {})
    ad = pd.get("actuator", {})
    try:
        base_type = BaseType(pd.get("base_type", "cart"))
    except ValueError as exc:
        raise ValueError(f"plant: {exc}") from exc
    segments = [_segment_from_dict(s) for s in pd.get("segments", [{}])]
    plant = PlantParams(
        base_type=base_type,
        gravity=_get(pd, "gravity", 9.81, float),
        cart_mass=_get(pd, "cart_mass", 1.0, float),
        cart_viscous_friction=_get(pd, "cart_viscous_friction", 0.1, float),
        track_length=_get(pd, "track_length", 4.0, float),
        segments=segments,
        actuator=ActuatorParams(
            max_output=_get(ad, "max_output", 30.0, float),
            rate_limit=_get(ad, "rate_limit", 0.0, float),
            time_constant=_get(ad, "time_constant", 0.005, float),
            deadband=_get(ad, "deadband", 0.0, float),
        ),
    )
    cd = data.get("controller", {})
    try:
        action = ControlAction(cd.get("action", "direct" if plant.has_cart else "reverse"))
    except ValueError as exc:
        raise ValueError(f"controller: {exc}") from exc
    controller = ControllerConfig(
        enabled=_get(cd, "enabled", True, bool),
        frequency_hz=_get(cd, "frequency_hz", 500.0, float),
        output_min=_get(cd, "output_min", -plant.actuator.max_output, float),
        output_max=_get(cd, "output_max", plant.actuator.max_output, float),
        action=action,
        loops=[_loop_from_dict(loop) for loop in cd.get("loops", [])],
    )
    sd = data.get("simulation", {})
    simulation = SimulationSettings(
        dt=_get(sd, "dt", 0.001, float),
        sensor_noise_angle_deg=_get(sd, "sensor_noise_angle_deg", 0.0, float),
        sensor_noise_rate_dps=_get(sd, "sensor_noise_rate_dps", 0.0, float),
        sensor_noise_position=_get(sd, "sensor_noise_position", 0.0, float),
        noise_seed=_get(sd, "noise_seed", 1, int),
        telemetry_rate_hz=_get(sd, "telemetry_rate_hz", 200.0, float),
    )
    ed = data.get("experiment", {})
    dd = ed.get("disturbance", {})
    experiment = ExperimentSettings(
        duration=_get(ed, "duration", 8.0, float),
        settle_tolerance_deg=_get(ed, "settle_tolerance_deg", 1.0, float),
        disturbance=DisturbanceSettings(
            enabled=_get(dd, "enabled", False, bool),
            time=_get(dd, "time", 2.0, float),
            force=_get(dd, "force", 2.0, float),
            duration=_get(dd, "duration", 0.1, float),
        ),
    )
    cfg = AppConfig(
        name=_get(data, "name", "Custom configuration", str),
        description=_get(data, "description", "", str),
        plant=plant,
        controller=controller,
        simulation=simulation,
        experiment=experiment,
        push_force=_get(data.get("interaction", {}), "push_force", 1.0, float),
    )
    cfg.validate()
    return cfg


def save_config(cfg: AppConfig, path: str | Path) -> None:
    Path(path).write_text(json.dumps(config_to_dict(cfg), indent=2) + "\n", encoding="utf-8")


def load_config(path: str | Path) -> AppConfig:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{Path(path).name} is not valid JSON: {exc}") from exc
    return config_from_dict(data)
