"""Built-in benchmark of the Qt Quick spike (Run benchmark → report → Copy).

Runs the live simulation through the rendering variants, each for a few seconds, and
records every displayed frame. The render-thread-Python variants come last, and the
report is saved after every phase (``previous report`` on the next start), so a crash
in one variant does not lose the numbers of the others.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QStandardPaths, QTimer, Signal, Slot, qVersion
from PySide6.QtGui import QGuiApplication

from . import scene_items, threads
from .bridge import GraphBridge, SimBridge, summarise

PHASES = [  # label, pendulum, graph, physics
    ("QML items · no graph · physics thread", "items", "off", "thread"),
    ("QML items · graph Shape (curve renderer) · thread", "items", "shape", "thread"),
    ("QML items · graph Shape (geometry renderer) · thread", "items", "shapegeo", "thread"),
    ("QML items · graph Qt Graphs · thread", "items", "qtgraphs", "thread"),
    ("QML items · no graph · physics process", "items", "off", "process"),
    ("QML items · graph Shape (curve renderer) · process", "items", "shape", "process"),
    ("QML items · graph QPainter item · thread", "items", "painted", "thread"),
    ("QML items · graph scene-graph lines · thread", "items", "node", "thread"),
    ("Scene-graph pendulum (Python) · no graph · thread", "node", "off", "thread"),
]
SETTLE_S = 1.5
RECORD_S = float(os.environ.get("PENDULUM_BENCH_SECONDS", "5"))


def report_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or "."
    return Path(base) / "quick_spike_report.txt"


class Benchmark(QObject):
    changed = Signal()
    finished = Signal()

    def __init__(self, sim: SimBridge, graph: GraphBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim, self.graph = sim, graph
        self.window = None
        self.info: dict[str, str] = {}
        self.warnings: list[str] = []
        self._phase = -1
        self._blocks: list[str] = []
        self._report = ""
        self._progress = ""
        self._copied = False
        self._saved: tuple[str, str, str, bool] | None = None
        self._start = (0.0, 0, {}, {})
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._step)
        self._recording = False
        self.first_frame = False
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

    # ------------------------------------------------------------------ run
    @Slot()
    def start(self) -> None:
        if self._phase >= 0:
            return
        self._saved = (self.sim.pendulumMode, self.graph.mode, self.sim.physics, self.sim.running)
        self._blocks = [self.header()]
        self._phase = -1
        self._copied = False
        self.sim.reset()
        self.sim.running = True
        self._next()

    @Slot()
    def cancel(self) -> None:
        if self._phase >= 0:
            self._timer.stop()
            self._stop_recording()
            self._finish("cancelled")

    def _next(self) -> None:
        self._phase += 1
        while self._phase < len(PHASES) and PHASES[self._phase][3] == "process" \
                and not self.sim.processAvailable:
            self._blocks.append(f"{PHASES[self._phase][0]}\n  skipped: no fork on this platform")
            self._phase += 1
        if self._phase >= len(PHASES):
            self._finish("complete")
            return
        label, pendulum, graph, physics = PHASES[self._phase]
        self.sim.physics = physics
        self.sim.pendulumMode = pendulum
        self.graph.mode = graph
        self.graph.error = ""
        self._progress = f"{self._phase + 1}/{len(PHASES)}  {label}"
        self._save(f"… running phase {self._phase + 1}: {label} (if this is the last line, the app stopped here)")
        self.changed.emit()
        self._recording = False
        self._timer.start(int(SETTLE_S * 1000))

    def _step(self) -> None:
        if not self._recording:                      # settled: start recording
            self._recording = True
            self.sim.recorder.recording = []
            self.graph.recording = []
            swaps = int(self.window.property("swaps")) if self.window is not None else 0
            self._start = (time.perf_counter(), swaps, self.sim.source.stats(), threads.thread_times())
            self._timer.start(int(RECORD_S * 1000))
            return
        frames, graph_ms = self._stop_recording()
        started, swaps0, stats0, cpu0 = self._start
        seconds = time.perf_counter() - started
        swaps = (int(self.window.property("swaps")) if self.window is not None else 0) - swaps0
        block = summarise(PHASES[self._phase][0], frames, seconds, graph_ms, swaps, stats0,
                          self.sim.source.stats(), cpu0, threads.thread_times())
        if self.graph.error:
            block += f"\n  note: {self.graph.error}"
        self._blocks.append(block)
        self._next()

    def _stop_recording(self) -> tuple[list, list]:
        frames, graph_ms = self.sim.recorder.recording or [], self.graph.recording or []
        self.sim.recorder.recording = None
        self.graph.recording = None
        return frames, graph_ms

    def _finish(self, how: str) -> None:
        self._phase = -1
        if self._saved is not None:
            pendulum, graph, physics, running = self._saved
            self.sim.physics, self.sim.pendulumMode, self.graph.mode = physics, pendulum, graph
            self.sim.running = running
        self._progress = ""
        self._save(f"benchmark {how}")
        self._report = self.text(f"benchmark {how}")
        print(self._report, flush=True)
        self.changed.emit()
        self.finished.emit()

    @Slot()
    def firstFrame(self) -> None:  # noqa: N802 - QML API
        """Called by the window when its first frame has been presented."""
        if not self.first_frame:
            from ...mobile.startup import TIMER

            self.first_frame = True
            TIMER.mark("first frame presented")

    @Slot()
    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self._report)
        self._copied = True
        self.changed.emit()

    # --------------------------------------------------------------- report
    def text(self, tail: str) -> str:
        from ...mobile.startup import TIMER

        parts = list(self._blocks)
        notes = [f"  {k}: {v}" for k, v in scene_items.NOTES.items()]
        if self.warnings:
            notes += ["  QML warnings:"] + [f"    {w}" for w in self.warnings[:12]]
        if notes:
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
        cfg = self.sim.config
        lines = [
            "Pendulum Quick (Qt Quick spike) – benchmark",
            f"Python {platform.python_version()} on {sys.platform} {platform.machine()}; "
            f"PySide6 {PySide6.__version__}, Qt {qVersion()}",
            geo,
            f"graphics: {self.info.get('graphics', '?')}; render loop: {threads.render_loop()}",
            f"scenario '{cfg.name}', dt {cfg.simulation.dt * 1000:g} ms, telemetry "
            f"{cfg.simulation.telemetry_rate_hz:g} Hz; {RECORD_S:g} s per phase; {cpu.describe()}",
            f"interpreter switch interval {sys.getswitchinterval() * 1000:g} ms",
        ]
        return "\n".join(lines)
