"""Full-screen auto-tune flow for the mobile app: set-up → running → result.

The screen covers the whole window while it is open, so the rest of the app is
locked until the search finishes or is cancelled. The search itself runs in a
:class:`TuneWorker` thread that drives :func:`~pendulum_sim.simulation.tuning.robust_tune`
with the shared worker pool (all CPU cores but one); the UI thread only receives
small progress records, so it keeps animating and Android never reports the app
as not responding.

* **Set-up** – Quick / Balanced / Thorough with an estimated duration measured on
  this device (one calibration simulation), and the number of CPU cores used.
* **Running** – progress bar with percentage (smoothly extrapolated between
  updates), the phase in plain words, elapsed and remaining time, cores in use,
  best result so far with its gains, a live plot of the best push per candidate
  and a spinner. *Cancel* stops the workers at once and offers the best result.
* **Result** – recoverable push before → after, old vs new gains, response metrics
  (settling time, overshoot, peak force, control energy) and a preview of both
  responses to the same push; *Apply*, *Discard* or *Rerun*.

If the app goes to the background the search is cancelled (see ``MobileWindow``).
"""

from __future__ import annotations

import copy
import math
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..control import ControllerConfig
from ..simulation import AppConfig, parallel
from ..simulation.tuning import (
    CancelToken,
    TuneProgress,
    TuningCancelled,
    TuningResult,
    TuningSettings,
    calibrate_push_test_seconds,
    compare_responses,
    estimate_seconds,
    robust_tune,
)
from .plot_math import format_tick, nice_ticks

PRESETS = [("quick", "Quick", "Fewer candidates – a good first improvement"),
           ("normal", "Balanced", "Recommended"),
           ("thorough", "Thorough", "Most candidates – best result, takes longest")]
ACCENT = QColor("#4FA3FF")
MUTED = QColor("#9AA0A6")


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "–"
    seconds = max(0, int(round(seconds)))
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds} s"


def loop_name(signal: str) -> str:
    return "Cart x" if signal == "cart" else f"θ{signal[5:]}"


def gains_text(ctrl: ControllerConfig | None) -> str:
    if ctrl is None:
        return "–"
    return "\n".join(f"{loop_name(lp.signal)}:  Kp {lp.kp:.4g} · Ki {lp.ki:.4g} · Kd {lp.kd:.4g}"
                     for lp in ctrl.loops if lp.enabled)


