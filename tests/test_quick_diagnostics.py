"""Start-up journal of the Qt Quick app (Qt-free)."""

from __future__ import annotations

import faulthandler

from pendulum_sim.quick.diagnostics import COMPLETE, Journal


def _close(journal: Journal) -> None:
    faulthandler.disable()
    for f in (journal._log, journal._fault):
        if f is not None:
            f.close()


def test_unfinished_start_is_reported_by_the_next_one(tmp_path):
    first = Journal(tmp_path)
    assert not first.previous_failed                 # nothing before it
    first.write("step  0.100 s  loading screen shown")
    try:
        raise ValueError("boom")
    except ValueError:
        first.exception("loading the backend")
    _close(first)                                     # "crashed": never completed

    second = Journal(tmp_path)
    assert second.previous_failed
    report = second.report()
    assert "loading screen shown" in report and "ValueError: boom" in report
    second.complete()
    _close(second)

    third = Journal(tmp_path)
    assert not third.previous_failed
    assert COMPLETE in third.previous
    _close(third)


def test_native_crash_output_is_included(tmp_path):
    journal = Journal(tmp_path)
    _close(journal)
    (tmp_path / "fault.log").write_text("Fatal Python error: Segmentation fault\n  File \"x.py\", line 1")
    nxt = Journal(tmp_path)
    assert "Segmentation fault" in nxt.report()
    _close(nxt)
