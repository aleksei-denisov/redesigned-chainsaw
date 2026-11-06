"""Predefined demonstration scenarios.

Each scenario is a function returning a fresh :class:`AppConfig`. The same
scenarios are exported to ``configs/*.json`` by ``scripts/export_examples.py``.
"""

from __future__ import annotations

import math
from typing import Callable

from ..control import (
    AntiWindup,
    ControlAction,
    ControllerConfig,
    LQRWeights,
    PIDLoopConfig,
    lqr_controller,
)
from ..physics import ActuatorParams, BaseType, PlantParams, SegmentParams
from .config import AppConfig, DisturbanceSettings, ExperimentSettings

_LENGTHS = [0.5, 0.4, 0.3]
_MASSES = [0.2, 0.15, 0.1]


def make_segments(n: int, angles_deg: list[float] | None = None, friction: float = 0.0005) -> list[SegmentParams]:
    angles = angles_deg or [0.0] * n
    segs = []
    for i in range(n):
        segs.append(SegmentParams(
            length=_LENGTHS[i],
            mass=_MASSES[i],
            com_ratio=0.5,
            inertia=SegmentParams.rod_inertia(_MASSES[i], _LENGTHS[i]),
            joint_viscous_friction=friction,
            initial_angle=math.radians(angles[i] if i < len(angles) else 0.0),
        ))
    return segs


def cart_plant(n: int, angles_deg: list[float], max_force: float = 40.0) -> PlantParams:
    return PlantParams(
        base_type=BaseType.CART,
        cart_mass=1.0,
        cart_viscous_friction=0.1,
        track_length=4.0,
        segments=make_segments(n, angles_deg),
        actuator=ActuatorParams(max_output=max_force, time_constant=0.002),
    )


def fixed_plant(n: int, angles_deg: list[float], max_torque: float = 2.0, friction: float = 0.0005) -> PlantParams:
    return PlantParams(
        base_type=BaseType.FIXED,
        segments=make_segments(n, angles_deg, friction),
        actuator=ActuatorParams(max_output=max_torque, time_constant=0.002),
    )


def single_cart_pid() -> AppConfig:
    plant = cart_plant(1, [8.0])
    controller = ControllerConfig(
        output_min=-40.0, output_max=40.0, action=ControlAction.DIRECT,
        loops=[
            PIDLoopConfig(name="Segment 1 angle", signal="theta1", kp=40.0, ki=5.0, kd=5.0),
            PIDLoopConfig(name="Cart position", signal="cart", enabled=False, kp=0.0, kd=0.0),
        ],
    )
    return AppConfig(
        name="Single pendulum on cart – PID",
        description="Classic cart-pole balanced by one PID loop on the angle. "
                    "The cart is not regulated, so it slowly drifts – enable the cart loop "
                    "(or use the LQR-seeded scenario) to hold it in place.",
        plant=plant, controller=controller,
        experiment=ExperimentSettings(duration=6.0),
    )


def single_cart_full() -> AppConfig:
    plant = cart_plant(1, [10.0])
    return AppConfig(
        name="Single pendulum on cart – angle + cart loops",
        description="Angle loop plus a cart-position loop, gains seeded by LQR. "
                    "Note the negative-looking behaviour of the cart loop: to return to the "
                    "centre the cart first moves away (non-minimum-phase).",
        plant=plant, controller=lqr_controller(plant),
        experiment=ExperimentSettings(
            duration=8.0, disturbance=DisturbanceSettings(enabled=True, time=4.0, force=1.0, duration=0.1)),
    )


def single_fixed_pid() -> AppConfig:
    plant = fixed_plant(1, [15.0])
    controller = ControllerConfig(
        output_min=-2.0, output_max=2.0, action=ControlAction.REVERSE,
        loops=[PIDLoopConfig(name="Segment 1 angle", signal="theta1", kp=6.0, ki=3.0, kd=0.35)],
    )
    return AppConfig(
        name="Single pendulum, base motor – PID",
        description="Fixed pivot with a torque motor (reverse-acting PID). Try lowering D "
                    "to see oscillations, or raising P to see faster, more aggressive recovery.",
        plant=plant, controller=controller,
        experiment=ExperimentSettings(duration=5.0),
    )


