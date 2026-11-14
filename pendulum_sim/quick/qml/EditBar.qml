import QtQuick
import Instrument 1.0

// Undo / Redo / Reset of a parameter screen (`target`: the model or controller bridge with
// canUndo, canRedo, undo(), redo(); `onReset`: what Reset does – back to the scenario).
Card {
    id: bar
    property QtObject target
    signal reset()
    radius: Theme.radiusCard
    implicitHeight: col.implicitHeight + 24
    Column {
        id: col
        x: 12
        y: 12
        width: parent.width - 24
        spacing: 8
        Caption { text: "Edits" }
        Row {
            width: parent.width
            spacing: 6
            readonly property real third: (width - 2 * spacing) / 3
            Btn { width: parent.third; text: "Undo"; enabled: bar.target && bar.target.canUndo; onClicked: bar.target.undo() }
            Btn { width: parent.third; text: "Redo"; enabled: bar.target && bar.target.canRedo; onClicked: bar.target.redo() }
            Btn { width: parent.third; text: "Reset"; onClicked: bar.reset() }
        }
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: "Reset returns to the values the scenario was loaded with; it can be undone."
            color: Theme.textMuted
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
    }
}
