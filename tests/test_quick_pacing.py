"""Frame pacing, idle garbage collection and the Android hint wrappers (Qt-free)."""

from __future__ import annotations

import gc

import pytest

from pendulum_sim.quick.backend import android_perf
from pendulum_sim.quick.backend.pacing import IdleCollector, Pacer, p95, supported_rates


def test_supported_rates():
    assert supported_rates(120) == [120, 60, 40, 30]
    assert supported_rates(60) == [60, 30]
    assert supported_rates(90) == [90, 45, 30]
    assert supported_rates(119.9) == [120, 60, 40, 30]
    assert supported_rates(30) == [30]


def test_p95():
    assert p95([]) == 0.0
    assert p95(list(range(101))) == 95


def _shown(pacer: Pacer, ticks: int) -> list[bool]:
    return [pacer.vsync() for _ in range(ticks)]


def test_fixed_rates_show_every_kth_vsync():
    pacer = Pacer(120, "60")
    assert pacer.rate == 60 and pacer.divisor == 2
    assert _shown(pacer, 6) == [True, False, True, False, True, False]
    pacer.set_mode("40")
    assert _shown(pacer, 6) == [True, False, False, True, False, False]
    pacer.set_mode("120")
    assert all(_shown(pacer, 5))
    pacer.set_mode("40")
    pacer.set_refresh(60)                      # 40 is not reachable at 60 Hz → 30
    assert pacer.rate == 30


def _run(pacer: Pacer, seconds: float, cost_ms: float, t0: float = 0.0) -> float:
    """Simulate frames at the pacer's current rate with a constant cost."""
    t = t0
    end = t0 + seconds
    while t < end:
        pacer.frame_done(t, cost_ms)
        t += 1.0 / pacer.rate
    return t


def test_auto_steps_down_when_frames_are_too_expensive_and_back_up():
    pacer = Pacer(120, "auto")
    assert pacer.rate == 120
    t = _run(pacer, 1.5, cost_ms=9.0)          # 9 ms > 0.85 · 8.33 ms
    assert pacer.rate == 60
    t = _run(pacer, 1.5, cost_ms=9.0, t0=t)    # fits 60 fps (16.7 ms): stays
    assert pacer.rate == 60
    t = _run(pacer, 2.0, cost_ms=4.0, t0=t)    # cheap, but not yet held long enough
    assert pacer.rate == 60
    _run(pacer, 3.0, cost_ms=4.0, t0=t)        # 4 ms < 0.75 · 8.33 ms for 3 s → back to 120
    assert pacer.rate == 120


def test_failed_tries_back_off():
    pacer = Pacer(120, "auto")
    t = _run(pacer, 1.5, cost_ms=9.0)          # down to 60
    assert pacer.rate == 60
    t = _run(pacer, 4.0, cost_ms=5.0, t0=t)    # 5 ms fits 0.75 · 8.33 ms → up after 3 s
    assert pacer.rate == 120
    t = _run(pacer, 1.5, cost_ms=9.0, t0=t)    # too expensive at 120 after all → down: failed try
    assert pacer.rate == 60
    t = _run(pacer, 4.5, cost_ms=5.0, t0=t)    # now it needs 6 s
    assert pacer.rate == 60
    _run(pacer, 2.5, cost_ms=5.0, t0=t)
    assert pacer.rate == 120


def test_auto_steps_down_on_missed_frames():
    pacer = Pacer(120, "auto")
    t = 0.0
    for i in range(60):                        # every 5th frame arrives two intervals late
        t += (2.5 if i % 5 == 0 else 1.0) / 120
        pacer.frame_done(t, 2.0)
    assert pacer.rate == 60


def test_fixed_mode_never_adapts():
    pacer = Pacer(120, "120")
    _run(pacer, 3.0, cost_ms=30.0)
    assert pacer.rate == 120


def test_idle_collector_collects_only_with_slack():
    collector = IdleCollector()
    try:
        collector.start(now=0.0)
        assert not gc.isenabled()
        assert not collector.frame_slack(0.5, slack_ms=10.0)       # not due yet
        assert not collector.frame_slack(1.5, slack_ms=1.0)        # due, but no time to spare
        assert collector.frame_slack(1.6, slack_ms=6.0)
        assert not collector.frame_slack(1.7, slack_ms=6.0)        # next one a second later
        collector.idle()
        assert collector.collections == 2
    finally:
        collector.stop()
        gc.unfreeze()
    assert gc.isenabled()


def test_android_wrappers_are_no_ops_elsewhere():
    if android_perf.IS_ANDROID:
        pytest.skip("desktop behaviour")
    hints = android_perf.PerformanceHints([android_perf.gui_thread_id()], 8.33)
    assert not hints.available and hints.reason == "not Android"
    hints.report(5.0)
    hints.set_target(16.6)
    hints.close()
    assert "off" in hints.describe()
    assert android_perf.thermal_status() == "unknown"
    assert android_perf.gui_thread_id() > 0


class _FakeLib:
    """libandroid stand-in: records the calls the wrapper makes."""

    def __init__(self) -> None:
        self.calls = []
        outer = self

        class Fn:
            def __init__(self, name, result):
                self.name, self.result = name, result

            def __call__(self, *args):
                outer.calls.append((self.name, args))
                return self.result

        self.APerformanceHint_getManager = Fn("manager", 1)
        self.APerformanceHint_createSession = Fn("create", 7)
        self.APerformanceHint_reportActualWorkDuration = Fn("report", 0)
        self.APerformanceHint_updateTargetWorkDuration = Fn("target", 0)
        self.APerformanceHint_closeSession = Fn("close", None)
        self.AThermal_acquireManager = Fn("thermal", 3)
        self.AThermal_getCurrentThermalStatus = Fn("status", 2)
        self.AThermal_releaseManager = Fn("release", None)


def test_hint_session_calls():
    lib = _FakeLib()
    hints = android_perf.PerformanceHints([101, 102], 8.33, lib=lib)
    assert hints.available
    create = next(args for name, args in lib.calls if name == "create")
    assert list(create[1][:2]) == [101, 102] and create[2] == 2 and create[3] == 8_330_000
    hints.report(4.5)
    hints.set_target(16.67)
    hints.close()
    names = [name for name, _args in lib.calls]
    assert names[-3:] == ["report", "target", "close"]
    assert ("report", (7, 4_500_000)) in lib.calls
    assert android_perf.thermal_status(lib) == "moderate"
