"""Touch-enabled pendulum view for the mobile interface.

All drawing is inherited unchanged from :class:`~pendulum_sim.ui.pendulum_view.PendulumView`;
this subclass only replaces the mouse interaction with touch gestures:

=====================  =====================================================
Gesture                Effect
=====================  =====================================================
tap                    tip push (0.1 s pulse) towards the tapped side
one-finger drag        continuous tip force pulling towards the finger
                       ("rubber band"), proportional to the horizontal offset
two-finger drag        pan the camera
pinch                  zoom about the centre of the two fingers
=====================  =====================================================

With a mouse (desktop preview of the mobile UI) the left button behaves like a
single finger and the wheel zooms, so the shell can be tried without a device.

The view never touches the simulator: it emits :attr:`tapPush` (direction ±1)
and :attr:`dragOffset` (horizontal finger–tip offset divided by the total
pendulum length, 0 when released). The window turns these into forces.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen

from ..ui.pendulum_view import PendulumView

TAP_SLOP_PX = 12.0       # finger movement below this still counts as a tap
TAP_MAX_S = 0.35         # longer presses are not taps
DRAG_COLOR = QColor("#E74C3C")


class TouchPendulumView(PendulumView):
    tapPush = Signal(int)          # −1 / +1
    dragOffset = Signal(float)     # (finger_x − tip_x) / total length; 0.0 on release

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self._one: dict | None = None        # single-finger gesture state
        self._two: dict | None = None        # two-finger gesture state
        self._drag_point: QPointF | None = None

    # ------------------------------------------------------------ geometry
    def tip_world(self) -> np.ndarray | None:
        if self.frame is None:
            return None
        return np.asarray(self.frame.kin.joints[-1], dtype=float)

    def _total_length(self) -> float:
        return float(self.frame.lengths.sum()) if self.frame is not None else 1.0

    # ---------------------------------------------------- one-finger logic
    def one_down(self, pos: QPointF) -> None:
        self._one = {"start": QPointF(pos), "t": time.monotonic(), "dragging": False}

    def one_move(self, pos: QPointF) -> None:
        g = self._one
        if g is None:
            return
        d = pos - g["start"]
        if not g["dragging"] and math.hypot(d.x(), d.y()) > TAP_SLOP_PX:
            g["dragging"] = True
        if g["dragging"]:
            tip = self.tip_world()
            if tip is not None:
                offset = (self.to_world(pos)[0] - tip[0]) / max(self._total_length(), 1e-6)
                self._drag_point = QPointF(pos)
                self.dragOffset.emit(float(offset))
                self.update()

    def one_up(self, pos: QPointF) -> None:
        g, self._one = self._one, None
        if g is None:
            return
        if g["dragging"]:
            self._end_drag()
        elif time.monotonic() - g["t"] <= TAP_MAX_S:
            tip = self.tip_world()
            if tip is not None:
                self.tapPush.emit(1 if self.to_world(pos)[0] >= tip[0] else -1)

    def one_cancel(self) -> None:
        if self._one is not None and self._one["dragging"]:
            self._end_drag()
        self._one = None

    def _end_drag(self) -> None:
        self._drag_point = None
        self.dragOffset.emit(0.0)
        self.update()

    # ---------------------------------------------------- two-finger logic
    def two_begin(self, a: QPointF, b: QPointF) -> None:
        self.one_cancel()
        self._two = {"centre": (a + b) / 2.0, "dist": max(_dist(a, b), 1.0),
                     "scale": self.scale, "camera": self.camera.copy()}

    def two_move(self, a: QPointF, b: QPointF) -> None:
        g = self._two
        if g is None:
            self.two_begin(a, b)
            return
        # Restore the start camera, zoom about the start centre, then pan by the centre motion.
        self.scale, self.camera = g["scale"], g["camera"].copy()
        anchor = self.to_world(g["centre"])
        self.scale = max(10.0, min(5000.0, g["scale"] * max(_dist(a, b), 1.0) / g["dist"]))
        self.camera += anchor - self.to_world(g["centre"])
        centre = (a + b) / 2.0
        shift = centre - g["centre"]
        self.camera -= np.array([shift.x(), -shift.y()]) / self.scale
        self.zoomChanged.emit(self.scale)
        self.update()

    def two_end(self) -> None:
        self._two = None

    # ------------------------------------------------------------ Qt events
    def event(self, ev) -> bool:  # noqa: D401 - Qt API
        t = ev.type()
        if t in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd,
                 QEvent.Type.TouchCancel):
            self._handle_touch(ev)
            ev.accept()
            return True
        return super().event(ev)

    def _handle_touch(self, ev) -> None:
        t = ev.type()
        if t == QEvent.Type.TouchCancel:
            self.one_cancel()
            self.two_end()
            return
        released = QEvent.Type.TouchEnd == t
        active = [p for p in ev.points() if p.state() != p.State.Released] if not released else []
        if len(active) >= 2:
            self.two_move(active[0].position(), active[1].position())
            return
        if self._two is not None:            # went from two fingers to fewer: end the pinch and
            self.two_end()                   # ignore the remaining finger until all are lifted
            return
        pts = ev.points()
        if not pts:
            return
        p = pts[0]
        if t == QEvent.Type.TouchBegin:
            self.one_down(p.position())
        elif released or p.state() == p.State.Released:
            self.one_up(p.position())
        else:
            self.one_move(p.position())

    # Mouse = one finger (desktop preview). Wheel zoom and painting come from the base class.
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.one_down(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.one_move(event.position())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.one_up(event.position())

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pass  # a double tap must not trigger the base class's "fit"

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        tip = self.tip_world()
        if self._drag_point is None or tip is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(DRAG_COLOR, 3, Qt.PenStyle.DashLine)
        p.setPen(pen)
        a = self.to_screen(tip)
        p.drawLine(a, self._drag_point)
        p.setBrush(DRAG_COLOR)
        p.drawEllipse(self._drag_point, 10, 10)
        p.end()


def _dist(a: QPointF, b: QPointF) -> float:
    d = a - b
    return math.hypot(d.x(), d.y())
