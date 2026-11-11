import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Simulate screen: pendulum card, read-outs, compact trace (θ1 and cart x).
// Tablet landscape: right column of 340 dp. Portrait: read-outs as a row of compact
// cards under the view, trace hidden unless switched on.
Item {
    id: screen
    property bool shown: false
    readonly property bool wide: width >= height
    property bool traceInPortrait: false

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

        // ------------------------------------------------- read-outs and trace
        ColumnLayout {
            Layout.preferredWidth: screen.wide ? 340 : -1
            Layout.maximumWidth: screen.wide ? 340 : Number.POSITIVE_INFINITY
            Layout.fillWidth: !screen.wide
            Layout.fillHeight: screen.wide
            spacing: Theme.gap

            GridLayout {
                columns: screen.wide ? 2 : 4
                columnSpacing: Theme.gap
                rowSpacing: Theme.gap
                Layout.fillWidth: true
                Readout { Layout.fillWidth: true; compact: !screen.wide; label: "Angle θ"; value: sim.angleText; unit: "°"; valueColor: Theme.angle }
                Readout { Layout.fillWidth: true; compact: !screen.wide; label: "Rate ω"; value: sim.rateText; unit: "°/s" }
                Readout { Layout.fillWidth: true; compact: !screen.wide; label: "Cart x"; value: sim.cartText; unit: "m"; valueColor: Theme.accent }
                Readout { Layout.fillWidth: true; compact: !screen.wide; label: "Force F"; value: sim.forceText; unit: "N" }
            }

            Card {
                id: traceCard
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 120
                visible: screen.wide || screen.traceInPortrait
                Caption { x: 14; y: 12; text: "Trace · θ / x" }
                Row {
                    anchors.right: parent.right
                    anchors.rightMargin: 14
                    y: 12
                    spacing: 12
                    Repeater {
                        model: [["θ1", Theme.angle], ["x", Theme.accent]]
                        Row {
                            required property var modelData
                            spacing: 5
                            Rectangle { width: 14; height: 2; color: modelData[1]; anchors.verticalCenter: parent.verticalCenter }
                            Mono { text: modelData[0]; color: Theme.textMuted; font.pixelSize: 12 }
                        }
                    }
                }
                Rectangle {                       // centre line of the plot area
                    x: 14
                    width: parent.width - 28
                    y: trace.y + trace.height / 2
                    height: 1
                    color: Theme.grid
                }
                GraphPlot {
                    id: trace
                    anchors.fill: parent
                    anchors.margins: 14
                    anchors.topMargin: 38
                    view: graphs.trace
                    colors: [Theme.angle, Theme.accent]
                    active: screen.shown && traceCard.visible
                }
            }
        }
    }
}
