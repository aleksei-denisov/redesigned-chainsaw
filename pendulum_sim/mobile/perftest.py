"""On-device performance test (*Settings → Debug → Run performance test*).

Runs the live simulation through a fixed sequence and records every frame:

1. *Simulate* screen (no graphs visible),
2. *Graphs* screen with 2, 3 and 4 graphs,
3. *Graphs* with 3 graphs again under ``cProfile`` (Python hot spots; timings of
   this phase are inflated by the profiler and not reported).

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

PHASES = [  # (label, screen, graphs or None, seconds, profiled)
    ("Simulate screen (no graphs)", "simulate", None, 6.0, False),
    ("Graphs screen, 2 graphs", "graphs", ["angle", "control"], 6.0, False),
    ("Graphs screen, 3 graphs", "graphs", ["angle", "control", "cart_position"], 6.0, False),
    ("Graphs screen, 4 graphs", "graphs", ["angle", "angular_velocity", "control", "cart_position"], 6.0, False),
    ("Python profile, 3 graphs", "graphs", ["angle", "control", "cart_position"], 4.0, True),
]
SETTLE_S = 1.0


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


class PerformanceTest(QObject):
    """Drives the window through :data:`PHASES` with timers (the UI keeps running)."""

    progress = Signal(str)
    finished = Signal(str)

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.blocks: list[str] = []
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
        if self._phase >= len(PHASES):
            self._done()
            return
        label, screen, graphs, seconds, profiled = PHASES[self._phase]
        if graphs is not None:
            w.telemetry.set_selection(graphs)
        w.navigate(screen, remember=False)
        if not w.running:
            w.start()
        self.progress.emit(f"{self._phase + 1}/{len(PHASES)}: {label}")
        QTimer.singleShot(int(SETTLE_S * 1000), self._begin_recording)

    def _begin_recording(self) -> None:
        if not self.running:
            return
        _label, _screen, _graphs, seconds, profiled = PHASES[self._phase]
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
        label, _screen, _graphs, _seconds, profiled = PHASES[self._phase]
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
        report = "\n\n".join(self.blocks + [TIMER.report()])
        print(report, flush=True)
        self.finished.emit(report)

    def _restore(self) -> None:
        if self._saved is None:
            return
        current, selection, running = self._saved
        w = self.window
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
            f"settings: {p.fps} fps, graphs {p.graph_fps} Hz, view resolution {p.render}, "
            f"antialias {'on' if p.antialias else 'off'}, fast cores {'on' if p.fast_cores else 'off'}; "
            f"{cpu.describe()}",
            f"start-up to main window {dict(TIMER.marks).get('main window shown', TIMER.total):.2f} s",
        ])
