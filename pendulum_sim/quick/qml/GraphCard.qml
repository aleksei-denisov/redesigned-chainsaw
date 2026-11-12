import QtQuick
import PendulumItems 1.0
import Instrument 1.0

// One live graph (Graphs-screen card or side-panel graph tile): title and units, legend,
// horizontal grid lines with monospace tick labels (left axis; right axis for a second
// unit), the curves (GraphLines, drawn by the scene graph from NumPy-built geometry).
Card {
    id: card
    property QtObject view
    property bool active: false
    property var colors: Theme.palette
    property string title: view ? view.title : ""
    radius: Theme.radiusCard
    readonly property bool twoAxes: view !== null && view.unitRight.length > 0

    Row {                                       // title (uppercase) and units (as written)
        x: 14
        y: 12
        width: legend.x - x - 8
        spacing: 8
        clip: true
        Caption {
            id: titleText
            visible: card.title.length > 0
            text: card.title
            anchors.verticalCenter: parent.verticalCenter
        }
        Mono {
            text: card.view ? card.view.unit + (card.twoAxes ? " | " + card.view.unitRight : "") : ""
            color: Theme.textMuted
            font.pixelSize: 12
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    Row {
        id: legend
        anchors.right: parent.right
        anchors.rightMargin: 14
        y: 10
        spacing: 12
        Repeater {
            model: card.view ? card.view.legend : []
            Row {
                required property var modelData
                spacing: 5
                Rectangle {
                    width: 14; height: 2
                    color: card.colors[modelData.index % card.colors.length]
                    opacity: modelData.dashed ? 0.6 : 1.0
                    anchors.verticalCenter: parent.verticalCenter
                }
                Mono {
                    text: modelData.label + (card.twoAxes ? " " + modelData.unit : "")
                    color: Theme.textMuted
                    font.pixelSize: 12
                }
            }
        }
    }

    TextMetrics { id: labelWidth; font.family: Theme.monoFont; font.pixelSize: 11; text: "-000.00" }

    Item {
        id: area
        anchors.fill: parent
        anchors.topMargin: 36
        anchors.bottomMargin: 12
        anchors.leftMargin: 14 + labelWidth.width + 8
        anchors.rightMargin: 14 + (card.twoAxes ? labelWidth.width + 8 : 0)

        Repeater {
            model: card.view ? card.view.ticks : []
            Item {
                required property var modelData
                y: modelData.y
                width: area.width
                Rectangle { width: area.width; height: 1; color: Theme.grid }
                Mono {
                    anchors.right: parent.left
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.top
                    text: modelData.text
                    color: Theme.textMuted
                    font.pixelSize: 11
                }
            }
        }
        Repeater {
            model: card.view ? card.view.ticksRight : []
            Mono {
                required property var modelData
                x: area.width + 8
                y: modelData.y - height / 2
                text: modelData.text
                color: Theme.textMuted
                font.pixelSize: 11
            }
        }
        GraphLines {
            anchors.fill: parent
            view: card.view
            colors: card.colors
            active: card.active
        }
    }
}
