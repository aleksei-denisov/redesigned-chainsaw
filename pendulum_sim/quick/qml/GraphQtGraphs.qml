import QtQuick
import QtGraphs
import Instrument 1.0

// Graph traces with the Qt Graphs module (two LineSeries filled from Python).
Item {
    id: g

    function size() { graph.setSize(width, height) }
    onWidthChanged: size()
    onHeightChanged: size()

    GraphsView {
        anchors.fill: parent
        axisX: ValueAxis { min: 0; max: Math.max(g.width, 16) }
        axisY: ValueAxis { min: 0; max: Math.max(g.height, 16) }
        LineSeries { id: first; color: Theme.angle; width: 2 }
        LineSeries { id: second; color: Theme.accent; width: 2 }
    }

    Component.onCompleted: {
        size()
        graph.setSeries(first, second)
    }
}
