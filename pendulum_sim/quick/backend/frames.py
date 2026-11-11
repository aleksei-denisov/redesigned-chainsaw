"""QML singleton ``Frames``: the vsync-driven frame loop.

QML's ``FrameAnimation`` calls :meth:`FrameDriver.vsync` on every vsync. The pacer
(``pacing.py``) decides whether this vsync shows a new frame; if so the simulation
bridge reads the latest physics state (``Sim.advance``) and QML moves the pendulum.
Skipped vsyncs change nothing, so Qt Quick does not render them.

Per shown frame the driver measures the work it cost: the GUI thread's CPU time since
the previous shown frame (Python, QML bindings, event handling and – with Qt's basic
render loop – the rendering itself) and the render thread's average CPU per frame
(read once a second from ``/proc``). The larger of the two is the frame's critical
path; it feeds the pacer, the Android performance hint session and the report. When a
frame leaves enough slack, the young generation of the garbage collector runs there.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from . import threads
from .android_perf import PerformanceHints, gui_thread_id
from .pacing import IdleCollector, Pacer, p95
from .simulation import SimulationBridge

STATS_HZ = 4.0


class FrameDriver(QObject):
    statsChanged = Signal()
    rateChanged = Signal()
    enabledChanged = Signal()

    def __init__(self, sim: SimulationBridge, refresh_hz: float, mode: str = "auto",
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self.pacer = Pacer(refresh_hz, mode)
        self.collector = IdleCollector()
        self.hints: PerformanceHints | None = None
        self.render_tid: int | None = None
        self._enabled = True
        self._cpu_last = 0.0
        self._wall_last = 0.0
        self._render_ms = 0.0
        self._render_cpu = (0.0, 0.0, 0)          # (CPU seconds, wall, shown frames) at the last reading
        self._shown = 0
        self.vsyncs = 0
        self.recording: list[tuple[float, float, float]] | None = None    # (gap ms, Python ms, work ms)
        self._recent: list[float] = []            # work of recent frames (statistics for the overlay)
        self._gaps: list[float] = []
        self._stats = {"rate": "", "work": "", "gap": "", "render": "", "gc": "", "hints": ""}
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / STATS_HZ))
        self._timer.timeout.connect(self._update_stats)
        self._timer.start()
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(1000)
        self._render_timer.timeout.connect(self._read_render_thread)

    # ------------------------------------------------------------- set-up
    def start_threads(self) -> None:
        """After the first frame: find Qt's render thread, open the hint session."""
        table = threads.thread_table()
        self.render_tid = next((tid for tid, (name, _cpu) in table.items() if name == threads.RENDER_THREAD), None)
        tids = [gui_thread_id()] + ([self.render_tid] if self.render_tid else [])
        self.hints = PerformanceHints(tids, self.pacer.interval_ms)
        if self.render_tid:
            self._render_cpu = (self._render_seconds(), time.perf_counter(), self._shown)
            self._render_timer.start()

    def _render_seconds(self) -> float:
        try:
            return threads._cpu_seconds(Path(f"/proc/self/task/{self.render_tid}"))
        except (OSError, ValueError, IndexError):
            return 0.0

    def _read_render_thread(self) -> None:
        cpu, wall, shown = self._render_seconds(), time.perf_counter(), self._shown
        cpu0, _wall0, shown0 = self._render_cpu
        if shown > shown0:
            self._render_ms = 1000.0 * (cpu - cpu0) / (shown - shown0)
        self._render_cpu = (cpu, wall, shown)

    # ------------------------------------------------------------ settings
    def set_mode(self, mode: str) -> None:
        before = self.pacer.rate
        self.pacer.set_mode(mode)
        if self.pacer.rate != before:
            self._rate_changed()

    def set_refresh(self, refresh_hz: float) -> None:
        before = self.pacer.rate
        self.pacer.set_refresh(refresh_hz)
        if self.pacer.rate != before:
            self._rate_changed()

    def _rate_changed(self) -> None:
        if self.hints is not None:
            self.hints.set_target(self.pacer.interval_ms)
        self.rateChanged.emit()

    rate = Property(int, lambda self: self.pacer.rate, notify=rateChanged)
    mode = Property(str, lambda self: self.pacer.mode, notify=rateChanged)

    def _set_enabled(self, on: bool) -> None:
        if bool(on) != self._enabled:
            self._enabled = bool(on)
            if not on:
                self.pacer.paused()
                self._wall_last = 0.0
                self.collector.idle()
            self.enabledChanged.emit()

    enabled = Property(bool, lambda self: self._enabled, _set_enabled, notify=enabledChanged)

    # ----------------------------------------------------------- per vsync
    @Slot()
    def vsync(self) -> None:
        self.vsyncs += 1
        if not self.pacer.vsync():
            return
        now = time.perf_counter()
        cpu = time.thread_time()
        work = gap = 0.0
        if self._wall_last:
            gui_ms = (cpu - self._cpu_last) * 1000.0
            work = max(gui_ms, self._render_ms)
            rate = self.pacer.rate
            self.pacer.frame_done(now, work)
            if self.pacer.rate != rate:
                self._rate_changed()
            if self.hints is not None:
                self.hints.report(work)
            gap = (now - self._wall_last) * 1000.0
            if len(self._recent) < 512:
                self._recent.append(work)
                self._gaps.append(gap)
        self._cpu_last = cpu
        self._wall_last = now
        self._shown += 1
        self.sim.advance()
        python_ms = (time.perf_counter() - now) * 1000.0
        if self.recording is not None and gap:
            self.recording.append((gap, python_ms, work))
        self.collector.frame_slack(now, self.pacer.interval_ms - python_ms, time.perf_counter)

    # ---------------------------------------------------------- statistics
    def _update_stats(self) -> None:
        work, gaps = self._recent, self._gaps
        self._recent, self._gaps = [], []
        s = self._stats
        s["rate"] = f"{self.pacer.rate} fps" + (" auto" if self.pacer.mode == "auto" else "")
        if work:
            s["work"] = f"work {sum(work) / len(work):4.1f} / p95 {p95(work):4.1f} ms"
            s["gap"] = f"gap p95 {p95(gaps):4.1f} max {max(gaps):4.1f} ms"
        s["render"] = f"render thread {self._render_ms:4.1f} ms" if self.render_tid else "basic render loop"
        s["gc"] = f"gc {self.collector.collections} × {self.collector.last_ms:.1f} ms"
        s["hints"] = "hints on" if self.hints is not None and self.hints.available else "hints off"
        self.statsChanged.emit()

    statsText = Property(str, lambda self: "\n".join(v for v in self._stats.values() if v), notify=statsChanged)

    def describe(self) -> str:
        hints = self.hints.describe() if self.hints is not None else "not started"
        return (f"pacing {self.pacer.mode} at {self.pacer.rate} fps of {self.pacer.refresh_hz:g} Hz "
                f"(rate changes so far: {self.pacer.changes}); performance hints: {hints}; "
                f"render thread: {'tid ' + str(self.render_tid) if self.render_tid else 'none (basic render loop)'}; "
                f"garbage collection: {'idle-time only' if self.collector.active else 'automatic'}, "
                f"{self.collector.collections} young collections, last {self.collector.last_ms:.2f} ms")

    def shutdown(self) -> None:
        self._timer.stop()
        self._render_timer.stop()
        if self.hints is not None:
            self.hints.close()
        self.collector.stop()
