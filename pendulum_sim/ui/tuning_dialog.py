"""Dialog that runs the robust auto-tuner in the background."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..simulation import AppConfig
from ..simulation.tuning import TuningResult, TuningSettings, default_workers, robust_tune
from .widgets import hint


class TuningThread(QThread):
    progress = Signal(float, str)
    finishedResult = Signal(object)
    failed = Signal(str)

    def __init__(self, config: AppConfig, settings: TuningSettings) -> None:
        super().__init__()
        self.config = config
        self.settings = settings
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            def report(fraction: float, message: str) -> bool:
                self.progress.emit(fraction, message)
                return not self._cancel

            self.finishedResult.emit(robust_tune(self.config, self.settings, report))
        except Exception as exc:  # shown to the user in the dialog
            self.failed.emit(str(exc))


class RobustTuneDialog(QDialog):
    """Explain, run, report and (on *Apply*) return a :class:`TuningResult`."""

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Auto-tune for robustness")
        self.setMinimumWidth(460)
        self.config = config
        self.result_value: TuningResult | None = None
        self.thread: TuningThread | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(hint(
            "Searches for the controller that recovers from the strongest push for this exact "
            "pendulum (masses, lengths, actuator limit and lag, controller rate). Every candidate "
            "is a stabilising LQR design mapped onto the PID loops; each is tested on the full "
            "nonlinear simulation by pushing the tip for 0.1 s with increasing force. Among "
            "equally robust candidates the one that settles fastest wins. Integral gains are set "
            "to 0 and all loops use the recommended settings."))
        form = QFormLayout()
        self.effort = QComboBox()
        self.effort.addItem("Quick", "quick")
        self.effort.addItem("Normal (recommended)", "normal")
        self.effort.addItem("Thorough", "thorough")
        self.effort.setCurrentIndex(1)
        form.addRow("Search effort", self.effort)
        cores = default_workers()
        form.addRow("Parallel workers", QLabel(f"{cores} CPU core(s)"))
        layout.addLayout(form)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.status = QLabel("Press Start to begin.")
        self.status.setWordWrap(True)
        self.report = QLabel("")
        self.report.setWordWrap(True)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.report)

        self.start_btn = QPushButton("Start")
        self.buttons = QDialogButtonBox()
        self.apply_btn = self.buttons.addButton("Apply gains", QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_btn = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.addButton(self.start_btn, QDialogButtonBox.ButtonRole.ActionRole)
        self.apply_btn.setEnabled(False)
        layout.addWidget(self.buttons)

        self.start_btn.clicked.connect(self.start)
        self.apply_btn.clicked.connect(self.accept)
        self.close_btn.clicked.connect(self.reject)

    def start(self) -> None:
        if self.thread is not None:
            return
        settings = TuningSettings.preset(self.effort.currentData())
        self.thread = TuningThread(self.config.copy(), settings)
        self.thread.progress.connect(self._on_progress)
        self.thread.finishedResult.connect(self._on_result)
        self.thread.failed.connect(self._on_failed)
        self.thread.finished.connect(self._on_thread_done)
        self.start_btn.setEnabled(False)
        self.effort.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.report.setText("")
        self.status.setText("Starting…")
        self.thread.start()

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress.setValue(int(fraction * 1000))
        self.status.setText(message)

    def _on_result(self, result: TuningResult) -> None:
        self.result_value = result
        before, after = result.baseline_max_push, result.max_push
        gain = f" (+{(after / before - 1) * 100:.0f} %)" if before > 0 and after > before else ""
        lines = [
            f"<b>Recoverable tip push (0.1 s pulse)</b>: {before:.3g} N → <b>{after:.3g} N</b>{gain}",
            f"Recommended test push: {result.recommended_push:g} N (set as the toolbar push)",
            f"{result.evaluations} candidates in {result.elapsed:.0f} s"
            + (" – <i>cancelled early, best so far</i>" if result.cancelled else ""),
        ]
        if after <= before * 1.02:
            lines.append("Your current gains are already about as robust as this search can find; "
                         "applying gives you the normalised recommended settings.")
        self.report.setText("<br>".join(lines))
        self.status.setText("Finished. Apply the gains or cancel to keep the current ones.")
        self.apply_btn.setEnabled(True)
        self.progress.setValue(1000)

    def _on_failed(self, message: str) -> None:
        self.status.setText(f"Tuning failed: {message}")

    def _on_thread_done(self) -> None:
        self.start_btn.setEnabled(self.result_value is None)
        self.effort.setEnabled(self.result_value is None)
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None

    def reject(self) -> None:
        if self.thread is not None:
            self.status.setText("Stopping…")
            self.thread.cancel()
            self.thread.wait(30000)
        super().reject()
