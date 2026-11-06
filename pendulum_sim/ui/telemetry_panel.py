"""Real-time telemetry graphs (pyqtgraph).

Performance notes (the graphs, not the physics, are the expensive part of a frame):
* live curves are drawn without antialiasing and with peak downsampling to the pixel width;
* the left axes have a fixed width, so changing tick labels never re-layouts the linked plots;
* angles are shown wrapped to ±180° with gaps at the wrap, so a spinning pendulum keeps a
  fixed, readable y-range instead of an ever-growing one;
* hidden graphs are not updated at all;
* the y axes use :class:`~.axis_scaling.SmoothAxis` (expand at once, shrink slowly,
  symmetric for signed signals, control capped near the actuator limit) and the x axis
  shows a fixed, steadily scrolling time window – no jumping scales.
"""

import time

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..simulation import TelemetryRecorder
from .axis_scaling import SmoothAxis, data_extent
from .widgets import LOOP_COLORS, SEGMENT_COLORS, spin

GRAPHS = [
    ("angles", "Angular positions", "deg"),
    ("velocities", "Angular velocities", "deg/s"),
    ("error", "Control error (angle loops)", "deg"),
    ("control", "Control signal", "N / N·m"),
    ("components", "PID components", "output"),
    ("energy", "System energy", "J"),
    ("cart", "Cart position", "m"),
]
DEFAULT_ON = {"angles", "error", "control", "energy"}
# Per graph: symmetric about zero?, smallest displayed span (in the graph's units).
AXIS_STYLE = {
    "angles": (False, 2.0),
    "velocities": (True, 10.0),
    "error": (True, 1.0),
    "control": (True, 0.2),
    "components": (True, 0.05),
    "energy": (False, 0.01),
    "cart": (True, 0.05),
}
AXIS_WIDTH = 58  # px; fixed so tick-label changes never trigger a re-layout


def wrap_degrees(values: np.ndarray) -> np.ndarray:
    """Wrap to (−180°, 180°] and insert NaN at each wrap so no vertical line is drawn."""
    out = (values + 180.0) % 360.0 - 180.0
    if out.size > 1:
        jumps = np.abs(np.diff(out)) > 180.0
        out[1:][jumps] = np.nan
    return out


class TelemetryPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.graph_button = QToolButton()
        self.graph_button.setText("Graphs ▾")
        self.graph_button.setToolTip("Choose which graphs are shown")
        self.graph_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.graph_button)
        self.toggles: dict[str, QAction] = {}
        for key, title, _ in GRAPHS:
            act = QAction(title, menu)
            act.setCheckable(True)
            act.setChecked(key in DEFAULT_ON)
            act.toggled.connect(self._update_visibility)
            menu.addAction(act)
            self.toggles[key] = act
        self.graph_button.setMenu(menu)
        bar.addWidget(self.graph_button)
        self.wrap_angles = QAction("Wrap angles to ±180°", menu)
        self.wrap_angles.setCheckable(True)
        self.wrap_angles.setChecked(True)
        self.wrap_angles.toggled.connect(lambda _: self._force_refresh())
        self.smooth_y = QAction("Calm Y scaling (expand fast, shrink slowly)", menu)
        self.smooth_y.setCheckable(True)
        self.smooth_y.setChecked(True)
        self.smooth_y.toggled.connect(self._on_smooth_toggled)
        menu.addSeparator()
        menu.addAction(self.wrap_angles)
        menu.addAction(self.smooth_y)
        bar.addStretch(1)
        bar.addWidget(QLabel("Loop:"))
        self.loop_combo = QComboBox()
        self.loop_combo.setToolTip("Loop shown in the PID components graph")
        bar.addWidget(self.loop_combo)
        bar.addWidget(QLabel("Window:"))
        self.window = spin(1.0, 120.0, 10.0, 0, 1.0, "s")
        bar.addWidget(self.window)
        layout.addLayout(bar)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.splitter, 1)
        self.plots: dict[str, pg.PlotWidget] = {}
        self.curves: dict[str, list] = {}
        first = None
        for key, title, unit in GRAPHS:
            plot = pg.PlotWidget(title=title)
            plot.setLabel("left", unit)
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.addLegend(offset=(6, 6))
            plot.setClipToView(True)
            plot.setDownsampling(auto=True, mode="peak")
            plot.setMinimumHeight(55)
            plot.getAxis("left").setWidth(AXIS_WIDTH)
            plot.setMenuEnabled(True)
            if first is None:
                first = plot
            else:
                plot.setXLink(first)
            self.plots[key] = plot
            self.curves[key] = []
            self.splitter.addWidget(plot)
        self.plots[GRAPHS[-1][0]].setLabel("bottom", "time", "s")
        self.axes = {key: SmoothAxis(symmetric=sym, min_span=span) for key, (sym, span) in AXIS_STYLE.items()}
        self._on_smooth_toggled(True)
        self._signature: tuple | None = None
        self._version = -1
        self._update_visibility()

    # ----------------------------------------------------------- layout
    def _update_visibility(self) -> None:
        for key, plot in self.plots.items():
            plot.setVisible(self.toggles[key].isChecked())

    def _force_refresh(self) -> None:
        self._version = -1

    def _on_smooth_toggled(self, enabled: bool) -> None:
        for key, plot in self.plots.items():
            plot.enableAutoRange(axis="y", enable=not enabled)
            plot.enableAutoRange(axis="x", enable=False)
            self.axes[key].reset()
        self._force_refresh()

    def set_output_limit(self, limit: float) -> None:
        """Cap the control graph near the actuator limit (unsaturated spikes are clipped)."""
        self.axes["control"].cap = 1.1 * abs(limit) if limit > 0 else None
        self.axes["control"].reset()

    def reset_scales(self) -> None:
        for axis in self.axes.values():
            axis.reset()
        self._force_refresh()

    def _curve(self, key: str, name: str, color: str, style=Qt.PenStyle.SolidLine, width: float = 1.3):
        pen = pg.mkPen(color, width=width, style=style)
        curve = self.plots[key].plot([], [], pen=pen, name=name, antialias=False)
        self.curves[key].append(curve)
        return curve

    def configure(self, n_segments: int, has_cart: bool, loop_labels: list[str]) -> None:
        signature = (n_segments, has_cart, tuple(loop_labels))
        if signature == self._signature:
            return
        self._signature = signature
        for key, plot in self.plots.items():
            plot.clear()
            legend = plot.plotItem.legend
            if legend is not None:
                legend.clear()
            self.curves[key] = []
        for i in range(n_segments):
            c = SEGMENT_COLORS[i]
            self._curve("angles", f"θ{i + 1}", c)
            self._curve("velocities", f"ω{i + 1}", c)
        for k, label in enumerate(loop_labels):
            if label != "cart":
                self._curve("error", f"e {label}", LOOP_COLORS[k % len(LOOP_COLORS)])
        self._curve("control", "controller command", "#8E44AD")
        self._curve("control", "unsaturated", "#8E44AD", Qt.PenStyle.DashLine, 1.0)
        self._curve("control", "actuator output", "#E67E22")
        self._curve("control", "tip disturbance [N]", "#E74C3C", Qt.PenStyle.DotLine, 1.2)
        self._curve("components", "P", "#2E86DE")
        self._curve("components", "I", "#27AE60")
        self._curve("components", "D", "#E67E22")
        self._curve("energy", "kinetic", "#2E86DE")
        self._curve("energy", "potential", "#27AE60")
        self._curve("energy", "total", "#C0392B", width=2.0)
        self._curve("cart", "x", "#34495E")
        self._curve("cart", "cart-loop error", "#8E44AD", Qt.PenStyle.DashLine, 1.0)
        self.toggles["cart"].setEnabled(has_cart)
        self.loop_combo.blockSignals(True)
        current = self.loop_combo.currentIndex()
        self.loop_combo.clear()
        for label in loop_labels:
            self.loop_combo.addItem("Cart x" if label == "cart" else label)
        self.loop_combo.setCurrentIndex(max(0, min(current, len(loop_labels) - 1)))
        self.loop_combo.blockSignals(False)
        self._loop_labels = loop_labels
        for axis in self.axes.values():
            axis.reset()
        self._version = -1

    # ----------------------------------------------------------- refresh
    def refresh(self, tel: TelemetryRecorder, force: bool = False) -> None:
        if self._signature is None or (tel.version == self._version and not force):
            return
        self._version = tel.version
        t_all = tel.column("t")
        if t_all.size == 0:
            for curves in self.curves.values():
                for c in curves:
                    c.setData([], [])
            return
        start = np.searchsorted(t_all, t_all[-1] - self.window.value())
        sl = slice(start, None)
        t = t_all[sl]
        n_segments, has_cart, labels = self._signature
        vis = {k: self.plots[k].isVisible() for k in self.plots}
        series: dict[str, list[np.ndarray]] = {k: [] for k in self.plots}

        def put(key: str, curve, y: np.ndarray, **kw) -> None:
            curve.setData(t, y, **kw)
            series[key].append(y)

        if vis["angles"] or vis["velocities"]:
            for i in range(n_segments):
                ang = np.degrees(tel.column(f"theta{i + 1}")[sl])
                if self.wrap_angles.isChecked():
                    put("angles", self.curves["angles"][i], wrap_degrees(ang), connect="finite")
                else:
                    put("angles", self.curves["angles"][i], ang)
                put("velocities", self.curves["velocities"][i], np.degrees(tel.column(f"omega{i + 1}")[sl]))
        if vis["error"]:
            c = 0
            for k, label in enumerate(labels):
                if label != "cart":
                    put("error", self.curves["error"][c], np.degrees(tel.column(f"error{k}")[sl]))
                    c += 1
        if vis["control"]:
            cc = self.curves["control"]
            put("control", cc[0], tel.column("command")[sl])
            put("control", cc[1], tel.column("unsaturated")[sl])
            put("control", cc[2], tel.column("actuator")[sl])
            put("control", cc[3], tel.column("tip_force")[sl])
        if vis["components"] and labels:
            k = max(0, min(self.loop_combo.currentIndex(), len(labels) - 1))
            for j, kind in enumerate(("p", "i", "d")):
                put("components", self.curves["components"][j], tel.column(f"{kind}{k}")[sl])
        if vis["energy"]:
            ec = self.curves["energy"]
            put("energy", ec[0], tel.column("ke")[sl])
            put("energy", ec[1], tel.column("pe")[sl])
            put("energy", ec[2], tel.column("energy")[sl])
        if vis["cart"] and has_cart:
            put("cart", self.curves["cart"][0], tel.column("x")[sl])
            if "cart" in labels:
                k = labels.index("cart")
                put("cart", self.curves["cart"][1], tel.column(f"error{k}")[sl])
        self._apply_ranges(t, series, vis)

    def _apply_ranges(self, t: np.ndarray, series: dict[str, list[np.ndarray]], vis: dict[str, bool]) -> None:
        window = self.window.value()
        t_end = float(t[-1]) if t.size else 0.0
        x_hi = max(window, t_end)
        for key, plot in self.plots.items():
            if vis[key]:
                plot.setXRange(x_hi - window, x_hi, padding=0.0)
        if not self.smooth_y.isChecked():
            return
        now = time.monotonic()
        for key, arrays in series.items():
            if not vis[key] or not arrays:
                continue
            lo, hi = self.axes[key].update(*data_extent(arrays), now)
            self.plots[key].setYRange(lo, hi, padding=0.0)
