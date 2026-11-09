"""Explorable plots for the mobile interface: zoom, pan and read values with the fingers.

:class:`PlotView` draws a few curves (:class:`~.plot_math.Curve`) with title, legend,
grid and tick labels – QPainter only, no pyqtgraph (whose Python scene graph costs tens
of milliseconds per repaint on a tablet). Each curve is reduced to two points per device
pixel column of the visible range (:func:`~.plot_math.downsample_minmax`), so a pan or
a pinch redraws in a few milliseconds however long the recording is.

Gestures (``interactive=True``):

* **pinch** – zooms: a sideways pinch zooms the time axis, a vertical pinch the values,
  a diagonal one both; the point between the fingers stays under the fingers;
* **one-finger drag** – pans;
* **double tap** – fits the data again;
* **tap** – read-out: a vertical cursor with the value of every curve at that time.

Mouse and wheel do the same on the desktop preview (wheel: time, Shift+wheel: values).
With ``interactive=False`` (thumbnails, e.g. the experiment comparison) a tap emits
:attr:`PlotView.clicked` and drags are left to the surrounding scroll area.

:class:`GraphExplorer` shows one plot full-window with *Live*, *Fit*, *Image*, *Data*
and *Close* – opened from the Graphs page (tap a graph) and from the Experiments page.
"""

from __future__ import annotations

import math
import re
import time
from typing import Callable

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QEventPoint, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .graphs import _polygon
from .plot_math import (
    Curve,
    curves_extent,
    downsample_minmax,
    finite_runs,
    format_tick,
    nice_ticks,
    padded_range,
    value_at,
    visible_slice,
    write_curves_csv,
    zoom_range,
)

TAP_SLOP_PX = 12           # movement still counted as a tap
TAP_MAX_S = 0.4
DOUBLE_TAP_S = 0.35
PINCH_MIN_PX = 36          # finger separation along an axis needed to zoom that axis


def fmt_value(v: float) -> str:
    """Compact value for the read-out (4 significant digits, no scientific notation for normal sizes)."""
    if not math.isfinite(v):
        return "–"
    if v == 0:
        return "0"
    mag = math.floor(math.log10(abs(v)))
    if -4 <= mag < 6:
        return f"{v:.{max(0, 3 - mag)}f}".rstrip("0").rstrip(".") if mag < 3 else f"{v:.0f}"
    return f"{v:.3g}"


