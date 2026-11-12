"""Slider mappings shared by the Widgets and Qt Quick editors (session.sliders)."""

from __future__ import annotations

import math

import pytest

from pendulum_sim.session import sliders


@pytest.mark.parametrize("mapping,lo,hi,dec", [("linear", -3.0, 7.0, 2), ("log", 0.001, 50.0, 3),
                                               ("symlog", -1e6, 1e6, 3), ("symlog", 0.0, 5.0, 5)])
def test_position_and_value_are_inverse(mapping, lo, hi, dec):
    for t in (0.0, 0.1, 0.37, 0.5, 0.9, 1.0):
        v = sliders.from_position(t, lo, hi, mapping, dec)
        assert lo - 1e-9 <= v <= hi + 1e-9
        assert sliders.to_position(v, lo, hi, mapping, dec) == pytest.approx(t, abs=1e-9)


def test_log_is_linear_in_decades_and_symlog_fine_near_zero():
    assert sliders.from_position(0.5, 0.01, 100.0, "log") == pytest.approx(1.0)
    assert sliders.from_position(0.5, -1e6, 1e6, "symlog", 3) == pytest.approx(0.0, abs=1e-9)
    near = sliders.from_position(0.51, -1e6, 1e6, "symlog", 3)
    assert 0 < near < 1e3                                     # 1 % of the slider: small, not 20 000


def test_nudge_steps():
    assert sliders.nudge(1.0, 1, 0, 10, step=0.5) == 1.5
    assert sliders.nudge(0.0, 1, 0, 10, "symlog", 3) == pytest.approx(0.001)
    assert sliders.nudge(123.0, 1, 0, 1e6, "symlog", 3) == pytest.approx(128.0)     # 2 % → 5
    assert sliders.nudge(5.0, -1, 0, 10, "linear") == pytest.approx(4.9)
    assert math.isclose(sliders.nudge(2.0, 1, 0.001, 50, "log"), 2.05)
