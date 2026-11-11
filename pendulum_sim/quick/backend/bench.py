"""QML context object ``bench``: the built-in performance test (Run → report → Copy).

Runs the live app through a few situations – the Simulate screen at automatic and fixed
frame rates, the Graphs screen with four graphs, the physics in a thread for
comparison – a few seconds each, and records every shown frame. The report has the
same parts as the Widgets app's performance report: fps, frame gaps p50/p95/max,
per-frame breakdown, busy time, threads, start-up timeline – plus CPU clocks, thermal
status, the pacing and performance-hint state.

The report is saved after every phase, so if the app dies during the test the next start
offers what was measured up to then.
"""

from __future__ import annotations

import os
import platform
import statistics
import sys
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QStandardPaths, QTimer, Signal, Slot, qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow

from . import threads
from .android_perf import api_level, thermal_status
from .pacing import p95

BENCH_GRAPHS = ["angle", "angular_velocity", "cart_position", "control"]
PHASES = [  # label, screen, frame rate, physics
    ("Simulate · frame rate auto · physics process", "simulate", "auto", "process"),
    ("Simulate · 120 fps · physics process", "simulate", "120", "process"),
    ("Simulate · 60 fps · physics process", "simulate", "60", "process"),
    ("Graphs, 4 graphs · 120 fps · physics process", "graphs", "120", "process"),
    ("Graphs, 4 graphs · 60 fps · physics process", "graphs", "60", "process"),
    ("Simulate · 120 fps · physics thread (comparison)", "simulate", "120", "thread"),
]
SETTLE_S = 1.5
RECORD_S = float(os.environ.get("PENDULUM_BENCH_SECONDS", "5"))


def report_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or "."
    return Path(base) / "quick_performance_report.txt"


def summarise(label: str, frames: list[tuple[float, float, float]], seconds: float, presented: int,
              graph_ms: list[float], stats0: dict, stats1: dict, rates: list[int]) -> str:
    """One report block from recorded frames ``(gap ms, Python ms, work ms)``."""
    if not frames:
        return f"{label}\n  no frames recorded"
    gaps = [f[0] for f in frames]
    python = [f[1] for f in frames]
    work = [f[2] for f in frames]
    steps = stats1["steps"] - stats0["steps"]
    phys = (stats1["busy_s"] - stats0["busy_s"]) / max(seconds, 1e-9)
    shown_rates = sorted(set(rates), reverse=True)
    lines = [
        label,
        f"  {len(frames) / seconds:6.1f} fps shown ({'/'.join(str(r) for r in shown_rates)} fps paced)   "
        f"{presented / seconds:6.1f} fps presented by Qt",
        f"  frame gap p50 {statistics.median(gaps):5.1f}  p95 {p95(gaps):5.1f}  max {max(gaps):6.1f} ms   "
        f"gaps > 33 ms: {sum(1 for g in gaps if g > 33.0)}",
        f"  work per frame (critical path): mean {sum(work) / len(work):5.2f}  p95 {p95(work):5.2f}  "
        f"max {max(work):5.2f} ms",
        f"  of which Python on the GUI thread: mean {sum(python) / len(python):5.2f}  p95 {p95(python):5.2f}  "
        f"max {max(python):5.2f} ms",
        f"  physics: {steps / seconds:6.0f} steps/s, {100 * phys:4.1f} % of one core, "
        f"real-time factor {stats1['realtime_factor']:.2f}",
    ]
    if graph_ms:
        lines.append(f"  graph data per update: mean {sum(graph_ms) / len(graph_ms):5.2f}  "
                     f"p95 {p95(graph_ms):5.2f} ms  ({len(graph_ms) / seconds:4.1f} updates/s)")
    return "\n".join(lines)


