"""Graph line geometry of the Qt Quick app: decimation and antialiased strokes (Qt-free)."""

from __future__ import annotations

import numpy as np

from pendulum_sim.quick.backend.linegeom import VERTEX, decimate, merge, stroke


def test_few_samples_are_drawn_as_they_are():
    t = np.linspace(0, 1, 50)
    y = np.sin(t)
    xs, ys = decimate(t, y, 0.0, 1.0, 100)
    assert xs is t and ys is y


def test_decimation_keeps_real_samples_in_time_order():
    rng = np.random.default_rng(1)
    t = np.linspace(0.0, 10.0, 20001)
    y = np.sin(t) + 0.05 * rng.standard_normal(t.size)
    y[12345] = 5.0                                          # a one-sample spike
    xs, ys = decimate(t, y, 0.0, 10.0, 500)
    assert xs.size <= 2 * 500 + 2
    assert np.all(np.diff(xs) > 0)                          # strictly increasing: no vertical steps
    idx = np.searchsorted(t, xs)
    assert np.array_equal(t[idx], xs) and np.array_equal(y[idx], ys)   # only real samples
    assert ys.max() == 5.0                                  # the spike survives with its height
    assert xs[0] == t[0] and xs[-1] == t[-1]
    # every column's extremes are present
    col = np.minimum((t * 50).astype(int), 499)
    for c in (0, 137, 499):
        sel = col == c
        assert y[sel].max() in ys and y[sel].min() in ys


def test_stroke_geometry():
    xs = np.array([0.0, 10.0, 20.0, 20.0, 30.0])            # a repeated point is dropped
    ys = np.array([0.0, 0.0, 10.0, 10.0, 10.0])
    v, i = stroke(xs, ys, width=2.0, feather=0.5, rgba=(47, 91, 255, 255))
    assert v.dtype == VERTEX and v.size == 4 * 4
    assert i.size == 3 * 18 and i.max() == v.size - 1
    # the first point's vertices lie on its normal at ±(1 + 0.5) and ±1
    assert np.allclose(v["y"][:4], [1.5, 1.0, -1.0, -1.5]) and np.allclose(v["x"][:4], 0.0)
    assert tuple(v["rgba"][0]) == (0, 0, 0, 0) and tuple(v["rgba"][1]) == (47, 91, 255, 255)


def test_sharp_spike_stays_narrow():
    xs = np.array([0.0, 1.0, 2.0])
    ys = np.array([0.0, -100.0, 0.0])                       # a needle
    v, _i = stroke(xs, ys, width=2.0, feather=0.5, rgba=(0, 0, 0, 255))
    tip = np.column_stack((v["x"][4:8], v["y"][4:8])) - [1.0, -100.0]
    assert np.hypot(tip[:, 0], tip[:, 1]).max() <= 1.5 * 2.0 + 1e-4      # miter limit


def test_dashes_and_merge():
    xs = np.linspace(0, 100, 101)
    ys = np.zeros(101)
    solid = stroke(xs, ys, 2.0, 0.5, (255, 0, 0, 255))
    dashed = stroke(xs, ys, 2.0, 0.5, (255, 0, 0, 255), dash=5.0)
    assert dashed[1].size == solid[1].size // 2
    v, i = merge([solid, dashed, stroke(np.zeros(1), np.zeros(1), 2.0, 0.5, (0, 0, 0, 255))])
    assert v.size == 2 * 404 and i.max() <= v.size - 1 and i[solid[1].size:].min() >= 404


def test_premultiplied_colour():
    v, _i = stroke(np.array([0.0, 1.0]), np.array([0.0, 0.0]), 2.0, 0.5, (200, 100, 50, 128))
    assert tuple(v["rgba"][1]) == (100, 50, 25, 128)
