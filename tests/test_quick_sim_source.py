"""Physics off the GUI thread (Qt Quick app): forked-process and thread sources (Qt-free).

The sources only pace, apply edits and publish – the numbers must be the core's: a
published sample at step ``k`` equals a plain ``Simulator`` stepped ``k`` times, and a
sequence of edits gives exactly what the session's operations give on a simulator of
our own (which tests/test_session_equivalence.py ties to the desktop's behaviour).
"""

from __future__ import annotations

import copy
import time
import tracemalloc

import numpy as np
import pytest

from pendulum_sim.control import LQRWeights
from pendulum_sim.quick.backend.sim_source import (
    H_HIST,
    MAX_STATE,
    MSG_DIVERGED,
    MSG_REJECTED,
    R_EPOCH,
    R_STATE,
    R_STEPS,
    R_T,
    R_WALL,
    RENDER_DELAY_S,
    ROW,
    ForkSource,
    ThreadSource,
    make_source,
)
from pendulum_sim.session import Session, apply, operations
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


def newest(source):
    return source.sample(time.monotonic() + 1.0)          # newest sample, no blending


@pytest.fixture(params=KINDS)
def source(request):
    src = make_source(request.param, scenario("triple_cart_lqr"))
    assert src.kind == request.param
    src.start()
    assert wait_for(lambda: src.sample() is not None)        # the child's first publication
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
    assert wait_for(lambda: not source.running)
    time.sleep(0.05)
    last = newest(source)
    assert last.steps >= 150 and last.steps == int(source.stats()["steps"])
    ref = reference(config, last.steps)
    np.testing.assert_array_equal(last.state, ref.state)   # bit-identical to the core
    assert last.t == pytest.approx(ref.time) and last.applied == pytest.approx(ref.applied)
    assert last.steps * config.simulation.dt <= 5.0        # real-time pacing, never faster


def test_telemetry_tail_matches_the_core(source):
    config = scenario("triple_cart_lqr")
    source.set_running(True)
    assert wait_for(lambda: source.stats()["steps"] >= 300)
    source.set_running(False)
    assert wait_for(lambda: not source.running)
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
    assert source.latest("t") == tail["t"][-1]


def test_reset_and_push(source):
    source.set_running(True)
    assert wait_for(lambda: source.stats()["steps"] >= 50)
    source.push(5.0, 0.1)
    assert wait_for(lambda: abs(newest(source).tip) > 0, 3.0)
    source.set_running(False)
    source.reset()
    assert wait_for(lambda: newest(source).steps == 0)
    time.sleep(0.05)
    s = newest(source)
    np.testing.assert_array_equal(s.state, Simulator(scenario("triple_cart_lqr")).state)
    assert source.tail(("t",), 30.0)["t"].size == 1          # telemetry starts again
    assert source.stats()["epoch"] >= 1


def test_display_frames_never_slow_the_simulation(source):
    """Nobody reads samples for a while (a stalled display): simulated time keeps real time."""
    source.set_running(True)
    assert wait_for(lambda: source.stats()["steps"] > 0)
    t0, s0 = time.monotonic(), source.stats()["steps"]
    time.sleep(0.5)                                         # no read() calls at all
    t1, s1 = time.monotonic(), source.stats()["steps"]
    rate = (s1 - s0) * scenario("triple_cart_lqr").simulation.dt / (t1 - t0)
    assert 0.8 < rate < 1.1, rate


def test_edits_give_exactly_the_session_operations(source):
    """Paused, with exact step counts: every edit lands as on a simulator of our own."""
    session = Session(scenario("triple_cart_lqr"))
    ref = apply(None, ("config", session.config))
    expected = 0

    def run(*ops, steps=0):
        nonlocal ref, expected
        for op in ops:
            source.send(*op)
            if op[0] == "step":
                expected += operations.single_step(ref)[0]
            elif op[0] == "reset_if_idle":
                if ref.time == 0.0:
                    ref.reset()
            else:
                ref = apply(ref, op)
        if steps:
            source.send("steps", steps)
            expected += operations.run_steps(ref, steps)[0]
        assert wait_for(lambda: source.stats()["steps"] == expected), (source.stats()["steps"], expected)
        time.sleep(0.02)
        got = newest(source)
        assert got.t == ref.time and got.steps == ref.steps
        np.testing.assert_array_equal(got.state, ref.state)

    run(steps=200)
    ctrl, ops = session.seed_lqr(LQRWeights(angle=150.0))
    run(*ops, steps=150)
    plant = copy.deepcopy(session.config.plant)
    plant.segments[0].mass *= 1.1
    run(*session.change_plant(plant, False).operations, steps=150)
    settings = copy.deepcopy(session.config.simulation)
    settings.dt = 0.0005
    run(*session.change_simulation(settings), steps=150)
    run(("reset_integrators",), ("step",))
    structure = copy.deepcopy(session.config.plant)
    structure.segments.pop()
    change = session.change_plant(structure, True)
    run(*change.operations, steps=100)
    assert newest(source).state.size == 6                   # two segments now
    _ctrl, ops = session.restore_gains()
    run(*ops, steps=100)
    # telemetry of the new structure matches too
    tel = source.tail(("t", "theta1", "theta2", "actuator"), 30.0)
    n = tel["t"].size
    assert n == len(ref.telemetry)
    for name in tel:
        np.testing.assert_array_equal(tel[name], ref.telemetry.column(name))


