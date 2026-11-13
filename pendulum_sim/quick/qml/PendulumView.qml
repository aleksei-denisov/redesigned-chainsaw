import QtQuick
import PendulumItems 1.0
import Instrument 1.0

// The pendulum as plain QML items. Python only changes four numbers per frame
// (sim.cartX, sim.th1..3); place() turns them into item positions and rotations, and
// the scene graph renders them without calling Python.
// Touch: a drag that starts on the pendulum pulls that point towards the finger (same
// force law as the Widgets app's tip drag, at the grabbed point; a drag that starts
// away from it pulls the tip); a tap on the pendulum pushes that point away from the
// finger with the push force (Settings / dock). The physics applies the force exactly
// at that point (quick-core-v2: MultiLinkPendulum.push_arms).
// Geometry follows the Widgets view: cart 0.30 × 0.12 m, rail 0.17 m below the pivot,
// wheels 0.09 m either side, camera centred 42 % of the pendulum length above the pivot.
Item {
    id: view
    clip: true

    property var lens: sim.lengths
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
    property bool trailActive: true                 // the trajectory is drawn (Settings: off / tip / all)
    property var jointX: []                         // joint positions in pixels, pivot first (place())
    property var jointY: []
    readonly property real grabRadius: 48            // how close to the pendulum a touch must be

    // the point of the pendulum nearest to (px, py): {segment, ratio, x, y, distance}
    function nearest(px, py) {
        var best = { segment: -1, ratio: 1, x: tipX, y: 0, distance: 1e9 }
        for (var i = 0; i + 1 < jointX.length; ++i) {
            var ax = jointX[i], ay = jointY[i], bx = jointX[i + 1], by = jointY[i + 1]
            var dx = bx - ax, dy = by - ay
            var len2 = dx * dx + dy * dy
            var t = len2 > 0 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0
            var x = ax + t * dx, y = ay + t * dy
            var d = Math.hypot(px - x, py - y)
            if (d < best.distance)
                best = { segment: i, ratio: t, x: x, y: y, distance: d }
        }
        return best
    }
    // where a point (segment, ratio) is now, in pixels
    function pointAt(segment, ratio) {
        var n = jointX.length - 1
        if (n < 1)
            return { x: tipX, y: 0 }
        if (segment < 0 || segment >= n)
            return { x: jointX[n], y: jointY[n] }
        return { x: jointX[segment] + ratio * (jointX[segment + 1] - jointX[segment]),
                 y: jointY[segment] + ratio * (jointY[segment + 1] - jointY[segment]) }
    }

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
        visible: sim.hasCart
        width: (sim.trackLength + 0.30) * view.pxPerM
        height: 3
        x: view.width / 2 - width / 2
        y: view.originY + 0.17 * view.pxPerM - 1.5
        color: Theme.track
    }
    Repeater {
        model: sim.hasCart ? 2 : 0
        Rectangle {
            width: 4
            height: 0.12 * view.pxPerM
            x: track.x + (index === 0 ? -2 : track.width - 2)
            y: track.y - height + 3
            color: Theme.tick
        }
    }

    // trajectory of the tip (or of every joint), fading with age: `trail` draws it in metres,
    // this view tells it the mapping to pixels
    GraphLines {
        anchors.fill: parent
        view: trail                                 // (inside, `view` is this item's property)
        colors: Theme.segments
        lineWidth: 1.6
        active: parent.trailActive && trail.mode !== "off"
    }
    readonly property var trailMapping: [pxPerM, width / 2, originY]
    onTrailMappingChanged: trail.setMapping(pxPerM, width / 2, originY)

    Item {
        id: cart
        visible: sim.hasCart
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
        model: sim.segments
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
        model: sim.segments + 1
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
        var cx = width / 2 + sim.cartX * s
        cart.x = cx - cart.width / 2
        var th = [sim.th1, sim.th2, sim.th3]
        var jx = cx
        var jy = originY
        var n = links.count
        jointX.length = 0
        jointY.length = 0
        jointX.push(jx)
        jointY.push(jy)
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
            jointX.push(jx)
            jointY.push(jy)
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
        property int segment: -1                    // the grabbed point (-1: the tip)
        property real ratio: 1
        property real pointX: 0
        property real pointY: 0
        onActiveChanged: {
            if (active) {
                var g = view.nearest(centroid.pressPosition.x, centroid.pressPosition.y)
                var onIt = g.distance <= view.grabRadius
                segment = onIt ? g.segment : -1
                ratio = onIt ? g.ratio : 1
            } else {
                sim.endDrag()
            }
        }
        onCentroidChanged: {
            if (!active)
                return
            var p = view.pointAt(segment, ratio)
            pointX = p.x
            pointY = p.y
            sim.dragAt((centroid.position.x - p.x) / (view.total * view.pxPerM), segment, ratio)
        }
    }
    Rectangle {                                   // the finger's pull, while dragging
        visible: drag.active
        x: Math.min(drag.centroid.position.x, drag.pointX)
        width: Math.abs(drag.centroid.position.x - drag.pointX)
        y: drag.pointY - 1
        height: 2
        color: Theme.angle
        opacity: 0.7
    }
    Rectangle {                                   // the grabbed point
        visible: drag.active
        x: drag.pointX - width / 2
        y: drag.pointY - height / 2
        width: 16; height: 16; radius: 8
        color: "transparent"
        border.color: Theme.angle
        border.width: 2
    }
    TapHandler {                                  // tap on the pendulum: push that point away from the finger
        onTapped: (point) => {
            var p = point.position
            var g = view.nearest(p.x, p.y)
            if (g.distance > view.grabRadius)
                return
            sim.pushAt(g.x >= p.x ? 1 : -1, g.segment, g.ratio)
            tapMark.x = g.x - tapMark.width / 2
            tapMark.y = g.y - tapMark.height / 2
            tapMark.opacity = 1
            tapFade.restart()
        }
    }
    Rectangle {
        id: tapMark
        width: 22; height: 22; radius: 11
        color: "transparent"
        border.color: Theme.angle
        border.width: 2
        opacity: 0
        NumberAnimation on opacity { id: tapFade; running: false; to: 0; duration: 500 }
    }

    Connections {
        target: sim
        function onFrame() { view.place() }
    }
    onWidthChanged: place()
    onLensChanged: Qt.callLater(place)
    onHeightChanged: place()
    Component.onCompleted: place()
}
