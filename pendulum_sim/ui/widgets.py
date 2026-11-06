"""Small reusable Qt widget helpers."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(mid); font-size: 11px;")
    return label


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
