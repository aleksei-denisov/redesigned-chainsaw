import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Graphs screen: choose up to four graphs (remembered), the time window and the display
// smoothing; one card per graph. Graph data is computed only while this screen is shown.
Item {
    id: screen
    property bool shown: false
    readonly property bool wide: width >= height

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gap

        Card {
            Layout.fillWidth: true
            Layout.preferredHeight: chooser.implicitHeight + 24
            Flow {
                id: chooser
                x: 12
                y: 12
                width: parent.width - 24
                spacing: 6
                Repeater {
                    model: graphs.available
                    Btn {
                        required property var modelData
                        text: modelData.title
                        implicitHeight: Theme.touch
                        textSize: 14
                        active: graphs.selection.indexOf(modelData.key) >= 0
                        onClicked: graphs.select(modelData.key, !active)
                    }
                }
                Item { width: 12; height: 1 }
                Seg {
                    title: "Window"
                    options: graphs.windows.map(w => [w, w + " s"])
                    value: graphs.windowS
                    onPicked: (v) => graphs.windowS = v
                }
                Item { width: 12; height: 1 }
                Seg {
                    title: "Smoothing"
                    options: [["off", "Off"], ["light", "Light"], ["medium", "Medium"], ["strong", "Strong"]]
                    value: graphs.smoothing
                    onPicked: (v) => graphs.smoothing = v
                }
            }
        }

        GridLayout {
            id: grid
            Layout.fillWidth: true
            Layout.fillHeight: true
            readonly property int count: graphs.selection.length
            columns: screen.wide && count > 1 ? 2 : 1
            columnSpacing: Theme.gap
            rowSpacing: Theme.gap
            Repeater {
                model: graphs.selection
                GraphCard {
                    required property string modelData
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 120
                    view: graphs.view(modelData)
                    colors: modelData === "angle" || modelData === "angular_velocity" ? Theme.segments : Theme.palette
                    active: screen.shown
                }
            }
            Body {
                visible: grid.count === 0
                text: "Choose graphs above."
                color: Theme.textMuted
            }
        }
    }
}
