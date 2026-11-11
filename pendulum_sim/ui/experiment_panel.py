"""Experiment mode: run, store and compare controller configurations."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pyqtgraph as pg
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..simulation import (
    AppConfig,
    DisturbanceSettings,
    ExperimentResult,
    ExperimentSettings,
    export_csv,
    load_experiments,
    run_experiment,
    save_experiments,
)
from ..session import rules
from . import file_io
from .widgets import EXPERIMENT_COLORS, check, group, hint, scrollable, spin

COLUMNS = ["Show", "Name", "Controller gains", "Settling [s]", "Max dev [°]", "Overshoot [%]",
           "RMSE [°]", "u RMS", "u peak", "∫u² dt", "Max |x| [m]", "Status"]


class ExperimentThread(QThread):
    progress = Signal(float)
    finishedResult = Signal(object)
    failed = Signal(str)

    def __init__(self, config: AppConfig, name: str) -> None:
        super().__init__()
        self.config = config
        self.name = name
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # executes in the worker thread
        try:
            def report(fraction: float) -> bool:
                self.progress.emit(fraction)
                return not self._cancel

            result = run_experiment(self.config, self.name, report)
            self.finishedResult.emit(result)
        except Exception as exc:  # report every failure to the GUI thread
            self.failed.emit(str(exc))


class ExperimentPanel(QWidget):
    statusMessage = Signal(str)

    def __init__(self, get_config: Callable[[], AppConfig]) -> None:
        super().__init__()
        self.get_config = get_config
        self.results: list[ExperimentResult] = []
        self.thread: ExperimentThread | None = None
        self._counter = 1

        outer = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        # ---------------------------------------------------- left: controls
        left = QWidget()
        lv = QVBoxLayout(left)
        box, form = group("Experiment")
        self.name = QLineEdit()
        self.name.setPlaceholderText("auto: Run 1, Run 2, …")
        self.duration = spin(0.1, 600.0, 8.0, 1, 1.0, "s")
        self.tolerance = spin(0.01, 45.0, 1.0, 2, 0.25, "°", "Band used for the settling time")
        form.addRow("Name", self.name)
        form.addRow("Duration", self.duration)
        form.addRow("Settling band ±", self.tolerance)
        lv.addWidget(box)

        dbox, dform = group("Scheduled disturbance (tip force)")
        self.dist_on = check("Apply disturbance", False)
        self.dist_time = spin(0.0, 600.0, 2.0, 2, 0.5, "s")
        self.dist_force = spin(-1000.0, 1000.0, 1.0, 3, 0.1, "N")
        self.dist_dur = spin(0.001, 10.0, 0.1, 3, 0.05, "s")
        dform.addRow("", self.dist_on)
        dform.addRow("Start time", self.dist_time)
        dform.addRow("Force", self.dist_force)
        dform.addRow("Duration", self.dist_dur)
        lv.addWidget(dbox)

        self.reuse = check("Reuse plant, initial conditions and noise\nof the first (baseline) experiment", True,
                           "Guarantees identical initial conditions: only the controller differs")
        lv.addWidget(self.reuse)
        self.run_btn = QPushButton("▶  Run experiment")
        self.run_btn.setMinimumHeight(34)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.run_btn, 2)
        row.addWidget(self.cancel_btn, 1)
        lv.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        lv.addWidget(self.progress)

        fbox, fform = group("Results")
        self.save_btn = QPushButton("Save results…")
        self.load_btn = QPushButton("Load results…")
        self.csv_btn = QPushButton("Export selected as CSV…")
        self.remove_btn = QPushButton("Remove selected")
        self.clear_btn = QPushButton("Clear all")
        self.apply_btn = QPushButton("Load selected gains into controller")
        for b in (self.save_btn, self.load_btn, self.csv_btn, self.apply_btn, self.remove_btn, self.clear_btn):
            fform.addRow(b)
        lv.addWidget(fbox)
        lv.addWidget(hint(
            "Workflow: run the current configuration, change PID gains in the Controller panel, run again "
            "and compare. Runs are deterministic, so with 'Reuse' enabled every run starts from exactly "
            "the same state. Settling time = first time after which every segment stays inside the band."))
        lv.addStretch(1)
        left.setMinimumWidth(280)
        splitter.addWidget(scrollable(left))  # scrolls instead of forcing a tall window

        # --------------------------------------------- right: plots + table
        right = QWidget()
        rv = QVBoxLayout(right)
        top = QHBoxLayout()
        top.addWidget(QLabel("Segment:"))
        self.segment = QComboBox()
        for i in range(3):
            self.segment.addItem(f"θ{i + 1}", i)
        top.addWidget(self.segment)
        top.addStretch(1)
        rv.addLayout(top)
        vsplit = QSplitter(Qt.Orientation.Vertical)
        self.plots = {}
        first = None
        for key, title, unit in (("angle", "Angular displacement", "deg"),
                                 ("error", "Control error", "deg"),
                                 ("effort", "Control effort (actuator output)", "N / N·m"),
                                 ("cart", "Cart position", "m")):
            plot = pg.PlotWidget(title=title)
            plot.setLabel("left", unit)
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.addLegend(offset=(6, 6))
            plot.setMinimumHeight(55)
            if first is None:
                first = plot
            else:
                plot.setXLink(first)
            self.plots[key] = plot
            vsplit.addWidget(plot)
        self.plots["cart"].setLabel("bottom", "time", "s")
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        vsplit.addWidget(self.table)
        vsplit.setSizes([200, 200, 200, 150, 220])
        rv.addWidget(vsplit, 1)
        splitter.addWidget(right)
        splitter.setSizes([320, 900])

        self.run_btn.clicked.connect(self.run)
        self.cancel_btn.clicked.connect(self.cancel)
        self.save_btn.clicked.connect(self.save)
        self.load_btn.clicked.connect(self.load)
        self.csv_btn.clicked.connect(self.export_selected)
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_btn.clicked.connect(self.clear)
        self.segment.currentIndexChanged.connect(self.redraw)
        self.table.itemChanged.connect(self._on_item_changed)

    # ---------------------------------------------------------- settings
    def set_settings(self, e: ExperimentSettings) -> None:
        for w, v in ((self.duration, e.duration), (self.tolerance, e.settle_tolerance_deg),
                     (self.dist_time, e.disturbance.time), (self.dist_force, e.disturbance.force),
                     (self.dist_dur, e.disturbance.duration)):
            w.setValue(v)
        self.dist_on.setChecked(e.disturbance.enabled)

    def settings(self) -> ExperimentSettings:
        return ExperimentSettings(
            duration=self.duration.value(),
            settle_tolerance_deg=self.tolerance.value(),
            disturbance=DisturbanceSettings(
                enabled=self.dist_on.isChecked(), time=self.dist_time.value(),
                force=self.dist_force.value(), duration=self.dist_dur.value()),
        )

    # --------------------------------------------------------------- run
    def build_config(self) -> AppConfig:
        reuse = self.results[0].config if self.reuse.isChecked() and self.results else None
        return rules.experiment_config(self.get_config(), self.settings(), reuse)

    def run(self) -> None:
        if self.thread is not None:
            return
        try:
            cfg = self.build_config()
            cfg.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid configuration", str(exc))
            return
        name = self.name.text().strip() or f"Run {self._counter}"
        self._counter += 1
        self.thread = ExperimentThread(cfg, name)
        self.thread.progress.connect(lambda f: self.progress.setValue(int(f * 1000)))
        self.thread.finishedResult.connect(self._on_result)
        self.thread.failed.connect(self._on_failed)
        self.thread.finished.connect(self._on_thread_finished)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.statusMessage.emit(f"Running experiment '{name}'…")
        self.thread.start()

    def cancel(self) -> None:
        if self.thread is not None:
            self.thread.cancel()

    def _on_result(self, result: ExperimentResult) -> None:
        if result.message == "cancelled":
            self.statusMessage.emit(f"Experiment '{result.name}' cancelled")
            return
        self.results.append(result)
        self._rebuild_table()
        self.redraw()
        state = "diverged" if result.diverged else "fell" if result.metrics.fell else "ok"
        self.statusMessage.emit(f"Experiment '{result.name}' finished ({state})")
        self.name.clear()

    def _on_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Experiment failed", message)

    def _on_thread_finished(self) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None

    def shutdown(self) -> None:
        """Stop a running experiment (called when the window closes)."""
        if self.thread is not None:
            self.thread.cancel()
            self.thread.wait(5000)

    # ------------------------------------------------------------ table
    def _color(self, index: int) -> str:
        return EXPERIMENT_COLORS[index % len(EXPERIMENT_COLORS)]

    def _rebuild_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.results))
        for row, r in enumerate(self.results):
            m = r.metrics
            show = QTableWidgetItem()
            show.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            show.setCheckState(Qt.CheckState.Checked if r.visible else Qt.CheckState.Unchecked)
            show.setBackground(QColor(self._color(row)))
            self.table.setItem(row, 0, show)
            settle = "—" if m.settling_time is None else f"{m.settling_time:.3f}"
            status = "diverged" if r.diverged else "FELL" if m.fell else "stable"
            values = [r.name, r.gains_summary(), settle, f"{m.max_deviation:.2f}", f"{m.overshoot_pct:.1f}",
                      f"{m.rmse:.3f}", f"{m.control_rms:.3f}", f"{m.control_peak:.3f}",
                      f"{m.control_energy:.3f}", f"{m.max_cart_displacement:.3f}", status]
            for col, v in enumerate(values, start=1):
                item = QTableWidgetItem(v)
                if col == 2:
                    item.setToolTip(v)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0 and item.row() < len(self.results):
            self.results[item.row()].visible = item.checkState() == Qt.CheckState.Checked
            self.redraw()

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()})

    # ------------------------------------------------------------ plots
    def redraw(self) -> None:
        seg = self.segment.currentData() or 0
        for plot in self.plots.values():
            plot.clear()
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
        for idx, r in enumerate(self.results):
            if not r.visible:
                continue
            pen = pg.mkPen(self._color(idx), width=1.8)
            t = r.data["t"]
            label = r.name
            if seg < r.n_segments:
                self.plots["angle"].plot(t, r.angle_deg(seg), pen=pen, name=label)
                self.plots["error"].plot(t, r.error_deg(seg), pen=pen, name=label)
            self.plots["effort"].plot(t, r.data["actuator"], pen=pen, name=label)
            if r.config.plant.has_cart:
                self.plots["cart"].plot(t, r.data["x"], pen=pen, name=label)
        tol = self.tolerance.value()
        for y in (tol, -tol):
            line = pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen("#7F8C8D", style=Qt.PenStyle.DashLine))
            self.plots["error"].addItem(line)

    # ------------------------------------------------------------- files
    def save(self) -> None:
        if not self.results:
            return
        path = file_io.save_file_name(self, "Save experiment results", "experiments.json", "JSON (*.json)")
        if path:
            try:
                file_io.write_with(path, lambda local: save_experiments(self.results, local))
                self.statusMessage.emit(f"Saved {len(self.results)} experiments to "
                                        f"{Path(file_io.display_name(path)).name}")
            except OSError as exc:
                QMessageBox.critical(self, "Save failed", str(exc))

    def load(self) -> None:
        path = file_io.open_file_name(self, "Load experiment results", "", "JSON (*.json)")
        if not path:
            return
        try:
            loaded = file_io.read_with(path, load_experiments)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        self.results.extend(loaded)
        self._rebuild_table()
        self.redraw()
        self.statusMessage.emit(f"Loaded {len(loaded)} experiments")

    def export_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Export CSV", "Select an experiment in the table first.")
            return
        r = self.results[rows[0]]
        path = file_io.save_file_name(self, "Export time series", f"{r.name}.csv", "CSV (*.csv)")
        if path:
            try:
                file_io.write_with(path, lambda local: export_csv(r, local))
            except OSError as exc:
                QMessageBox.critical(self, "Export failed", str(exc))

    def remove_selected(self) -> None:
        for row in reversed(self._selected_rows()):
            del self.results[row]
        self._rebuild_table()
        self.redraw()

    def clear(self) -> None:
        self.results.clear()
        self._counter = 1
        self._rebuild_table()
        self.redraw()

    def selected_result(self) -> ExperimentResult | None:
        rows = self._selected_rows()
        return self.results[rows[0]] if rows else None
