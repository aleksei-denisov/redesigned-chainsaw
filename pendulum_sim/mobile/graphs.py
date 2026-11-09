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
from PySide6.QtCore import QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QAction, QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..simulation.telemetry import TelemetryRecorder
from .plot_math import (
    DEFAULT_SELECTION,
    GRAPHS,
    MAX_VISIBLE,
    AxisRange,
    GraphDef,
    Series,
    downsample_minmax,
    finite_extent,
    format_tick,
    graph_available,
    graph_series,
    nice_ticks,
    split_wraps,
    window_slice,
    wrap_degrees,
)

try:  # zero-copy NumPy view of a QPolygonF (pyqtgraph's helpers, already a dependency)
    from pyqtgraph.functions import create_qpolygonf, ndarray_from_qpolygonf
except ImportError:  # pragma: no cover
    create_qpolygonf = ndarray_from_qpolygonf = None

WINDOWS_S = [5, 10, 20, 30]


def _polygon(xs: np.ndarray, ys: np.ndarray) -> QPolygonF:
    n = int(xs.size)
    if create_qpolygonf is None:
        return QPolygonF([QPointF(float(a), float(b)) for a, b in zip(xs, ys)])
    poly = create_qpolygonf(n)
    if n:
        buf = ndarray_from_qpolygonf(poly)
        buf[:, 0] = xs
        buf[:, 1] = ys
    return poly


