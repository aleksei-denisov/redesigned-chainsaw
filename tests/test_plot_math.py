"""Graph buffering, downsampling and axis rules of the mobile strip charts (Qt-free)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from pendulum_sim.mobile.plot_math import (
    GRAPHS,
    AxisRange,
    downsample_minmax,
    finite_extent,
    format_tick,
    graph_available,
    graph_series,
    nice_ticks,
    split_wraps,
    window_slice,
)
from pendulum_sim.simulation import Simulator, scenario
from pendulum_sim.simulation.telemetry import COLUMNS


def test_window_slice_keeps_one_sample_before_the_window():
    t = np.arange(0.0, 20.0, 0.005)
    sl = window_slice(t, 10.0)
    assert t[sl][0] < t[-1] - 10.0 <= t[sl][1]
    assert t[sl][-1] == t[-1]
    assert window_slice(np.array([]), 10.0) == slice(0, 0)


def test_downsampling_bounds_points_and_keeps_peaks():
    rng = np.random.default_rng(0)
    x = np.linspace(0.0, 10.0, 20_001)
    y = rng.normal(0.0, 0.1, x.size)
    y[12_345] = 7.5            # single-sample spike
    y[777] = -3.0
    xs, ys = downsample_minmax(x, y, 0.0, 10.0, 600)
    assert xs.size <= 2 * 600
    assert ys.max() == 7.5 and ys.min() == -3.0
    assert np.all(np.diff(xs) >= 0)                     # time order preserved
    # every pixel column's extremes are exact
    col = np.clip(((x - 0.0) * 60.0).astype(int), 0, 599)
    for c in (0, 123, 370, 599):
        sel = y[col == c]
        assert sel.min() in ys and sel.max() in ys


def test_downsampling_passthrough_and_nans():
    x = np.linspace(0, 1, 100)
    y = np.sin(x)
    xs, ys = downsample_minmax(x, y, 0, 1, 600)
    assert xs is x and ys is y                          # no copy when already small
    x = np.linspace(0, 1, 10_000)
    y = np.ones_like(x)
    y[:5000] = np.nan
    xs, ys = downsample_minmax(x, y, 0, 1, 100)
    assert np.isnan(ys[:2]).all() and np.all(ys[-2:] == 1.0)


def test_split_wraps():
    y = np.array([170.0, 175.0, 179.0, -179.0, -175.0, -150.0])
    parts = split_wraps(y)
    assert [(p.start, p.stop) for p in parts] == [(0, 3), (3, 6)]


@pytest.mark.parametrize("lo,hi", [(0.0, 1.0), (-0.004, 0.003), (-37.0, 112.0), (1e-17, 3e-17)])
def test_ticks_are_readable(lo, hi):
    ticks, step = nice_ticks(lo, hi)
    assert 2 <= len(ticks) <= 7
    labels = [format_tick(v, step) for v in ticks]
    assert not any("e" in s for s in labels)            # never scientific notation
    assert "-0" not in labels


def test_axis_range_minimum_span_and_hysteresis():
    ax = AxisRange(min_span=2.0)
    lo, hi = ax.update(-1e-15, 1e-15, 0.0)              # numerically zero signal
    assert hi - lo >= 2.0 and lo < 0 < hi
    lo, hi = ax.update(-20.0, 25.0, 0.1)                # expands immediately
    assert lo <= -20.0 and hi >= 25.0
    span = hi - lo
    assert ax.update(-0.1, 0.1, 0.2) == (lo, hi)        # no immediate shrink
    lo2, hi2 = ax.update(-0.1, 0.1, 2.0)                # shrinks after the delay
    assert hi2 - lo2 < span and hi2 - lo2 >= 2.0
    assert ax.update(math.nan, math.nan, 3.0) == (lo2, hi2)
    assert finite_extent([np.array([np.nan, 1.0]), np.array([-2.0])]) == (-2.0, 1.0)


def test_catalog_matches_telemetry_columns():
    for n in (1, 2, 3):
        labels = ["cart"] + [f"θ{i + 1}" for i in range(n)]
        for key in GRAPHS:
            for s in graph_series(key, n, labels, loop=1):
                assert s.column in COLUMNS, (key, s.column)
    assert not graph_available("cart_position", 2, has_cart=False)
    assert graph_series("pid", 1, [], 0) == []


def test_reading_graph_data_does_not_copy_the_recorder():
    sim = Simulator(scenario("single_cart_full"))
    for _ in range(200):
        sim.step()
    tel = sim.telemetry
    a, b = tel.column("theta1"), tel.column("t")
    assert np.shares_memory(a, tel._data) and np.shares_memory(b, tel._data)


def test_cpu_helpers_are_safe_everywhere():
    from pendulum_sim.mobile import cpu

    assert cpu.performance_cores({0: 1_800_000, 1: 1_800_000, 2: 2_420_000, 3: 3_200_000}) == {2, 3}
    assert cpu.performance_cores({0: 2_000_000, 1: 2_000_000}) == set()     # all alike
    assert cpu.performance_cores({}) == set()
    assert cpu._ranges([0, 1, 2, 4, 6, 7]) == "0–2,4,6–7"
    text = cpu.describe()
    assert isinstance(text, str) and text


# ------------------------------------------------------- scrolling strip charts
def test_ring_runs_split_at_the_wrap():
    from pendulum_sim.mobile.plot_math import ring_runs

    assert ring_runs(5, 7, 0, 10) == [(5, 3)]
    assert ring_runs(8, 12, 0, 10) == [(8, 2), (0, 3)]
    assert ring_runs(13, 14, 3, 10) == [(0, 2)]
    assert ring_runs(0, 50, 0, 10) == [(0, 10)]          # more than the ring: everything
    assert ring_runs(5, 4, 0, 10) == []


def test_ring_blits_put_the_newest_column_at_the_right_edge():
    from pendulum_sim.mobile.plot_math import ring_blits

    w = 10
    for ring0 in (0, 3, -7):
        for newest in range(ring0 + w - 1, ring0 + 3 * w):
            blits = ring_blits(newest, ring0, w)
            assert sum(length for _s, length, _d in blits) == w
            # the destination column w-1 shows image column (newest - ring0) mod w
            for src, length, dst in blits:
                if dst <= w - 1 < dst + length:
                    assert src + (w - 1 - dst) == (newest - ring0) % w


def test_finite_runs_split_at_nan_and_wraps():
    from pendulum_sim.mobile.plot_math import finite_runs

    assert finite_runs(np.array([1.0, 2, np.nan, 3, 4, 5])) == [slice(0, 2), slice(3, 6)]
    assert finite_runs(np.array([170.0, 179, -179, -170]), wrap=True) == [slice(0, 2), slice(2, 4)]
    assert finite_runs(np.array([170.0, 179, -179, -170])) == [slice(0, 4)]
    assert finite_runs(np.array([np.nan, np.nan])) == []
    assert finite_runs(np.array([np.nan, 1, np.nan, 2, 3, np.nan])) == [slice(1, 2), slice(3, 5)]


# ------------------------------------------------------------ explorable plots
def test_visible_slice_includes_one_sample_beyond_each_edge():
    from pendulum_sim.mobile.plot_math import visible_slice

    x = np.arange(10.0)
    assert visible_slice(x, 2.5, 5.5) == slice(2, 7)
    assert visible_slice(x, -5, 50) == slice(0, 10)
    assert visible_slice(np.zeros(0), 0, 1) == slice(0, 0)


def test_zoom_keeps_the_centre_in_place():
    from pendulum_sim.mobile.plot_math import zoom_range

    assert zoom_range(0.0, 10.0, 2.0, 5.0) == (2.5, 7.5)
    lo, hi = zoom_range(0.0, 10.0, 2.0, 2.0)              # 2.0 stays at 20 % of the width
    assert hi - lo == 5.0 and (2.0 - lo) / (hi - lo) == pytest.approx(0.2)
    lo, hi = zoom_range(0.0, 10.0, 0.5, 5.0)              # zoom out
    assert (lo, hi) == (-5.0, 15.0)
    assert zoom_range(0.0, 1e-9, 1e6, 0.0, min_span=1e-6)[1] == pytest.approx(1e-6)


def test_value_at_takes_the_nearest_sample():
    from pendulum_sim.mobile.plot_math import value_at

    x, y = np.arange(5.0), np.arange(5.0) * 2
    assert value_at(x, y, 2.6) == 6.0 and value_at(x, y, 2.4) == 4.0
    assert value_at(x, y, -3) == 0.0 and value_at(x, y, 99) == 8.0
    assert np.isnan(value_at(np.zeros(0), np.zeros(0), 1.0))


def test_extent_and_padding():
    from pendulum_sim.mobile.plot_math import Curve, curves_extent, padded_range

    c = [Curve(np.arange(5.0), np.array([0.0, 1.0, np.nan, 10.0, 2.0])), Curve(np.zeros(0), np.zeros(0))]
    assert curves_extent(c) == (0.0, 4.0, 0.0, 10.0)
    assert curves_extent(c, 0.0, 1.0) == (0.0, 4.0, 0.0, 1.0)     # y only near [0, 1]
    lo, hi = padded_range(1.0, 1.0, 2.0)
    assert hi - lo == pytest.approx(2.0 * 1.12) and (lo + hi) / 2 == pytest.approx(1.0)
    assert padded_range(float("nan"), float("nan"), 2.0) == (-1.0, 1.0)


def test_curves_csv_shared_and_separate_time(tmp_path):
    import csv

    from pendulum_sim.mobile.plot_math import Curve, write_curves_csv

    t = np.arange(3.0)
    path = tmp_path / "a.csv"
    write_curves_csv(path, [Curve(t, np.array([1.0, np.nan, 3.0]), label="θ1"), Curve(t, np.ones(3), label="u")])
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0] == ["t_s", "θ1", "u"] and rows[2] == ["1", "", "1"] and len(rows) == 4
    write_curves_csv(path, [Curve(t, t, label="run 1"), Curve(t[:2], t[:2], label="run 2")])
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0] == ["t_s (run 1)", "run 1", "t_s (run 2)", "run 2"] and rows[3] == ["2", "2", "", ""]
    with pytest.raises(ValueError):
        write_curves_csv(path, [])
