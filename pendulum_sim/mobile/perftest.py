"""On-device performance test (*Settings → Debug → Run performance test*).

Runs the live simulation through a fixed sequence and records every frame:

1. GPU drawing: *Simulate* screen (no graphs visible), *Graphs* screen with 2, 3
   and 4 graphs, then 3 graphs again under ``cProfile`` (Python hot spots; timings
   of this phase are inflated by the profiler and not reported),
2. software drawing: *Simulate* and *Graphs* with 3 graphs (for comparison).

For each timed phase the report lists frames per second, frame-gap percentiles,
the busy time per frame split into physics, pendulum view, graph data, graph
painting and the whole screen update (painting + composing + hand-over to the
display, whose remainder is the upload), the samples drawn per second and the
graph scroll rate. The text can be copied to the clipboard and is printed to the
system log (``adb logcat``).
"""

from __future__ import annotations

import cProfile
import io
import platform
import pstats
import statistics
import sys
import time

from PySide6.QtCore import QObject, QTimer, Signal

G2 = ["angle", "control"]
G3 = ["angle", "control", "cart_position"]
G4 = ["angle", "angular_velocity", "control", "cart_position"]
PHASES = [  # (label, screen, graphs or None, seconds, profiled, drawing or None = keep)
    ("GPU · Simulate screen (no graphs)", "simulate", None, 5.0, False, "gpu"),
    ("GPU · Graphs screen, 2 graphs", "graphs", G2, 5.0, False, "gpu"),
    ("GPU · Graphs screen, 3 graphs", "graphs", G3, 5.0, False, "gpu"),
    ("GPU · Graphs screen, 4 graphs", "graphs", G4, 5.0, False, "gpu"),
    ("Python profile, 3 graphs, GPU", "graphs", G3, 4.0, True, "gpu"),
    ("Software · Simulate screen (no graphs)", "simulate", None, 5.0, False, "software"),
    ("Software · Graphs screen, 3 graphs", "graphs", G3, 5.0, False, "software"),
]
SETTLE_S = 1.5             # after switching screen / drawing mode, before recording


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def summarise(label: str, frames: list[tuple], seconds: float) -> str:
    """One report block from recorded frames ``(gap, physics, view, gdata, gpaint, update, points)``."""
    if not frames:
        return f"{label}: no frames recorded"
    gaps = [f[0] for f in frames]
    n = len(frames)

    def mean(i: int) -> float:
        return sum(f[i] for f in frames) / n

    points = sum(f[6] for f in frames)
    scrolls = sum(1 for f in frames if f[3] > 0.0)
    # the screen update contains the view and graph painting (fallback: their sum)
    busy = [f[1] + f[3] + max(f[5], f[2] + f[4]) for f in frames]
    upload = max(0.0, mean(5) - mean(2) - mean(4))
    return "\n".join([
        f"{label}",
        f"  {n / seconds:5.1f} fps   frame gap p50 {statistics.median(gaps):5.1f}  p95 {_pct(gaps, 0.95):5.1f}"
        f"  max {max(gaps):6.1f} ms",
        f"  per frame (ms): physics {mean(1):5.2f} · view {mean(2):5.2f} · graph data {mean(3):5.2f}"
        f" · graph paint {mean(4):5.2f} · screen update {mean(5):5.2f} (upload ≈ {upload:4.2f})",
        f"  busy per frame: mean {sum(busy) / n:5.2f}  p95 {_pct(busy, 0.95):5.2f} ms"
        f"   graph steps {scrolls / seconds:4.1f}/s, {points / seconds:6.0f} samples drawn/s",
    ])


TEXT_SAMPLES = ("t =    12.34 s   x = +0.123 m", "θ1 =   +1.23°   ω1 =  +12.3°/s")


def text_benchmark(repeats: int = 40) -> str:
    """Microseconds per text measurement / drawn line for a few fonts (text was the
    largest cost of the pendulum view on a tablet: font fallback for a missing family)."""
    from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF, QImage, QPainter

    fonts = [("app font", QFont()), ('"Monospace"', QFont("Monospace")),
             ("system monospace", QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))]
    img = QImage(600, 60, QImage.Format.Format_ARGB32_Premultiplied)
    lines = ["Text cost (µs per call: measure / draw; ASCII line, line with θ ω °):"]
    for name, font in fonts:
        cells = []
        for text in TEXT_SAMPLES:
            fm = QFontMetricsF(font)
            t0 = time.perf_counter()
            for _ in range(repeats):
                fm.horizontalAdvance(text)
            measure = (time.perf_counter() - t0) / repeats * 1e6
            p = QPainter(img)
            p.setFont(font)
            t0 = time.perf_counter()
            for _ in range(repeats):
                p.drawText(4, 40, text)
            draw = (time.perf_counter() - t0) / repeats * 1e6
            p.end()
            cells.append(f"{measure:7.0f} / {draw:7.0f}")
        lines.append(f"  {name:<18} {font.family()[:18]:<18} " + "   ".join(cells))
    return "\n".join(lines)


