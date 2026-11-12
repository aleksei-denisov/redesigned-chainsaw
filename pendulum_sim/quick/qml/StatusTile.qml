import QtQuick
import Instrument 1.0

// Side-panel status tile: scenario, controller type and gains (panel.status rows).
Card {
    id: tile
    property string tileId
    property var lines: panel.status[tileId] || []
    radius: Theme.radiusCard
    implicitHeight: col.implicitHeight + 24
    Column {
        id: col
        x: 14
        y: 12
        width: tile.width - 28
        spacing: 4
        Caption { text: "Controller" }
        Repeater {
            model: tile.lines
            Row {
                required property var modelData
                width: col.width
                spacing: 8
                Text {
                    id: label
                    width: Math.min(implicitWidth, col.width * 0.4)
                    text: modelData.label
                    elide: Text.ElideRight
                    color: Theme.textMuted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Mono {
                    width: col.width - label.width - 8
                    text: modelData.value
                    wrapMode: Text.Wrap
                    font.pixelSize: 13
                }
            }
        }
        Mono { visible: tile.lines.length === 0; text: "—"; color: Theme.textMuted }
    }
}
