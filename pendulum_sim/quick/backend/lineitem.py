"""``GraphLines``: the curves of one graph view as a scene-graph item (QML type).

The view (``graphs.GraphView``) prepares the triangles of every series with NumPy at
the graph rate – incrementally, see ``curves.py`` – and the horizontal offset by which
the curves have scrolled. ``updatePaintNode`` only copies the vertex and index buffers
into the scene graph's geometry and sets the offset on a transform node – with a
threaded render loop it runs on the render thread while the GUI thread waits, and does
no more than two memory copies per series.

Registered before the QML engine starts (``app.py``); NumPy is imported on first use,
so the registration does not slow down the loading screen.

**Ownership.** The item never calls ``self.window()`` (see :meth:`GraphLines._dpr`).
Nodes, geometries and materials pass to Qt when attached (``appendChildNode``,
``setGeometry`` / ``setMaterial`` with ``OwnsGeometry`` / ``OwnsMaterial``, as in
PySide's scene-graph examples); the item keeps their wrappers only to update them. The
vertex layout (``AttributeSet``) a geometry refers to is kept for the whole run.
"""

from __future__ import annotations

import ctypes
import weakref

from PySide6.QtCore import Property, QObject, Signal
from PySide6.QtGui import QColor, QMatrix4x4
from PySide6.QtQuick import (
    QQuickItem,
    QSGGeometry,
    QSGGeometryNode,
    QSGNode,
    QSGTransformNode,
    QSGVertexColorMaterial,
)

MAX_SERIES = 6
NOTES: dict[str, str] = {}          # problems seen while drawing (shown in the performance report)
_ATTRIBUTES: list = []              # the vertex layout every geometry refers to (never freed)
INSTANCES: "weakref.WeakSet[GraphLines]" = weakref.WeakSet()     # for diagnostics (--graph-stress)


def _attributes():
    if not _ATTRIBUTES:
        _ATTRIBUTES.append(QSGGeometry.defaultAttributes_ColoredPoint2D())
    return _ATTRIBUTES[0]


def _new_geometry(wide: bool) -> QSGGeometry:
    geometry = QSGGeometry(_attributes(), 0, 0, _index_type(wide))
    geometry.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangles)
    return geometry


def _index_type(wide: bool) -> int:
    kind = QSGGeometry.Type.UnsignedIntType if wide else QSGGeometry.Type.UnsignedShortType
    return int(getattr(kind, "value", kind))


def _copy(address, data) -> None:
    """Copy a NumPy array's bytes to a scene-graph buffer."""
    if data.nbytes:
        ctypes.memmove(int(address), data.ctypes.data, data.nbytes)


