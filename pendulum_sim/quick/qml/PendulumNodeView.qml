import QtQuick
import PendulumSpike 1.0

// The pendulum as a custom scene-graph item (Python writes the geometry on the render thread).
PendulumNode {
    id: node
    Connections {
        target: sim
        function onFrame() { node.refresh() }
    }
}
