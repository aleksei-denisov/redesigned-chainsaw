"""Shared texts of the auto-tune and experiment screens (pendulum_sim/session/texts.py)."""

from __future__ import annotations

from types import SimpleNamespace

from pendulum_sim.session import texts
from pendulum_sim.simulation.tuning import TuneProgress


def progress(**kw):
    base = dict(phase="explore", message="Trying candidates", done=3, total=10, elapsed=6.0, eta=14.0, workers=3,
                pool="fork")
    base.update(kw)
    return TuneProgress(**base)


def test_durations():
    assert texts.fmt_duration(None) == "–"
    assert texts.fmt_duration(42.4) == "42 s"
    assert texts.fmt_duration(125) == "2 min 05 s"
    assert texts.fmt_duration(3725) == "1 h 02 min"


def test_progress_texts():
    u = progress(simulations=17, pool_note="")
    assert texts.tune_activity(u) == "Working on 3 CPU cores · 17 simulations so far"
    assert texts.tune_best(progress(best_push=0.0)) == ("–", "")
    assert texts.tune_best(progress(baseline_push=0.12))[0] == "Current controller recovers from 0.12 N"
    assert "<b>0.3 N</b>" in texts.tune_best(progress(best_push=0.3, baseline_push=0.12))[0]
    assert texts.tune_fraction(u, 0.0) == 0.3
    assert 0.3 < texts.tune_fraction(u, 1.0) < 0.4                 # extrapolated, not past the next unit
    assert texts.tune_times(6.0, u, 2.0) == "Elapsed 6 s · about 12 s left"
    assert texts.tune_times(1.0, None, 0.0) == "Elapsed 1 s"


def test_result_texts():
    result = SimpleNamespace(baseline_max_push=0.1, max_push=0.15, evaluations=24, elapsed=95.0, cancelled=False,
                             recommended_push=0.07)
    assert texts.tune_headline(result) == "Recoverable tip push: 0.1 N → 0.15 N (+50 %)"
    assert texts.tune_notes(result).startswith("24 candidates in 1 min 35 s.")
    assert "0.07 N" in texts.tune_notes(result)
    assert texts.tune_estimate("normal", None, 3) == "measuring this device…"
    assert texts.tune_estimate("quick", 0.5, 3).startswith("about ")
    assert texts.tune_cores(1, 4).startswith("Uses 1 CPU core in parallel")