class GraphLines(QQuickItem):
    viewChanged = Signal()
    colorsChanged = Signal()
    dashedChanged = Signal()
    lineWidthChanged = Signal()
    activeChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._view: QObject | None = None
        self._colors: list[QColor] = []
        self._dashed: list[bool] = []
        self._width = 2.0
        self._active = False
        self._pending = False                           # the view has new curves for the next sync
        self._nodes: list[list] = []                    # [node, geometry, wide, material]
        self.widthChanged.connect(self._sync)
        self.heightChanged.connect(self._sync)
        self.visibleChanged.connect(self._sync)
        self._window_dpr = 0.0                          # from windowChanged; see _dpr()
        self.windowChanged.connect(self._on_window)
        self.setClip(True)                              # curves scroll in from the right, out at the left
        INSTANCES.add(self)

    # ------------------------------------------------------------ properties
    def _set_view(self, view: QObject | None) -> None:
        if view is self._view:
            return
        if self._view is not None:
            try:
                self._view.updated.disconnect(self._rebuild)
            except (RuntimeError, TypeError):
                pass
            self._view.setActive(False)
        self._view = view
        if view is not None:
            view.updated.connect(self._rebuild)
            self._style_changed()
        self._sync()
        self.viewChanged.emit()

    view = Property(QObject, lambda self: self._view, _set_view, notify=viewChanged)

    def _set_colors(self, colors) -> None:
        self._colors = [QColor(c) for c in (colors or [])]
        self.colorsChanged.emit()
        self._style_changed()

    colors = Property("QVariantList", lambda self: self._colors, _set_colors, notify=colorsChanged)

    def _set_dashed(self, dashed) -> None:
        self._dashed = [bool(d) for d in (dashed or [])]       # (dashes come from the signal catalogue)
        self.dashedChanged.emit()

    dashed = Property("QVariantList", lambda self: self._dashed, _set_dashed, notify=dashedChanged)

    def _set_line_width(self, width: float) -> None:
        self._width = float(width)
        self.lineWidthChanged.emit()
        self._style_changed()

    lineWidth = Property(float, lambda self: self._width, _set_line_width, notify=lineWidthChanged)

    def _set_active(self, on: bool) -> None:
        if bool(on) != self._active:
            self._active = bool(on)
            self._sync()
            self.activeChanged.emit()

    active = Property(bool, lambda self: self._active, _set_active, notify=activeChanged)

    def _on_window(self, window) -> None:
        self._window_dpr = float(window.effectiveDevicePixelRatio()) if window is not None else 0.0
        self._sync()

    def _dpr(self) -> float:
        """The window's pixel ratio – never through ``self.window()``: PySide makes an object
        returned by a method a child of the caller's wrapper, so when this item is destroyed
        (every side-panel edit) the window's Python wrapper was invalidated with it, or the
        window deleted with a garbage-collected wrapper – the crash after Move up / down."""
        if self._window_dpr > 0:
            return self._window_dpr
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        return float(screen.devicePixelRatio()) if screen is not None else 1.0

    def _sync(self) -> None:
        """Tell the view its size (in item pixels and physical pixels) and whether it is shown."""
        view = self._view
        if view is None:
            return
        w, h = self.width(), self.height()
        view.setSize(w, h)
        if view.dpr != self._dpr():
            view.dpr = self._dpr()
            self._style_changed()
        view.setActive(self._active and self.isVisible() and w > 2 and h > 2)

    # -------------------------------------------------------------- geometry
    def _style_changed(self) -> None:
        """Colours, width or pixel density changed: the view redraws its curves."""
        from .curves import Style

        view = self._view
        if view is None:
            return
        colors = [(c.red(), c.green(), c.blue(), c.alpha()) for c in self._colors] or [(0, 0, 0, 255)]
        view.set_style(Style(colors, self._width, 1.0 / max(self._dpr(), 1.0)))

    def _rebuild(self) -> None:
        """The view has new curves: take them at the next sync."""
        self._pending = True
        self.update()

    def updatePaintNode(self, old, data):  # noqa: N802 - Qt API (render thread with a threaded loop)
        try:
            return self._update_nodes(old)
        except Exception as exc:  # noqa: BLE001 - keep drawing the rest of the app
            NOTES["GraphLines"] = f"drawing failed: {type(exc).__name__}: {exc}"
            return old

    def _update_nodes(self, old):
        import numpy as np

        root = old
        if root is None or not self._nodes:
            root = QSGTransformNode()
            self._nodes = []
            for _ in range(MAX_SERIES):
                node = QSGGeometryNode()
                geometry = _new_geometry(False)
                node.setGeometry(geometry)
                node.setFlag(QSGNode.Flag.OwnsGeometry)
                material = QSGVertexColorMaterial()
                node.setMaterial(material)
                node.setFlag(QSGNode.Flag.OwnsMaterial)
                root.appendChildNode(node)               # OwnedByParent: deleted with the root
                self._nodes.append([node, geometry, False, material])
            self._pending = True
        if not self._pending:
            return root
        self._pending = False
        out = self._view.output if self._view is not None else None
        curves = out.curves if out is not None else []
        matrix = QMatrix4x4()
        matrix.translate(out.dx if out is not None else 0.0, 0.0)
        root.setMatrix(matrix)
        root.markDirty(QSGNode.DirtyStateBit.DirtyMatrix)
        for k, entry in enumerate(self._nodes):
            node, geometry = entry[0], entry[1]
            vertices, indices = curves[k] if k < len(curves) else (None, None)
            if vertices is None or not indices.size:
                if geometry.indexCount():
                    geometry.allocate(0, 0)
                    node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)
                continue
            wide = vertices.size > 65535
            if wide != entry[2]:                         # index width changes: a new geometry
                geometry = _new_geometry(wide)
                node.setGeometry(geometry)               # (Qt deletes the old one: OwnsGeometry)
                entry[1], entry[2] = geometry, wide
            if geometry.vertexCount() != vertices.size or geometry.indexCount() != indices.size:
                geometry.allocate(vertices.size, indices.size)
            _copy(geometry.vertexData(), np.ascontiguousarray(vertices))
            _copy(geometry.indexData(), indices.astype(np.uint32 if wide else np.uint16, copy=False))
            node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)
        return root
