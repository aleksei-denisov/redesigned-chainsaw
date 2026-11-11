"""Comparison variants of the spike that run Python on the render thread.

The spike's default rendering uses plain QML items and ``Shape`` paths, where the render
thread never calls Python. These three items exist to measure the alternatives the
plan asks about:

* :class:`PendulumNode` – the pendulum as a custom scene-graph item (``QQuickItem`` +
  ``QSGGeometry``), geometry written in ``updatePaintNode``;
* :class:`LineNode` – the graph traces as ``QSGGeometry`` line strips (vertices written
  through the geometry's buffer in one NumPy assignment, or point by point as fallback);
* :class:`PaintedGraph` – the traces with ``QPainter`` in a ``QQuickPaintedItem``.

``updatePaintNode`` / ``paint`` are called by Qt on the render thread while the GUI
thread is blocked, so they need the interpreter lock there.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtQuick import (
    QQuickItem,
    QQuickPaintedItem,
    QSGFlatColorMaterial,
    QSGGeometry,
    QSGGeometryNode,
    QSGNode,
)

try:
    from shiboken6 import VoidPtr as _VoidPtr
except ImportError:  # pragma: no cover
    _VoidPtr = None

BRIDGES: dict[str, object] = {}        # "sim", "graph": set by the app before the QML loads
COLORS = {"cart": "#2F5BFF", "track": "#101828", "links": ["#2F5BFF", "#1D3FCC", "#0E9384"],
          "trace1": "#B54708", "trace2": "#2F5BFF"}
NOTES: dict[str, str] = {}             # which code path was used (shown in the report)


def _write(geometry: QSGGeometry, xs, ys) -> None:
    """Fill a Point2D geometry from coordinate sequences."""
    n = len(xs)
    if _VoidPtr is not None:
        try:
            buf = np.frombuffer(_VoidPtr(geometry.vertexData(), 8 * n, True), np.float32).reshape(n, 2)
            buf[:, 0] = xs
            buf[:, 1] = ys
            NOTES["geometry"] = "vertex buffer written in one NumPy assignment"
            return
        except Exception as exc:  # noqa: BLE001 - fall back to the documented API
            NOTES["geometry"] = f"point-by-point (buffer access failed: {type(exc).__name__})"
    vertices = geometry.vertexDataAsPoint2D()
    for i in range(n):
        vertices[i].set(float(xs[i]), float(ys[i]))
    geometry.setVertexDataAsPoint2D(vertices)


class _GeometryItem(QQuickItem):
    """Shared plumbing: flat-coloured geometry nodes under one root node."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._keep: list[object] = []          # Python references to the scene-graph objects

    @Slot()
    def refresh(self) -> None:
        self.update()

    def updatePaintNode(self, old, data):  # noqa: N802 - Qt API, called on the render thread
        try:
            return self._update(old)
        except Exception as exc:  # noqa: BLE001 - reported in the benchmark, keeps the old node
            NOTES[type(self).__name__] = f"updatePaintNode failed: {type(exc).__name__}: {exc}"
            return old

    def _update(self, old):
        raise NotImplementedError

    def _node(self, root: QSGNode, color: str, vertices: int, mode, line_width: float = 1.0) -> QSGGeometry:
        node = QSGGeometryNode()
        geometry = QSGGeometry(QSGGeometry.defaultAttributes_Point2D(), vertices)
        geometry.setDrawingMode(mode)
        geometry.setLineWidth(line_width)
        node.setGeometry(geometry)
        node.setFlag(QSGNode.Flag.OwnsGeometry)
        material = QSGFlatColorMaterial()
        material.setColor(QColor(color))
        node.setMaterial(material)
        node.setFlag(QSGNode.Flag.OwnsMaterial)
        root.appendChildNode(node)
        self._keep += [node, geometry, material]
        return geometry


def _quad(ax: float, ay: float, bx: float, by: float, half: float) -> tuple[list[float], list[float]]:
    """Triangle-strip corners of a thick line from a to b."""
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length * half, dx / length * half
    return [ax + nx, ax - nx, bx + nx, bx - nx], [ay + ny, ay - ny, by + ny, by - ny]


class PendulumNode(_GeometryItem):
    """Track, cart and links as scene-graph geometry (same layout as PendulumItems.qml)."""

    def _update(self, old):
        sim = BRIDGES.get("sim")
        if sim is None:
            return old
        root = old
        if root is None:
            root = QSGNode()
            self._keep = [root]
            strip = QSGGeometry.DrawingMode.DrawTriangleStrip
            self._track = self._node(root, COLORS["track"], 4, strip)
            self._cart = self._node(root, COLORS["cart"], 4, strip)
            self._links = [self._node(root, COLORS["links"][i], 4, strip) for i in range(sim.n)]
        w, h = self.width(), self.height()
        lengths = sim._lengths
        total = sum(lengths)
        scale = max(20.0, min(0.42 * h / total, 0.45 * w / total))
        cy = h / 2 + 0.42 * total * scale                 # screen y of world y = 0
        cart_x, angles = sim.state()
        cx = w / 2 + cart_x * scale
        half_track = (sim.config.plant.track_length / 2 + 0.15) * scale
        _write(self._track, *_quad(w / 2 - half_track, cy + 0.17 * scale, w / 2 + half_track, cy + 0.17 * scale, 1.5))
        _write(self._cart, *_quad(cx - 0.15 * scale, cy + 0.08 * scale, cx + 0.15 * scale, cy + 0.08 * scale,
                                  0.06 * scale))
        jx, jy = cx, cy
        for i, geometry in enumerate(self._links):
            length = lengths[i] * scale
            nx, ny = jx + length * math.sin(angles[i]), jy - length * math.cos(angles[i])
            _write(geometry, *_quad(jx, jy, nx, ny, 5.0))
            jx, jy = nx, ny
        for obj in self._keep:
            if isinstance(obj, QSGGeometryNode):
                obj.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)
        return root


class LineNode(_GeometryItem):
    """The two graph traces as line strips."""

    def _update(self, old):
        graph = BRIDGES.get("graph")
        if graph is None:
            return old
        curves = graph.last_curves
        if len(curves) != 2:
            return old
        root = old
        if root is None:
            root = QSGNode()
            self._keep = [root]
            strip = QSGGeometry.DrawingMode.DrawLineStrip
            self._traces = [self._node(root, COLORS["trace1"], 2, strip, 2.0),
                            self._node(root, COLORS["trace2"], 2, strip, 2.0)]
        for geometry, (xs, ys) in zip(self._traces, curves):
            if geometry.vertexCount() != len(xs):
                geometry.allocate(len(xs))
            _write(geometry, xs, ys)
        for obj in self._keep:
            if isinstance(obj, QSGGeometryNode):
                obj.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)
        return root


class PaintedGraph(QQuickPaintedItem):
    """The two traces drawn with QPainter into a framebuffer object."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRenderTarget(QQuickPaintedItem.RenderTarget.FramebufferObject)
        self.setAntialiasing(True)

    @Slot()
    def refresh(self) -> None:
        self.update()

    def paint(self, painter: QPainter) -> None:  # called on the render thread
        graph = BRIDGES.get("graph")
        if graph is None:
            return
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for poly, color in zip(graph.polygons(), (COLORS["trace1"], COLORS["trace2"])):
                pen = QPen(QColor(color), 2.0)
                pen.setCosmetic(True)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPolyline(poly)
        except Exception as exc:  # noqa: BLE001 - reported in the benchmark
            NOTES["PaintedGraph"] = f"paint failed: {type(exc).__name__}: {exc}"
