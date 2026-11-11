import QtQuick
import PendulumItems 1.0
import Instrument 1.0

// One graph: title and unit, legend, horizontal grid lines with monospace tick labels
// (horizontal, never clipped), the curves.
Card {
    id: card
    property QtObject view
    property bool active: false
    property var colors: Theme.palette
    radius: Theme.radiusCard

    Caption { id: title; x: 14; y: 12; text: card.view ? card.view.title + (card.view.unit ? "  ·  " + card.view.unit : "") : "" }
    Row {
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
                    anchors.verticalCenter: parent.verticalCenter
                }
                Mono { text: modelData.label; color: Theme.textMuted; font.pixelSize: 12 }
            }
        }
    }

    Item {
        id: area
        anchors.fill: parent
        anchors.topMargin: 36
        anchors.bottomMargin: 12
        anchors.leftMargin: 14 + labelWidth.width + 8
        anchors.rightMargin: 14

        TextMetrics { id: labelWidth; font.family: Theme.monoFont; font.pixelSize: 11; text: "-0000.0" }

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
        GraphLines {
            anchors.fill: parent
            view: card.view
            colors: card.colors
            dashed: card.view ? card.view.legend.map(s => s.dashed) : []
            active: card.active
        }
    }
}
