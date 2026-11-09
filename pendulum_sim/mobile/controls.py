"""Touch editors: switches, slider rows with steppers and tap-to-type, choice chips,
integer steppers, collapsible sections and kinetic scroll pages.

Every editor is at least 48 dp tall and emits its change only when the user commits
it (slider released, stepper tapped, value typed), so the simulation is not rebuilt
on every pixel of a drag; the shown value follows the finger live.

Value mappings for sliders: ``linear``; ``log`` (positive quantities spanning decades:
masses, frequencies, weights); ``symlog`` (signed gains: fine near zero, decades
further out).
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Property, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScroller,
    QScrollerProperties,
    QSizePolicy,
    QSlider,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .icons import icon

SLIDER_STEPS = 1000


# ------------------------------------------------------------------ helpers
def label(text: str, role: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    if role:
        lab.setProperty("role", role)
    return lab


def tool_button(icon_name: str, text: str = "", tip: str = "", size: int = 24, flat: bool = False) -> QToolButton:
    b = QToolButton()
    b.setIcon(icon(icon_name))
    b.setIconSize(QSize(size, size))
    if text:
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    else:
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setMinimumSize(theme.TOUCH_PX, theme.TOUCH_PX)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if tip:
        b.setToolTip(tip)
        b.setAccessibleName(tip)
    if flat:
        b.setProperty("flat", True)
    return b


def push_button(text: str, icon_name: str | None = None, primary: bool = False) -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name, theme.ON_ACCENT if primary else theme.TEXT))
        b.setIconSize(QSize(22, 22))
    if primary:
        b.setProperty("primary", True)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return b


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 12, 16, 12)
    lay.setSpacing(6)
    return frame, lay


def fmt_number(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    if decimals and "." in text:
        text = text.rstrip("0").rstrip(".") if abs(value) >= 1e-12 else "0"
    return "0" if text in ("-0", "") else text


# ------------------------------------------------------------------- switch
class Switch(QAbstractButton):
    """Material-style on/off switch (52 × 32 dp)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"thumb", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(56, theme.TOUCH_PX)

    def _get_thumb(self) -> float:
        return self._pos

    def _set_thumb(self, v: float) -> None:
        self._pos = v
        self.update()

    thumb = Property(float, _get_thumb, _set_thumb)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # noqa: N802 - keep the thumb in place without animation
        super().setChecked(on)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = 30
        w = 52
        x0 = (self.width() - w) / 2
        y0 = (self.height() - h) / 2
        on = self._pos
        track = QColor(theme.ACCENT if self.isChecked() else theme.OUTLINE)
        if not self.isEnabled():
            track = QColor(theme.SURFACE_2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(int(x0), int(y0), w, h, h / 2, h / 2)
        d = h - 8
        cx = x0 + 4 + on * (w - 8 - d)
        p.setBrush(QColor(theme.ON_ACCENT if self.isChecked() else theme.TEXT_2) if self.isEnabled()
                   else QColor(theme.DISABLED))
        p.drawEllipse(int(cx), int(y0 + 4), d, d)
        p.end()


class SwitchRow(QWidget):
    toggled = Signal(bool)

    def __init__(self, text: str, hint: str = "", checked: bool = False) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.label = label(text)
        col.addWidget(self.label)
        if hint:
            col.addWidget(label(hint, "secondary"))
        lay.addLayout(col, 1)
        self.switch = Switch()
        self.switch.setChecked(checked)
        self.switch.setAccessibleName(text)
        lay.addWidget(self.switch)
        self.switch.toggled.connect(self.toggled)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - the whole row toggles
        if self.isEnabled() and self.rect().contains(event.position().toPoint()):
            self.switch.toggle()

    def isChecked(self) -> bool:  # noqa: N802
        return self.switch.isChecked()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        self.switch.blockSignals(True)
        self.switch.setChecked(on)
        self.switch.blockSignals(False)


# ------------------------------------------------------------------ number
class NumberRow(QWidget):
    """Label + value (tap to type) + slider + large −/+ steppers.

    ``valueChanged`` fires on commit only. ``value()``/``setValue()`` mirror a spin box,
    so the row can stand in for one.
    """

    valueChanged = Signal(float)

    def __init__(self, text: str, minimum: float, maximum: float, value: float = 0.0, decimals: int = 3,
                 unit: str = "", mapping: str = "linear", step: float | None = None, hint: str = "",
                 slider: bool = True) -> None:
        super().__init__()
        self.minimum, self.maximum = minimum, maximum
        self.decimals = decimals
        self.unit = unit
        self.mapping = mapping
        self.step = step
        self._value = self._clamp(value)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = label(text)
        head.addWidget(self.title, 1)
        # value: a flat button that turns into a numeric field when tapped
        self.value_stack = QStackedLayout()
        self.value_btn = QPushButton()
        self.value_btn.setProperty("flat", True)
        self.value_btn.setStyleSheet(f"QPushButton {{ background: transparent; border: none; "
                                     f"color: {theme.ACCENT}; font-weight: 600; padding: 0 6px; }}")
        self.value_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.value_btn.clicked.connect(self._begin_edit)
        self.edit = QLineEdit()
        self.edit.setValidator(QDoubleValidator(minimum, maximum, max(decimals, 6)))
        self.edit.setInputMethodHints(Qt.InputMethodHint.ImhFormattedNumbersOnly)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.edit.editingFinished.connect(self._end_edit)
        self.value_stack.addWidget(self.value_btn)
        self.value_stack.addWidget(self.edit)
        vw = QWidget()
        vw.setLayout(self.value_stack)
        vw.setMinimumWidth(88)
        head.addWidget(vw)
        outer.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.minus = tool_button("minus", tip=f"Decrease {text}")
        self.plus = tool_button("plus", tip=f"Increase {text}")
        self.minus.setAutoRepeat(True)
        self.plus.setAutoRepeat(True)
        self.minus.setAutoRepeatDelay(400)
        self.plus.setAutoRepeatDelay(400)
        self.minus.clicked.connect(lambda: self._nudge(-1))
        self.plus.clicked.connect(lambda: self._nudge(+1))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.actionTriggered.connect(self._on_slider_action)
        self.slider.setVisible(slider)
        row.addWidget(self.minus)
        row.addWidget(self.slider, 1)
        if not slider:
            row.addStretch(1)
        row.addWidget(self.plus)
        outer.addLayout(row)
        if hint:
            outer.addWidget(label(hint, "secondary"))
        self._sync()

    # mapping value <-> slider position (0 … 1)
    def _to_pos(self, v: float) -> float:
        lo, hi = self.minimum, self.maximum
        if self.mapping == "log" and lo > 0:
            return (math.log10(max(v, lo)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
        if self.mapping == "symlog":
            f = self._symlog_scale()
            a, b = math.asinh(lo / f), math.asinh(hi / f)
            return (math.asinh(v / f) - a) / (b - a)
        return (v - lo) / (hi - lo) if hi > lo else 0.0

    def _from_pos(self, t: float) -> float:
        lo, hi = self.minimum, self.maximum
        if self.mapping == "log" and lo > 0:
            return 10 ** (math.log10(lo) + t * (math.log10(hi) - math.log10(lo)))
        if self.mapping == "symlog":
            f = self._symlog_scale()
            a, b = math.asinh(lo / f), math.asinh(hi / f)
            return f * math.sinh(a + t * (b - a))
        return lo + t * (hi - lo)

    def _symlog_scale(self) -> float:
        return max(10 ** (-self.decimals), 1e-3 * max(abs(self.minimum), abs(self.maximum)))

    def _clamp(self, v: float) -> float:
        return min(max(float(v), self.minimum), self.maximum)

    def _round(self, v: float) -> float:
        return round(v, self.decimals)

    def _nudge(self, direction: int) -> None:
        v = self._value
        if self.step is not None:
            new = v + direction * self.step
        elif self.mapping in ("log", "symlog"):
            mag = abs(v)
            unit = 10 ** (-self.decimals)
            if mag < 10 * unit:
                new = v + direction * unit
            else:   # ~2 % of the value, rounded to a "nice" 1/2/5 step
                raw = 0.02 * mag
                e = 10 ** math.floor(math.log10(raw))
                nice = min((m * e for m in (1, 2, 5, 10) if m * e >= raw), default=raw)
                new = v + direction * nice
        else:
            new = v + direction * (self.maximum - self.minimum) / 100.0
        self._commit(new)

    def _on_slider_moved(self, pos: int) -> None:
        self._value = self._clamp(self._round(self._from_pos(pos / SLIDER_STEPS)))
        self._show_value()

    def _on_slider_released(self) -> None:
        self._commit(self._value)

    def _on_slider_action(self, action: int) -> None:
        # taps on the groove (page steps) commit at once
        if action in (QSlider.SliderAction.SliderPageStepAdd, QSlider.SliderAction.SliderPageStepSub):
            QTimer.singleShot(0, lambda: self._commit(self._from_pos(self.slider.value() / SLIDER_STEPS)))

    def _begin_edit(self) -> None:
        self.edit.setText(fmt_number(self._value, self.decimals))
        self.value_stack.setCurrentIndex(1)
        self.edit.setFocus()
        self.edit.selectAll()

    def _end_edit(self) -> None:
        if self.value_stack.currentIndex() != 1:
            return
        self.value_stack.setCurrentIndex(0)
        try:
            v = float(self.edit.text().replace(",", "."))
        except ValueError:
            self._show_value()
            return
        self._commit(v)

    def _commit(self, v: float) -> None:
        self._value = self._clamp(self._round(v))
        self._sync()
        self.valueChanged.emit(self._value)

    def _show_value(self) -> None:
        unit = f" {self.unit}" if self.unit else ""
        self.value_btn.setText(f"{fmt_number(self._value, self.decimals)}{unit}")

    def _sync(self) -> None:
        self._show_value()
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(min(max(self._to_pos(self._value), 0.0), 1.0) * SLIDER_STEPS)))
        self.slider.blockSignals(False)

    # spin-box compatible API
    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:  # noqa: N802 - silent, like set_silently()
        self._value = self._clamp(v)
        self._sync()

    def setRange(self, minimum: float, maximum: float) -> None:  # noqa: N802
        self.minimum, self.maximum = minimum, maximum
        self.setValue(self._value)


# ------------------------------------------------------------------ choices
class FlowLayout(QLayout):
    """Wraps its items onto new lines (chips on narrow screens, no horizontal scrolling)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Everything on one line (the preferred size); wraps when narrower."""
        hints = [it.sizeHint() for it in self._items if not it.isEmpty()]
        if not hints:
            return QSize(0, 0)
        w = sum(h.width() for h in hints) + self._spacing * (len(hints) - 1)
        return QSize(w, max(h.height() for h in hints))

    def minimumSize(self) -> QSize:  # noqa: N802
        s = QSize()
        for it in self._items:
            if not it.isEmpty():
                s = s.expandedTo(it.minimumSize())
        return s

    def _layout(self, rect: QRect, dry: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            if it.isEmpty():                  # hidden widgets take no space
                continue
            hint = it.sizeHint()
            hint.setWidth(min(hint.width(), max(rect.width(), it.minimumSize().width())))
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                line_h = 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class ChoiceRow(QWidget):
    """Label + exclusive chips (enumerations). ``changed(value)`` on tap."""

    changed = Signal(object)

    def __init__(self, text: str, options: list[tuple[object, str]], value=None, hint: str = "") -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(6)
        if text:
            outer.addWidget(label(text))
        holder = QWidget()
        self.flow = FlowLayout(holder)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[object, QPushButton] = {}
        for val, txt in options:
            b = QPushButton(txt)
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.group.addButton(b)
            self.flow.addWidget(b)
            self.buttons[val] = b
            b.clicked.connect(lambda _=False, v=val: self.changed.emit(v))
        outer.addWidget(holder)
        if hint:
            outer.addWidget(label(hint, "secondary"))
        if value is not None:
            self.setValue(value)

    def value(self):
        for v, b in self.buttons.items():
            if b.isChecked():
                return v
        return None

    def setValue(self, value) -> None:  # noqa: N802
        b = self.buttons.get(value)
        if b is not None:
            b.setChecked(True)


class ToggleChips(QWidget):
    """Non-exclusive chips (e.g. P / I / D, graph selection). ``changed(key, on)``."""

    changed = Signal(object, bool)

    def __init__(self, options: list[tuple[object, str]]) -> None:
        super().__init__()
        self.flow = FlowLayout(self)
        self.buttons: dict[object, QPushButton] = {}
        for val, txt in options:
            b = QPushButton(txt)
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.flow.addWidget(b)
            self.buttons[val] = b
            b.toggled.connect(lambda on, v=val: self.changed.emit(v, on))

    def set_checked(self, val, on: bool) -> None:
        b = self.buttons[val]
        b.blockSignals(True)
        b.setChecked(on)
        b.blockSignals(False)


class StepperRow(QWidget):
    """Label + [−] integer [+] (segment count)."""

    valueChanged = Signal(int)

    def __init__(self, text: str, minimum: int, maximum: int, value: int, hint: str = "") -> None:
        super().__init__()
        self.minimum, self.maximum = minimum, maximum
        self._value = value
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        col = QVBoxLayout()
        col.addWidget(label(text))
        if hint:
            col.addWidget(label(hint, "secondary"))
        lay.addLayout(col, 1)
        self.minus = tool_button("minus", tip=f"Fewer: {text}")
        self.plus = tool_button("plus", tip=f"More: {text}")
        self.shown = QLabel()
        self.shown.setProperty("role", "value")
        self.shown.setMinimumWidth(40)
        self.shown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.minus)
        lay.addWidget(self.shown)
        lay.addWidget(self.plus)
        self.minus.clicked.connect(lambda: self._set(self._value - 1, emit=True))
        self.plus.clicked.connect(lambda: self._set(self._value + 1, emit=True))
        self._set(value, emit=False)

    def _set(self, v: int, emit: bool) -> None:
        v = min(max(v, self.minimum), self.maximum)
        changed = v != self._value
        self._value = v
        self.shown.setText(str(v))
        self.minus.setEnabled(v > self.minimum)
        self.plus.setEnabled(v < self.maximum)
        if emit and changed:
            self.valueChanged.emit(v)

    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:  # noqa: N802
        self._set(v, emit=False)


class _ClickLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Section(QWidget):
    """Collapsible group: a tappable header (title + chevron) and its content."""

    def __init__(self, title: str, expanded: bool = True, subtitle: str = "") -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.frame, inner = card()
        # header: chevron button + a word-wrapped title (long titles wrap instead of
        # forcing the page wider than a phone); tapping either toggles the section
        self.header = QToolButton()
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.header.setIconSize(QSize(22, 22))
        self.header.setProperty("flat", True)
        self.header.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0; }")
        self.header.setFixedWidth(32)
        self.header.toggled.connect(self.set_expanded)
        self.header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.title_label = _ClickLabel(title if not subtitle else f"{title}   ·   {subtitle}")
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(f"QLabel {{ font-weight: 600; font-size: {theme.BODY_PX + 1}px; }}")
        self.title_label.setMinimumHeight(theme.TOUCH_PX)
        self.title_label.clicked.connect(self.header.toggle)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addWidget(self.header)
        head.addWidget(self.title_label, 1)
        inner.addLayout(head)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 4, 0, 4)
        self.body_layout.setSpacing(6)
        inner.addWidget(self.body)
        outer.addWidget(self.frame)
        self.set_expanded(expanded)

    def set_expanded(self, on: bool) -> None:
        self.body.setVisible(on)
        self.header.setIcon(icon("chevron_down" if on else "chevron_right", theme.TEXT_2))
        if self.header.isChecked() != on:
            self.header.blockSignals(True)
            self.header.setChecked(on)
            self.header.blockSignals(False)

    def add(self, widget: QWidget) -> QWidget:
        self.body_layout.addWidget(widget)
        return widget

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def title(self) -> str:
        return self.title_label.text()


