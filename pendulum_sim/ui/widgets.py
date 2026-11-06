"""Small reusable Qt widget helpers."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSpinBox,
    QWidget,
)

SEGMENT_COLORS = ["#2E86DE", "#E67E22", "#27AE60"]
LOOP_COLORS = ["#8E44AD", "#2E86DE", "#E67E22", "#27AE60"]
EXPERIMENT_COLORS = [
    "#2E86DE", "#E74C3C", "#27AE60", "#8E44AD", "#E67E22",
    "#16A085", "#C0392B", "#2C3E50", "#D35400", "#7F8C8D",
]


def spin(
    minimum: float,
    maximum: float,
    value: float = 0.0,
    decimals: int = 3,
    step: float = 0.1,
    suffix: str = "",
    tooltip: str = "",
) -> QDoubleSpinBox:
    """A double spin box that only emits ``valueChanged`` on commit/arrow steps."""
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setKeyboardTracking(False)
    box.setAccelerated(True)
    if suffix:
        box.setSuffix(f" {suffix}")
    if tooltip:
        box.setToolTip(tooltip)
    box.setValue(value)
    box.setMinimumWidth(110)
    return box


def int_spin(minimum: int, maximum: int, value: int, tooltip: str = "") -> QSpinBox:
    box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setKeyboardTracking(False)
    if tooltip:
        box.setToolTip(tooltip)
    return box


def check(text: str, checked: bool = False, tooltip: str = "") -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(checked)
    if tooltip:
        box.setToolTip(tooltip)
    return box


def group(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return box, form


def scrollable(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    return area


HINT_OPACITY = 0.72  # hint text = normal text colour, slightly dimmed (readable in light and dark mode)


class HintLabel(QLabel):
    """Secondary explanatory text.

    The colour is derived from the current text colour instead of a fixed grey, so it
    keeps its contrast in dark mode and follows live light/dark switching (macOS).
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setWordWrap(True)
        font = self.font()
        font.setPointSizeF(max(9.0, font.pointSizeF() * 0.9))
        self.setFont(font)
        self._updating = False
        self._apply_colour()

    def _apply_colour(self) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            base = QApplication.palette()
            colour = QColor(base.color(QPalette.ColorRole.WindowText))
            colour.setAlphaF(HINT_OPACITY)
            pal = self.palette()
            pal.setColor(QPalette.ColorRole.WindowText, colour)
            self.setPalette(pal)
        finally:
            self._updating = False

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        if event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.StyleChange):
            self._apply_colour()
        super().changeEvent(event)


def hint(text: str) -> QLabel:
    return HintLabel(text)


def set_silently(widget, value) -> None:
    """Set a widget value without emitting change signals."""
    widget.blockSignals(True)
    try:
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        else:
            widget.setValue(value)
    finally:
        widget.blockSignals(False)
