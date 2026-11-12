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
import time
from typing import Callable

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QRegularExpression,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QAbstractScrollArea,
    QAbstractSlider,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..session import sliders
from . import theme
from .icons import icon

SLIDER_STEPS = 1000
# Typed numbers: digits, one decimal separator ("." or "," – whatever the keyboard of the
# device language offers), optional sign and exponent. Locale-independent on purpose:
# QDoubleValidator follows the device language (Russian: decimal comma), rejected
# "30.408" as invalid, and Qt never finishes editing on invalid text – the field stayed
# open for good.
NUMBER_PATTERN = r"[-+]?[0-9]*([.,][0-9]*)?([eE][-+]?[0-9]*)?"


def parse_number(text: str) -> float | None:
    """Float from typed text (``,`` or ``.`` as decimal separator); None if it is not one."""
    try:
        v = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


class _EditCloser(QObject):
    """Application-wide: a touch anywhere outside the number being typed finishes it.

    Sliders, steppers, buttons and scroll areas take no keyboard focus, so a tap on
    them did not end the edit (``editingFinished`` only comes with focus loss or Enter).
    """

    active: "NumberRow | None" = None
    since = 0.0
    GRACE_S = 0.35                        # events of the tap that opened the field
    _instance: "_EditCloser | None" = None

    @classmethod
    def watch(cls, row: "NumberRow") -> None:
        """Installed only while a number is being typed (an app-wide filter sees every event)."""
        import time

        if cls.active is not None and cls.active is not row:
            cls.active.finish_edit()
        app = QApplication.instance()
        if app is not None and cls._instance is None:
            cls._instance = cls()
            app.installEventFilter(cls._instance)
        cls.active = row
        cls.since = time.monotonic()

    @classmethod
    def release(cls, row: "NumberRow") -> None:
        if cls.active is row:
            cls.active = None
        if cls.active is None and cls._instance is not None:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(cls._instance)
            cls._instance.deleteLater()
            cls._instance = None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        import time

        row = _EditCloser.active
        if row is None or event.type() not in (QEvent.Type.MouseButtonPress, QEvent.Type.TouchBegin):
            return False
        if not isinstance(obj, QWidget) or time.monotonic() - _EditCloser.since < _EditCloser.GRACE_S:
            return False
        try:
            if obj is not row.edit and not row.edit.isAncestorOf(obj):
                QTimer.singleShot(0, row.finish_edit)     # not inside the filter
        except RuntimeError:              # the row was deleted meanwhile
            _EditCloser.active = None
        return False


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
        self.hint_label = label(hint, "secondary")
        col.addWidget(self.hint_label)
        if not hint:                    # hide() only: show() on a parentless widget opens a window
            self.hint_label.hide()
        lay.addLayout(col, 1)
        self.switch = Switch()
        self.switch.setChecked(checked)
        self.switch.setAccessibleName(text)
        lay.addWidget(self.switch)
        self.switch.toggled.connect(self.toggled)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - the whole row toggles
        if self.isEnabled() and self.rect().contains(event.position().toPoint()):
            self.switch.toggle()

    def set_hint(self, text: str) -> None:
        self.hint_label.setText(text)
        self.hint_label.setVisible(bool(text))

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
        # value: a flat button that turns into a numeric field when tapped. The stacked
        # layout gets its widget first: QStackedLayout shows the widget it is given, and a
        # parentless widget that is shown becomes a window for a moment (70 value buttons
        # flashed over the loading screen on Android)
        vw = QWidget()
        self.value_stack = QStackedLayout(vw)
        self.value_btn = QPushButton()
        self.value_btn.setProperty("flat", True)
        self.value_btn.setStyleSheet(f"QPushButton {{ background: transparent; border: none; "
                                     f"color: {theme.ACCENT}; font-weight: 600; padding: 0 6px; }}")
        self.value_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.value_btn.clicked.connect(self._begin_edit)
        self.edit = QLineEdit()
        self.edit.setValidator(QRegularExpressionValidator(QRegularExpression(NUMBER_PATTERN), self.edit))
        # numbers only (ImhNoPredictiveText added a text flag to the number keyboard type and
        # Android's keyboard no longer came up)
        self.edit.setInputMethodHints(Qt.InputMethodHint.ImhFormattedNumbersOnly)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.edit.editingFinished.connect(self._end_edit)
        self.edit.returnPressed.connect(self._end_edit)
        self.value_stack.addWidget(self.value_btn)
        self.value_stack.addWidget(self.edit)
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
        self.minus.pressed.connect(self.finish_edit)
        self.plus.pressed.connect(self.finish_edit)
        self.minus.clicked.connect(lambda: self._nudge(-1))
        self.plus.clicked.connect(lambda: self._nudge(+1))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.sliderPressed.connect(self.finish_edit)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.actionTriggered.connect(self._on_slider_action)
        if not slider:
            # hide() only: setVisible(True) on a widget that has no parent yet makes it a
            # top-level window for a moment (seen as a stray slider flashing on Android)
            self.slider.hide()
        row.addWidget(self.minus)
        row.addWidget(self.slider, 1)
        if not slider:
            row.addStretch(1)
        row.addWidget(self.plus)
        outer.addLayout(row)
        if hint:
            outer.addWidget(label(hint, "secondary"))
        self._sync()

    # mapping value <-> slider position (0 … 1): the shared rules (session.sliders)
    def _to_pos(self, v: float) -> float:
        return sliders.to_position(v, self.minimum, self.maximum, self.mapping, self.decimals)

    def _from_pos(self, t: float) -> float:
        return sliders.from_position(t, self.minimum, self.maximum, self.mapping, self.decimals)

    def _symlog_scale(self) -> float:
        return sliders.symlog_scale(self.minimum, self.maximum, self.decimals)

    def _clamp(self, v: float) -> float:
        return min(max(float(v), self.minimum), self.maximum)

    def _round(self, v: float) -> float:
        return round(v, self.decimals)

    def _nudge(self, direction: int) -> None:
        self._commit(sliders.nudge(self._value, direction, self.minimum, self.maximum, self.mapping,
                                   self.decimals, self.step))

    def _on_slider_moved(self, pos: int) -> None:
        self._value = self._clamp(self._round(self._from_pos(pos / SLIDER_STEPS)))
        self._show_value()

    def _on_slider_released(self) -> None:
        self._commit(self._value)

    PAGE_ACTIONS = (QSlider.SliderAction.SliderPageStepAdd.value, QSlider.SliderAction.SliderPageStepSub.value)

    def _on_slider_action(self, action) -> None:
        # taps on the groove (page steps) commit at once; the signal carries a plain int,
        # which never equals the (non-int) enum member – compare the values
        if int(getattr(action, "value", action)) in self.PAGE_ACTIONS:
            QTimer.singleShot(0, lambda: self._commit(self._from_pos(self.slider.value() / SLIDER_STEPS)))

    @property
    def editing(self) -> bool:
        return self.value_stack.currentIndex() == 1

    def _begin_edit(self) -> None:
        _EditCloser.watch(self)
        self.edit.setText(fmt_number(self._value, self.decimals))
        self.value_stack.setCurrentIndex(1)
        self.edit.setFocus(Qt.FocusReason.MouseFocusReason)
        self.edit.selectAll()
        # the tap went to the value button, not to the field: ask for the keyboard explicitly
        QTimer.singleShot(0, self._show_keyboard)

    def _show_keyboard(self) -> None:
        if self.editing and self.edit.hasFocus():
            im = QGuiApplication.inputMethod()
            if im is not None:
                im.show()

    def finish_edit(self) -> None:
        """Commit what was typed (or keep the old value if it is not a number)."""
        self._end_edit()

    def _end_edit(self) -> None:
        if not self.editing:
            return
        _EditCloser.release(self)
        im = QGuiApplication.inputMethod()
        if im is not None and self.edit.hasFocus():
            im.commit()                    # digits still being composed by the keyboard
        text = self.edit.text()
        self.value_stack.setCurrentIndex(0)
        self.edit.clearFocus()
        if im is not None:
            im.hide()
        v = parse_number(text)
        if v is None:
            self._show_value()             # not a number: the old value stays
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
        self.hint_label = label(hint, "secondary")
        outer.addWidget(self.hint_label)
        if not hint:                    # hide() only: show() on a parentless widget opens a window
            self.hint_label.hide()
        if value is not None:
            self.setValue(value)

    def set_hint(self, text: str) -> None:
        self.hint_label.setText(text)
        self.hint_label.setVisible(bool(text))

    def set_option_visible(self, value, on: bool) -> None:
        b = self.buttons.get(value)
        if b is not None:
            b.setVisible(on)

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


