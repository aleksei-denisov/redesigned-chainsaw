"""Fixed-timestep pacing used by the mobile shell (Qt-free)."""

from __future__ import annotations

import math

import pytest

from pendulum_sim.simulation import scenario
from pendulum_sim.simulation.engine import Simulator
from pendulum_sim.simulation.realtime import RealtimeStepper


def _sim():
    return Simulator(scenario("single_cart_full"))


def test_simulated_time_tracks_wall_time_independent_of_frame_rate():
    for frame in (1 / 30, 1 / 60, 1 / 120):
        sim, st = _sim(), RealtimeStepper(budget_s=10.0)
        frames = int(round(2.0 / frame))
        for _ in range(frames):
            st.tick(sim, frame)
        assert sim.time == pytest.approx(2.0, abs=2 * sim.config.simulation.dt)  # remainder carried


def test_speed_scales_simulated_time():
    sim, st = _sim(), RealtimeStepper(budget_s=10.0)
    for _ in range(60):
        st.tick(sim, 1 / 60, speed=0.25)
    assert sim.time == pytest.approx(0.25, abs=sim.config.simulation.dt)


def test_long_stall_is_clamped():
    sim, st = _sim(), RealtimeStepper(budget_s=10.0, max_frame_s=0.1)
    st.tick(sim, 30.0)  # e.g. returning from the background
    assert sim.time == pytest.approx(0.1, abs=sim.config.simulation.dt)


def test_budget_drops_backlog():
    ticks = iter(range(10**6))
    st = RealtimeStepper(budget_s=0.5, clock=lambda: float(next(ticks)))  # each clock read = 1 s
    sim = _sim()
    done = st.tick(sim, 0.1)
    assert 0 < done < int(round(0.1 / sim.config.simulation.dt))
    assert st.accum == 0.0
    assert st.realtime_factor < 0.1


def test_zero_or_negative_wall_does_nothing():
    sim, st = _sim(), RealtimeStepper()
    assert st.tick(sim, 0.0) == 0 and st.tick(sim, -1.0) == 0
    assert sim.time == 0.0
    assert math.isfinite(st.physics_ms)
