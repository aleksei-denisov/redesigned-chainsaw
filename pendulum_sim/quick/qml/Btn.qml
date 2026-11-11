import QtQuick
import Instrument 1.0

// Button: filled (accent), active (accent-soft, e.g. a selected option) or plain.
Rectangle {
    id: btn
    property string text
    property bool filled: false
    property bool active: false
    property int textSize: Theme.body
    signal clicked()
    implicitWidth: Math.max(caption.implicitWidth + 28, Theme.touch)
    implicitHeight: Theme.dockButton
    radius: Theme.radiusButton
    opacity: enabled ? 1.0 : 0.45
    color: filled ? (tap.pressed ? Qt.darker(Theme.accent, 1.1) : Theme.accent)
                  : (active ? Theme.accentSoft : (tap.pressed ? Theme.border : Theme.surfaceSubtle))
    border.color: filled || active ? Theme.accent : Theme.border
    border.width: 1
    Text {
        id: caption
        anchors.centerIn: parent
        text: btn.text
        color: btn.filled ? Theme.onAccent : (btn.active ? Theme.accentSoftText : Theme.text)
        font.family: Theme.uiFont
        font.pixelSize: btn.textSize
        font.weight: btn.filled ? Font.DemiBold : Font.Medium
    }
    TapHandler {
        id: tap
        onTapped: btn.clicked()
    }
}
