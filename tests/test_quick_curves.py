"""Live graphs of the Qt Quick app: stable scales and incremental curves (Qt-free)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from pendulum_sim.quick.backend.curves import GraphSpec, GraphState, Style
from pendulum_sim.quick.backend.scale import SHRINK_DELAY_S, StableScale
from pendulum_sim.quick.backend.signals import by_key, catalogue

CAT = by_key(catalogue(3, True, ["θ1", "θ2", "θ3", "cart"]))
RATE = 200.0


def _telemetry(t_end: float, seconds: float = 30.0, seed: int = 3) -> dict[str, np.ndarray]:
    t = np.arange(max(0.0, t_end - seconds), t_end + 1e-9, 1.0 / RATE)
    rng = np.random.default_rng(seed)
    return {"t": t,
            "theta1": 0.02 * np.sin(2.0 * t) + 0.002 * np.sin(97 * t),
            "x": 0.1 * np.sin(0.5 * t) + 0.0005 * rng.standard_normal(t.size),
            "command": 5.0 * np.sin(3.0 * t)}


# ------------------------------------------------------------------ scales
def test_scale_expands_at_once_and_shrinks_only_after_the_delay():
    s = StableScale("auto", min_span=1.0)
    lo, hi = s.update(-1.0, 1.0, 0.0)
    assert lo <= -1.0 and hi >= 1.0
    lo2, hi2 = s.update(-5.0, 5.0, 0.1)                # expansion starts at once (gliding)
    s.update(-5.0, 5.0, 0.5)
    assert s.lo <= -5.0 and s.hi >= 5.0                # … and is complete after the glide
    s.update(-0.2, 0.2, 1.0)                           # small data: no shrink yet
    s.update(-0.2, 0.2, 1.0 + SHRINK_DELAY_S - 0.1)
    assert s.hi >= 5.0
    s.update(-0.2, 0.2, 1.0 + SHRINK_DELAY_S + 0.1)    # held long enough: shrink target set
    s.update(-0.2, 0.2, 1.0 + SHRINK_DELAY_S + 0.5)
    assert s.hi < 2.0
    s.update(-0.2, 0.2, 1.0 + SHRINK_DELAY_S + 0.6)
    assert not s.moving                                    # standing still again


def test_scale_glides_instead_of_jumping():
    s = StableScale("auto", min_span=1.0)
    s.update(-1.0, 1.0, 0.0)
    start = s.hi
    s.update(-10.0, 10.0, 1.0)
    mid = s.update(-10.0, 10.0, 1.15)[1]
    end = s.update(-10.0, 10.0, 1.5)[1]
    assert start < mid < end


def test_symmetric_and_fixed_scales():
    s = StableScale("symmetric", min_span=1.0)
    lo, hi = s.update(0.2, 3.0, 0.0)
    assert lo == -hi and hi >= 3.0
    f = StableScale("fixed", min_span=1.0, fixed=(-2.0, 7.0))
    assert f.update(-50.0, 50.0, 0.0) == (-2.0, 7.0)


# ------------------------------------------------------------------ curves
def _state(signals, **kw) -> GraphState:
    g = GraphState(GraphSpec(signals, **kw), CAT, RATE)
    g.set_style(Style([(255, 0, 0, 255), (0, 0, 255, 255)], 2.0, 0.5))
    return g


def _screen_points(out, k):
    """Vertex positions on screen (with the curve's scroll offset) of series k."""
    v, _i = out.curves[k]
    return np.column_stack((v["x"] + out.dx, v["y"]))


def test_two_units_get_two_axes():
    g = _state(["theta1", "x"])
    out = g.update(_telemetry(12.0), 300.0, 120.0, 2.25, 12.0)
    assert [a.unit for a in out.axes] == ["°", "m"]
    assert [d["axis"] for d in out.legend] == [0, 1]
    assert all(0.0 <= y <= 120.0 for y, _label in out.axes[0].ticks)


@pytest.mark.parametrize("smoothing", ["off", "medium"])
def test_incremental_curves_equal_a_full_redraw(smoothing):
    """Refreshing step by step gives the same picture as drawing the last window at once."""
    g = _state(["theta1", "x"], smoothing=smoothing, scale="fixed", fixed=[(-2.0, 2.0), (-0.2, 0.2)])
    t = 11.0
    out = None
    while t < 14.0:
        t = round(t + 1.0 / 30.0, 6)
        out = g.update(_telemetry(t), 300.0, 120.0, 2.25, t)
    assert out is not None and not out.redrawn            # fixed scale: incremental at the end
    fresh = _state(["theta1", "x"], smoothing=smoothing, scale="fixed", fixed=[(-2.0, 2.0), (-0.2, 0.2)])
    full = fresh.update(_telemetry(t), 300.0, 120.0, 2.25, t)
    for k in range(2):
        a, b = _screen_points(out, k), _screen_points(full, k)
        visible_a = a[(a[:, 0] >= 0) & (a[:, 0] <= 300)]
        visible_b = b[(b[:, 0] >= 0) & (b[:, 0] <= 300)]
        n = min(len(visible_a), len(visible_b))
        assert n > 100 and abs(len(visible_a) - len(visible_b)) <= 8
        # the newest points match exactly; older ones may differ only by where the window began
        assert np.allclose(visible_a[-n // 2:], visible_b[-n // 2:], atol=0.05)


def test_incremental_refresh_only_strokes_new_points():
    g = _state(["theta1"], scale="fixed", fixed=[(-2.0, 2.0)])
    g.update(_telemetry(10.0), 300.0, 120.0, 2.25, 10.0)
    c = g.curves[0]
    before = c.verts.copy()
    out = g.update(_telemetry(10.0 + 1 / 30), 300.0, 120.0, 2.25, 10.0 + 1 / 30)
    assert not out.redrawn
    # vertices far from the newest end are reused unchanged (only shifted in the list)
    tail_before = before[-200:-20].view(np.uint32)           # compare bits (colours are packed into floats)
    now = c.verts.view(np.uint32)
    start = len(before) - 200
    assert np.array_equal(tail_before, now[start:start + len(tail_before)])


def test_a_reset_starts_the_curves_again():
    g = _state(["theta1"])
    g.update(_telemetry(20.0), 300.0, 120.0, 1.0, 20.0)
    out = g.update(_telemetry(0.5), 300.0, 120.0, 1.0, 20.1)     # time went back: new run
    v, _i = out.curves[0]
    assert out.redrawn and v.size and (v["x"] + out.dx).min() > 280.0     # 0.5 s of data at the right edge


def test_wrapped_angles_break_the_line():
    t = np.arange(0.0, 4.0, 1.0 / RATE)
    theta = np.where(t < 2.0, math.radians(170.0) + 0.2 * t, -math.radians(170.0) + 0.2 * (t - 2.0))
    g = _state(["theta1"], scale="fixed", fixed=[(-180.0, 180.0)])
    out = g.update({"t": t, "theta1": theta}, 300.0, 120.0, 1.0, 4.0)
    v, i = out.curves[0]
    pts = np.column_stack((v["x"][1::4], v["y"][1::4]))
    seg_starts = sorted({int(k) // 4 for k in i[::18]})
    jumps = [s for s in seg_starts if abs(pts[s + 1, 1] - pts[s, 1]) > 50]
    assert not jumps                                         # no line across the ±180° jump