class PlotView(QWidget):
    """Curves with axes; zoom/pan/read-out by touch (see the module docstring)."""

    tapped = Signal(float)          # x (data) of a tap – interactive mode
    clicked = Signal()              # a tap – thumbnail mode
    viewChanged = Signal()          # the user zoomed, panned or fitted

    def __init__(self, parent: QWidget | None = None, interactive: bool = True, compact: bool = False) -> None:
        super().__init__(parent)
        self.curves: list[Curve] = []
        self.title = ""
        self.y_label = ""
        self.x_label = "s"
        self.hlines: list[float] = []          # dashed reference lines (e.g. the settling band)
        self.marker_x: float | None = None     # moving time marker (replay)
        self.cursor_x: float | None = None     # read-out position (tap)
        self.min_y_span = 1e-6
        self.interactive = interactive
        self.compact = compact
        self._view: list[float] | None = None  # [x0, x1, y0, y1]; None = fitted to the data
        self._plot = QRectF()
        self._press: tuple[QPointF, float] | None = None
        self._pan_from: tuple[QPointF, tuple[float, float, float, float]] | None = None
        self._moved = False
        self._last_tap = (0.0, QPointF())
        self._touch: dict | None = None
        self.paint_ms = 0.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(120 if compact else 180)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        f = QFont(self.font())
        if f.pixelSize() > 0:
            f.setPixelSize(13 if compact else 15)
            self.setFont(f)
        if interactive:
            self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
            # touch through an event filter, not an ``event()`` override (a Python event()
            # that calls back into Qt crashed PySide6 on Python 3.10 with busy threads)
            self.installEventFilter(self)

    # ------------------------------------------------------------- data
    def set_curves(self, curves: list[Curve], title: str | None = None, y_label: str | None = None,
                   keep_view: bool = False) -> None:
        self.curves = list(curves)
        if title is not None:
            self.title = title
        if y_label is not None:
            self.y_label = y_label
        if not keep_view:
            self._view = None
        self.update()

    def set_marker(self, x: float | None) -> None:
        if x != self.marker_x:
            self.marker_x = x
            self.update()

    # ------------------------------------------------------------- view
    @property
    def fitted(self) -> bool:
        return self._view is None

    def view_range(self) -> tuple[float, float, float, float]:
        if self._view is not None:
            return tuple(self._view)  # type: ignore[return-value]
        xlo, xhi, ylo, yhi = curves_extent(self.curves)
        if not (math.isfinite(xlo) and math.isfinite(xhi)):
            xlo, xhi = 0.0, 1.0
        if xhi <= xlo:
            xlo, xhi = xlo - 0.5, xhi + 0.5
        y0, y1 = padded_range(ylo, yhi, self.min_y_span)
        for y in self.hlines:                      # reference lines stay in view when fitted
            y0, y1 = min(y0, y), max(y1, y)
        return xlo, xhi, y0, y1

    def fit(self) -> None:
        self._view = None
        self.update()

    def set_x_range(self, x0: float, x1: float) -> None:
        """Show ``[x0, x1]`` with the value axis fitted to the data in it."""
        if x1 <= x0:
            x1 = x0 + 1.0
        _xl, _xh, ylo, yhi = curves_extent(self.curves, x0, x1)
        y0, y1 = padded_range(ylo, yhi, self.min_y_span)
        self._view = [x0, x1, y0, y1]
        self.update()

    def zoom(self, fx: float, fy: float, centre: QPointF | None = None) -> None:
        """Zoom in by ``fx`` (time) and ``fy`` (values) around ``centre`` (widget px)."""
        x0, x1, y0, y1 = self.view_range()
        cx, cy = self.to_data(centre if centre is not None else self._plot.center())
        x0, x1 = zoom_range(x0, x1, fx, cx, min_span=1e-6)
        y0, y1 = zoom_range(y0, y1, fy, cy, min_span=1e-9)
        self._view = [x0, x1, y0, y1]
        self.update()
        self.viewChanged.emit()

    def to_data(self, pt: QPointF) -> tuple[float, float]:
        x0, x1, y0, y1 = self.view_range()
        r = self._plot if self._plot.width() > 0 else QRectF(self.rect())
        x = x0 + (pt.x() - r.left()) / max(r.width(), 1.0) * (x1 - x0)
        y = y0 + (r.bottom() - pt.y()) / max(r.height(), 1.0) * (y1 - y0)
        return x, y

    def values_at(self, x: float) -> list[tuple[str, str, float]]:
        """(label, colour, value) of every labelled curve at ``x``."""
        return [(c.label, c.color, value_at(c.x, c.y, x)) for c in self.curves if c.label]

    # ------------------------------------------------------------ input
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if obj is self and event.type() in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate,
                                            QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
            self._on_touch(event)
            return True
        return False

    def _on_touch(self, event) -> None:
        event.accept()
        kind = event.type()
        pts = [p for p in event.points()]
        now = time.monotonic()
        if kind == QEvent.Type.TouchCancel:
            self._touch = None
            return
        if kind == QEvent.Type.TouchBegin or self._touch is None:
            self._touch = {"t": now, "start": {p.id(): p.position() for p in pts}, "view": self.view_range(),
                           "moved": False, "fingers": len(pts), "first": pts[0].position() if pts else QPointF()}
        g = self._touch
        down = [p for p in pts if p.state() != QEventPoint.State.Released]
        g["fingers"] = max(g["fingers"], len(down))
        ids = tuple(p.id() for p in down[:2])
        if g.get("ids") != ids:                     # finger added or lifted: restart from here
            g["ids"] = ids
            g["start"] = {p.id(): p.position() for p in down}
            g["view"] = self.view_range()
        if len(down) >= 2:
            self._pinch(g, down[0], down[1])
        elif len(down) == 1:
            self._drag(g, down[0].position())
        if kind == QEvent.Type.TouchEnd:
            if not g["moved"] and g["fingers"] == 1 and now - g["t"] < TAP_MAX_S:
                self._tap(g["first"])
            self._touch = None

    def _pinch(self, g: dict, a, b) -> None:
        sa, sb = g["start"].get(a.id()), g["start"].get(b.id())
        if sa is None or sb is None:
            return
        pa, pb = a.position(), b.position()
        dx0, dy0 = abs(sb.x() - sa.x()), abs(sb.y() - sa.y())
        dx1, dy1 = abs(pb.x() - pa.x()), abs(pb.y() - pa.y())
        fx = dx1 / dx0 if dx0 >= PINCH_MIN_PX else 1.0
        fy = dy1 / dy0 if dy0 >= PINCH_MIN_PX else 1.0
        m0 = QPointF(0.5 * (sa.x() + sb.x()), 0.5 * (sa.y() + sb.y()))
        m1 = QPointF(0.5 * (pa.x() + pb.x()), 0.5 * (pa.y() + pb.y()))
        x0, x1, y0, y1 = g["view"]
        r = self._plot
        w, h = max(r.width(), 1.0), max(r.height(), 1.0)
        cx = x0 + (m0.x() - r.left()) / w * (x1 - x0)
        cy = y0 + (r.bottom() - m0.y()) / h * (y1 - y0)
        sx = max((x1 - x0) / max(fx, 1e-3), 1e-6)
        sy = max((y1 - y0) / max(fy, 1e-3), 1e-9)
        nx0 = cx - (m1.x() - r.left()) / w * sx
        ny0 = cy - (r.bottom() - m1.y()) / h * sy
        self._view = [nx0, nx0 + sx, ny0, ny0 + sy]
        g["moved"] = True
        self.update()
        self.viewChanged.emit()

    def _drag(self, g: dict, pos: QPointF) -> None:
        start = next(iter(g["start"].values()), pos)
        dx, dy = pos.x() - start.x(), pos.y() - start.y()
        if not g["moved"] and math.hypot(dx, dy) < TAP_SLOP_PX:
            return
        g["moved"] = True
        x0, x1, y0, y1 = g["view"]
        r = self._plot
        sx = (x1 - x0) / max(r.width(), 1.0)
        sy = (y1 - y0) / max(r.height(), 1.0)
        self._view = [x0 - dx * sx, x1 - dx * sx, y0 + dy * sy, y1 + dy * sy]
        self.update()
        self.viewChanged.emit()

    def _tap(self, pos: QPointF) -> None:
        if not self.interactive:
            self.clicked.emit()
            return
        now = time.monotonic()
        last_t, last_pos = self._last_tap
        if now - last_t < DOUBLE_TAP_S and math.hypot(pos.x() - last_pos.x(), pos.y() - last_pos.y()) < 40:
            self._last_tap = (0.0, QPointF())
            self.cursor_x = None
            self.fit()
            self.viewChanged.emit()
            return
        self._last_tap = (now, pos)
        if self._plot.contains(pos):
            x, _y = self.to_data(pos)
            self.cursor_x = x
            self.update()
            self.tapped.emit(x)

    # mouse: desktop preview, and the thumbnails (their touches arrive as mouse events)
    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        self._press = (pos, time.monotonic())
        self._moved = False
        self._pan_from = (pos, self.view_range()) if self.interactive else None
        event.accept()                             # (an ignored press gets no release)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press is None:
            return
        pos = event.position()
        start = self._press[0]
        if math.hypot(pos.x() - start.x(), pos.y() - start.y()) >= TAP_SLOP_PX:
            self._moved = True
        if self._moved and self._pan_from is not None:
            g = {"start": {0: self._pan_from[0]}, "view": self._pan_from[1], "moved": True}
            self._drag(g, pos)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        press, self._press = self._press, None
        if press is None:
            return
        pos, start = event.position(), press[0]
        moved = self._moved or math.hypot(pos.x() - start.x(), pos.y() - start.y()) >= TAP_SLOP_PX
        if not moved and time.monotonic() - press[1] < TAP_MAX_S + 0.4:
            self._tap(pos)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.interactive:
            self.cursor_x = None
            self.fit()
            self.viewChanged.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self.interactive:
            event.ignore()
            return
        f = 1.15 ** (event.angleDelta().y() / 120.0)
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.zoom(1.0, f, event.position())
        else:
            self.zoom(f, 1.0, event.position())

    # ---------------------------------------------------------- painting
    def _legend_rows(self, fm: QFontMetricsF, width: float) -> list[list[tuple[Curve, float]]]:
        rows: list[list[tuple[Curve, float]]] = [[]]
        x = 0.0
        for c in self.curves:
            if not c.label:
                continue
            w = 22 + fm.horizontalAdvance(c.label) + 14
            if rows[-1] and x + w > width:
                rows.append([])
                x = 0.0
            rows[-1].append((c, w))
            x += w
        return [r for r in rows if r]

    def paintEvent(self, event) -> None:  # noqa: N802
        started = time.perf_counter()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.SURFACE))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = QFontMetricsF(self.font())
        line_h = fm.height()
        top = 6.0
        if self.title or self.y_label:
            p.setPen(QColor(theme.TEXT))
            title = self.title + (f"  [{self.y_label}]" if self.y_label else "")
            p.drawText(QPointF(8, top + fm.ascent()), fm.elidedText(title, Qt.TextElideMode.ElideRight,
                                                                     self.width() - 16))
            top += line_h + 4
        legend = self._legend_rows(fm, self.width() - 16)
        for row in legend:
            x = 8.0
            for c, w in row:
                pen = QPen(QColor(c.color), 2.4)
                if c.dashed:
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                yy = top + line_h / 2
                p.drawLine(QPointF(x, yy), QPointF(x + 16, yy))
                p.setPen(QColor(theme.TEXT_2))
                p.drawText(QPointF(x + 22, top + fm.ascent()), c.label)
                x += w
            top += line_h + 2
        top += 4
        bottom_h = line_h + 8
        x0, x1, y0, y1 = self.view_range()
        plot_h = max(self.height() - top - bottom_h, 10.0)
        yticks, ystep = nice_ticks(y0, y1, max(2, int(plot_h / (48 if not self.compact else 40))))
        ylabels = [format_tick(v, ystep) for v in yticks]
        left = max([fm.horizontalAdvance(s) for s in ylabels] + [fm.horizontalAdvance("0")]) + 12
        r = QRectF(left, top, max(self.width() - left - 10, 10.0), plot_h)
        self._plot = r
        p.fillRect(r, QColor(theme.BG))
        sx = r.width() / (x1 - x0) if x1 > x0 else 1.0
        sy = r.height() / (y1 - y0) if y1 > y0 else 1.0

        def px(x: float) -> float:
            return r.left() + (x - x0) * sx

        def py(y: float) -> float:
            return r.bottom() - (y - y0) * sy

        # grid and ticks
        grid = QPen(QColor(theme.OUTLINE), 1)
        grid.setCosmetic(True)
        for v, s in zip(yticks, ylabels):
            yy = py(v)
            p.setPen(QPen(QColor(theme.TEXT_2 if abs(v) < 0.5 * ystep else theme.OUTLINE), 1))
            p.drawLine(QPointF(r.left(), yy), QPointF(r.right(), yy))
            p.setPen(QColor(theme.TEXT_2))
            p.drawText(QRectF(0, yy - line_h / 2, left - 6, line_h),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, s)
        xticks, xstep = nice_ticks(x0, x1, max(2, int(r.width() / 90)))
        for v in xticks:
            xx = px(v)
            p.setPen(grid)
            p.drawLine(QPointF(xx, r.top()), QPointF(xx, r.bottom()))
            s = format_tick(v, xstep)
            w = fm.horizontalAdvance(s)
            p.setPen(QColor(theme.TEXT_2))
            p.drawText(QPointF(min(max(xx - w / 2, r.left()), self.width() - w - 2), r.bottom() + 4 + fm.ascent()), s)
        if self.x_label:
            w = fm.horizontalAdvance(self.x_label)
            p.setPen(QColor(theme.TEXT_2))
            p.drawText(QPointF(r.right() - w - 4, r.bottom() - 6), self.x_label)
        # reference lines
        p.setClipRect(r)
        ref = QPen(QColor(theme.TEXT_2), 1, Qt.PenStyle.DashLine)
        ref.setCosmetic(True)
        p.setPen(ref)
        for y in self.hlines:
            p.drawLine(QPointF(r.left(), py(y)), QPointF(r.right(), py(y)))
        # curves: decimated to two points per device column of the visible range
        columns = max(1, int(r.width() * self.devicePixelRatioF()))
        for c in self.curves:
            if c.x.size == 0:
                continue
            sl = visible_slice(c.x, x0, x1)
            xs, ys = downsample_minmax(c.x[sl], c.y[sl], x0, x1, columns)
            if xs.size == 0:
                continue
            qx = r.left() + (xs - x0) * sx
            qy = np.clip(r.bottom() - (ys - y0) * sy, -1e5, 1e5)
            pen = QPen(QColor(c.color), c.width)
            pen.setCosmetic(True)
            if c.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            for run in finite_runs(ys, wrap=c.wrap):
                if run.stop - run.start >= 2:
                    p.drawPolyline(_polygon(qx[run], qy[run]))
                elif run.stop - run.start == 1:
                    p.drawPoint(QPointF(float(qx[run.start]), float(qy[run.start])))
        if self.marker_x is not None and x0 <= self.marker_x <= x1:
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawLine(QPointF(px(self.marker_x), r.top()), QPointF(px(self.marker_x), r.bottom()))
        p.setClipping(False)
        if not self.curves:
            p.setPen(QColor(theme.TEXT_2))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "No data yet")
        p.setPen(QPen(QColor(theme.OUTLINE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        if self.cursor_x is not None and x0 <= self.cursor_x <= x1:
            self._paint_readout(p, fm, r, px(self.cursor_x))
        p.end()
        self.paint_ms = (time.perf_counter() - started) * 1000.0

    def _paint_readout(self, p: QPainter, fm: QFontMetricsF, r: QRectF, xx: float) -> None:
        pen = QPen(QColor(theme.TEXT), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawLine(QPointF(xx, r.top()), QPointF(xx, r.bottom()))
        x_text = f"t = {fmt_value(self.cursor_x)} s" if self.x_label == "s" else \
            f"{self.x_label or 'x'} = {fmt_value(self.cursor_x)}"
        lines = [(x_text, theme.TEXT)]
        lines += [(f"{label}: {fmt_value(v)}", color) for label, color, v in self.values_at(self.cursor_x)]
        w = max(fm.horizontalAdvance(t) for t, _ in lines) + 16
        h = len(lines) * fm.height() + 10
        bx = xx + 10 if xx + 10 + w <= r.right() else xx - 10 - w
        box = QRectF(max(bx, r.left() + 2), r.top() + 6, w, h)
        p.setPen(QPen(QColor(theme.OUTLINE), 1))
        p.setBrush(QColor(theme.SURFACE_2))
        p.drawRoundedRect(box, 8, 8)
        y = box.top() + 5 + fm.ascent()
        for text, color in lines:
            p.setPen(QColor(color))
            p.drawText(QPointF(box.left() + 8, y), text)
            y += fm.height()


def slug(text: str) -> str:
    """File-name friendly version of a title."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return s or "graph"


def save_plot_image(parent: QWidget, plot: PlotView, title: str) -> str | None:
    """Ask for a file and save ``plot`` as PNG; returns the shown file name (None if cancelled)."""
    from ..ui import file_io

    path = file_io.save_file_name(parent, "Save graph as image", f"{slug(title)}.png", "PNG image (*.png)", ".png")
    if not path:
        return None
    image = plot.grab().toImage()
    file_io.write_with(path, lambda local: _write_image(image, local))
    return file_io.display_name(path)


def _write_image(image, local) -> None:
    if not image.save(str(local), "PNG"):
        raise OSError("could not encode the image")


def save_plot_data(parent: QWidget, curves: list[Curve], title: str, x_name: str = "t_s") -> str | None:
    """Ask for a file and save the curves as CSV; returns the shown file name (None if cancelled)."""
    from ..ui import file_io

    path = file_io.save_file_name(parent, "Save graph data", f"{slug(title)}.csv", "CSV (*.csv)", ".csv")
    if not path:
        return None
    file_io.write_with(path, lambda local: write_curves_csv(local, curves, x_name))
    return file_io.display_name(path)


class GraphExplorer(QWidget):
    """One plot over the whole window: Live / Fit / Image / Data / Close.

    ``open_live`` follows a running recording (the last ``window_s`` seconds) until the
    plot is touched, then freezes so it can be explored; *Live* returns to following.
    ``open_static`` shows fixed curves (experiment results).
    """

    closed = Signal()
    LIVE_INTERVAL_MS = 100

    def __init__(self, parent: QWidget, message: Callable[[str], None] | None = None) -> None:
        super().__init__(parent)
        from .controls import push_button, tool_button

        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"GraphExplorer {{ background: {theme.BG}; }}")
        self.message = message or (lambda text: None)
        self._source: Callable[[], list[Curve]] | None = None
        self.window_s = 10.0
        self.title = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.title_label = QLabel()
        self.title_label.setStyleSheet(f"QLabel {{ font-weight: 600; font-size: {theme.BODY_PX + 1}px; }}")
        bar.addWidget(self.title_label, 1)
        self.live_btn = push_button("Live", "live")
        self.live_btn.setCheckable(True)
        self.live_btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.fit_btn = tool_button("fit", "Fit", tip="Show all data")
        self.image_btn = tool_button("image", "Image", tip="Save the graph as PNG")
        self.data_btn = tool_button("table", "Data", tip="Save the graph's data as CSV")
        self.close_btn = tool_button("close", tip="Close")
        for b in (self.live_btn, self.fit_btn, self.image_btn, self.data_btn, self.close_btn):
            bar.addWidget(b)
        v.addLayout(bar)
        self.plot = PlotView(self, interactive=True)
        v.addWidget(self.plot, 1)
        self.hint = QLabel("Pinch to zoom (sideways: time, up/down: values) · drag to move · "
                           "double-tap to fit · tap to read values")
        self.hint.setProperty("role", "secondary")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        self.timer = QTimer(self)
        self.timer.setInterval(self.LIVE_INTERVAL_MS)
        self.timer.timeout.connect(self._pull)
        self.live_btn.toggled.connect(self._set_live)
        self.fit_btn.clicked.connect(self._fit)
        self.image_btn.clicked.connect(self._save_image)
        self.data_btn.clicked.connect(self._save_data)
        self.close_btn.clicked.connect(self.close_explorer)
        self.plot.viewChanged.connect(self._user_moved)
        self.hide()

    # ---------------------------------------------------------------- open
    def open_live(self, title: str, unit: str, source: Callable[[], list[Curve]], window_s: float) -> None:
        self._source = source
        self.window_s = window_s
        self._open(title, unit)
        self.live_btn.show()
        self.live_btn.setChecked(True)
        self._pull()

    def open_static(self, title: str, unit: str, curves: list[Curve], hlines: list[float] | None = None,
                    marker_x: float | None = None) -> None:
        self._source = None
        self._open(title, unit)
        self.live_btn.hide()
        self.live_btn.setChecked(False)
        self.plot.hlines = list(hlines or [])
        self.plot.marker_x = marker_x
        self.plot.set_curves(curves)

    def _open(self, title: str, unit: str) -> None:
        self.title = title
        self.title_label.setText(title)
        self.plot.title = ""
        self.plot.y_label = unit
        self.plot.hlines = []
        self.plot.marker_x = None
        self.plot.cursor_x = None
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()

    def close_explorer(self) -> None:
        self.timer.stop()
        self._source = None
        self.hide()
        self.closed.emit()

    @property
    def is_open(self) -> bool:
        return self.isVisible()

    # ---------------------------------------------------------------- live
    def _set_live(self, on: bool) -> None:
        if on and self._source is not None:
            self.plot.cursor_x = None
            self.timer.start()
            self._pull()
        else:
            self.timer.stop()

    def _pull(self) -> None:
        if self._source is None or not self.live_btn.isChecked():
            return
        curves = self._source()
        self.plot.set_curves(curves, keep_view=True)
        _xl, xhi, _yl, _yh = curves_extent(curves)
        if math.isfinite(xhi):
            self.plot.set_x_range(xhi - self.window_s, xhi)

    def _user_moved(self) -> None:
        if self.live_btn.isChecked():
            self.live_btn.blockSignals(True)       # freeze: the data stop moving under the fingers
            self.live_btn.setChecked(False)
            self.live_btn.blockSignals(False)
            self.timer.stop()

    def _fit(self) -> None:
        self.plot.cursor_x = None
        self.plot.fit()

    # ---------------------------------------------------------------- files
    def _save_image(self) -> None:
        try:
            name = save_plot_image(self, self.plot, self.title)
        except OSError as exc:
            self.message(f"Could not save the image: {exc}")
            return
        if name:
            self.message(f"Graph saved to {name}")

    def _save_data(self) -> None:
        if not self.plot.curves:
            self.message("No data to save yet")
            return
        try:
            name = save_plot_data(self, self.plot.curves, self.title)
        except (OSError, ValueError) as exc:
            self.message(f"Could not save the data: {exc}")
            return
        if name:
            self.message(f"Graph data saved to {name}")

    def follow_parent(self) -> None:
        """Cover the parent again (called from the window's resize)."""
        parent = self.parentWidget()
        if parent is not None and self.isVisible():
            self.setGeometry(parent.rect())
