import QtQuick
import PendulumSpike 1.0

// Graph traces as a custom scene-graph item (Python fills the vertex buffers).
LineNode {
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
