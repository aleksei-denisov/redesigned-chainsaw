import QtQuick
import QtQuick.Shapes

// Curves of one graph view (graphs.view(key) or graphs.trace): per series a list of
// polylines, already decimated to the pixel width and in item pixels. Drawn by the
// scene graph (Shape, curve renderer); Python only hands over the polygons.
Item {
    id: plot
    property QtObject view
    property var colors: []
    property var dashed: []
    property real lineWidth: 2
    property bool active: false

    function pathsAt(i) {
        var p = view ? view.paths : []
        return i < p.length ? p[i] : []
    }
    function sync() {
        if (view) {
            view.setSize(width, height)
            view.setActive(active && visible && width > 2 && height > 2)
        }
    }
    onWidthChanged: sync()
    onHeightChanged: sync()
    onActiveChanged: sync()
    onVisibleChanged: sync()
    onViewChanged: sync()
    Component.onCompleted: sync()
    Component.onDestruction: if (view) view.setActive(false)

    component Series: ShapePath {
        property int index
        fillColor: "transparent"
        strokeWidth: index < (plot.view ? plot.view.paths.length : 0) ? plot.lineWidth : 0
        strokeColor: plot.colors.length ? plot.colors[index % plot.colors.length] : "black"
        strokeStyle: plot.dashed[index] ? ShapePath.DashLine : ShapePath.SolidLine
        dashPattern: [4, 3]
        capStyle: ShapePath.RoundCap
        joinStyle: ShapePath.RoundJoin
        PathMultiline { paths: plot.pathsAt(index) }
    }

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        Series { index: 0 }
        Series { index: 1 }
        Series { index: 2 }
        Series { index: 3 }
        Series { index: 4 }
    }
}
