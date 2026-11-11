"""Comparison variants of the spike that run Python on the render thread.

The spike's default rendering uses plain QML items and ``Shape`` paths, where the render
thread never calls Python. These three items exist to measure the alternatives the
plan asks about:

* :class:`PendulumNode` – the pendulum as a custom scene-graph item (``QQuickItem`` +
  ``QSGGeometry``), geometry written in ``updatePaintNode``;
* :class:`LineNode` – a graph trace as a ``QSGGeometry`` line strip (vertices written
  through the geometry's buffer in one NumPy assignment, or point by point as fallback);
* :class:`PaintedGraph` – the traces with ``QPainter`` in a ``QQuickPaintedItem``.

``updatePaintNode`` / ``paint`` are called by Qt on the render thread while the GUI
thread is blocked, so they need the interpreter lock there.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Property, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtQuick import (
    QQuickItem,
    QQuickPaintedItem,
    QSGFlatColorMaterial,
    QSGGeometry,
    QSGGeometryNode,
    QSGNode,
    QSGVertexColorMaterial,
)

try:
    from shiboken6 import VoidPtr as _VoidPtr
except ImportError:  # pragma: no cover
    _VoidPtr = None

BRIDGES: dict[str, object] = {}        # "sim", "graph": set by the app before the QML loads
COLORS = {"cart": "#2F5BFF", "track": "#101828", "links": ["#2F5BFF", "#1D3FCC", "#0E9384"],
          "trace1": "#B54708", "trace2": "#2F5BFF"}
NOTES: dict[str, str] = {}             # which code path was used (shown in the report)

POINT = np.dtype([("x", np.float32), ("y", np.float32)])
COLORED_POINT = np.dtype([("x", np.float32), ("y", np.float32), ("rgba", np.uint8, 4)])


def vertex_array(geometry: QSGGeometry, dtype: np.dtype) -> np.ndarray | None:
    """The geometry's vertex buffer as a writable NumPy array (``None`` if not accessible).

    ``vertexData()`` arrives as a read-only ``VoidPtr``; a writable one is made from its
    address (or, failing that, a ctypes array at that address).
    """
    if geometry.sizeOfVertex() != dtype.itemsize:
        return None
    size = dtype.itemsize * geometry.vertexCount()
    try:
        address = int(geometry.vertexData())
        if _VoidPtr is not None:
            array = np.frombuffer(_VoidPtr(address, size, True), dtype)
            if array.flags.writeable:
                return array
        import ctypes

        return np.frombuffer((ctypes.c_char * size).from_address(address), dtype)
    except Exception as exc:  # noqa: BLE001 - the caller falls back or reports
        NOTES["vertex buffer"] = f"not accessible from NumPy ({type(exc).__name__}: {exc})"
        return None


class _GeometryItem(QQuickItem):
    """One flat geometry node per item, as in Qt for Python's custom-geometry example.

    The attribute set must outlive the geometry (the geometry only references it), and the
    node, geometry and material belong to the scene graph once handed over; the Python
    references are kept so the wrappers stay valid between frames.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._attributes = None
        self._geometry: QSGGeometry | None = None
        self._material = None
        self._node: QSGGeometryNode | None = None

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

    def _create(self, attributes, material, vertices: int, mode, line_width: float = 1.0) -> QSGGeometryNode:
        self._attributes = attributes
        self._geometry = QSGGeometry(self._attributes, vertices)
        self._geometry.setDrawingMode(mode)
        self._geometry.setLineWidth(line_width)
        self._material = material
        self._node = QSGGeometryNode()
        self._node.setGeometry(self._geometry)
        self._node.setFlag(QSGNode.Flag.OwnsGeometry)
        self._node.setMaterial(self._material)
        self._node.setFlag(QSGNode.Flag.OwnsMaterial)
        return self._node


def _quad(ax: float, ay: float, bx: float, by: float, half: float) -> list[tuple[float, float]]:
    """Two triangles (six corners) of a thick line from a to b."""
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length * half, dx / length * half
    a1, a2, b1, b2 = (ax + nx, ay + ny), (ax - nx, ay - ny), (bx + nx, by + ny), (bx - nx, by - ny)
    return [a1, a2, b1, b1, a2, b2]


def _rgba(color: str) -> tuple[int, int, int, int]:
    c = QColor(color)
    return c.red(), c.green(), c.blue(), 255


