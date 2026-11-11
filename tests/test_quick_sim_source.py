"""Physics off the GUI thread (Qt Quick app): thread and forked-process sources (Qt-free).

The sources only pace and publish – the numbers must be the core's: a published sample
at step ``k`` equals a plain ``Simulator`` stepped ``k`` times.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from pendulum_sim.quick.backend.sim_source import (
    RENDER_DELAY_S,
    ForkSource,
    Sample,
    ThreadSource,
    interpolate,
    make_source,
)
from pendulum_sim.simulation import Simulator, scenario

KINDS = ["thread"] + (["process"] if ForkSource.available() else [])


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def reference(config, steps, pushes=()):
    sim = Simulator(config.copy())
    for _ in range(steps):
        for at, force, duration in pushes:
            if sim.steps == at:
                sim.apply_disturbance(force, duration)
        sim.step()
    return sim


@pytest.fixture(params=KINDS)
def source(request):
    src = make_source(request.param, scenario("triple_cart_lqr"))
    assert src.kind == request.param
    src.start()
    yield src
    src.stop()


def test_samples_are_exactly_the_core(source):
    config = scenario("triple_cart_lqr")
    first = source.sample(time.monotonic())
    assert first is not None and first.steps == 0 and first.t == 0.0
    np.testing.assert_array_equal(first.state, Simulator(config.copy()).state)
    source.set_running(True)
    assert wait_for(lambda: source.stats()["steps"] >= 150)
    source.set_running(False)
    assert wait_for(lambda: source.stats()["running"] == 0.0)
    time.sleep(0.05)
    last = source.sample(time.monotonic() + 1.0)          # newest sample, no blending
    assert last.steps >= 150 and last.steps == int(source.stats()["steps"])
    ref = reference(config, last.steps)
    np.testing.assert_array_equal(last.state, ref.state)   # bit-identical to the core
    assert last.t == pytest.approx(ref.time) and last.applied == pytest.approx(ref.applied)
    # real-time pacing: about as many steps as milliseconds (dt = 1 ms), never more
    dt = config.simulation.dt
    assert last.steps * dt <= 5.0


def test_telemetry_tail_matches_the_core(source):
    config = scenario("triple_cart_lqr")
    source.set_running(True)
    assert wait_for(lambda: source.stats()["steps"] >= 300)
    source.set_running(False)
    assert wait_for(lambda: source.stats()["running"] == 0.0)
    time.sleep(0.05)
    steps = int(source.stats()["steps"])
    tail = source.tail(("t", "theta1", "x", "actuator"), 30.0)
    ref = reference(config, steps).telemetry
    n = tail["t"].size
    assert n == len(ref) and n > 50
    for name in tail:
        np.testing.assert_array_equal(tail[name], ref.column(name))
    short = source.tail(("t",), 0.1)["t"]
    assert 15 <= short.size <= 25 and short[-1] == tail["t"][-1]


def test_reset_and_push(source):
    source.set_running(True)
    assert wait_for(lambda: source.stats()["steps"] >= 50)
    source.push(5.0, 0.1)
    assert wait_for(lambda: abs(source.sample(time.monotonic() + 1).tip) > 0, 3.0)
    source.set_running(False)
    source.reset()
    assert wait_for(lambda: source.sample(time.monotonic() + 1).steps == 0)
    time.sleep(0.05)
    s = source.sample(time.monotonic() + 1)
    np.testing.assert_array_equal(s.state, Simulator(scenario("triple_cart_lqr")).state)
    assert source.tail(("t",), 30.0)["t"].size == 1          # telemetry starts again
    assert source.stats()["epoch"] >= 1


def test_display_frames_never_slow_the_simulation(source):
    """Nobody reads samples for a while (a stalled display): simulated time keeps real time."""
    source.set_running(True)
    assert wait_for(lambda: source.stats()["steps"] > 0)
    t0, s0 = time.monotonic(), source.stats()["steps"]
    time.sleep(0.5)                                         # no sample() calls at all
    t1, s1 = time.monotonic(), source.stats()["steps"]
    rate = (s1 - s0) * scenario("triple_cart_lqr").simulation.dt / (t1 - t0)
    assert 0.8 < rate < 1.1, rate


def test_interpolation_between_samples():
    a = Sample(10.0, 1.0, 100, 0.0, 0.0, np.array([0.0, 1.0]))
    b = Sample(10.002, 1.002, 102, 2.0, 0.0, np.array([1.0, 3.0]))
    mid = interpolate([a, b], 10.001)
    assert mid.t == pytest.approx(1.001) and mid.applied == pytest.approx(1.0)
    np.testing.assert_allclose(mid.state, [0.5, 2.0])
    assert interpolate([a, b], 9.0) is a and interpolate([a, b], 11.0) is b
    assert interpolate([], 1.0) is None
    reset = Sample(10.004, 0.0, 0, 0.0, 0.0, np.array([9.0, 9.0]))
    assert interpolate([a, b, reset], 10.003) is reset      # never blend across a reset
    assert RENDER_DELAY_S >= 0.004


def test_thread_source_is_the_fallback():
    src = make_source("something else", scenario("single_cart_full"))
    assert isinstance(src, ThreadSource)
