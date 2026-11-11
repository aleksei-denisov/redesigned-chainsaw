"""Start-up timing reference (pendulum_sim/mobile/startup.py, used by both mobile apps)."""

from __future__ import annotations

import builtins
import sys

import pytest

from pendulum_sim.mobile import startup

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")


def test_process_age_is_small_and_positive():
    age = startup.process_age()
    assert age is not None and 0.0 <= age < 3600.0


def test_process_age_without_proc_uptime(monkeypatch):
    """Android apps may not read /proc/uptime: the boot-time clock is used instead."""
    real_open = builtins.open

    def guarded(path, *args, **kwargs):
        if path == "/proc/uptime":
            raise PermissionError(path)
        return real_open(path, *args, **kwargs)

    reference = startup.process_age()
    monkeypatch.setattr(builtins, "open", guarded)
    age = startup.process_age()
    assert age is not None
    assert abs(age - reference) < 0.5
