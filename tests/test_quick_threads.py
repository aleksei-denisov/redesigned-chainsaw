"""Per-thread CPU times for the Qt Quick benchmark (Qt-free, Linux/Android only)."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from pendulum_sim.quick.backend import threads

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")


def test_gui_thread_time_grows_with_work():
    before = threads.thread_times()
    assert "gui" in before
    end = time.perf_counter() + 0.12
    while time.perf_counter() < end:
        sum(range(1000))
    after = threads.thread_times()
    assert 0.05 < after["gui"] - before["gui"] < 0.5


def test_other_threads_are_counted_separately():
    stop = threading.Event()

    def spin() -> None:
        while not stop.is_set():
            sum(range(1000))

    worker = threading.Thread(target=spin, daemon=True)
    before = threads.thread_times()
    worker.start()
    time.sleep(0.2)
    after = threads.thread_times()
    stop.set()
    worker.join()
    assert after.get("other", 0.0) - before.get("other", 0.0) > 0.03


def test_render_loop_without_qt_is_not_threaded():
    assert threads.render_loop().startswith("not threaded")
