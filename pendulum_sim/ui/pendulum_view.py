"""QPainter-based real-time pendulum visualisation.

World coordinates are metres with +y up; the camera maps them to pixels.
Mouse wheel zooms around the cursor, left-drag pans, double-click fits the view.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..control import wrap_angle
from ..physics import Kinematics
from .widgets import SEGMENT_COLORS


@dataclass
class ViewSettings:
    show_trails: bool = True
    show_com: bool = True
    show_labels: bool = True
    show_velocity: bool = True
    show_equilibrium: bool = True
    show_force: bool = True
    show_grid: bool = True
    show_hud: bool = True
    follow_cart: bool = False
    trail_length: int = 400       # samples kept per trail (one sample per frame)
    trail_all_joints: bool = False
    link_width_px: float = 9.0


@dataclass
class FrameData:
    kin: Kinematics
    angles: np.ndarray
    velocities: np.ndarray
    cart_velocity: float
    time: float
    applied: float
    max_output: float
    tip_force: float
    has_cart: bool
    track_length: float
    lengths: np.ndarray


class PendulumView(QWidget):
    zoomChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = ViewSettings()
        self.scale = 220.0               # pixels per metre
        self.camera = np.array([0.0, 0.35])
        self.frame: FrameData | None = None
        self.trails: list[deque] = []
        self._drag_origin: QPointF | None = None
        self._drag_camera = self.camera.copy()
        self.setMinimumSize(300, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # ---------------------------------------------------------------- data
    def set_frame(self, frame: FrameData) -> None:
        n = len(frame.angles)
        if len(self.trails) != n + 1:
            self.clear_trails()
            self.trails = [deque(maxlen=self.settings.trail_length) for _ in range(n + 1)]
        for k, trail in enumerate(self.trails):
            if trail.maxlen != self.settings.trail_length:
                self.trails[k] = trail = deque(trail, maxlen=self.settings.trail_length)
            trail.append(tuple(frame.kin.joints[k]))
        self.frame = frame
        if self.settings.follow_cart and frame.has_cart:
            self.camera[0] = frame.kin.cart[0]
        self.update()

    def clear_trails(self) -> None:
        for trail in self.trails:
            trail.clear()

    def fit(self) -> None:
        """Zoom so the full (upright) pendulum and some margin are visible."""
        total = float(self.frame.lengths.sum()) if self.frame is not None else 1.0
        h = max(self.height(), 1)
        w = max(self.width(), 1)
        self.scale = max(20.0, min(0.42 * h / max(total, 0.1), 0.45 * w / max(total, 0.1)))
        x = self.frame.kin.cart[0] if (self.frame is not None and self.frame.has_cart) else 0.0
        self.camera = np.array([x, 0.42 * total])
        self.zoomChanged.emit(self.scale)
        self.update()

    def set_zoom(self, scale: float) -> None:
        self.scale = max(10.0, min(5000.0, scale))
        self.update()

    # ------------------------------------------------------------ transforms
    def to_screen(self, p) -> QPointF:
        cx, cy = self.width() / 2.0, self.height() * 0.55
        return QPointF(cx + (p[0] - self.camera[0]) * self.scale, cy - (p[1] - self.camera[1]) * self.scale)

    def to_world(self, pt: QPointF) -> np.ndarray:
        cx, cy = self.width() / 2.0, self.height() * 0.55
        return np.array([(pt.x() - cx) / self.scale + self.camera[0], -(pt.y() - cy) / self.scale + self.camera[1]])

    # ---------------------------------------------------------- interaction
    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt API)
        steps = event.angleDelta().y() / 120.0
        if steps == 0:
            return
        anchor = self.to_world(event.position())
        self.scale = max(10.0, min(5000.0, self.scale * (1.15**steps)))
        new_anchor = self.to_world(event.position())
        self.camera += anchor - new_anchor
        self.zoomChanged.emit(self.scale)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position()
            self._drag_camera = self.camera.copy()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None:
            d = event.position() - self._drag_origin
            self.camera = self._drag_camera - np.array([d.x(), -d.y()]) / self.scale
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.fit()

    # --------------------------------------------------------------- paint
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        bg = pal.base().color()
        fg = pal.text().color()
        p.fillRect(self.rect(), bg)
        dark = bg.lightness() < 128
        if self.settings.show_grid:
            self._draw_grid(p, dark)
        f = self.frame
        if f is None:
            p.setPen(fg)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No simulation loaded")
            return
        self._draw_base(p, f, fg, dark)
        if self.settings.show_equilibrium:
            self._draw_equilibrium(p, f, fg)
        if self.settings.show_trails:
            self._draw_trails(p)
        self._draw_links(p, f, fg, dark)
        if self.settings.show_force:
            self._draw_force(p, f)
        if self.settings.show_hud:
            self._draw_hud(p, f, fg)
        self._draw_scale_bar(p, fg)
        p.end()

    def _draw_grid(self, p: QPainter, dark: bool) -> None:
        spacing = 0.1 if self.scale > 150 else 0.5 if self.scale > 40 else 1.0
        minor = QColor(255, 255, 255, 18) if dark else QColor(0, 0, 0, 14)
        major = QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 32)
        tl = self.to_world(QPointF(0, 0))
        br = self.to_world(QPointF(self.width(), self.height()))
        x = math.floor(tl[0] / spacing) * spacing
        while x <= br[0]:
            is_major = abs(round(x / (spacing * 5)) * spacing * 5 - x) < 1e-9
            p.setPen(QPen(major if is_major else minor, 1))
            sx = self.to_screen((x, 0)).x()
            p.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))
            x += spacing
        y = math.floor(br[1] / spacing) * spacing
        while y <= tl[1]:
            is_major = abs(round(y / (spacing * 5)) * spacing * 5 - y) < 1e-9
            p.setPen(QPen(major if is_major else minor, 1))
            sy = self.to_screen((0, y)).y()
            p.drawLine(QPointF(0, sy), QPointF(self.width(), sy))
            y += spacing

    def _draw_base(self, p: QPainter, f: FrameData, fg: QColor, dark: bool) -> None:
        metal = QColor("#7F8C8D")
        body = QColor("#34495E") if not dark else QColor("#5D6D7E")
        if f.has_cart:
            half = f.track_length / 2.0
            rail_y = -0.17
            p.setPen(QPen(metal, 3))
            p.drawLine(self.to_screen((-half - 0.15, rail_y)), self.to_screen((half + 0.15, rail_y)))
            for sx in (-half - 0.15, half + 0.15):  # end stops at the cart-centre limits
                p.setPen(QPen(QColor("#C0392B"), 4))
                p.drawLine(self.to_screen((sx, rail_y)), self.to_screen((sx, rail_y + 0.12)))
            # centre mark
            p.setPen(QPen(fg, 1, Qt.PenStyle.DotLine))
            p.drawLine(self.to_screen((0, rail_y - 0.04)), self.to_screen((0, rail_y + 0.04)))
            x = f.kin.cart[0]
            tl = self.to_screen((x - 0.15, -0.02))
            br = self.to_screen((x + 0.15, -0.14))
            p.setPen(QPen(fg, 1.5))
            p.setBrush(body)
            p.drawRoundedRect(QRectF(tl, br), 5, 5)
            p.setBrush(QColor("#2C3E50"))
            r = max(3.0, 0.025 * self.scale)
            for wx in (x - 0.09, x + 0.09):
                p.drawEllipse(self.to_screen((wx, rail_y + 0.0)), r, r)
        else:
            tl = self.to_screen((-0.12, -0.01))
            br = self.to_screen((0.12, -0.08))
            p.setPen(QPen(fg, 1.5))
            p.setBrush(body)
            p.drawRect(QRectF(tl, br))
            p.setPen(QPen(metal, 1))
            for k in range(7):  # ground hatch
                x0 = -0.12 + k * 0.04
                p.drawLine(self.to_screen((x0, -0.08)), self.to_screen((x0 - 0.03, -0.11)))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_equilibrium(self, p: QPainter, f: FrameData, fg: QColor) -> None:
        """Ghost of the upright equilibrium configuration."""
        ghost = QColor(fg)
        ghost.setAlpha(90)
        p.setPen(QPen(ghost, 1.5, Qt.PenStyle.DashLine))
        base = f.kin.joints[0]
        top = base + np.array([0.0, float(f.lengths.sum())])
        p.drawLine(self.to_screen(base), self.to_screen(top))
        y = 0.0
        for length in f.lengths:
            y += length
            p.drawEllipse(self.to_screen((base[0], y)), 3, 3)

    def _draw_trails(self, p: QPainter) -> None:
        indices = range(1, len(self.trails)) if self.settings.trail_all_joints else [len(self.trails) - 1]
        for k in indices:
            pts = self.trails[k]
            if len(pts) < 2:
                continue
            color = QColor(SEGMENT_COLORS[(k - 1) % len(SEGMENT_COLORS)])
            pts_list = list(pts)
            chunks = 8
            size = max(2, len(pts_list) // chunks + 1)
            for c in range(0, len(pts_list) - 1, size - 1):
                part = pts_list[c : c + size]
                if len(part) < 2:
                    continue
                color.setAlpha(int(25 + 200 * (c + len(part)) / len(pts_list)))
                p.setPen(QPen(color, 1.6))
                p.drawPolyline(QPolygonF([self.to_screen(pt) for pt in part]))

    def _draw_links(self, p: QPainter, f: FrameData, fg: QColor, dark: bool) -> None:
        width = self.settings.link_width_px
        outline = QColor("#1B2631") if not dark else QColor("#ECF0F1")
        joints = f.kin.joints
        for i in range(len(f.angles)):
            a, b = self.to_screen(joints[i]), self.to_screen(joints[i + 1])
            p.setPen(QPen(outline, width + 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, b)
            p.setPen(QPen(QColor(SEGMENT_COLORS[i]), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, b)
        # joints (pivot first)
        for k, j in enumerate(joints[:-1]):
            pt = self.to_screen(j)
            p.setPen(QPen(outline, 2))
            p.setBrush(QColor("#F7F9F9"))
            r = width * (0.75 if k == 0 else 0.6)
            p.drawEllipse(pt, r, r)
            p.setBrush(outline)
            p.drawEllipse(pt, 2, 2)
        tip = self.to_screen(joints[-1])
        p.setBrush(QColor(SEGMENT_COLORS[len(f.angles) - 1]))
        p.drawEllipse(tip, width * 0.45, width * 0.45)
        p.setBrush(Qt.BrushStyle.NoBrush)

        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() * 0.9))
        p.setFont(font)
        for i in range(len(f.angles)):
            com = self.to_screen(f.kin.coms[i])
            if self.settings.show_com:
                self._draw_com_marker(p, com, width * 0.7)
            if self.settings.show_velocity:
                self._draw_velocity(p, com, f.angles[i], f.velocities[i], QColor(SEGMENT_COLORS[i]))
            if self.settings.show_labels:
                self._draw_angle(p, f, i, fg)

    @staticmethod
    def _draw_com_marker(p: QPainter, c: QPointF, r: float) -> None:
        """Classic centre-of-mass symbol: a circle with alternating quadrants."""
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setPen(QPen(QColor("#1B2631"), 1))
        p.setBrush(QColor("white"))
        p.drawEllipse(rect)
        p.setBrush(QColor("#1B2631"))
        p.drawPie(rect, 0, 90 * 16)
        p.drawPie(rect, 180 * 16, 90 * 16)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_velocity(self, p: QPainter, c: QPointF, angle: float, omega: float, color: QColor) -> None:
        """Curved arrow around the centre of mass; sweep ∝ angular velocity."""
        if abs(omega) < 0.02:
            return
        radius = 22.0
        sweep = max(-300.0, min(300.0, -math.degrees(omega) * 0.25))  # clockwise for ω > 0
        start = 90.0 - math.degrees(angle) + 90.0      # perpendicular to the segment
        rect = QRectF(c.x() - radius, c.y() - radius, 2 * radius, 2 * radius)
        pen = QPen(color, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, int(start * 16), int(sweep * 16))
        end = math.radians(start + sweep)
        tip = QPointF(c.x() + radius * math.cos(end), c.y() - radius * math.sin(end))
        # tangent direction at the arrow end
        direction = -1.0 if sweep < 0 else 1.0
        tx, ty = -math.sin(end) * direction, -math.cos(end) * direction
        size = 7.0
        left = QPointF(tip.x() - size * (tx + 0.5 * ty), tip.y() - size * (ty - 0.5 * tx))
        right = QPointF(tip.x() - size * (tx - 0.5 * ty), tip.y() - size * (ty + 0.5 * tx))
        p.setBrush(color)
        p.drawPolygon(QPolygonF([tip, left, right]))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_angle(self, p: QPainter, f: FrameData, i: int, fg: QColor) -> None:
        joint = self.to_screen(f.kin.joints[i])
        theta = math.degrees(wrap_angle(float(f.angles[i])))
        radius = 30.0 + 6.0 * i
        rect = QRectF(joint.x() - radius, joint.y() - radius, 2 * radius, 2 * radius)
        color = QColor(SEGMENT_COLORS[i])
        p.setPen(QPen(color, 1.2, Qt.PenStyle.DotLine))
        p.drawLine(joint, QPointF(joint.x(), joint.y() - radius - 8))
        p.setPen(QPen(color, 1.6))
        p.drawArc(rect, 90 * 16, int(-theta * 16))
        mid = math.radians(90.0 - theta / 2.0)
        label = QPointF(joint.x() + (radius + 14) * math.cos(mid) - 18, joint.y() - (radius + 14) * math.sin(mid) + 4)
        p.setPen(fg)
        p.drawText(label, f"θ{i + 1} {theta:+.1f}°")

    def _draw_force(self, p: QPainter, f: FrameData) -> None:
        if f.max_output <= 0:
            return
        frac = max(-1.0, min(1.0, f.applied / f.max_output))
        color = QColor("#C0392B") if abs(frac) > 0.98 else QColor("#D35400")
        if f.has_cart:
            origin = self.to_screen((f.kin.cart[0], -0.08))
            end = QPointF(origin.x() + frac * 110.0, origin.y())
            self._arrow(p, origin, end, color, 3.0)
        elif abs(frac) > 1e-3:
            base = self.to_screen(f.kin.joints[0])
            r = 18.0
            rect = QRectF(base.x() - r, base.y() - r, 2 * r, 2 * r)
            p.setPen(QPen(color, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            # positive torque rotates towards +θ (clockwise on screen)
            p.drawArc(rect, 90 * 16, int(-frac * 270 * 16))
        if f.tip_force != 0.0:
            tip = self.to_screen(f.kin.tip)
            start = QPointF(tip.x() - math.copysign(60.0, f.tip_force), tip.y())
            self._arrow(p, start, tip, QColor("#E74C3C"), 3.0)

    @staticmethod
    def _arrow(p: QPainter, a: QPointF, b: QPointF, color: QColor, width: float) -> None:
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy)
        if length < 2:
            return
        ux, uy = dx / length, dy / length
        p.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(a, b)
        size = 9.0
        path = QPainterPath(b)
        path.lineTo(b.x() - size * ux + 0.6 * size * uy, b.y() - size * uy - 0.6 * size * ux)
        path.lineTo(b.x() - size * ux - 0.6 * size * uy, b.y() - size * uy + 0.6 * size * ux)
        path.closeSubpath()
        p.fillPath(path, QBrush(color))

    def _draw_hud(self, p: QPainter, f: FrameData, fg: QColor) -> None:
        lines = [f"t = {f.time:7.2f} s"]
        for i, (a, w) in enumerate(zip(f.angles, f.velocities)):
            lines.append(f"θ{i + 1} = {math.degrees(wrap_angle(float(a))):+7.2f}°   ω{i + 1} = {math.degrees(w):+8.1f}°/s")
        if f.has_cart:
            lines.append(f"x = {f.kin.cart[0]:+6.3f} m   ẋ = {f.cart_velocity:+6.2f} m/s")
            lines.append(f"F = {f.applied:+7.2f} N")
        else:
            lines.append(f"τ = {f.applied:+7.3f} N·m")
        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSizeF(max(8.0, self.font().pointSizeF() * 0.9))
        p.setFont(font)
        metrics = p.fontMetrics()
        h = metrics.height()
        w = max(metrics.horizontalAdvance(s) for s in lines) + 16
        box = QColor(self.palette().window().color())
        box.setAlpha(215)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(box)
        p.drawRoundedRect(QRectF(8, 8, w, h * len(lines) + 10), 6, 6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(fg)
        for k, s in enumerate(lines):
            p.drawText(QPointF(16, 8 + 5 + h * (k + 1) - metrics.descent()), s)

    def _draw_scale_bar(self, p: QPainter, fg: QColor) -> None:
        candidates = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]
        length = next((c for c in candidates if c * self.scale >= 60), 5.0)
        px = length * self.scale
        y = self.height() - 16
        p.setPen(QPen(fg, 2))
        p.drawLine(QPointF(16, y), QPointF(16 + px, y))
        p.drawLine(QPointF(16, y - 4), QPointF(16, y + 4))
        p.drawLine(QPointF(16 + px, y - 4), QPointF(16 + px, y + 4))
        label = f"{length * 100:.0f} cm" if length < 1 else f"{length:.0f} m"
        p.drawText(QPointF(22 + px, y + 4), label)
