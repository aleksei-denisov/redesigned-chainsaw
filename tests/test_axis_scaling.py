"""The live-graph axis scaler: expand at once, shrink slowly, symmetric, capped."""

from __future__ import annotations

import math

import numpy as np

from pendulum_sim.ui.axis_scaling import SmoothAxis, data_extent


def test_expands_immediately_so_data_is_never_clipped():
    ax = SmoothAxis(symmetric=True, min_span=0.1)
    ax.update(-1.0, 1.0, 0.0)
    lo, hi = ax.update(-0.2, 7.5, 0.05)
    assert lo <= -7.5 and hi >= 7.5


def test_shrinks_slowly_after_a_spike():
    ax = SmoothAxis(symmetric=True, min_span=0.1, shrink_tau=2.5)
    ax.update(-10.0, 10.0, 0.0)
    _, hi_after_short = ax.update(-0.5, 0.5, 0.1)    # 0.1 s later the signal is small again
    assert hi_after_short > 0.9 * 12.5                # barely moved
    hi = hi_after_short
    t = 0.1
    while t < 15.0:
        t += 0.1
        _, hi = ax.update(-0.5, 0.5, t)
    assert hi < 0.5 / 0.45 + 0.01                    # zooms back in until the data fills ~half


def test_no_jitter_for_small_fluctuations():
    ax = SmoothAxis(symmetric=True, min_span=0.1)
    ranges = []
    rng = np.random.default_rng(0)
    for k in range(200):
        amp = 1.0 + 0.3 * rng.random()
        ranges.append(ax.update(-amp, amp, 0.066 * k))
    his = np.array([r[1] for r in ranges[5:]])
    assert np.ptp(his) < 1e-9                         # range never changes


def test_cap_limits_spikes():
    ax = SmoothAxis(symmetric=True, min_span=0.1, cap=40.0, margin=0.25)
    lo, hi = ax.update(-5000.0, 3.0, 0.0)
    assert hi <= 50.0 and lo >= -50.0


def test_asymmetric_axis_and_min_span():
    ax = SmoothAxis(symmetric=False, min_span=2.0, margin=0.0)
    lo, hi = ax.update(5.0, 5.1, 0.0)
    assert math.isclose(hi - lo, 2.0) and lo < 5.0 < hi


def test_data_extent_ignores_nan():
    lo, hi = data_extent([np.array([np.nan, 1.0, -2.0]), np.array([])])
    assert (lo, hi) == (-2.0, 1.0)
    assert all(math.isnan(v) for v in data_extent([np.array([np.nan])]))
