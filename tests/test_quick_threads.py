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


def test_cpu_frequencies_from_sysfs(tmp_path):
    for name, cpus, now, hardware, limit in (("policy0", "0 1 2 3", 1804800, 1804800, 1804800),
                                             ("policy4", "4 5 6", 1900800, 2419200, 2000000),
                                             ("policy7", "7", 3187200, 3187200, 3187200)):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "related_cpus").write_text(cpus + "\n")
        (folder / "scaling_cur_freq").write_text(f"{now}\n")
        (folder / "cpuinfo_max_freq").write_text(f"{hardware}\n")
        (folder / "scaling_max_freq").write_text(f"{limit}\n")
    assert threads.cpu_frequencies(tmp_path) == (
        "cpu 0-3: 1.80 of 1.80 GHz · cpu 4-6: 1.90 of 2.42 GHz (limit 2.00) · cpu 7: 3.19 of 3.19 GHz")
    assert threads.cpu_frequencies(tmp_path / "missing") == ""


def test_thread_table_names_the_gui_thread_and_lists_workers():
    stop = threading.Event()

    def spin() -> None:
        while not stop.is_set():
            sum(range(1000))

    worker = threading.Thread(target=spin, name="spinner", daemon=True)
    before = threads.thread_table()
    started = time.perf_counter()
    worker.start()
    time.sleep(0.25)
    after = threads.thread_table()
    stop.set()
    worker.join()
    assert any(name == "GUI" for name, _cpu in after.values())
    rows = threads.busiest_threads(before, after, time.perf_counter() - started)
    assert rows and rows[0][2] > 10.0                    # the spinner is the busiest thread
    assert worker.native_id in {tid for _name, tid, _share in rows}


def test_busiest_threads_counts_new_threads_from_zero():
    before = {1: ("GUI", 1.0)}
    after = {1: ("GUI", 1.5), 7: ("QSGRenderThread", 0.25), 9: ("idle", 0.001)}
    assert threads.busiest_threads(before, after, 1.0) == [("GUI", 1, 50.0), ("QSGRenderThread", 7, 25.0)]
