import QtQuick
import QtQuick.Layouts
import Pendulum 1.0
import Instrument 1.0

// Graphs screen: choose up to four graphs (remembered) and the time window; one card
// per graph. Graph data is computed only while this screen is shown.
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
                    model: Graphs.available
                    Btn {
                        required property var modelData
                        text: modelData.title
                        implicitHeight: Theme.touch
                        textSize: 14
                        active: Graphs.selection.indexOf(modelData.key) >= 0
                        onClicked: Graphs.select(modelData.key, !active)
                    }
                }
                Item { width: 12; height: 1 }
                Seg {
                    title: "Window"
                    options: Graphs.windows.map(w => [w, w + " s"])
                    value: Graphs.windowS
                    onPicked: (v) => Graphs.windowS = v
                }
            }
        }

        GridLayout {
            id: grid
            Layout.fillWidth: true
            Layout.fillHeight: true
            readonly property int count: Graphs.selection.length
            columns: screen.wide && count > 1 ? 2 : 1
            columnSpacing: Theme.gap
            rowSpacing: Theme.gap
            Repeater {
                model: Graphs.selection
                GraphCard {
                    required property string modelData
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 120
                    view: Graphs.view(modelData)
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