def windup_demo() -> AppConfig:
    plant = fixed_plant(1, [25.0], max_torque=0.8)
    controller = ControllerConfig(
        output_min=-0.8, output_max=0.8, action=ControlAction.REVERSE,
        loops=[PIDLoopConfig(name="Segment 1 angle", signal="theta1", kp=6.0, ki=20.0, kd=0.35,
                             anti_windup=AntiWindup.NONE)],
    )
    return AppConfig(
        name="Integral windup demonstration",
        description="Large integral gain with a weak motor and anti-windup OFF: the integrator "
                    "winds up while the output is saturated and causes a large overshoot. "
                    "Run an experiment, switch anti-windup to 'clamp' or 'back_calculation', "
                    "run again and compare.",
        plant=plant, controller=controller,
        experiment=ExperimentSettings(duration=6.0),
    )


def double_cart_lqr() -> AppConfig:
    plant = cart_plant(2, [4.0, -2.0])
    return AppConfig(
        name="Double pendulum on cart – multi-loop (LQR seeded)",
        description="Two segments balanced by three parallel loops (cart, θ1, θ2). "
                    "The PD gains reproduce an LQR state-feedback design.",
        plant=plant, controller=lqr_controller(plant),
        experiment=ExperimentSettings(
            duration=8.0, disturbance=DisturbanceSettings(enabled=True, time=4.0, force=0.2, duration=0.1)),
    )


def triple_cart_lqr() -> AppConfig:
    plant = cart_plant(3, [2.0, -1.0, 1.0], max_force=60.0)
    return AppConfig(
        name="Triple pendulum on cart – multi-loop (LQR seeded)",
        description="Three segments on a cart. Needs fast, coordinated feedback from all "
                    "loops; small gain changes quickly destabilise it.",
        plant=plant,
        controller=lqr_controller(plant, LQRWeights(cart_position=10, angle=100, effort=0.1)),
        experiment=ExperimentSettings(duration=8.0),
    )


def double_fixed_lqr() -> AppConfig:
    plant = fixed_plant(2, [5.0, -3.0])
    return AppConfig(
        name="Double pendulum, base motor (pendubot) – LQR seeded",
        description="Underactuated: only the lower joint has a motor, yet both segments are "
                    "balanced through dynamic coupling.",
        plant=plant, controller=lqr_controller(plant),
        experiment=ExperimentSettings(duration=6.0),
    )


def double_free_swing() -> AppConfig:
    plant = fixed_plant(2, [120.0, -30.0], friction=0.0)
    plant.segments[0].joint_viscous_friction = 0.0
    controller = ControllerConfig(enabled=False, output_min=-2.0, output_max=2.0,
                                  action=ControlAction.REVERSE,
                                  loops=[PIDLoopConfig(signal="theta1", enabled=False)])
    return AppConfig(
        name="Double pendulum free swing (chaos)",
        description="Frictionless double pendulum without control. Motion is chaotic; the total "
                    "energy plot stays flat, which validates the integrator.",
        plant=plant, controller=controller,
        experiment=ExperimentSettings(duration=10.0),
    )


def hanging_damped() -> AppConfig:
    plant = fixed_plant(1, [150.0], friction=0.01)
    controller = ControllerConfig(enabled=False, output_min=-2.0, output_max=2.0,
                                  action=ControlAction.REVERSE,
                                  loops=[PIDLoopConfig(signal="theta1", enabled=False)])
    return AppConfig(
        name="Hanging pendulum with joint friction",
        description="Released from 150°, the pendulum swings down to the stable hanging "
                    "equilibrium (180°) and the oscillation decays due to joint friction.",
        plant=plant, controller=controller,
        experiment=ExperimentSettings(duration=10.0),
    )


SCENARIOS: dict[str, Callable[[], AppConfig]] = {
    "single_cart_pid": single_cart_pid,
    "single_cart_full": single_cart_full,
    "single_fixed_pid": single_fixed_pid,
    "windup_demo": windup_demo,
    "double_cart_lqr": double_cart_lqr,
    "double_fixed_lqr": double_fixed_lqr,
    "triple_cart_lqr": triple_cart_lqr,
    "double_free_swing": double_free_swing,
    "hanging_damped": hanging_damped,
}

DEFAULT_SCENARIO = "single_cart_full"


def scenario(key: str) -> AppConfig:
    cfg = SCENARIOS[key]()
    cfg.validate()
    return cfg