def make_kinetic(area: QAbstractScrollArea) -> QAbstractScrollArea:
    """Finger scrolling with a little overshoot for any scroll area (idempotent)."""
    vp = area.viewport()
    if vp.property("kinetic"):
        return area
    vp.setProperty("kinetic", True)
    QScroller.grabGesture(vp, QScroller.ScrollerGestureType.TouchGesture)
    props = QScroller.scroller(vp).scrollerProperties()
    props.setScrollMetric(QScrollerProperties.ScrollMetric.OvershootDragResistanceFactor, 0.3)
    props.setScrollMetric(QScrollerProperties.ScrollMetric.OvershootScrollDistanceFactor, 0.1)
    props.setScrollMetric(QScrollerProperties.ScrollMetric.DragStartDistance, 0.004)
    QScroller.scroller(vp).setScrollerProperties(props)
    return area


def scroll_area(inner: QWidget) -> QScrollArea:
    """Vertical, kinetic, never horizontal: content squeezed to the viewport width."""
    area = QScrollArea()
    inner.setSizePolicy(QSizePolicy.Policy.Ignored, inner.sizePolicy().verticalPolicy())
    area.setWidget(inner)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return make_kinetic(area)


class ProgressBar(QWidget):
    """Rounded progress bar with a centred percentage, painted directly.

    Replaces ``QProgressBar``, whose ``setValue`` (synchronous repaint through the
    style sheet + ``valueChanged`` emission) crashed PySide6 with Python 3.10 on
    Windows while worker processes were shutting down. ``setValue`` here only stores
    the value and schedules a normal repaint.
    """

    def __init__(self, maximum: int = 1000, show_text: bool = True, height: int = 30) -> None:
        super().__init__()
        self._max = max(1, maximum)
        self._value = 0
        self.show_text = show_text
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def setValue(self, value: int) -> None:  # noqa: N802 - QProgressBar API
        value = max(0, min(int(value), self._max))
        if value != self._value:
            self._value = value
            self.update()

    def value(self) -> int:
        return self._value

    def maximum(self) -> int:
        return self._max

    def text(self) -> str:
        return f"{round(100 * self._value / self._max)} %"

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, self.minimumHeight())

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        radius = r.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SURFACE_2))
        p.drawRoundedRect(r, radius, radius)
        frac = self._value / self._max
        if frac > 0:
            chunk = QRectF(r.left(), r.top(), max(r.height(), r.width() * frac), r.height())
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(chunk, radius, radius)
        if self.show_text:
            f = QFont(self.font())
            f.setBold(True)
            p.setFont(f)
            p.setPen(self.palette().text().color())
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class ActionBar(QWidget):
    """Fixed bar at the bottom of a screen: its buttons never scroll out of reach."""

    def __init__(self, buttons: list[tuple[QWidget, int]]) -> None:
        super().__init__()
        self.setObjectName("actionbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(10)
        for b, stretch in buttons:
            row.addWidget(b, stretch)


class ScrollPage(QScrollArea):
    """Vertical page with kinetic touch scrolling, no horizontal scrolling."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content = QWidget()
        self.content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.layout_ = QVBoxLayout(self.content)
        self.layout_.setContentsMargins(16, 12, 16, 24)
        self.layout_.setSpacing(12)
        self.setWidget(self.content)
        make_kinetic(self)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.layout_.addWidget(widget, stretch)
        return widget

    def finish(self) -> None:
        self.layout_.addStretch(1)


def bind(row, handler: Callable) -> None:
    """Connect a row's commit signal to ``handler`` (ignores the value argument)."""
    sig = getattr(row, "valueChanged", None) or getattr(row, "toggled", None) or getattr(row, "changed", None)
    sig.connect(lambda *_: handler())
