import QtQuick
import Instrument 1.0

// Read-out card: uppercase label, large monospace value, unit.
Rectangle {
    id: ro
    property string label
    property string value
    property string unit
    property color valueColor: Theme.text
    property bool compact: false
    radius: Theme.radiusCard
    color: Theme.surface
    border.color: Theme.border
    border.width: 1
    implicitHeight: compact ? 60 : 84

    Caption { x: 14; y: ro.compact ? 8 : 12; text: ro.label }
    Mono {
        id: roValue
        x: 14
        y: ro.compact ? 26 : 34
        text: ro.value
        color: ro.valueColor
        font.pixelSize: ro.compact ? 20 : Theme.readout
    }
    Mono {
        anchors.left: roValue.right
        anchors.leftMargin: 6
        anchors.baseline: roValue.baseline
        text: ro.unit
        color: Theme.textMuted
        font.pixelSize: 13
    }
}
