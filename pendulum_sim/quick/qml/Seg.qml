import QtQuick
import Instrument 1.0

// Segmented control: one option active. options = [[value, text], …]
Row {
    id: seg
    property string title
    property var options: []
    property var value
    property int buttonHeight: Theme.touch
    signal picked(var v)
    spacing: 6
    Caption {
        text: seg.title
        visible: seg.title.length > 0
        anchors.verticalCenter: parent.verticalCenter
        rightPadding: 4
    }
    Repeater {
        model: seg.options
        Btn {
            required property var modelData
            text: modelData[1]
            active: String(seg.value) === String(modelData[0])
            implicitHeight: seg.buttonHeight
            onClicked: seg.picked(modelData[0])
        }
    }
}
