"""Experiments screen: shared settings form and plot thinning (Qt-free parts)."""

from __future__ import annotations

import numpy as np

from pendulum_sim.session.forms import ExperimentForm
from pendulum_sim.simulation.config import ExperimentSettings


def test_experiment_form_round_trip():
    f = ExperimentForm()
    assert f.get("reuse") is True
    e = f.set("dist_on", True)
    assert e.disturbance.enabled and e.duration == ExperimentSettings().duration
    f.set_settings(ExperimentSettings(duration=3.0, settle_tolerance_deg=0.5))
    assert f.settings().duration == 3.0 and f.settings().settle_tolerance_deg == 0.5
    assert f.set("duration", 1e6).duration == 600.0              # clamped as the Widgets rows


def test_thin_keeps_peaks():
    import importlib.util

    if importlib.util.find_spec("PySide6") is None:              # the bridge module imports Qt
        import pytest

        pytest.skip("PySide6 not installed")
    from pendulum_sim.quick.backend.experiments import thin

    t = np.linspace(0, 10, 20001)
    y = np.sin(t)
    y[12345] = 50.0
    ts, ys = thin(t, y, 600)
    assert len(ts) <= 600 and max(ys) == 50.0 and ts == sorted(ts)