class PendulumNode(_GeometryItem):
    """Track, cart and links as one coloured triangle list (same layout as PendulumItems.qml)."""

    def _update(self, old):
        sim = BRIDGES.get("sim")
        if sim is None:
            return old
        n = sim.n
        if old is None or self._node is None:
            self._create(QSGGeometry.defaultAttributes_ColoredPoint2D(), QSGVertexColorMaterial(),
                         6 * (2 + n), QSGGeometry.DrawingMode.DrawTriangles)
            self._colors = np.repeat(np.array(
                [_rgba(COLORS["track"]), _rgba(COLORS["cart"])] + [_rgba(COLORS["links"][i % 3]) for i in range(n)],
                dtype=np.uint8), 6, axis=0)
        vertices = vertex_array(self._geometry, COLORED_POINT)
        if vertices is None:
            NOTES.setdefault("PendulumNode", "nothing drawn: the vertex buffer is not accessible from NumPy")
            return self._node
        w, h = self.width(), self.height()
        lengths = sim._lengths
        total = sum(lengths)
        scale = max(20.0, min(0.42 * h / total, 0.45 * w / total))
        cy = h / 2 + 0.42 * total * scale                 # screen y of world y = 0
        cart_x, angles = sim.state()
        cx = w / 2 + cart_x * scale
        half_track = (sim.config.plant.track_length / 2 + 0.15) * scale
        points = _quad(w / 2 - half_track, cy + 0.17 * scale, w / 2 + half_track, cy + 0.17 * scale, 1.5)
        points += _quad(cx - 0.15 * scale, cy + 0.08 * scale, cx + 0.15 * scale, cy + 0.08 * scale, 0.06 * scale)
        jx, jy = cx, cy
        for i in range(n):
            length = lengths[i] * scale
            nx, ny = jx + length * math.sin(angles[i]), jy - length * math.cos(angles[i])
            points += _quad(jx, jy, nx, ny, 5.0)
            jx, jy = nx, ny
        xy = np.array(points, dtype=np.float32)
        vertices["x"] = xy[:, 0]
        vertices["y"] = xy[:, 1]
        vertices["rgba"] = self._colors
        NOTES["PendulumNode"] = "vertex buffer written in one NumPy assignment"
        self._node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)
        return self._node


class LineNode(_GeometryItem):
    """One graph trace (``trace`` 0 or 1 of the graph bridge) as a line strip in ``color``."""

    traceChanged = Signal()
    colorChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._trace = 0
        self._color = QColor(COLORS["trace1"])

    def _set_trace(self, index: int) -> None:
        if index != self._trace:
            self._trace = index
            self.traceChanged.emit()
            self.update()

    def _set_color(self, color: QColor) -> None:
        if color != self._color:
            self._color = QColor(color)
            self.colorChanged.emit()
            self.update()

    trace = Property(int, lambda self: self._trace, _set_trace, notify=traceChanged)
    color = Property(QColor, lambda self: self._color, _set_color, notify=colorChanged)

    def _update(self, old):
        graph = BRIDGES.get("graph")
        curves = graph.last_curves if graph is not None else []
        if not 0 <= self._trace < len(curves):
            return old
        xs, ys = curves[self._trace]
        if old is None or self._node is None:
            self._create(QSGGeometry.defaultAttributes_Point2D(), QSGFlatColorMaterial(), len(xs),
                         QSGGeometry.DrawingMode.DrawLineStrip, 2.0)
        if self._material.color() != self._color:
            self._material.setColor(self._color)
            self._node.markDirty(QSGNode.DirtyStateBit.DirtyMaterial)
        if self._geometry.vertexCount() != len(xs):
            self._geometry.allocate(len(xs))
        vertices = vertex_array(self._geometry, POINT)
        if vertices is not None:
            vertices["x"] = xs
            vertices["y"] = ys
            NOTES["LineNode"] = "vertex buffer written in one NumPy assignment"
        else:                                             # documented API: one Python object per point
            points = self._geometry.vertexDataAsPoint2D()
            for i in range(len(xs)):
                points[i].set(float(xs[i]), float(ys[i]))
            self._geometry.setVertexDataAsPoint2D(points)
            NOTES["LineNode"] = "vertices written point by point (no buffer access)"
        self._node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)
        return self._node


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
