"""Touch-friendly presentation of the *unchanged* desktop widgets.

Rather than re-implementing the parameter panels for touch, the mobile app keeps
the desktop panels and changes how standard widgets are sized and hit-tested:

* :class:`TouchStyle` – a ``QProxyStyle`` over Fusion that gives buttons, combo
  boxes, line edits, tabs and spin boxes a finger-sized minimum height, enlarges
  check boxes and scroll bars, and lays out the spin-box buttons as wide, side by
  side ``−`` / ``+`` targets instead of two tiny stacked arrows;
* when a widget is first polished, spin boxes switch to ``−``/``+`` symbols,
  panels and lists get kinetic (flick) scrolling and dialogs get a
  :class:`DialogFitter` that keeps them inside the screen (some dialogs have
  desktop minimum widths).

Everything happens once per widget at polish time; nothing runs per frame.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QDialog,
    QProxyStyle,
    QScrollArea,
    QScroller,
    QStyle,
    QStyleFactory,
    QWidget,
)

TOUCH_PX = 44            # minimum height of interactive controls (≈ 7 mm at 160 dpi)
SPIN_BUTTON_PX = 44      # width of each spin-box button
INDICATOR_PX = 26        # check box / radio button indicator
SCROLLBAR_PX = 14

_TALL = {
    QStyle.ContentsType.CT_PushButton,
    QStyle.ContentsType.CT_ToolButton,
    QStyle.ContentsType.CT_ComboBox,
    QStyle.ContentsType.CT_LineEdit,
    QStyle.ContentsType.CT_TabBarTab,
    QStyle.ContentsType.CT_SpinBox,
}
_ROW = {QStyle.ContentsType.CT_CheckBox, QStyle.ContentsType.CT_RadioButton}


class TouchStyle(QProxyStyle):
    def __init__(self, base: str = "Fusion") -> None:
        super().__init__(QStyleFactory.create(base))

    def pixelMetric(self, metric, option=None, widget=None) -> int:  # noqa: N802 (Qt API)
        pm = QStyle.PixelMetric
        if metric in (pm.PM_IndicatorWidth, pm.PM_IndicatorHeight,
                      pm.PM_ExclusiveIndicatorWidth, pm.PM_ExclusiveIndicatorHeight):
            return INDICATOR_PX
        if metric == pm.PM_ScrollBarExtent:
            return SCROLLBAR_PX
        if metric in (pm.PM_SliderThickness, pm.PM_SliderLength):
            return max(super().pixelMetric(metric, option, widget), 28)
        return super().pixelMetric(metric, option, widget)

    def sizeFromContents(self, ct, option, size, widget=None):  # noqa: N802
        s = super().sizeFromContents(ct, option, size, widget)
        if ct in _TALL:
            s.setHeight(max(s.height(), TOUCH_PX))
        elif ct in _ROW:
            s.setHeight(max(s.height(), TOUCH_PX - 8))
        if ct == QStyle.ContentsType.CT_SpinBox:
            s.setWidth(s.width() + 2 * SPIN_BUTTON_PX - 20)   # room for the wider buttons
        return s

    def subControlRect(self, cc, option, sc, widget=None) -> QRect:  # noqa: N802
        if cc == QStyle.ComplexControl.CC_SpinBox and getattr(option, "buttonSymbols", None) != \
                QAbstractSpinBox.ButtonSymbols.NoButtons:
            r = option.rect
            bw = min(SPIN_BUTTON_PX, r.width() // 4)
            sub = QStyle.SubControl
            if sc == sub.SC_SpinBoxUp:          # [ value | − | + ]
                return QRect(r.right() - bw + 1, r.top(), bw, r.height())
            if sc == sub.SC_SpinBoxDown:
                return QRect(r.right() - 2 * bw + 1, r.top(), bw, r.height())
            if sc == sub.SC_SpinBoxEditField:
                return QRect(r.left() + 3, r.top() + 2, r.width() - 2 * bw - 6, r.height() - 4)
            if sc == sub.SC_SpinBoxFrame:
                return QRect(r)
        return super().subControlRect(cc, option, sc, widget)

    def styleHint(self, hint, option=None, widget=None, data=None) -> int:  # noqa: N802
        if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
            return 10**6       # no hover on touch screens: avoid tool tips on long press
        return super().styleHint(hint, option, widget, data)

    def polish(self, arg):  # noqa: D401 - Qt API (overloaded: QWidget / QPalette / QApplication)
        if isinstance(arg, QWidget):
            prepare_touch_widget(arg)
        return super().polish(arg)


_fitter: "DialogFitter | None" = None


def prepare_touch_widget(widget: QWidget) -> None:
    """One-time touch adjustments for a single widget (called from :meth:`TouchStyle.polish`)."""
    global _fitter
    if isinstance(widget, QAbstractSpinBox):
        if widget.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.UpDownArrows:
            widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
    elif isinstance(widget, (QScrollArea, QAbstractItemView)):
        # Not QGraphicsView: the graphs use one-finger drags for panning.
        QScroller.grabGesture(widget.viewport(), QScroller.ScrollerGestureType.TouchGesture)
    elif isinstance(widget, QDialog) and not widget.property("touchFitted"):
        widget.setProperty("touchFitted", True)
        if _fitter is None:
            _fitter = DialogFitter()
        widget.installEventFilter(_fitter)


class DialogFitter(QObject):
    """Shrink and centre a dialog that would not fit on the screen (installed per dialog)."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(obj, QDialog):
            fit_to_screen(obj)
        return False


def fit_to_screen(dialog: QWidget, margin: int = 8) -> None:
    screen = dialog.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    max_w, max_h = avail.width() - 2 * margin, avail.height() - 2 * margin
    if dialog.minimumWidth() > max_w:
        dialog.setMinimumWidth(max_w)
    if dialog.minimumHeight() > max_h:
        dialog.setMinimumHeight(max_h)
    w = min(max(dialog.width(), dialog.sizeHint().width()), max_w)
    h = min(max(dialog.height(), dialog.sizeHint().height()), max_h)
    dialog.resize(w, h)
    geo = dialog.frameGeometry()
    geo.moveCenter(avail.center())
    dialog.move(geo.topLeft())


def apply_touch_style(app) -> TouchStyle:
    style = TouchStyle()
    app.setStyle(style)
    app.setAttribute(Qt.ApplicationAttribute.AA_SynthesizeMouseForUnhandledTouchEvents, True)
    return style
