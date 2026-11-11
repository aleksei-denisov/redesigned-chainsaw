import QtQuick
import Pendulum 1.0
import Instrument 1.0

// The pendulum as plain QML items. Python only changes four numbers per frame
// (Sim.cartX, Sim.th1..3); place() turns them into item positions and rotations, and
// the scene graph renders them without calling Python. One-finger drag pulls the tip
// towards the finger (same force law as the Widgets app: Sim.drag(offset)).
// Geometry follows the Widgets view: cart 0.30 × 0.12 m, rail 0.17 m below the pivot,
// wheels 0.09 m either side, camera centred 42 % of the pendulum length above the pivot.
Item {
    id: view
    clip: true

    property var lens: Sim.lengths
    property real total: {
        var s = 0
        for (var i = 0; i < lens.length; i++)
            s += lens[i]
        return Math.max(s, 0.1)
    }
    property real pxPerM: Math.max(20, Math.min(0.42 * height / total, 0.45 * width / total))
    property real originY: height / 2 + 0.42 * total * pxPerM
    property real linkW: 10
    readonly property var linkColors: Theme.segments
    property real tipX: width / 2

    // grid: a line every 0.25 m, centre line stronger
    Repeater {
        model: 41
        Rectangle {
            x: Math.round(view.width / 2 + (index - 20) * 0.25 * view.pxPerM)
            width: 1
            height: view.height
            color: index === 20 ? Theme.gridCenter : Theme.grid
        }
    }
    Repeater {
        model: 31
        Rectangle {
            y: Math.round(view.originY - (index - 10) * 0.25 * view.pxPerM)
            width: view.width
            height: 1
            color: Theme.grid
        }
    }

    // track with end stops
    Rectangle {
        id: track
        visible: Sim.hasCart
        width: (Sim.trackLength + 0.30) * view.pxPerM
        height: 3
        x: view.width / 2 - width / 2
        y: view.originY + 0.17 * view.pxPerM - 1.5
        color: Theme.track
    }
    Repeater {
        model: Sim.hasCart ? 2 : 0
        Rectangle {
            width: 4
            height: 0.12 * view.pxPerM
            x: track.x + (index === 0 ? -2 : track.width - 2)
            y: track.y - height + 3
            color: Theme.tick
        }
    }

    Item {
        id: cart
        visible: Sim.hasCart
        width: 0.30 * view.pxPerM
        height: 0.12 * view.pxPerM
        y: view.originY + 0.02 * view.pxPerM
        Rectangle {
            anchors.fill: parent
            radius: 5
            color: Theme.accentSoft
            border.color: Theme.accent
            border.width: 1.5
            antialiasing: true
        }
        Repeater {
            model: 2
            Rectangle {
                property real r: Math.max(3, 0.025 * view.pxPerM)
                width: 2 * r
                height: 2 * r
                radius: r
                x: (index === 0 ? 0.06 : 0.24) * view.pxPerM - r
                y: 0.15 * view.pxPerM - r
                color: Theme.track
                antialiasing: true
            }
        }
    }

    Repeater {
        id: links
        model: Sim.segments
        Rectangle {
            width: view.linkW
            height: view.lens[index] * view.pxPerM
            radius: view.linkW / 2
            color: view.linkColors[index % 3]
            transformOrigin: Item.Bottom
            antialiasing: true
        }
    }
    Repeater {
        id: joints
        model: Sim.segments + 1
        Rectangle {
            property real r: index === 0 ? view.linkW * 0.75 : view.linkW * 0.6
            width: 2 * r
            height: 2 * r
            radius: r
            color: Theme.surface
            border.color: Theme.text
            border.width: 2
            antialiasing: true
        }
    }

    function place() {
        var s = pxPerM
        var cx = width / 2 + Sim.cartX * s
        cart.x = cx - cart.width / 2
        var th = [Sim.th1, Sim.th2, Sim.th3]
        var jx = cx
        var jy = originY
        var n = links.count
        for (var i = 0; i < n; i++) {
            var link = links.itemAt(i)
            var joint = joints.itemAt(i)
            if (!link || !joint)
                return
            var len = lens[i] * s
            link.x = jx - linkW / 2
            link.y = jy - len
            link.rotation = th[i] * 57.29577951308232
            joint.x = jx - joint.width / 2
            joint.y = jy - joint.height / 2
            jx += len * Math.sin(th[i])
            jy -= len * Math.cos(th[i])
        }
        var tip = joints.itemAt(n)
        if (tip) {
            tip.x = jx - tip.width / 2
            tip.y = jy - tip.height / 2
        }
        tipX = jx
    }

    DragHandler {
        id: drag
        target: null
        dragThreshold: 10
        onCentroidChanged: if (active) Sim.drag((centroid.position.x - view.tipX) / (view.total * view.pxPerM))
        onActiveChanged: if (!active) Sim.endDrag()
    }
    Rectangle {                                   // the finger's pull, while dragging
        visible: drag.active
        x: Math.min(drag.centroid.position.x, view.tipX)
        width: Math.abs(drag.centroid.position.x - view.tipX)
        y: drag.centroid.position.y - 1
        height: 2
        color: Theme.angle
        opacity: 0.6
    }

    Connections {
        target: Sim
        function onFrame() { view.place() }
    }
    onWidthChanged: place()
    onLensChanged: Qt.callLater(place)
    onHeightChanged: place()
    Component.onCompleted: place()
}
