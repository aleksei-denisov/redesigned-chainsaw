import QtQuick
import Instrument 1.0

// One-line text field (layout names, fixed axis limits). `accepted` on Enter / focus loss.
Rectangle {
    id: field
    property alias text: input.text
    property alias validator: input.validator
    property alias inputMethodHints: input.inputMethodHints
    property string placeholder
    property bool mono: false
    signal accepted()
    function edit() { input.forceActiveFocus(); input.selectAll() }
    implicitWidth: 160
    implicitHeight: Theme.touch
    radius: Theme.radiusButton
    color: Theme.surfaceSubtle
    border.color: input.activeFocus ? Theme.accent : Theme.border
    border.width: 1
    TextInput {
        id: input
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        verticalAlignment: TextInput.AlignVCenter
        clip: true
        color: Theme.text
        selectionColor: Theme.accentSoft
        selectedTextColor: Theme.text
        font.family: field.mono ? Theme.monoFont : Theme.uiFont
        font.pixelSize: Theme.body
        onAccepted: { field.accepted(); focus = false }
        onActiveFocusChanged: if (!activeFocus) field.accepted()
    }
    Text {
        anchors.fill: input
        verticalAlignment: Text.AlignVCenter
        visible: input.text.length === 0 && !input.activeFocus
        text: field.placeholder
        color: Theme.textMuted
        font: input.font
    }
}
