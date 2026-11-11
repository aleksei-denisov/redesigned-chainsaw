import QtQuick
import PendulumSpike 1.0
import Instrument 1.0

// Graph variant "Lines": each trace is a custom scene-graph item written in Python.
Item {
    id: g
    function size() { graph.setSize(width, height) }
    onWidthChanged: size()
    onHeightChanged: size()
    Component.onCompleted: size()

    LineNode { id: first; anchors.fill: parent; trace: 0; color: Theme.angle }
    LineNode { id: second; anchors.fill: parent; trace: 1; color: Theme.accent }

    Connections {
        target: graph
        function onUpdated() {
            first.refresh()
            second.refresh()
        }
    }
}
