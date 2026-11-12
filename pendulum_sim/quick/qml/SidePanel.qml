import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// The configurable side panel of the Simulate screen (layout: the `panel` backend).
// Rows of tiles – half-width values share a row (two in the side column, four in
// portrait), graphs and status tiles take a row each; "fill" graphs share the height
// that is left. Long-press a tile to edit: tap a tile to change it, drag it by its
// grip to move it, × removes it, the bar on top adds tiles; Done or Back finishes.
Item {
    id: sidePanel
    property bool compact: false              // portrait: four values per row
    property bool shown: false
    readonly property real naturalHeight: (editBar.visible ? editBar.implicitHeight + Theme.gap : 0)
                                          + column.implicitHeight

    readonly property var graphHeights: ({ "small": 120, "medium": 180, "large": 260, "fill": 150 })

    // ------------------------------------------------------------ dragging
    property var frames: ({})                 // tile id → its frame (drop targets)
    property string dragId: ""
    property string dropId: ""

    function indexOf(tileId) {
        var ts = panel.tiles
        for (var i = 0; i < ts.length; ++i)
            if (ts[i].id === tileId)
                return i
        return -1
    }
    function tileName(t) {
        if (!t)
            return ""
        if (t.type === "value")
            return t.name
        if (t.type === "graph")
            return "Graph"
        return "Controller"
    }
    function dragStart(tileId, source, x, y) {
        dragId = tileId
        dropId = ""
        ghost.text = tileName(panel.tiles[indexOf(tileId)])
        dragMove(source, x, y)
    }
    function dragMove(source, x, y) {
        var p = source.mapToItem(sidePanel, x, y)
        ghost.x = Math.max(0, Math.min(sidePanel.width - ghost.width, p.x - ghost.width / 2))
        ghost.y = Math.max(0, Math.min(sidePanel.height - ghost.height, p.y - ghost.height / 2))
        var found = ""
        for (var id in frames) {
            var f = frames[id]
            if (!f)
                continue
            var q = source.mapToItem(f, x, y)
            if (q.x >= 0 && q.y >= 0 && q.x < f.width && q.y < f.height)
                found = id
        }
        dropId = found
        var fy = source.mapToItem(flick, x, y).y          // scroll when held near an edge
        if (fy > flick.height - 48)
            flick.contentY = Math.min(flick.contentY + 12, Math.max(0, flick.contentHeight - flick.height))
        else if (fy < 48)
            flick.contentY = Math.max(flick.contentY - 12, 0)
    }
    function dragEnd(drop) {
        if (drop && dropId.length && dropId !== dragId)
            panel.move(dragId, indexOf(dropId))
        dragId = ""
        dropId = ""
    }
    function addTile(kind) {
        var id = panel.add(kind)
        if (id.length) {
            panel.selected = id
            Qt.callLater(() => flick.contentY = Math.max(0, flick.contentHeight - flick.height))
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gap

        // --------------------------------------------------------- edit bar
        Card {
            id: editBar
            visible: panel.editing
            Layout.fillWidth: true
            implicitHeight: editColumn.implicitHeight + 24
            radius: Theme.radiusCard
            border.color: Theme.accent
            Column {
                id: editColumn
                x: 12
                y: 12
                width: editBar.width - 24
                spacing: 8
                RowLayout {
                    width: parent.width
                    Caption { text: "Edit panel"; color: Theme.accentSoftText; Layout.fillWidth: true }
                    Btn { text: "Done"; filled: true; implicitHeight: Theme.touch; onClicked: panel.editing = false }
                }
                Flow {
                    width: parent.width
                    spacing: 6
                    Repeater {
                        model: [["value", "+ Value"], ["graph", "+ Graph"], ["status", "+ Status"]]
                        Btn {
                            required property var modelData
                            text: modelData[1]
                            textSize: 14
                            implicitHeight: Theme.touch
                            enabled: !panel.full
                            onClicked: sidePanel.addTile(modelData[0])
                        }
                    }
                }
                Text {
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: "Tap a tile to change it · drag ≡ to move · × removes"
                    color: Theme.textMuted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
            }
        }

        // ------------------------------------------------------------ tiles
        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentHeight: column.height
            flickableDirection: Flickable.VerticalFlick
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height + 1
            clip: true

            ColumnLayout {
                id: column
                width: flick.width
                height: Math.max(flick.height, implicitHeight)
                spacing: Theme.gap

                Repeater {
                    model: sidePanel.compact ? panel.rowsCompact : panel.rows
                    RowLayout {
                        id: row
                        required property var modelData                  // tile indices
                        readonly property var first: panel.tiles[modelData[0]] || ({})
                        readonly property bool fill: first.type === "graph" && first.height === "fill"
                        readonly property bool halves: first.type === "value" && first.size === "half"
                        Layout.fillWidth: true
                        Layout.fillHeight: fill
                        Layout.minimumHeight: fill ? sidePanel.graphHeights.fill : -1
                        spacing: Theme.gap
                        Repeater {
                            model: row.modelData
                            TileFrame {}
                        }
                        Repeater {                                       // keeps a lone half tile half wide
                            model: row.halves ? (sidePanel.compact ? 4 : 2) - row.modelData.length : 0
                            Item { Layout.fillWidth: true; Layout.preferredWidth: 10; implicitHeight: 1 }
                        }
                    }
                }
            }
        }
    }

    Rectangle {                                                          // follows the finger while dragging
        id: ghost
        property alias text: ghostText.text
        visible: sidePanel.dragId.length > 0
        width: Math.min(220, sidePanel.width - 16)
        height: Theme.touch
        radius: Theme.radiusButton
        color: Theme.accent
        opacity: 0.92
        z: 10
        Text {
            id: ghostText
            anchors.centerIn: parent
            width: parent.width - 20
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            color: Theme.onAccent
            font.family: Theme.uiFont
            font.pixelSize: Theme.body
            font.weight: Font.DemiBold
        }
    }

    // ------------------------------------------------------------ one tile
    component TileFrame: Item {
        id: frame
        required property int modelData                                  // index in panel.tiles
        readonly property var tile: panel.tiles[modelData] || ({})
        readonly property string tileId: tile.id || ""
        readonly property bool isDropTarget: sidePanel.dropId === tileId && sidePanel.dragId !== tileId
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredWidth: 10
        Layout.preferredHeight: tile.type === "graph" ? sidePanel.graphHeights[tile.height] || 150
                                                      : (content.item ? content.item.implicitHeight : 84)
        opacity: sidePanel.dragId === tileId ? 0.35 : 1.0

        Component.onCompleted: sidePanel.frames[tileId] = frame
        Component.onDestruction: if (sidePanel.frames[tileId] === frame) delete sidePanel.frames[tileId]

        Loader {
            id: content
            anchors.fill: parent
            sourceComponent: frame.tile.type === "value" ? valueTile
                           : frame.tile.type === "graph" ? graphTile
                           : frame.tile.type === "status" ? statusTile : null
        }
        Component {
            id: valueTile
            Readout {
                compact: sidePanel.compact
                label: frame.tile.name + (frame.tile.available ? "" : " · n/a")
                value: panel.values[frame.tileId] || "—"
                unit: frame.tile.unit
                valueColor: Theme.token(frame.tile.color)
            }
        }
        Component {
            id: graphTile
            GraphCard {
                view: panel.view(frame.tileId)
                colors: Theme.tilePalette
                active: sidePanel.shown
            }
        }
        Component {
            id: statusTile
            StatusTile { tileId: frame.tileId }
        }

        MouseArea {                                                      // long press: edit; tap while editing: open
            anchors.fill: parent
            pressAndHoldInterval: 500
            onPressAndHold: if (!panel.editing) panel.editing = true
            onClicked: if (panel.editing) panel.selected = frame.tileId
        }

        // edit-mode decorations
        Rectangle {
            anchors.fill: parent
            visible: panel.editing
            color: frame.isDropTarget ? Theme.accentSoft : "transparent"
            opacity: frame.isDropTarget ? 0.6 : 1.0
            radius: Theme.radiusCard
            border.color: Theme.accent
            border.width: frame.isDropTarget ? 3 : 2
        }
        Row {                                                            // grip (drag to move) and remove
            visible: panel.editing
            anchors.right: parent.right
            anchors.rightMargin: 4
            y: 4
            spacing: 4
            Rectangle {
                width: 36
                height: 36
                radius: 18
                color: Theme.accentSoft
                border.color: Theme.accent
                Text {
                    anchors.centerIn: parent
                    text: "≡"
                    color: Theme.accentSoftText
                    font.pixelSize: 20
                }
                MouseArea {
                    id: grip
                    anchors.fill: parent
                    anchors.margins: -4
                    preventStealing: true                               // not a scroll of the panel
                    onPressed: (m) => sidePanel.dragStart(frame.tileId, grip, m.x, m.y)
                    onPositionChanged: (m) => sidePanel.dragMove(grip, m.x, m.y)
                    onReleased: sidePanel.dragEnd(true)
                    onCanceled: sidePanel.dragEnd(false)
                }
            }
            Rectangle {
                width: 36
                height: 36
                radius: 18
                color: removeArea.pressed ? Theme.border : Theme.surface
                border.color: Theme.border
                Text {
                    anchors.centerIn: parent
                    text: "×"
                    color: Theme.text
                    font.pixelSize: 20
                }
                MouseArea {
                    id: removeArea
                    anchors.fill: parent
                    anchors.margins: -4
                    onClicked: panel.remove(frame.tileId)
                }
            }
        }
    }
}
