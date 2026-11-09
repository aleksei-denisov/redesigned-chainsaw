"""Lightweight live graphs for the mobile interface (replaces pyqtgraph there).

Why not pyqtgraph on a tablet: every repaint of a pyqtgraph plot runs a lot of
Python (graphics-scene items, tick generation, legend, downsampling), ~40 ms per
refresh for four plots on a Xiaomi Pad 6. A :class:`StripChart` instead

* keeps background, title, legend, grid and tick labels in a cached image that is
  rebuilt only when the y range or the size changes (the x axis shows *time ago*,
  ``−10 s … now``, so it is static too);
* reduces each series to at most two points per pixel column (min/max, peaks kept;
  :func:`.plot_math.downsample_minmax`), writes the pixel coordinates with NumPy
  straight into a reused ``QPolygonF`` and draws it with one ``drawPolyline`` call;
* does the data work only on a graph refresh (10 Hz by default, set by the window),
  never per displayed frame, and nothing at all while the graphs are not visible.

:class:`GraphsPanel` offers the same calls the desktop window makes on its
``TelemetryPanel`` (``configure``, ``set_output_limit``, ``refresh``), so the
inherited window logic drives it unchanged. Which graphs are shown (up to
:data:`.plot_math.MAX_VISIBLE`), the time window and the PID loop are remembered.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..simulation.telemetry import TelemetryRecorder
from .gpu import GPUHost
from .plot_math import (
    DEFAULT_SELECTION,
    GRAPHS,
    MAX_VISIBLE,
    AxisRange,
    GraphDef,
    Series,
    downsample_minmax,
    finite_extent,
    finite_runs,
    format_tick,
    graph_available,
    graph_series,
    nice_ticks,
    ring_blits,
    ring_runs,
    window_slice,
    wrap_degrees,
)

try:  # zero-copy NumPy view of a QPolygonF's point array (as pyqtgraph does it, without importing it)
    from shiboken6 import VoidPtr as _VoidPtr
except ImportError:  # pragma: no cover
    _VoidPtr = None

WINDOWS_S = [5, 10, 20, 30]


def polygon_buffer(n: int) -> tuple[QPolygonF, np.ndarray | None]:
    """A ``QPolygonF`` of ``n`` points and a writable ``(n, 2)`` float64 view of its memory."""
    poly = QPolygonF()
    if hasattr(poly, "resize"):
        poly.resize(n)
    else:  # pragma: no cover - older PySide6
        poly.fill(QPointF(), n)
    if n == 0 or _VoidPtr is None:
        return poly, None
    ptr = poly.data()
    buf = np.frombuffer(_VoidPtr(0 if ptr is None else ptr, 16 * n, True), np.float64).reshape(n, 2)
    return poly, buf


def _polygon(xs: np.ndarray, ys: np.ndarray) -> QPolygonF:
    n = int(xs.size)
    poly, buf = polygon_buffer(n)
    if buf is None:
        return QPolygonF([QPointF(float(a), float(b)) for a, b in zip(xs, ys)])
    buf[:, 0] = xs
    buf[:, 1] = ys
    return poly


FULL = -1          # StripChart.advance(): the trace must be redrawn from the history


class StripChart(GPUHost, QWidget):
    """One graph: title + legend, y axis with horizontal labels, time axis ``−window … 0``.

    The curves live in a *scrolling trace*: an image of the plot area in device pixels
    used as a ring of pixel columns (absolute column ``c = t · px_per_s`` sits at image
    column ``(c − ring0) mod width``). Every frame :meth:`advance` clears the columns
    that time has moved past, draws only the new samples as short line segments and
    the paint copies the ring in two parts so the newest column is at the right edge –
    a smooth, per-frame scroll whose cost does not depend on how much history is shown.
    The whole trace is redrawn (:meth:`update_data`, min/max-decimated) only when the y
    range, the size or the time window changes.
    """

    def __init__(self, gdef: GraphDef, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.gdef = gdef
        self.series: list[Series] = []
        self.axis = AxisRange(gdef.min_span)
        self.window_s = 10.0
        # Curve pen: "fast" = 1-device-pixel cosmetic line drawn twice (1 px apart) on dense
        # screens – Qt's fast line rasteriser, ≈2 px thick; "smooth" = antialiased 1.6 px
        # stroke (Qt's general stroker, several times slower on a tablet).
        self.pen_mode = "fast"
        self.paint_ms = 0.0
        self.update_ms = 0.0
        self.static_builds = 0
        self.full_redraws = 0
        self.points_drawn = 0           # samples drawn incrementally (counter, for measurements)
        self.stats = None               # performance.FrameStats (optional)
        self.source = None              # callable → (t, columns) for redraws after a resize
        self._paths: list[tuple[QPolygonF, QPen]] = []
        self._static: QImage | None = None
        self._static_key = None
        self._plot = QRectF()
        self._plot_px = (0, 0, 1, 1)    # trace area (inside the frame) in device pixels, integers
        self._trace_rect = QRectF()     # the same in logical pixels
        self._trace: QImage | None = None
        self._trace_range: tuple[float, float] | None = None
        self._ring0 = 0                 # absolute column at image column 0
        self._c_now = 0                 # newest absolute column drawn
        self._t_last: float | None = None
        self._last_y: list[float] = []
        self._pps = 1.0                 # device pixels per second of simulated time
        self._grid_rows: list[tuple[int, bool]] = []   # (y in trace px, is zero line)
        self._redraw_queued = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(56)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        f = QFont(self.font())
        if f.pixelSize() > 0:
            f.setPixelSize(14)          # axis text: 14 dp on the mobile theme
            self.setFont(f)

    # ----------------------------------------------------------- data
    def set_series(self, series: list[Series]) -> None:
        self.series = series
        self.axis.reset()
        self._static_key = None
        self._trace = None
        self._trace_range = None
        self._paths = []
        self.update()

    def invalidate(self) -> None:
        """Size / window / style changed: redraw from the history at the next chance."""
        self._static_key = None
        self._trace = None
        if self.source is not None and not self._redraw_queued:
            self._redraw_queued = True
            QTimer.singleShot(0, self._redraw_from_source)

    def _redraw_from_source(self) -> None:
        self._redraw_queued = False
        if self.source is not None and self.isVisible():
            t, columns = self.source()
            self.update_data(t, columns, time.monotonic())

    def update_data(self, t: np.ndarray, columns, now: float) -> None:
        """Re-evaluate the y range and redraw the whole trace from the history."""
        started = time.perf_counter()
        try:
            self._update_data(t, columns, now)
        finally:
            self.update_ms = (time.perf_counter() - started) * 1000.0

    def check_range(self, t: np.ndarray, columns, now: float) -> None:
        """Rate-limited (≈10 Hz) y-range bookkeeping; redraws only if the range changed."""
        if (self._trace is None and not self.gpu) or self._trace_range is None or not self.series:
            self.update_data(t, columns, now)
            return
        sl = window_slice(t, self.window_s)
        ys = []
        for s in self.series:
            y = columns(s.column)[sl] * s.scale
            ys.append(wrap_degrees(y) if s.wrap else y)
        lo, hi = self.axis.update(*finite_extent(ys), now)
        if (lo, hi) != self._trace_range:
            self.update_data(t, columns, now)

    def _make_pen(self, s: Series, width_px: float | None = None) -> QPen:
        if self.pen_mode == "smooth":
            pen = QPen(QColor(s.color), width_px or 1.6)
        else:
            pen = QPen(QColor(s.color), 1)
        pen.setCosmetic(True)
        if s.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        return pen

    # ----------------------------------------------------- trace (ring)
    def _y_px(self, ys: np.ndarray) -> np.ndarray:
        lo, hi = self._trace_range
        h = self._plot_px[3]
        return (hi - ys) * (h / (hi - lo))

    def _clear_columns(self, p: QPainter, start: int, length: int) -> None:
        h = self._plot_px[3]
        p.fillRect(start, 0, length, h, self.palette().base().color())
        fg = self.palette().text().color()
        for y, zero in self._grid_rows:
            c = QColor(fg)
            c.setAlpha(110 if zero else 40)
            p.setPen(QPen(c, 1))
            p.drawLine(start, y, start + length - 1, y)

    def _draw_runs(self, p: QPainter, s: Series, xi: np.ndarray, yi: np.ndarray) -> int:
        """Draw finite runs of one series into the trace; ``xi`` may exceed the ring width."""
        w = self._plot_px[2]
        pen = self._make_pen(s)
        p.setPen(pen)
        thicken = self.pen_mode != "smooth" and self.devicePixelRatioF() >= 1.5
        drawn = 0
        for run in finite_runs(yi, wrap=s.wrap):
            if run.stop - run.start < 2:
                continue
            xs, ys = xi[run], yi[run]
            for shift in (0.0, -float(w)) if xs[-1] >= w else (0.0,):
                poly = _polygon(xs + shift, ys)
                p.drawPolyline(poly)
                if thicken:
                    p.drawPolyline(_polygon(xs + shift, ys + 1.0))
            drawn += run.stop - run.start
        return drawn

    def advance(self, t: np.ndarray, columns) -> int:
        """Draw the samples newer than the last drawn one; returns their number or FULL."""
        if self.gpu:
            return self._advance_gpu(t, columns)
        if self._trace is None or self._t_last is None or not self.series or t.size == 0:
            return FULL
        t_new = float(t[-1])
        if t_new < self._t_last:
            return FULL                                   # reset: time went backwards
        if t_new == self._t_last:
            return 0
        x, y, w, h = self._plot_px
        c_new = int(math.floor(t_new * self._pps))
        if c_new - self._c_now >= w:
            return FULL                                   # more than a whole window passed
        i0 = int(np.searchsorted(t, self._t_last, side="left"))
        tt = t[i0:]
        lo, hi = self._trace_range
        segs = []
        for k, s in enumerate(self.series):
            ys = columns(s.column)[i0:] * s.scale
            if s.wrap:
                ys = wrap_degrees(ys)
            with np.errstate(invalid="ignore"):
                if np.any(ys[1:] < lo) or np.any(ys[1:] > hi):
                    return FULL                           # data left the range: expand now
            if tt.size and tt[0] != self._t_last:         # history compacted: prepend last point
                tseg = np.concatenate(([self._t_last], tt))
                ys = np.concatenate(([self._last_y[k]], ys))
            else:
                tseg = tt
            segs.append((s, tseg, ys))
        p = QPainter(self._trace)
        if self.pen_mode == "smooth":
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for start, length in ring_runs(self._c_now + 1, c_new, self._ring0, w):
            self._clear_columns(p, start, length)
        drawn = 0
        for s, tseg, ys in segs:
            if tseg.size < 2:
                continue
            xi = tseg * self._pps - self._ring0
            xi -= w * math.floor(xi[0] / w)               # first point inside the ring
            drawn += self._draw_runs(p, s, xi, self._y_px(ys))
        p.end()
        self._t_last = t_new
        self._last_y = [float(seg[2][-1]) for seg in segs]
        self._c_now = c_new
        self.points_drawn += drawn
        self.update(self._trace_rect.toAlignedRect())
        return drawn

    # ------------------------------------------------------- GPU drawing
    def _advance_gpu(self, t: np.ndarray, columns) -> int:
        """GPU mode: no trace image; check the new samples' range and repaint the surface."""
        if self._trace_range is None or self._t_last is None or not self.series or t.size == 0:
            return FULL
        t_new = float(t[-1])
        if t_new < self._t_last:
            return FULL
        if t_new == self._t_last:
            return 0
        i0 = int(np.searchsorted(t, self._t_last, side="right"))
        lo, hi = self._trace_range
        for s in self.series:
            ys = columns(s.column)[i0:] * s.scale
            if s.wrap:
                ys = wrap_degrees(ys)
            with np.errstate(invalid="ignore"):
                if np.any(ys < lo) or np.any(ys > hi):
                    return FULL
        self._t_last = t_new
        n = int(t.size - i0)
        self.points_drawn += n
        self.gpu_surface.update()
        return n

    def paint_gpu(self, p: QPainter) -> None:
        """The whole chart, drawn by the GPU every step: static layer + decimated curves."""
        started = time.perf_counter()
        if self._static is None or self._static_key is None:
            lo, hi = (self.axis.lo, self.axis.hi) if not math.isnan(self.axis.lo) else \
                (-0.5 * self.gdef.min_span, 0.5 * self.gdef.min_span)
            self._ensure_static(lo, hi)
        p.drawImage(0, 0, self._static)               # unchanged image: GL keeps its texture
        if self.source is not None and self._trace_range is not None and self.series:
            t, columns = self.source()
            sl = window_slice(t, self.window_s)
            tw = t[sl]
            if tw.size > 1:
                area = self._trace_rect
                lo, hi = self._trace_range
                t_now = float(tw[-1])
                cols = max(2, self._plot_px[2])        # ≤ 2 points per device-pixel column
                sx = area.width() / self.window_s
                sy = area.height() / (hi - lo)
                p.setClipRect(area)
                for s in self.series:
                    ys = columns(s.column)[sl] * s.scale
                    if s.wrap:
                        ys = wrap_degrees(ys)
                    xs, yd = downsample_minmax(tw, ys, t_now - self.window_s, t_now, cols)
                    px = area.right() - (t_now - xs) * sx
                    py = area.bottom() - (yd - lo) * sy
                    pen = QPen(QColor(s.color), 2.0 if self.gpu_surface.samples == 0 else 1.6)
                    pen.setCosmetic(self.gpu_surface.samples == 0)
                    if s.dashed:
                        pen.setStyle(Qt.PenStyle.DashLine)
                    p.setPen(pen)
                    for run in finite_runs(py, wrap=False) if not s.wrap else finite_runs(yd, wrap=True):
                        if run.stop - run.start >= 2:
                            p.drawPolyline(_polygon(px[run], py[run]))
                p.setClipping(False)
        self.paint_ms = (time.perf_counter() - started) * 1000.0
        if self.stats is not None:
            self.stats.add_graph_paint(self.paint_ms)

    def _update_data(self, t: np.ndarray, columns, now: float) -> None:
        self.full_redraws += 1
        sl = window_slice(t, self.window_s)
        tw = t[sl]
        if tw.size == 0 or not self.series:
            self._paths = []
            self._trace = None
            self.update()
            return
        t_now = float(tw[-1])
        raw = []
        for s in self.series:
            y = columns(s.column)[sl] * s.scale
            raw.append(wrap_degrees(y) if s.wrap else y)
        lo, hi = self.axis.update(*finite_extent(raw), now)
        self._ensure_static(lo, hi)
        x, y, w, h = self._plot_px
        self._pps = w / self.window_s
        self._trace_range = (lo, hi)
        if self.gpu:                                  # curves are drawn by paint_gpu
            self._trace = None
            self._paths = [(s.label, ys.size) for s, ys in zip(self.series, raw)]
            self._t_last = t_now
            self.update()
            return
        # horizontal grid rows (same ticks as the static labels)
        ticks, _step = self._y_ticks(lo, hi)
        self._grid_rows = [(int(round((hi - v) * h / (hi - lo))), abs(v) < 1e-12) for v in ticks]
        if self._trace is None or self._trace.width() != w or self._trace.height() != h:
            self._trace = QImage(max(1, w), max(1, h), QImage.Format.Format_RGB32)
        self._c_now = int(math.floor(t_now * self._pps))
        self._ring0 = self._c_now - (w - 1)
        p = QPainter(self._trace)
        if self.pen_mode == "smooth":
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._clear_columns(p, 0, w)
        paths = []
        for s, ys in zip(self.series, raw):
            xs, yd = downsample_minmax(tw, ys, t_now - self.window_s, t_now, w)
            xi = xs * self._pps - self._ring0
            self._draw_runs(p, s, xi, self._y_px(yd))
            paths.append((s.label, xs.size))
        p.end()
        self._paths = paths
        self._t_last = t_now
        self._last_y = [float(ys[-1]) if ys.size else math.nan for ys in raw]
        self.update()

    # --------------------------------------------------------- layout
    def _metrics(self) -> QFontMetricsF:
        return QFontMetricsF(self.font())

    def _y_tick_target(self) -> int:
        """Up to 4 y ticks, fewer on short charts so the labels never overlap."""
        fm = self._metrics()
        plot_h = self.height() - fm.height() * 3.1
        return max(2, min(4, int(plot_h / (fm.height() * 1.5))))

    def _y_ticks(self, lo: float, hi: float) -> tuple[list[float], float]:
        fm = self._metrics()
        ticks, step = nice_ticks(lo, hi, self._y_tick_target())
        plot_h = max(self.height() - fm.height() * 3.1, 10)
        while len(ticks) > 2 and plot_h / max(len(ticks) - 1, 1) < fm.height() * 1.05:
            ticks = ticks[::2]                       # thin out labels that would overlap
        return ticks, step

    def _layout_rect(self, lo: float | None = None, hi: float | None = None) -> QRectF:
        fm = self._metrics()
        if lo is None or math.isnan(self.axis.lo):
            label_w = fm.horizontalAdvance("-000.0")
        else:
            ticks, step = nice_ticks(lo, hi, self._y_tick_target())
            label_w = max(fm.horizontalAdvance(format_tick(v, step)) for v in ticks) if ticks else 20
        left = label_w + 12
        top = fm.height() * 1.6
        bottom = fm.height() * 1.5
        return QRectF(left, top, max(self.width() - left - 10, 10), max(self.height() - top - bottom, 10))

    def _ensure_static(self, lo: float, hi: float) -> None:
        dpr = self.devicePixelRatioF()
        pal = self.palette()
        key = (self.width(), self.height(), dpr, lo, hi, self.window_s, tuple(self.series),
               pal.base().color().rgba(), pal.text().color().rgba(), self.font().toString())
        if key == self._static_key and self._static is not None:
            return
        self._static_key = key
        self.static_builds += 1
        r = self._layout_rect(lo, hi)
        # plot rect on whole device pixels: the trace image is copied 1:1 (no resampling)
        px = (round(r.left() * dpr), round(r.top() * dpr), max(1, round(r.width() * dpr)),
              max(1, round(r.height() * dpr)))
        self._plot = QRectF(px[0] / dpr, px[1] / dpr, px[2] / dpr, px[3] / dpr)   # frame
        b = max(1, round(dpr))                       # the trace fills the inside of the frame
        self._plot_px = (px[0] + b, px[1] + b, max(1, px[2] - 2 * b), max(1, px[3] - 2 * b))
        ix, iy, iw, ih = self._plot_px
        self._trace_rect = QRectF(ix / dpr, iy / dpr, iw / dpr, ih / dpr)
        img = QImage(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)),
                     QImage.Format.Format_RGB32)
        img.setDevicePixelRatio(dpr)
        bg, fg = pal.base().color(), pal.text().color()
        img.fill(bg)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        fm = self._metrics()
        r = self._plot
        # title + unit (horizontal), legend on the same line
        title_font = QFont(self.font())
        title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(fg)
        title = f"{self.gdef.title}  [{self.gdef.unit}]"
        p.drawText(QPointF(r.left(), fm.ascent() + 2), title)
        x = r.left() + QFontMetricsF(title_font).horizontalAdvance(title) + 18
        p.setFont(self.font())
        for s in self.series:
            if x > self.width() - 40:
                break
            pen = QPen(QColor(s.color), 3)
            if s.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            y = fm.ascent() * 0.65 + 2
            p.drawLine(QPointF(x, y), QPointF(x + 16, y))
            p.setPen(fg)
            p.drawText(QPointF(x + 21, fm.ascent() + 2), s.label)
            x += 21 + fm.horizontalAdvance(s.label) + 16
        # grid + y labels
        grid = QColor(fg)
        grid.setAlpha(40)
        axis = QColor(fg)
        axis.setAlpha(110)
        ticks, step = self._y_ticks(lo, hi)
        inner = self._trace_rect
        sy = inner.height() / (hi - lo)
        for v in ticks:
            y = inner.bottom() - (v - lo) * sy
            p.setPen(QPen(axis if abs(v) < 1e-12 else grid, 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(fg)
            label = format_tick(v, step)
            p.drawText(QRectF(0, y - fm.height() / 2, r.left() - 6, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        # time axis: −window … now
        xticks, xstep = nice_ticks(-self.window_s, 0.0, 5)
        sx = r.width() / self.window_s
        for v in xticks:                             # tick marks below the frame (the plot scrolls)
            xx = r.left() + (v + self.window_s) * sx
            p.setPen(QPen(axis, 1))
            p.drawLine(QPointF(xx, r.bottom()), QPointF(xx, r.bottom() + 4))
            p.setPen(fg)
            label = "now" if abs(v) < 1e-9 else f"{format_tick(v, xstep)} s"
            w = fm.horizontalAdvance(label)
            p.drawText(QPointF(min(max(xx - w / 2, r.left()), self.width() - w - 2),
                               r.bottom() + fm.ascent() + 6), label)
        p.setPen(QPen(axis, 1))
        p.drawRect(r)
        p.end()
        self._static = img

    # ---------------------------------------------------------- paint
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._resize_surface()
        self.invalidate()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.gpu:
            return                                    # covered by the GPU surface
        started = time.perf_counter()
        if self._static is None or self._static_key is None:
            lo, hi = (self.axis.lo, self.axis.hi) if not math.isnan(self.axis.lo) else \
                (-0.5 * self.gdef.min_span, 0.5 * self.gdef.min_span)
            self._ensure_static(lo, hi)
        p = QPainter(self)
        area = self._trace_rect
        if not area.toAlignedRect().contains(event.rect()):
            p.drawImage(0, 0, self._static)            # frame, labels, legend (only when needed)
        if self._trace is not None:
            dpr = self.devicePixelRatioF()
            w, h = self._plot_px[2], self._plot_px[3]
            # with the screen's pixel ratio set on the image the copy is 1:1 in device pixels,
            # which Qt does as a plain memory copy (a ratio-1 image would go through scaling)
            self._trace.setDevicePixelRatio(dpr)
            for src_x, length, dst_x in ring_blits(self._c_now, self._ring0, w):
                p.drawImage(QPointF(area.left() + dst_x / dpr, area.top()), self._trace,
                            QRectF(src_x, 0, length, h))
            self._trace.setDevicePixelRatio(1.0)
        p.end()
        self.paint_ms = (time.perf_counter() - started) * 1000.0
        if self.stats is not None:
            self.stats.add_graph_paint(self.paint_ms)


class GraphsPanel(QWidget):
    """Selectable stack of :class:`StripChart` s fed from the telemetry recorder."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stats = None                    # performance.FrameStats (optional)
        self.pen_mode = "fast"
        self._tel: TelemetryRecorder | None = None
        self._gpu = (False, 0)
        self._advanced_version = -1
        self._signature: tuple[int, bool, list[str]] | None = None
        self._version = -1
        self._pending: TelemetryRecorder | None = None
        self.output_limit = 1.0
        self.charts: dict[str, StripChart] = {}
        settings = QSettings()
        saved = settings.value("graphs/selected", None)
        selected = [k for k in (str(saved).split(",") if saved else DEFAULT_SELECTION) if k in GRAPHS]
        self.selected: list[str] = selected[:MAX_VISIBLE] or list(DEFAULT_SELECTION)
        try:
            window = int(settings.value("graphs/window_s", 10))
        except (TypeError, ValueError):
            window = 10
        self.window_s = window if window in WINDOWS_S else 10

        from .controls import ChoiceRow, FlowLayout, ToggleChips

        self.chips = ToggleChips([(k, g.title) for k, g in GRAPHS.items()])
        for key, b in self.chips.buttons.items():
            b.setChecked(key in self.selected)
        self.chips.changed.connect(self._toggle)
        self.count_label = QLabel()
        self.count_label.setProperty("role", "secondary")
        self.window_row = ChoiceRow("", [(w, f"{w} s") for w in WINDOWS_S], self.window_s)
        self.window_row.changed.connect(self._on_window)
        self.loop_label = QLabel("PID loop")
        self.loop_combo = QComboBox()
        self.loop_combo.currentIndexChanged.connect(lambda _: self._rebuild_series())
        bar = QVBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        bar.addWidget(self.chips)
        loop_box = QWidget()
        lb = QHBoxLayout(loop_box)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.addWidget(self.loop_label)
        lb.addWidget(self.loop_combo)
        self.loop_box = loop_box
        while self.window_row.flow.count():           # window chips directly in the header row
            self.window_row.flow.takeAt(0)
        self.window_row.setParent(self)               # kept for its button group / signal
        self.window_row.hide()
        row_holder = QWidget()
        row = FlowLayout(row_holder, spacing=10)
        self.choose_btn = QPushButton("Choose graphs")
        self.choose_btn.setCheckable(True)
        self.choose_btn.setProperty("chip", True)
        self.choose_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.choose_btn.toggled.connect(self.chips.setVisible)
        self.choose_btn.hide()
        row.addWidget(self.choose_btn)
        row.addWidget(self.count_label)
        row.addWidget(loop_box)
        row.addWidget(QLabel("Window"))
        for b in self.window_row.buttons.values():
            row.addWidget(b)
        bar.addWidget(row_holder)
        self.stack = QVBoxLayout()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.setSpacing(6)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addLayout(bar)
        outer.addLayout(self.stack, 1)
        self._rebuild_charts()

    def set_compact(self, compact: bool) -> None:
        """Low screens (phone landscape): graph chips folded behind a 'Choose graphs' chip."""
        self.choose_btn.setVisible(compact)
        if compact:
            self.chips.setVisible(self.choose_btn.isChecked())
        else:
            self.chips.show()

    # ------------------------------------------ desktop TelemetryPanel API
    def configure(self, n_segments: int, has_cart: bool, loop_labels: list[str]) -> None:
        self._signature = (n_segments, has_cart, list(loop_labels))
        current = self.loop_combo.currentIndex()
        self.loop_combo.blockSignals(True)
        self.loop_combo.clear()
        for label in loop_labels:
            self.loop_combo.addItem("Cart x" if label == "cart" else label)
        self.loop_combo.setCurrentIndex(max(0, min(current, len(loop_labels) - 1)))
        self.loop_combo.blockSignals(False)
        for key, b in self.chips.buttons.items():
            b.setEnabled(graph_available(key, n_segments, has_cart))
        self._rebuild_charts()
        self._version = -1

    def set_output_limit(self, limit: float) -> None:
        self.output_limit = limit

    def refresh(self, tel: TelemetryRecorder, force: bool = False) -> None:
        """Slow path (≈10 Hz, from the window): y-range bookkeeping; full redraw if forced."""
        if self._signature is None:
            return
        self._tel = tel
        if not self.isVisible():
            self._pending = tel                # drawn when the graphs become visible
            return
        if tel.version == self._version and not force:
            return
        started = time.perf_counter()
        self._version = tel.version
        t = tel.column("t")
        now = time.monotonic()
        for chart in self.visible_charts():
            self._attach(chart)
            if force:
                chart.update_data(t, tel.column, now)
            else:
                chart.check_range(t, tel.column, now)
        if self.stats is not None:
            self.stats.add_graph((time.perf_counter() - started) * 1000.0)

    def advance(self, tel: TelemetryRecorder) -> int:
        """Per-frame fast path: draw only the new samples into each chart's scrolling trace."""
        if self._signature is None or not self.isVisible():
            return 0
        self._tel = tel
        if tel.version == self._advanced_version:
            return 0
        started = time.perf_counter()
        self._advanced_version = tel.version
        t = tel.column("t")
        drawn = 0
        for chart in self.visible_charts():
            self._attach(chart)
            n = chart.advance(t, tel.column)
            if n == FULL:
                chart.update_data(t, tel.column, time.monotonic())
            else:
                drawn += n
        if self.stats is not None:
            self.stats.add_graph((time.perf_counter() - started) * 1000.0, points=drawn)
        return drawn

    def set_gpu(self, on: bool, samples: int = 0) -> None:
        """GPU drawing for every chart (current and future ones)."""
        self._gpu = (bool(on), samples)
        for chart in self.charts.values():
            chart.set_gpu(on, samples)
            chart.invalidate()

    def _attach(self, chart: StripChart) -> None:
        chart.pen_mode = self.pen_mode
        chart.stats = self.stats
        on, samples = self._gpu
        if chart.gpu_surface is None and on and not chart.gpu_failed:
            chart.set_gpu(True, samples)
        elif chart.gpu_surface is not None and not on:
            chart.set_gpu(False)
        if chart.source is None:
            chart.source = self._source

    def _source(self):
        tel = self._tel
        if tel is None:
            return np.zeros(0), lambda name: np.zeros(0)
        return tel.column("t"), tel.column

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._pending is not None:
            tel, self._pending = self._pending, None
            self.refresh(tel, force=True)

    # ---------------------------------------------------------- selection
    def visible_charts(self) -> list[StripChart]:
        return [self.charts[k] for k in self.shown_keys() if k in self.charts]

    def shown_keys(self) -> list[str]:
        if self._signature is None:
            return list(self.selected)
        n, cart, _ = self._signature
        return [k for k in self.selected if graph_available(k, n, cart)]

    def _toggle(self, key: str, on: bool) -> None:
        if on and key not in self.selected:
            if len(self.selected) >= MAX_VISIBLE:
                oldest = self.selected.pop(0)
                self.chips.set_checked(oldest, False)
            self.selected.append(key)
        elif not on and key in self.selected:
            self.selected.remove(key)
        QSettings().setValue("graphs/selected", ",".join(self.selected))
        self._rebuild_charts()
        self._version = -1

    def set_selection(self, keys: list[str]) -> None:
        self.selected = [k for k in keys if k in GRAPHS][:MAX_VISIBLE]
        for key in self.chips.buttons:
            self.chips.set_checked(key, key in self.selected)
        QSettings().setValue("graphs/selected", ",".join(self.selected))
        self._rebuild_charts()
        self._version = -1

    def _on_window(self, value: int) -> None:
        self.window_s = int(value)
        QSettings().setValue("graphs/window_s", self.window_s)
        for chart in self.charts.values():
            chart.window_s = self.window_s
            chart.invalidate()
        self._version = -1

    def _rebuild_charts(self) -> None:
        while self.stack.count():
            item = self.stack.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
        for key in self.shown_keys():
            chart = self.charts.get(key)
            if chart is None:
                chart = self.charts[key] = StripChart(GRAPHS[key])
            chart.window_s = self.window_s
            self._attach(chart)
            self.stack.addWidget(chart, 1)
            chart.show()
        self.count_label.setText(f"{len(self.shown_keys())} of up to {MAX_VISIBLE} graphs")
        has_pid = "pid" in self.shown_keys()
        self.loop_box.setVisible(has_pid)
        self._rebuild_series()

    def _rebuild_series(self) -> None:
        if self._signature is None:
            return
        n, _cart, labels = self._signature
        for key in self.shown_keys():
            self.charts[key].set_series(graph_series(key, n, labels, self.loop_combo.currentIndex()))
        self._version = -1
