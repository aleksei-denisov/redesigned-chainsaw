"""Graph data of the Qt Quick screens (decimation, axes, runs), Qt-free."""

from __future__ import annotations

import numpy as np
import pytest

from pendulum_sim.quick.backend.graphdata import (
    available_graphs,
    columns,
    graph_axis,
    graph_frame,
    trace_axes,
    trace_frame,
)
from pendulum_sim.session import rules
from pendulum_sim.simulation import Simulator, scenario


@pytest.fixture(scope="module")
def recorded():
    cfg = scenario("triple_cart_lqr")
    sim = Simulator(cfg)
    sim.apply_disturbance(0.06, 0.1, start=0.5)
    for _ in range(4000):                          # 4 s at 1 ms, telemetry at 200 Hz
        sim.step()
    labels = rules.loop_labels(cfg.controller)
    keys = available_graphs(3, True)
    names = columns(keys, 3, labels, 0)
    data = {n: sim.telemetry.column(n).copy() for n in names}
    return cfg, labels, keys, data


def test_every_graph_gives_pixel_runs_inside_the_item(recorded):
    cfg, labels, keys, data = recorded
    assert keys == ["angle", "angular_velocity", "cart_position", "cart_velocity", "control", "error", "pid",
                    "energy"]
    for key in keys:
        frame = graph_frame(key, data, 3, labels, 0, 10.0, 300.0, 120.0, graph_axis(key), 0.0)
        assert frame.lo < frame.hi and frame.ticks and all(frame.lo <= t <= frame.hi for t in frame.ticks)
        assert frame.series and any(frame.series), key
        for runs in frame.series:
            for xs, ys in runs:
                assert xs.size == ys.size and xs.size <= 2 * 300 + 2
                assert np.all((xs >= -1e-6) & (xs <= 300.0 + 1e-6))
                assert np.all((ys >= -1e-6) & (ys <= 120.0 + 1e-6))


def test_decimation_follows_the_pixel_width(recorded):
    _cfg, labels, _keys, data = recorded
    narrow = graph_frame("angle", data, 3, labels, 0, 10.0, 40.0, 100.0, graph_axis("angle"), 0.0)
    assert sum(xs.size for xs, _ in narrow.series[0]) <= 2 * 40 + 2


def test_window_shorter_than_the_data_shows_the_end(recorded):
    _cfg, labels, _keys, data = recorded
    frame = graph_frame("cart_position", data, 3, labels, 0, 1.0, 200.0, 100.0, graph_axis("cart_position"), 0.0)
    xs, _ys = frame.series[0][-1]
    assert 199.0 - 1e-6 <= xs[-1] <= 200.0           # within the last pixel column


def test_wrapped_angles_split_into_runs():
    t = np.linspace(0.0, 2.0, 401)
    theta = np.linspace(0.0, 4.0 * np.pi, t.size)               # two full turns
    data = {"t": t, "theta1": theta}
    frame = graph_frame("angle", data, 1, [], 0, 2.0, 400.0, 100.0, graph_axis("angle"), 0.0)
    assert len(frame.series[0]) >= 3                             # broken at each wrap


def test_empty_and_tiny_items():
    frame = graph_frame("angle", {"t": np.zeros(0)}, 2, [], 0, 10.0, 300.0, 100.0, graph_axis("angle"), 0.0)
    assert frame.series == [[], []]
    trace = trace_frame({"t": np.zeros(0)}, 10.0, 300.0, 100.0, trace_axes(), 0.0)
    assert trace.series == [[], []]


def test_trace_scales_each_signal_on_its_own(recorded):
    _cfg, _labels, _keys, data = recorded
    trace = trace_frame(data, 10.0, 300.0, 100.0, trace_axes(), 0.0)
    for runs in trace.series:
        ys = np.concatenate([y for _x, y in runs])
        assert ys.min() >= -1e-6 and ys.max() <= 100.0 + 1e-6
    fixed = trace_frame(data, 10.0, 300.0, 100.0, trace_axes(), 0.0, has_cart=False)
    assert fixed.series[1] == []


def test_data_older_than_the_window_is_not_drawn(recorded):
    _cfg, labels, _keys, data = recorded
    frame = graph_frame("angle", data, 3, labels, 0, 2.0, 300.0, 100.0, graph_axis("angle"), 0.0)
    for runs in frame.series:
        for xs, _ys in runs:
            assert xs.min() >= -1e-6
