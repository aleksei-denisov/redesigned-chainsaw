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


def test_journal_mirrors_to_a_public_folder(tmp_path):
    private, public = tmp_path / "private", tmp_path / "public"
    private.mkdir()
    public.mkdir()
    first = Journal(private, public)
    first.write("step one")
    assert "step one" in (public / "start.log").read_text()
    second = Journal(private, public)                    # the first never completed
    assert "step one" in (public / "previous-start.log").read_text()
    assert (public / "previous-fault.log").exists()
    assert second.previous_failed


def test_public_folder_is_only_used_on_android(monkeypatch):
    from pendulum_sim.quick.diagnostics import public_folder

    monkeypatch.delenv("ANDROID_PRIVATE", raising=False)
    assert public_folder() is None
