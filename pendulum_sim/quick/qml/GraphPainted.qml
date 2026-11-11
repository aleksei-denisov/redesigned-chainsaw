import QtQuick
import PendulumSpike 1.0

// Graph traces painted with QPainter (QQuickPaintedItem, framebuffer object).
PaintedGraph {
    id: g
    function size() { graph.setSize(width, height) }
    onWidthChanged: size()
    onHeightChanged: size()
    Component.onCompleted: size()
    Connections {
        target: graph
        function onUpdated() { g.refresh() }
    }
}
