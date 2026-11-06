"""Real-time telemetry graphs (pyqtgraph)."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget

from ..simulation import TelemetryRecorder
from .widgets import LOOP_COLORS, SEGMENT_COLORS, check, spin

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


class TelemetryPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.toggles: dict[str, QCheckBox] = {}
        for key, title, _ in GRAPHS:
            box = check(title.split(" (")[0], key in DEFAULT_ON)
            box.toggled.connect(self._update_visibility)
            self.toggles[key] = box
            bar.addWidget(box)
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
            plot.setMinimumHeight(110)
            if first is None:
                first = plot
            else:
                plot.setXLink(first)
            self.plots[key] = plot
            self.curves[key] = []
            self.splitter.addWidget(plot)
        self.plots[GRAPHS[-1][0]].setLabel("bottom", "time", "s")
        self._signature: tuple | None = None
        self._version = -1
        self._update_visibility()

    # ----------------------------------------------------------- layout
    def _update_visibility(self) -> None:
        for key, plot in self.plots.items():
            plot.setVisible(self.toggles[key].isChecked())

    def _curve(self, key: str, name: str, color: str, style=Qt.PenStyle.SolidLine, width: float = 1.6):
        pen = pg.mkPen(color, width=width, style=style)
        curve = self.plots[key].plot([], [], pen=pen, name=name)
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
        if vis["angles"] or vis["velocities"]:
            for i in range(n_segments):
                self.curves["angles"][i].setData(t, np.degrees(tel.column(f"theta{i + 1}")[sl]))
                self.curves["velocities"][i].setData(t, np.degrees(tel.column(f"omega{i + 1}")[sl]))
        if vis["error"]:
            c = 0
            for k, label in enumerate(labels):
                if label != "cart":
                    self.curves["error"][c].setData(t, np.degrees(tel.column(f"error{k}")[sl]))
                    c += 1
        if vis["control"]:
            cc = self.curves["control"]
            cc[0].setData(t, tel.column("command")[sl])
            cc[1].setData(t, tel.column("unsaturated")[sl])
            cc[2].setData(t, tel.column("actuator")[sl])
            cc[3].setData(t, tel.column("tip_force")[sl])
        if vis["components"] and labels:
            k = max(0, min(self.loop_combo.currentIndex(), len(labels) - 1))
            for j, kind in enumerate(("p", "i", "d")):
                self.curves["components"][j].setData(t, tel.column(f"{kind}{k}")[sl])
        if vis["energy"]:
            ec = self.curves["energy"]
            ec[0].setData(t, tel.column("ke")[sl])
            ec[1].setData(t, tel.column("pe")[sl])
            ec[2].setData(t, tel.column("energy")[sl])
        if vis["cart"] and has_cart:
            self.curves["cart"][0].setData(t, tel.column("x")[sl])
            if "cart" in labels:
                k = labels.index("cart")
                self.curves["cart"][1].setData(t, tel.column(f"error{k}")[sl])
