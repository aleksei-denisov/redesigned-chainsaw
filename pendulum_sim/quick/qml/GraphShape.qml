import QtQuick
import QtQuick.Shapes
import Instrument 1.0

// Graph traces as Shape paths. Python hands over one QPolygonF per trace 30 times a
// second (already reduced to the pixel width); the render thread never calls Python.
Item {
    id: g
    property bool curve: graph.mode === "shape"

    function size() { graph.setSize(width, height) }
    onWidthChanged: size()
    onHeightChanged: size()
    Component.onCompleted: size()

    Shape {
        anchors.fill: parent
        preferredRendererType: g.curve ? Shape.CurveRenderer : Shape.GeometryRenderer
        ShapePath {
            strokeColor: Theme.angle
            strokeWidth: 2
            fillColor: "transparent"
            joinStyle: ShapePath.RoundJoin
            PathPolyline { path: graph.trace1 }
        }
        ShapePath {
            strokeColor: Theme.accent
            strokeWidth: 2
            fillColor: "transparent"
            joinStyle: ShapePath.RoundJoin
            PathPolyline { path: graph.trace2 }
        }
    }
}
