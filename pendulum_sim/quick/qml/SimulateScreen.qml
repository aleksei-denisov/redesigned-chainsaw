import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Simulate screen: pendulum card and the configurable side panel (SidePanel: values,
// graphs, controller status; long-press to edit). Tablet landscape: right column of
// 340 dp. Portrait: the panel under the view, four values per row, at most half the height.
Item {
    id: screen
    property bool shown: false
    readonly property bool wide: width >= height
    onShownChanged: if (!shown) panel.editing = false

    GridLayout {
        anchors.fill: parent
        columns: screen.wide ? 2 : 1
        columnSpacing: Theme.gap
        rowSpacing: Theme.gap

        // ----------------------------------------------------------- pendulum
        Card {
            id: viewCard
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 220
            clip: true

            PendulumView {
                anchors.fill: parent
                anchors.margins: 1
            }

            // HUD corner brackets
            Repeater {
                model: 4
                Item {
                    id: bracket
                    required property int index
                    readonly property bool atRight: index % 2 === 1
                    readonly property bool atBottom: index >= 2
                    x: atRight ? viewCard.width - 36 : 14
                    y: atBottom ? viewCard.height - 36 : 14
                    width: 22
                    height: 22
                    opacity: 0.45
                    Rectangle { width: 22; height: 2; color: Theme.accent; y: bracket.atBottom ? 20 : 0 }
                    Rectangle { width: 2; height: 22; color: Theme.accent; x: bracket.atRight ? 20 : 0 }
                }
            }

            Column {
                x: 26
                y: 24
                spacing: 4
                Row {
                    spacing: 8
                    Mono { text: "SIM"; color: Theme.textMuted }
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: sim.running ? Theme.success : Theme.tick
                    }
                    Mono { text: sim.running ? "LIVE" : "PAUSED"; color: sim.running ? Theme.success : Theme.textMuted }
                    Mono { text: sim.fpsText }
                }
                Mono { text: sim.timeText }
            }
            Column {
                anchors.right: parent.right
                anchors.rightMargin: 26
                y: 24
                spacing: 4
                Mono {
                    anchors.right: parent.right
                    text: sim.segments + (sim.segments === 1 ? " SEGMENT" : " SEGMENTS") + (sim.hasCart ? " · CART" : " · FIXED PIVOT")
                    color: Theme.textMuted
                }
                Mono { anchors.right: parent.right; text: sim.speedText; color: Theme.textMuted }
            }
        }

        // ------------------------------------------- configurable side panel
        SidePanel {
            id: sidePanel
            compact: !screen.wide
            shown: screen.shown
            Layout.preferredWidth: screen.wide ? 340 : -1
            Layout.maximumWidth: screen.wide ? 340 : Number.POSITIVE_INFINITY
            Layout.fillWidth: !screen.wide
            Layout.fillHeight: screen.wide
            Layout.preferredHeight: screen.wide ? -1 : Math.min(naturalHeight, screen.height * 0.5)
        }
    }
}