class StripChart(QWidget):
    """One graph: title + legend, y axis with horizontal labels, time axis ``−window … 0``."""

    def __init__(self, gdef: GraphDef, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.gdef = gdef
        self.series: list[Series] = []
        self.axis = AxisRange(gdef.min_span)
        self.window_s = 10.0
        self.antialias = True
        self.paint_ms = 0.0
        self._paths: list[tuple[QPolygonF, QPen]] = []
        self._static: QImage | None = None
        self._static_key = None
        self._plot = QRectF()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(110)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    # ----------------------------------------------------------- data
    def set_series(self, series: list[Series]) -> None:
        self.series = series
        self.axis.reset()
        self._static_key = None
        self._paths = []
        self.update()

    def update_data(self, t: np.ndarray, columns, now: float) -> None:
        """Recompute the curves from telemetry views ``t`` and ``columns(name)``."""
        sl = window_slice(t, self.window_s)
        tw = t[sl]
        if tw.size == 0 or not self.series:
            self._paths = []
            self.update()
            return
        x = tw - tw[-1]                                   # time ago: −window … 0
        x0 = -self.window_s
        plot_w = max(int(self._layout_rect().width()), 50)
        data = []
        for s in self.series:
            y = columns(s.column)[sl] * s.scale
            if s.wrap:
                y = wrap_degrees(y)
            data.append(downsample_minmax(x, y, x0, 0.0, plot_w))
        lo, hi = self.axis.update(*finite_extent([d[1] for d in data]), now)
        self._ensure_static(lo, hi)
        r = self._plot
        sx = r.width() / self.window_s
        sy = r.height() / (hi - lo)
        paths = []
        for s, (xs, ys) in zip(self.series, data):
            pen = QPen(QColor(s.color), 1.6)
            pen.setCosmetic(True)
            if s.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            keep = np.isfinite(ys)
            if not keep.all():
                xs, ys = xs[keep], ys[keep]
            px = r.left() + (xs - x0) * sx
            py = r.bottom() - (ys - lo) * sy
            parts = split_wraps(ys) if s.wrap else [slice(0, ys.size)]
            for part in parts:
                if part.stop - part.start >= 2:
                    paths.append((_polygon(px[part], py[part]), pen))
        self._paths = paths
        self.update()

    # --------------------------------------------------------- layout
    def _metrics(self) -> QFontMetricsF:
        return QFontMetricsF(self.font())

    def _layout_rect(self, lo: float | None = None, hi: float | None = None) -> QRectF:
        fm = self._metrics()
        if lo is None or math.isnan(self.axis.lo):
            label_w = fm.horizontalAdvance("-000.0")
        else:
            ticks, step = nice_ticks(lo, hi, 4)
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
        self._plot = self._layout_rect(lo, hi)
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
        ticks, step = nice_ticks(lo, hi, 4)
        sy = r.height() / (hi - lo)
        for v in ticks:
            y = r.bottom() - (v - lo) * sy
            p.setPen(QPen(axis if abs(v) < 1e-12 else grid, 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(fg)
            label = format_tick(v, step)
            p.drawText(QRectF(0, y - fm.height() / 2, r.left() - 6, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        # time axis: −window … now
        xticks, xstep = nice_ticks(-self.window_s, 0.0, 5)
        sx = r.width() / self.window_s
        for v in xticks:
            xx = r.left() + (v + self.window_s) * sx
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(xx, r.top()), QPointF(xx, r.bottom()))
            p.setPen(fg)
            label = "now" if abs(v) < 1e-9 else f"{format_tick(v, xstep)} s"
            w = fm.horizontalAdvance(label)
            p.drawText(QPointF(min(max(xx - w / 2, r.left()), self.width() - w - 2),
                               r.bottom() + fm.ascent() + 4), label)
        p.setPen(QPen(axis, 1))
        p.drawRect(r)
        p.end()
        self._static = img

    # ---------------------------------------------------------- paint
    def resizeEvent(self, event) -> None:  # noqa: N802
        self._static_key = None
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        started = time.perf_counter()
        if self._static is None or self._static_key is None:
            lo, hi = (self.axis.lo, self.axis.hi) if not math.isnan(self.axis.lo) else \
                (-0.5 * self.gdef.min_span, 0.5 * self.gdef.min_span)
            self._ensure_static(lo, hi)
        p = QPainter(self)
        p.drawImage(0, 0, self._static)
        if self._paths:
            p.setClipRect(self._plot.adjusted(1, 1, -1, -1))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, self.antialias)
            for poly, pen in self._paths:
                p.setPen(pen)
                p.drawPolyline(poly)
        p.end()
        self.paint_ms = (time.perf_counter() - started) * 1000.0


class GraphsPanel(QWidget):
    """Selectable stack of :class:`StripChart` s fed from the telemetry recorder."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stats = None                    # performance.FrameStats (optional)
        self.antialias = True
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

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.choose_btn = QToolButton()
        self.choose_btn.setText("Choose graphs")
        self.choose_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self)
        self.actions: dict[str, QAction] = {}
        for key, g in GRAPHS.items():
            act = self.menu.addAction(g.title)
            act.setCheckable(True)
            act.setChecked(key in self.selected)
            act.toggled.connect(lambda on, k=key: self._toggle(k, on))
            self.actions[key] = act
        self.choose_btn.setMenu(self.menu)
        self.count_label = QLabel()
        self.window_combo = QComboBox()
        for w in WINDOWS_S:
            self.window_combo.addItem(f"{w} s", w)
        self.window_combo.setCurrentIndex(WINDOWS_S.index(self.window_s))
        self.window_combo.currentIndexChanged.connect(self._on_window)
        self.loop_label = QLabel("PID loop")
        self.loop_combo = QComboBox()
        self.loop_combo.currentIndexChanged.connect(lambda _: self._rebuild_series())
        bar.addWidget(self.choose_btn)
        bar.addWidget(self.count_label)
        bar.addStretch(1)
        bar.addWidget(self.loop_label)
        bar.addWidget(self.loop_combo)
        bar.addWidget(QLabel("Window"))
        bar.addWidget(self.window_combo)
        self.stack = QVBoxLayout()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.setSpacing(6)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addLayout(bar)
        outer.addLayout(self.stack, 1)
        self._rebuild_charts()

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
        for key, act in self.actions.items():
            act.setEnabled(graph_available(key, n_segments, has_cart))
        self._rebuild_charts()
        self._version = -1

    def set_output_limit(self, limit: float) -> None:
        self.output_limit = limit

    def refresh(self, tel: TelemetryRecorder, force: bool = False) -> None:
        if self._signature is None:
            return
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
            chart.antialias = self.antialias
            chart.update_data(t, tel.column, now)
        if self.stats is not None:
            self.stats.add_graph((time.perf_counter() - started) * 1000.0
                                 + sum(c.paint_ms for c in self.visible_charts()))

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
                self.actions[oldest].blockSignals(True)
                self.actions[oldest].setChecked(False)
                self.actions[oldest].blockSignals(False)
            self.selected.append(key)
        elif not on and key in self.selected:
            self.selected.remove(key)
        QSettings().setValue("graphs/selected", ",".join(self.selected))
        self._rebuild_charts()
        self._version = -1

    def set_selection(self, keys: list[str]) -> None:
        for key, act in self.actions.items():
            act.blockSignals(True)
            act.setChecked(key in keys)
            act.blockSignals(False)
        self.selected = [k for k in keys if k in GRAPHS][:MAX_VISIBLE]
        QSettings().setValue("graphs/selected", ",".join(self.selected))
        self._rebuild_charts()
        self._version = -1

    def _on_window(self, _index: int) -> None:
        self.window_s = self.window_combo.currentData()
        QSettings().setValue("graphs/window_s", self.window_s)
        for chart in self.charts.values():
            chart.window_s = self.window_s
            chart._static_key = None
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
            self.stack.addWidget(chart, 1)
            chart.show()
        self.count_label.setText(f"{len(self.shown_keys())} of max {MAX_VISIBLE}")
        has_pid = "pid" in self.shown_keys()
        self.loop_label.setVisible(has_pid)
        self.loop_combo.setVisible(has_pid)
        self._rebuild_series()

    def _rebuild_series(self) -> None:
        if self._signature is None:
            return
        n, _cart, labels = self._signature
        for key in self.shown_keys():
            self.charts[key].set_series(graph_series(key, n, labels, self.loop_combo.currentIndex()))
        self._version = -1