class PerformanceTest(QObject):
    """Drives the window through :data:`PHASES` with timers (the UI keeps running)."""

    progress = Signal(str)
    finished = Signal(str)

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.blocks: list[str] = []
        self.phases: list[tuple] = list(PHASES)   # this run's copy (labels get notes appended)
        self._phase = -1
        self._profile: cProfile.Profile | None = None
        self._started = 0.0
        self._saved = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next)

    @property
    def running(self) -> bool:
        return self._phase >= 0

    def start(self) -> None:
        w = self.window
        self._saved = (w.current, list(w.telemetry.selected), w.running)
        self.blocks = [self._header()]
        self.phases = list(PHASES)
        self._phase = -1
        w.reset()
        w.start()
        self._next()

    def cancel(self) -> None:
        if self.running:
            self._timer.stop()
            self._stop_recording()
            self._restore()
            self._phase = -1

    # ------------------------------------------------------------------ phases
    def _next(self) -> None:
        w = self.window
        if self._phase >= 0:
            self._finish_phase()
        self._phase += 1
        if self._phase >= len(self.phases):
            self._done()
            return
        phase = self.phases[self._phase]
        label, screen, graphs, seconds, profiled = phase[:5]
        drawing = phase[5] if len(phase) > 5 else None
        if drawing is not None:
            used = w.set_drawing(drawing, w.perf.gpu_samples)
            if used != drawing:
                label += f" (not available: {used} drawing)"
                self.phases[self._phase] = (label,) + tuple(phase[1:])
        if graphs is not None:
            w.telemetry.set_selection(graphs)
        w.navigate(screen, remember=False)
        if not w.running:
            w.start()
        self.progress.emit(f"{self._phase + 1}/{len(self.phases)}: {label}")
        QTimer.singleShot(int(SETTLE_S * 1000), self._begin_recording)

    def _begin_recording(self) -> None:
        if not self.running:
            return
        _label, _screen, _graphs, seconds, profiled = self.phases[self._phase][:5]
        self.window.stats.recording = []
        self._started = time.perf_counter()
        if profiled:
            self._profile = cProfile.Profile()
            self._profile.enable()
        self._timer.start(int(seconds * 1000))

    def _stop_recording(self) -> list:
        if self._profile is not None:
            self._profile.disable()
        frames = self.window.stats.recording or []
        self.window.stats.recording = None
        return frames

    def _finish_phase(self) -> None:
        label, _screen, _graphs, _seconds, profiled = self.phases[self._phase][:5]
        elapsed = time.perf_counter() - self._started
        frames = self._stop_recording()
        if profiled and self._profile is not None:
            out = io.StringIO()
            stats = pstats.Stats(self._profile, stream=out)
            stats.sort_stats("tottime").print_stats(18)
            text = out.getvalue()
            body = text[text.find("ncalls"):] if "ncalls" in text else text
            self.blocks.append(f"{label} – top Python functions by own time over {elapsed:.1f} s:\n{body}")
            self._profile = None
        else:
            self.blocks.append(summarise(label, frames, elapsed))

    def _done(self) -> None:
        from .startup import TIMER

        self._phase = -1
        self._restore()
        from . import gpu

        errors = f"GPU drawing errors: {gpu.ERRORS['count']}"
        if gpu.ERRORS.get("first"):
            errors += "\nfirst error:\n" + gpu.ERRORS["first"].strip()[-1500:]
        report = "\n\n".join(self.blocks + [text_benchmark(), errors, TIMER.report()])
        print(report, flush=True)
        self.finished.emit(report)

    def _restore(self) -> None:
        if self._saved is None:
            return
        current, selection, running = self._saved
        w = self.window
        w.set_drawing(w.perf.drawing, w.perf.gpu_samples)
        w.telemetry.set_selection(selection)
        w.navigate(current, remember=False)
        if not running:
            w.pause()
        self._saved = None

    def _header(self) -> str:
        w = self.window
        from . import cpu
        from .startup import TIMER

        scr = w.screen()
        geo = f"{w.width()}×{w.height()} logical, pixel ratio {w.devicePixelRatioF():g}"
        if scr is not None:
            geo += f", {scr.refreshRate():.0f} Hz display"
        p = w.perf
        return "\n".join([
            "Inverted Pendulum Simulator – performance test",
            f"Python {platform.python_version()} on {sys.platform} {platform.machine()}; {geo}",
            f"scenario '{w.config.name}', dt {w.config.simulation.dt * 1000:g} ms, "
            f"telemetry {w.config.simulation.telemetry_rate_hz:g} Hz, graph window {w.telemetry.window_s} s",
            f"settings: {p.drawing} drawing, {p.fps} fps, graphs {p.graph_fps} Hz, view resolution {p.render}, "
            f"antialias {'on' if p.antialias else 'off'}, fast cores {'on' if p.fast_cores else 'off'}; "
            f"{cpu.describe()}",
            f"start-up to main window {dict(TIMER.marks).get('main window shown', TIMER.total):.2f} s",
        ])