# ------------------------------------------------------------------ widgets
class Spinner(QWidget):
    """Small rotating arc – visible motion even between progress updates."""

    def __init__(self, size: int = 36) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start(40)

    def stop(self) -> None:
        self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.setPen(QPen(QColor(128, 128, 128, 60), 4))
        p.drawEllipse(r)
        if self._timer.isActive():
            pen = QPen(ACCENT, 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(r, -self._angle * 16, 100 * 16)
        p.end()


class MiniPlot(QWidget):
    """Compact line plot of a few (x, y, colour, label) series with readable ticks."""

    def __init__(self, unit: str = "", min_height: int = 150) -> None:
        super().__init__()
        self.unit = unit
        self.series: list[tuple[np.ndarray, np.ndarray, QColor, str]] = []
        self.points = None
        self.x_label = ""
        self.setMinimumHeight(min_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_series(self, series, x_label: str = "", points=None) -> None:
        """Line ``series`` [(x, y, colour, legend)], optional ``points`` (x, y, colour, legend) as dots."""
        self.series = [(np.asarray(x, float), np.asarray(y, float), QColor(c), label) for x, y, c, label in series]
        self.points = None if points is None else (np.asarray(points[0], float), np.asarray(points[1], float),
                                                   QColor(points[2]), points[3])
        self.x_label = x_label
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base().color())
        fg = pal.text().color()
        fm = QFontMetricsF(self.font())
        finite = [(x[np.isfinite(y)], y[np.isfinite(y)]) for x, y, *_ in self.series]
        extra = [] if self.points is None else [(self.points[0], self.points[1])]
        xs = np.concatenate([x for x, _ in finite + extra]) if finite + extra else np.array([])
        ys = np.concatenate([y for _, y in finite + extra]) if finite + extra else np.array([])
        if xs.size < 2:
            p.setPen(MUTED)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "waiting for data…")
            p.end()
            return
        x0, x1 = float(xs.min()), float(xs.max())
        y0, y1 = float(ys.min()), float(ys.max())
        if y1 - y0 < 1e-9:
            y0, y1 = y0 - 0.5, y1 + 0.5
        pad = 0.08 * (y1 - y0)
        y0, y1 = y0 - pad, y1 + pad
        if x1 <= x0:
            x1 = x0 + 1
        yt, ystep = nice_ticks(y0, y1, 4)
        left = max(fm.horizontalAdvance(format_tick(v, ystep)) for v in yt) + 10 if yt else 30
        top = fm.height() * 1.5
        r = QRectF(left, top, self.width() - left - 8, self.height() - top - fm.height() * 1.6)
        grid = QColor(fg)
        grid.setAlpha(40)
        # header: unit + legend
        p.setPen(fg)
        head = f"{self.unit}  vs  {self.x_label}" if self.x_label else self.unit
        p.drawText(QPointF(4, fm.ascent()), head)
        lx = 4 + fm.horizontalAdvance(head) + 16
        if self.points is not None and self.points[3]:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.points[2])
            p.drawEllipse(QPointF(lx + 7, fm.ascent() * 0.65), 4, 4)
            p.setPen(fg)
            p.drawText(QPointF(lx + 18, fm.ascent()), self.points[3])
            lx += 18 + fm.horizontalAdvance(self.points[3]) + 14
        for _x, _y, col, label in self.series:
            if not label:
                continue
            p.setPen(QPen(col, 3))
            p.drawLine(QPointF(lx, fm.ascent() * 0.65), QPointF(lx + 14, fm.ascent() * 0.65))
            p.setPen(fg)
            p.drawText(QPointF(lx + 18, fm.ascent()), label)
            lx += 18 + fm.horizontalAdvance(label) + 14
        for v in yt:
            y = r.bottom() - (v - y0) / (y1 - y0) * r.height()
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(fg)
            p.drawText(QRectF(0, y - fm.height() / 2, left - 6, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, format_tick(v, ystep))
        xt, xstep = nice_ticks(x0, x1, 5)
        for v in xt:
            x = r.left() + (v - x0) / (x1 - x0) * r.width()
            label = format_tick(v, xstep)
            p.drawText(QPointF(x - fm.horizontalAdvance(label) / 2, r.bottom() + fm.ascent() + 3), label)
        p.setClipRect(r.adjusted(-6, -6, 6, 6))
        if self.points is not None:
            px_, py_, pcol, _ = self.points
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(pcol)
            for a, b in zip(px_, py_):
                if np.isfinite(b):
                    p.drawEllipse(QPointF(r.left() + (a - x0) / (x1 - x0) * r.width(),
                                          r.bottom() - (b - y0) / (y1 - y0) * r.height()), 3.5, 3.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
        for (x, y), (_x, _y, col, _label) in zip(finite, self.series):
            if x.size < 2:
                continue
            p.setPen(QPen(col, 2))
            px = r.left() + (x - x0) / (x1 - x0) * r.width()
            py = r.bottom() - (y - y0) / (y1 - y0) * r.height()
            p.drawPolyline([QPointF(a, b) for a, b in zip(px, py)])
        p.end()


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    head = QLabel(title)
    f = head.font()
    f.setBold(True)
    head.setFont(f)
    layout.addWidget(head)
    return frame, layout


def _big_button(text: str, primary: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setMinimumHeight(52)
    if primary:
        f = b.font()
        f.setBold(True)
        b.setFont(f)
        b.setDefault(True)
    return b


# ------------------------------------------------------------------- worker
class TuneWorker(QThread):
    calibrated = Signal(float)
    progressed = Signal(object)         # TuneProgress (copy)
    finishedResult = Signal(object, object)   # TuningResult, (old, new) ExperimentResults or None
    failed = Signal(str)

    def __init__(self, config: AppConfig, settings: TuningSettings | None, calibrate_only: bool = False) -> None:
        super().__init__()
        self.config = config.copy()
        self.settings = settings
        self.calibrate_only = calibrate_only
        self.token = CancelToken()

    def cancel(self) -> None:
        self.token.cancel()

    def run(self) -> None:
        try:
            if self.calibrate_only:
                self.calibrated.emit(calibrate_push_test_seconds(self.config))
                return

            def update(u: TuneProgress) -> bool:
                snap = copy.copy(u)
                snap.history = list(u.history)
                self.progressed.emit(snap)
                return not self.token.cancelled

            result = robust_tune(self.config, self.settings, on_update=update, cancel=self.token)
            comparison = None
            try:
                comparison = compare_responses(self.config, self.config.controller, result.controller,
                                               max(result.recommended_push, 1e-3), self.settings)
            except Exception:  # noqa: BLE001 - the summary works without the preview
                comparison = None
            self.finishedResult.emit(result, comparison)
        except TuningCancelled:
            self.failed.emit("Cancelled before any candidate was finished – nothing to apply.")
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self.failed.emit(str(exc))


# ------------------------------------------------------------------- screen
class AutotuneScreen(QWidget):
    applyRequested = Signal(object)     # TuningResult
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.config: AppConfig | None = None
        self.worker: TuneWorker | None = None
        self.calibration: TuneWorker | None = None
        self.push_test_s: float | None = None
        self.result: TuningResult | None = None
        self.last: TuneProgress | None = None
        self._last_update_time = 0.0
        self.preset = "normal"
        self.workers = parallel.default_workers()

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_setup())
        self.pages.addWidget(self._build_running())
        self.pages.addWidget(self._build_result())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Auto-tune for robustness")
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.35)
        f.setBold(True)
        title.setFont(f)
        title.setContentsMargins(16, 12, 16, 4)
        outer.addWidget(title)
        outer.addWidget(self.pages, 1)
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._tick)
        self.hide()

    # ------------------------------------------------------------ pages
    def _scroll(self, inner: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidget(inner)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return area

    def _build_setup(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(16, 8, 16, 16)
        v.setSpacing(12)
        intro = QLabel(
            "Searches for the controller that recovers from the strongest push for this exact "
            "pendulum. Every candidate is a stabilising LQR design mapped onto the PID loops and "
            "tested on the full nonlinear simulation. The app stays on this screen while it works; "
            "keep it in the foreground (leaving the app cancels the search).")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.preset_group = QButtonGroup(self)
        self.preset_buttons: dict[str, QPushButton] = {}
        grid = QGridLayout()
        grid.setSpacing(10)
        for col, (key, name, desc) in enumerate(PRESETS):
            b = QPushButton(f"{name}\n{desc}\nestimating…")
            b.setCheckable(True)
            b.setMinimumHeight(110)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setChecked(key == self.preset)
            self.preset_group.addButton(b)
            b.clicked.connect(lambda _=False, k=key: self._choose(k))
            self.preset_buttons[key] = b
            grid.addWidget(b, 0, col)
        v.addLayout(grid)
        self.cores_label = QLabel()
        self.cores_label.setWordWrap(True)
        v.addWidget(self.cores_label)
        v.addStretch(1)
        row = QHBoxLayout()
        self.close_btn = _big_button("Close")
        self.start_btn = _big_button("Start auto-tune", primary=True)
        row.addWidget(self.close_btn, 1)
        row.addWidget(self.start_btn, 2)
        v.addLayout(row)
        self.close_btn.clicked.connect(self.close_screen)
        self.start_btn.clicked.connect(self.start)
        return self._scroll(page)

    def _build_running(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(16, 8, 16, 16)
        v.setSpacing(12)
        head = QHBoxLayout()
        self.spinner = Spinner(40)
        head.addWidget(self.spinner)
        self.phase_label = QLabel("Starting…")
        self.phase_label.setWordWrap(True)
        f = self.phase_label.font()
        f.setPointSizeF(f.pointSizeF() * 1.15)
        self.phase_label.setFont(f)
        head.addWidget(self.phase_label, 1)
        v.addLayout(head)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setMinimumHeight(30)
        self.bar.setFormat("%p %")
        v.addWidget(self.bar)
        self.time_label = QLabel()
        self.cores_running = QLabel()
        self.cores_running.setWordWrap(True)
        v.addWidget(self.time_label)
        v.addWidget(self.cores_running)
        card, cl = _card("Best result so far")
        self.best_label = QLabel("–")
        self.best_label.setWordWrap(True)
        self.best_gains = QLabel("")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.best_gains.setFont(mono)
        cl.addWidget(self.best_label)
        cl.addWidget(self.best_gains)
        self.history_plot = MiniPlot("recoverable push [N]", 150)
        cl.addWidget(self.history_plot)
        v.addWidget(card, 1)
        self.cancel_btn = _big_button("Cancel")
        self.cancel_btn.clicked.connect(self.cancel)
        v.addWidget(self.cancel_btn)
        return self._scroll(page)

    def _build_result(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(16, 8, 16, 16)
        v.setSpacing(12)
        self.headline = QLabel()
        self.headline.setWordWrap(True)
        f = self.headline.font()
        f.setPointSizeF(f.pointSizeF() * 1.2)
        f.setBold(True)
        self.headline.setFont(f)
        v.addWidget(self.headline)
        self.result_note = QLabel()
        self.result_note.setWordWrap(True)
        v.addWidget(self.result_note)
        row = QHBoxLayout()
        old_card, ol = _card("Current gains")
        new_card, nl = _card("Tuned gains")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.old_gains = QLabel()
        self.new_gains = QLabel()
        for lab, lay in ((self.old_gains, ol), (self.new_gains, nl)):
            lab.setFont(mono)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        row.addWidget(old_card)
        row.addWidget(new_card)
        v.addLayout(row)
        metrics_card, ml = _card("Response to the same push (before → after)")
        self.metrics_grid = QGridLayout()
        self.metrics_grid.setHorizontalSpacing(18)
        ml.addLayout(self.metrics_grid)
        self.preview = MiniPlot("θ1 [deg]", 180)
        ml.addWidget(self.preview)
        v.addWidget(metrics_card, 1)
        buttons = QHBoxLayout()
        self.discard_btn = _big_button("Discard")
        self.rerun_btn = _big_button("Rerun")
        self.apply_btn = _big_button("Apply", primary=True)
        for b, stretch in ((self.discard_btn, 1), (self.rerun_btn, 1), (self.apply_btn, 2)):
            buttons.addWidget(b, stretch)
        v.addLayout(buttons)
        self.discard_btn.clicked.connect(self.close_screen)
        self.rerun_btn.clicked.connect(self.show_setup)
        self.apply_btn.clicked.connect(self._apply)
        return self._scroll(page)

    # ------------------------------------------------------------ flow
    @property
    def busy(self) -> bool:
        return self.worker is not None

    def open(self, config: AppConfig) -> None:
        self.config = config.copy()
        self.show_setup()
        if self.parent() is not None:
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()

    def show_setup(self) -> None:
        self.pages.setCurrentIndex(0)
        self.cores_label.setText(
            f"Uses {self.workers} CPU core{'s' if self.workers != 1 else ''} in parallel "
            f"({parallel.cpu_count()} available; one stays free for the display).")
        self._update_estimates()
        if self.push_test_s is None and self.calibration is None and self.config is not None:
            self.calibration = TuneWorker(self.config, None, calibrate_only=True)
            self.calibration.calibrated.connect(self._on_calibrated)
            self.calibration.finished.connect(self._calibration_done)
            self.calibration.start()

    def _on_calibrated(self, seconds: float) -> None:
        self.push_test_s = seconds
        self._update_estimates()

    def _calibration_done(self) -> None:
        if self.calibration is not None:
            self.calibration.deleteLater()
        self.calibration = None

    def _update_estimates(self) -> None:
        for key, name, desc in PRESETS:
            if self.push_test_s is None:
                est = "measuring this device…"
            else:
                est = f"about {fmt_duration(estimate_seconds(TuningSettings.preset(key), self.push_test_s, self.workers))}"
            self.preset_buttons[key].setText(f"{name}\n{desc}\n{est}")

    def _choose(self, key: str) -> None:
        self.preset = key

    def start(self) -> None:
        if self.config is None or self.worker is not None:
            return
        settings = TuningSettings.preset(self.preset)
        settings.workers = self.workers
        self.result = None
        self.last = None
        self.bar.setValue(0)
        self.phase_label.setText("Starting worker processes…")
        self.best_label.setText("–")
        self.best_gains.setText("")
        self.history_plot.set_series([])
        self.cores_running.setText(f"Working on {self.workers} CPU core{'s' if self.workers != 1 else ''}")
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")
        self.pages.setCurrentIndex(1)
        self._started = time.monotonic()
        self.worker = TuneWorker(self.config, settings)
        self.worker.progressed.connect(self._on_progress)
        self.worker.finishedResult.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._worker_done)
        self.spinner.start()
        self._ticker.start(100)
        self.worker.start()

    def cancel(self) -> None:
        """Stop the search now (workers are killed); the best result so far is offered."""
        if self.worker is not None:
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Stopping…")
            self.phase_label.setText("Stopping the workers…")
            self.worker.cancel()

    def _on_progress(self, u: TuneProgress) -> None:
        self.last = u
        self._last_update_time = time.monotonic()
        self.phase_label.setText(u.message)
        note = f" – {u.pool_note}" if u.pool_note else ""
        self.cores_running.setText(
            f"Working on {u.workers} CPU core{'s' if u.workers != 1 else ''}{note} · "
            f"{u.simulations} simulations so far")
        if u.best_push > 0:
            before = f" (current controller: {u.baseline_push:.3g} N)" if u.baseline_push is not None else ""
            self.best_label.setText(f"Recovers from a tip push of <b>{u.best_push:.3g} N</b>{before}")
            self.best_gains.setText(gains_text(u.best_controller))
        elif u.baseline_push is not None:
            self.best_label.setText(f"Current controller recovers from {u.baseline_push:.3g} N")
        if u.history:
            n = np.arange(1, len(u.history) + 1)
            pushes = np.array(u.candidate_pushes, float)
            pushes[pushes <= 0] = np.nan                  # rejected candidates are not plotted
            self.history_plot.set_series([(n, u.history, ACCENT, "best (ties → faster recovery)")],
                                         "candidate", points=(n, pushes, MUTED, "each candidate"))
        self._tick()

    def _tick(self) -> None:
        if self.worker is None:
            return
        elapsed = time.monotonic() - self._started
        u = self.last
        if u is None or u.total == 0:
            self.time_label.setText(f"Elapsed {fmt_duration(elapsed)}")
            return
        # smooth bar: extrapolate within the current unit, never past the next one
        frac = u.fraction
        if u.eta and u.eta > 0:
            since = time.monotonic() - self._last_update_time
            est = (u.elapsed + since) / (u.elapsed + u.eta)
            frac = max(frac, min(est, (u.done + 1) / u.total * 0.999))
        self.bar.setValue(int(frac * 1000))
        remaining = None if u.eta is None else max(0.0, u.eta - (time.monotonic() - self._last_update_time))
        rest = f" · about {fmt_duration(remaining)} left" if remaining is not None else " · estimating time left…"
        self.time_label.setText(f"Elapsed {fmt_duration(elapsed)}{rest}")

    def _on_result(self, result: TuningResult, comparison) -> None:
        self.result = result
        before, after = result.baseline_max_push, result.max_push
        gain = f" (+{(after / before - 1) * 100:.0f} %)" if before > 0 and after > before else ""
        self.headline.setText(f"Recoverable tip push: {before:.3g} N → {after:.3g} N{gain}")
        notes = [f"{result.evaluations} candidates in {fmt_duration(result.elapsed)}."]
        if result.cancelled:
            notes.insert(0, "Cancelled – this is the best result found so far.")
        if after <= before * 1.02:
            notes.append("The current gains are already about as robust as this search can find; "
                         "applying gives the normalised recommended settings.")
        notes.append(f"Applying also sets the push force to {result.recommended_push:g} N for testing.")
        self.result_note.setText(" ".join(notes))
        self.old_gains.setText(gains_text(self.config.controller if self.config else None))
        self.new_gains.setText(gains_text(result.controller))
        self._fill_metrics(comparison)
        self.pages.setCurrentIndex(2)

    def _fill_metrics(self, comparison) -> None:
        while self.metrics_grid.count():
            item = self.metrics_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if comparison is None:
            self.preview.set_series([])
            return
        old, new = comparison

        def st(m):
            return "did not settle" if m.settling_time is None else f"{m.settling_time:.2f} s"

        rows = [("Settling time", st(old.metrics), st(new.metrics)),
                ("Overshoot", f"{old.metrics.overshoot_pct:.0f} %", f"{new.metrics.overshoot_pct:.0f} %"),
                ("Max angle", f"{old.metrics.max_deviation:.2f}°", f"{new.metrics.max_deviation:.2f}°"),
                ("Peak force", f"{old.metrics.control_peak:.3g}", f"{new.metrics.control_peak:.3g}"),
                ("Control energy ∫u²", f"{old.metrics.control_energy:.3g}", f"{new.metrics.control_energy:.3g}"),
                ("Fell over", "yes" if old.metrics.fell else "no", "yes" if new.metrics.fell else "no")]
        for c, text in enumerate(("", "before", "after")):
            lab = QLabel(f"<b>{text}</b>")
            self.metrics_grid.addWidget(lab, 0, c)
        for r, (name, a, b) in enumerate(rows, start=1):
            for c, text in enumerate((name, a, b)):
                self.metrics_grid.addWidget(QLabel(text), r, c)
        deg = 180.0 / math.pi
        self.preview.set_series([
            (old.data["t"], old.data["theta1"] * deg, MUTED, "before"),
            (new.data["t"], new.data["theta1"] * deg, ACCENT, "after"),
        ], "time [s]")


    def _on_failed(self, message: str) -> None:
        self.result = None
        self.headline.setText("Auto-tune stopped")
        self.result_note.setText(message)
        self.old_gains.setText(gains_text(self.config.controller if self.config else None))
        self.new_gains.setText("–")
        self._fill_metrics(None)
        self.pages.setCurrentIndex(2)

    def _worker_done(self) -> None:
        self.spinner.stop()
        self._ticker.stop()
        self.apply_btn.setEnabled(self.result is not None)
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None

    def _apply(self) -> None:
        if self.result is not None:
            self.applyRequested.emit(self.result)
        self.close_screen()

    def close_screen(self) -> None:
        if self.worker is not None:
            self.cancel()
            self.worker.wait(10000)
        self.hide()
        self.closed.emit()

    def handle_back(self) -> bool:
        """Android back button: cancel while running, otherwise leave the screen."""
        if self.worker is not None:
            self.cancel()
        else:
            self.close_screen()
        return True