class Benchmark(QObject):
    changed = Signal()
    finished = Signal()
    showRequested = Signal()

    def __init__(self, app, sim, graphs, frames, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.app, self.sim, self.graphs, self.frames = app, sim, graphs, frames
        self.window = None
        self.warnings: list[str] = []
        self._phase = -1
        self._blocks: list[str] = []
        self._report = ""
        self._progress = ""
        self._copied = False
        self._saved: tuple | None = None
        self._start: tuple = ()
        self._recording = False
        self._rates: list[int] = []
        self._before = {"clocks": "", "thermal": ""}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._step)
        frames.rateChanged.connect(self._on_rate)
        try:
            previous = report_path().read_text(encoding="utf-8")
            if previous.strip():
                self._report = "Report of the previous run:\n\n" + previous
        except OSError:
            pass

    report = Property(str, lambda self: self._report, notify=changed)
    progress = Property(str, lambda self: self._progress, notify=changed)
    running = Property(bool, lambda self: self._phase >= 0, notify=changed)
    copied = Property(bool, lambda self: self._copied, notify=changed)

    def _on_rate(self) -> None:
        if self._recording:
            self._rates.append(self.frames.pacer.rate)

    # ------------------------------------------------------------------ run
    @Slot()
    def start(self) -> None:
        if self._phase >= 0:
            return
        self._saved = (self.app.screen, self.frames.pacer.mode, self.sim.physics, self.sim.running,
                       list(self.graphs.selection))
        self._blocks = [self.header()]
        self._before = {"clocks": threads.cpu_frequencies(), "thermal": thermal_status()}
        self._phase = -1
        self._copied = False
        for key in BENCH_GRAPHS:
            self.graphs.select(key, True)
        self.sim.restart()
        self._next()

    @Slot()
    def cancel(self) -> None:
        if self._phase >= 0:
            self._timer.stop()
            self._stop_recording()
            self._finish("cancelled")

    def _next(self) -> None:
        self._phase += 1
        while self._phase < len(PHASES) and PHASES[self._phase][3] == "process" and not self.sim.processAvailable:
            self._blocks.append(f"{PHASES[self._phase][0]}\n  skipped: no fork on this platform")
            self._phase += 1
        if self._phase >= len(PHASES):
            self._finish("complete")
            return
        label, screen, rate, physics = PHASES[self._phase]
        self.sim.physics = physics
        self.sim.start()
        self.frames.set_mode(rate)
        self.app.screen = screen
        self._progress = f"{self._phase + 1}/{len(PHASES)}  {label}"
        self._save(f"… running phase {self._phase + 1}: {label} (if this is the last line, the app stopped here)")
        self.changed.emit()
        self._recording = False
        self._timer.start(int(SETTLE_S * 1000))

    def _step(self) -> None:
        if not self._recording:                      # settled: start recording
            self._recording = True
            self._rates = [self.frames.pacer.rate]
            self.frames.recording = []
            self.graphs.recording = []
            swaps = int(self.window.property("swaps")) if self.window is not None else 0
            self._start = (time.perf_counter(), swaps, self.sim.source.stats(), threads.thread_table())
            self._timer.start(int(RECORD_S * 1000))
            return
        frames, graph_ms = self._stop_recording()
        started, swaps0, stats0, table0 = self._start
        seconds = time.perf_counter() - started
        presented = (int(self.window.property("swaps")) if self.window is not None else 0) - swaps0
        block = summarise(PHASES[self._phase][0], frames, seconds, presented, graph_ms, stats0,
                          self.sim.source.stats(), self._rates)
        busiest = threads.busiest_threads(table0, threads.thread_table(), seconds, limit=6)
        if busiest:
            block += "\n  threads (% of one core): " + ", ".join(f"{name} {share:.0f}" for name, _tid, share in busiest)
        self._blocks.append(block)
        self._next()

    def _stop_recording(self) -> tuple[list, list]:
        self._recording = False
        frames, graph_ms = self.frames.recording or [], self.graphs.recording or []
        self.frames.recording = None
        self.graphs.recording = None
        return frames, graph_ms

    def _finish(self, how: str) -> None:
        self._phase = -1
        if self._saved is not None:
            screen, mode, physics, running, selection = self._saved
            self.sim.physics = physics
            self.frames.set_mode(mode)
            for key in BENCH_GRAPHS:
                if key not in selection:
                    self.graphs.select(key, False)
            self.app.screen = screen
            self.sim.running = running
        self._progress = ""
        self._save(f"performance test {how}")
        self._report = self.text(f"performance test {how}")
        print(self._report, flush=True)
        self.changed.emit()
        self.finished.emit()

    @Slot()
    def show(self) -> None:
        """Open the report sheet (QML)."""
        self.showRequested.emit()

    @Slot()
    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self._report)
        self._copied = True
        self.changed.emit()

    # --------------------------------------------------------------- report
    def text(self, tail: str) -> str:
        from ...mobile.startup import TIMER

        parts = list(self._blocks)
        from .lineitem import NOTES as LINE_NOTES

        notes = [f"  {self.frames.describe()}"] + [f"  {k}: {v}" for k, v in LINE_NOTES.items()]
        if self._before["clocks"]:
            notes.append(f"  CPU clocks at the start: {self._before['clocks']}")
            notes.append(f"  CPU clocks now:          {threads.cpu_frequencies()}")
        notes.append(f"  thermal status: {self._before['thermal'] or '?'} at the start, {thermal_status()} now")
        if self.warnings:
            notes += ["  QML warnings:"] + [f"    {w}" for w in self.warnings[:12]]
        parts.append("Notes:\n" + "\n".join(notes))
        parts += [TIMER.report(), tail]
        return "\n\n".join(parts)

    def _save(self, tail: str) -> None:
        try:
            path = report_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.text(tail), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def graphics_api() -> str:
        try:
            api = QQuickWindow.graphicsApi()
            return getattr(api, "name", str(api))
        except Exception as exc:  # noqa: BLE001 - informational only
            return f"? ({type(exc).__name__})"

    def header(self) -> str:
        import PySide6

        from ...mobile import cpu

        app = QGuiApplication.instance()
        screen = self.window.screen() if self.window is not None else app.primaryScreen()
        geo = "?"
        if self.window is not None:
            geo = f"{self.window.width()}×{self.window.height()} logical, pixel ratio {self.window.devicePixelRatio():g}"
        if screen is not None:
            geo += f", {screen.refreshRate():.0f} Hz display"
        cfg = self.sim.session.config
        return "\n".join([
            "Pendulum Quick – performance test",
            f"Python {platform.python_version()} on {sys.platform} {platform.machine()}"
            + (f" (Android API {api_level()})" if api_level() else "")
            + f"; PySide6 {PySide6.__version__}, Qt {qVersion()}",
            geo,
            f"graphics: {self.graphics_api()}; render loop: {threads.render_loop()}",
            f"scenario '{cfg.name}', dt {cfg.simulation.dt * 1000:g} ms, telemetry "
            f"{cfg.simulation.telemetry_rate_hz:g} Hz; graphs refresh at {self.graphs.rateHz:g} Hz; "
            f"{RECORD_S:g} s per phase; {cpu.describe()}",
        ])