def test_divergence_and_refused_edits_are_reported(source):
    session = Session(scenario("single_cart_pid"))
    source.send("config", session.config)
    assert wait_for(lambda: newest(source).state.size == 4)
    bad_plant = copy.deepcopy(session.config.plant)
    bad_plant.segments[0].mass = -1.0
    source.send("plant", bad_plant)
    assert wait_for(lambda: (msg := source.message()) is not None and msg[0] == MSG_REJECTED and "mass" in msg[1])
    unstable = copy.deepcopy(session.config)                 # huge output, positive feedback
    unstable.plant.actuator.max_output = 1e7
    unstable.controller.output_min, unstable.controller.output_max = -1e7, 1e7
    for loop in unstable.controller.loops:
        loop.kp, loop.kd, loop.enabled = -1e5, 0.0, True
    source.send("config", unstable)
    source.set_running(True)
    got = []
    assert wait_for(lambda: got.append(source.message()) or (got[-1] is not None and got[-1][0] == MSG_DIVERGED))
    assert "diverged" in got[-1][1]
    assert wait_for(lambda: not source.running)
    assert source.message() is None                           # each message once


def test_dragging_applies_a_tip_force(source):
    source.set_running(True)
    source.send("drag", 0.5)
    assert wait_for(lambda: newest(source).tip == pytest.approx(0.5), 3.0)
    source.send("drag", 0.0)
    assert wait_for(lambda: newest(source).tip == 0.0, 3.0)


def test_read_interpolates_without_allocating():
    src = ThreadSource(scenario("triple_cart_lqr"))         # not started: rows written by hand
    hist, header = src.block.hist, src.block.header
    base = time.monotonic()
    for i in range(10):
        row = hist[i]
        row[:] = 0.0
        row[R_WALL], row[R_T], row[R_STEPS], row[R_EPOCH] = base + 0.002 * i, 0.002 * i, 2 * i, 0
        row[R_STATE:R_STATE + 2] = (i, 10.0 * i)
    hist[9, R_EPOCH] = 1                                      # a reset between rows 8 and 9
    hist[9, R_STATE] = 99.0
    header[H_HIST] = 10
    out = np.empty(ROW)
    assert src.read(out, base + 0.003)                        # between rows 1 and 2
    assert out[R_T] == pytest.approx(0.003) and out[R_STATE] == pytest.approx(1.5)
    assert out[R_STEPS] == 4                                  # whole steps of the later row
    assert src.read(out, base + 0.017) and out[R_STATE] == 99.0   # never blend across the reset
    assert src.read(out, base + 1.0) and out[R_STATE] == 99.0     # after the newest: hold it
    assert src.read(out, base - 1.0) and out[R_STATE] == 0.0      # older than kept: the oldest
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for k in range(2000):
        src.read(out, base + 0.0001 * (k % 160))
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    grown = sum(stat.size_diff for stat in after.compare_to(before, "filename") if stat.size_diff > 0)
    assert grown < 4096, grown                                # nothing kept per frame
    assert RENDER_DELAY_S >= 0.004 and MAX_STATE == 8


def test_commands_are_private_copies():
    """The thread source copies commands like the process source: later edits of the
    caller's objects never reach the simulator unannounced."""
    src = ThreadSource(scenario("single_cart_full"))
    session = Session(scenario("single_cart_full"))
    ctrl, ops = session.seed_lqr()
    src.send(*ops[0])
    ctrl.loops[0].kp = 12345.0
    assert src.commands[0][1].loops[0].kp != 12345.0


def test_thread_source_is_the_fallback():
    src = make_source("something else", scenario("single_cart_full"))
    assert isinstance(src, ThreadSource)
    with pytest.raises(KeyError):
        src.send("no such command")


def test_push_at_a_point_is_the_core_point_push(source):
    """A push at a point of a segment (quick-core-v2) gives what the core gives."""
    source.push(3.0, 0.1, 1, 0.5)
    source.send("steps", 300)
    assert wait_for(lambda: newest(source).steps == 300, 5.0)
    ref = Simulator(scenario("triple_cart_lqr"))
    ref.apply_disturbance(3.0, 0.1, segment=1, ratio=0.5)
    for _ in range(300):
        ref.step()
    np.testing.assert_array_equal(newest(source).state[:ref.state.size], ref.state)


def test_dragging_a_point(source):
    source.set_running(True)
    source.send("drag", 0.5, 0, 0.3)
    assert wait_for(lambda: newest(source).tip == pytest.approx(0.5), 3.0)
    source.send("drag", 0.0)
    assert wait_for(lambda: newest(source).tip == 0.0, 3.0)