class DragScroller(QObject):
    """Finger scrolling that starts anywhere on a page – on sliders, buttons and texts too.

    ``QScroller`` only scrolled where the finger landed on empty background: sliders,
    buttons and labels took the touch first, and the pages are full of them. This
    application-wide filter holds back the press on any widget inside a kinetic scroll
    area and decides from the first few pixels of movement:

    * mostly **vertical** → the page scrolls (and flings on release); the widget never
      sees the touch, so a slider under the finger does not move;
    * mostly **horizontal** on a slider → the press is handed to the slider, which is
      dragged as usual;
    * **no movement** (tap) or a **hold** (> 0.25 s) → the press (and release) is handed
      to the widget: buttons, chips, value fields work as before.

    Only mouse events pass through here (Android delivers touches to these widgets as
    synthesised mouse events); widgets that handle touch themselves (pendulum view,
    explorable plots) are unaffected.
    """

    SLOP_PX = 10
    HOLD_S = 0.25
    FRICTION = 0.0035            # fling deceleration, 1/ms (exponential)
    _instance: "DragScroller | None" = None

    @classmethod
    def install(cls) -> None:
        app = QApplication.instance()
        if app is not None and cls._instance is None:
            cls._instance = cls()
            app.installEventFilter(cls._instance)

    def __init__(self) -> None:
        super().__init__()
        self.mode = ""                      # "", "pending", "scroll", "pass"
        self.target: QWidget | None = None
        self.area: QAbstractScrollArea | None = None
        self.start = QPointF()
        self.start_value = 0
        self.samples: list[tuple[float, float]] = []
        self.replaying = False
        self.hold = QTimer(self)
        self.hold.setSingleShot(True)
        self.hold.timeout.connect(self._on_hold)
        self.fling = QTimer(self)
        self.fling.setInterval(16)
        self.fling.timeout.connect(self._fling_step)
        self.velocity = 0.0                 # px/ms, positive = content moves up (value grows)
        self._fling_last = 0.0

    @staticmethod
    def _area_of(widget: QWidget) -> QAbstractScrollArea | None:
        w = widget
        while w is not None:
            if w.property("noDragScroll"):
                return None
            parent = w.parentWidget()
            if isinstance(parent, QAbstractScrollArea) and parent.viewport() is w and w.property("kinetic"):
                return parent
            w = parent
        return None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        t = event.type()
        if t not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease,
                     QEvent.Type.MouseButtonDblClick) or self.replaying or not isinstance(obj, QWidget):
            return False
        if t == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            area = self._area_of(obj)
            if area is None or area.verticalScrollBar().maximum() <= 0 and not self.fling.isActive():
                return False
            self.fling.stop()
            self.mode, self.target, self.area = "pending", obj, area
            self.start = event.globalPosition()
            self.start_value = area.verticalScrollBar().value()
            self.samples = [(time.monotonic() * 1000.0, self.start.y())]
            self.hold.start(int(self.HOLD_S * 1000))
            return True                      # held back until we know what the finger does
        if obj is not self.target or self.mode in ("", "pass"):
            if t == QEvent.Type.MouseButtonRelease and obj is self.target:
                self.mode, self.target = "", None
            return False
        pos = event.globalPosition()
        if t == QEvent.Type.MouseButtonDblClick:
            return True
        if t == QEvent.Type.MouseMove:
            dx, dy = pos.x() - self.start.x(), pos.y() - self.start.y()
            if self.mode == "pending":
                if max(abs(dx), abs(dy)) < self.SLOP_PX:
                    return True
                self.hold.stop()
                if abs(dx) > abs(dy) and self._horizontal_target():
                    self._replay_press()
                    self.mode = "pass"
                    return False             # this move goes to the slider
                self.mode = "scroll"
            self.samples.append((time.monotonic() * 1000.0, pos.y()))
            self.samples = self.samples[-6:]
            if self.area is not None:
                self.area.verticalScrollBar().setValue(int(round(self.start_value - dy)))
            return True
        # release
        self.hold.stop()
        mode, self.mode = self.mode, ""
        if mode == "pending":                # a tap: the widget gets press and release
            self._replay_press()
            self.target = None
            return False
        self.target = None
        self._start_fling()
        return True

    def _horizontal_target(self) -> bool:
        w = self.target
        while w is not None and w is not self.area:
            if isinstance(w, QAbstractSlider) and w.orientation() == Qt.Orientation.Horizontal:
                return True
            if w.property("hdrag"):
                return True
            w = w.parentWidget()
        return False

    def _on_hold(self) -> None:
        if self.mode == "pending":
            self._replay_press()
            self.mode = "pass"

    def _replay_press(self) -> None:
        target = self.target
        if target is None:
            return
        from PySide6.QtGui import QMouseEvent

        local = QPointF(target.mapFromGlobal(self.start.toPoint()))
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, local, self.start, Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.replaying = True
        try:
            QApplication.sendEvent(target, ev)
        except RuntimeError:                 # widget deleted meanwhile
            pass
        finally:
            self.replaying = False

    # ---------------------------------------------------------------- fling
    def _start_fling(self) -> None:
        if len(self.samples) < 2 or self.area is None:
            return
        (t0, y0), (t1, y1) = self.samples[0], self.samples[-1]
        if t1 - t0 <= 0 or time.monotonic() * 1000.0 - t1 > 80:
            return                           # finger stopped before lifting
        self.velocity = -(y1 - y0) / (t1 - t0)
        if abs(self.velocity) > 0.3:
            self._fling_last = time.monotonic() * 1000.0
            self._fling_pos = float(self.area.verticalScrollBar().value())
            self.fling.start()

    def _fling_step(self) -> None:
        area = self.area
        if area is None:
            self.fling.stop()
            return
        now = time.monotonic() * 1000.0
        dt = min(now - self._fling_last, 50.0)
        self._fling_last = now
        self._fling_pos += self.velocity * dt
        self.velocity *= math.exp(-self.FRICTION * dt)
        bar = area.verticalScrollBar()
        bar.setValue(int(round(self._fling_pos)))
        if abs(self.velocity) < 0.05 or bar.value() in (bar.minimum(), bar.maximum()):
            self.fling.stop()


def make_kinetic(area: QAbstractScrollArea) -> QAbstractScrollArea:
    """Finger scrolling anywhere on the area's content (:class:`DragScroller`; idempotent)."""
    vp = area.viewport()
    if vp.property("kinetic"):
        return area
    vp.setProperty("kinetic", True)
    DragScroller.install()
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
