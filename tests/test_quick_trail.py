"""Trajectory of the Simulate view (Qt-free part)."""

from __future__ import annotations

import math

import numpy as np

from pendulum_sim.quick.backend.trailgeom import TrailBuffer, geometry, joints


def test_joints_from_the_upward_vertical():
    p = joints(0.1, [0.0, math.pi / 2], [0.5, 0.3])
    assert np.allclose(p, [[0.1, 0.0], [0.1, 0.5], [0.4, 0.5]])


def test_buffer_keeps_the_time_span_and_clears_on_reset():
    b = TrailBuffer(seconds=1.0)
    for k in range(300):
        b.add(k / 100.0, np.zeros((3, 2)) + k)
    assert b.t[0] >= 2.0 - 1e-9 and b.t[-1] == 2.99
    assert not b.add(2.99, np.zeros((3, 2)))            # paused: nothing new
    b.add(0.5, np.zeros((3, 2)))                         # time went back: a new run
    assert b.t.size == 1


def test_geometry_fades_from_old_to_new():
    b = TrailBuffer(seconds=10.0)
    for k in range(50):
        b.add(k * 0.01, joints(0.0, [0.3 * math.sin(k / 5), 0.0], [0.5, 0.3]))
    tip = geometry(b, "tip", 200.0, 400.0, 300.0, [(255, 0, 0, 255), (0, 255, 0, 255)], 1.6, 0.5)
    assert len(tip) == 1
    vertices, indices = tip[0]
    assert vertices.size == 4 * 50 and indices.max() < vertices.size
    core_alpha = vertices["rgba"][1::4, 3]
    assert core_alpha[0] < 40 and core_alpha[-1] == 255 and np.all(np.diff(core_alpha.astype(int)) >= 0)
    both = geometry(b, "all", 200.0, 400.0, 300.0, [(255, 0, 0, 255), (0, 255, 0, 255)], 1.6, 0.5)
    assert len(both) == 1 and both[0][0].size == 2 * 4 * 50
    assert geometry(b, "off", 200.0, 400.0, 300.0, [(255, 0, 0, 255)], 1.6, 0.5) == []
