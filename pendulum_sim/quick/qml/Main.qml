import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Qt Quick spike: one Simulate screen in the "Instrument" light theme.
// Python supplies state (context objects sim, graph, bench); everything visible is
// drawn by the scene graph.
Window {
    id: root
    visible: true
    width: 1138
    height: 637
    title: "Pendulum Quick (spike)"
    color: Theme.background

    property int swaps: 0                 // frames presented (read by the benchmark)
    onFrameSwapped: {
        swaps += 1
        if (swaps === 1)
            bench.firstFrame()
    }
    readonly property bool wide: width >= height

    FrameAnimation {
        running: true
        onTriggered: sim.advance()
    }

    // ------------------------------------------------------------ components
    component Card: Rectangle {
        radius: Theme.radiusPanel
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
    }

    component Label: Text {
        color: Theme.textMuted
        font.family: Theme.uiFont
        font.pixelSize: 12
        font.letterSpacing: 1.0
        font.capitalization: Font.AllUppercase
    }

    component Mono: Text {
        color: Theme.text
        font.family: Theme.monoFont
        font.pixelSize: 13
    }

    component Readout: Rectangle {
        id: ro
        property string label
        property string value
        property string unit
        property color valueColor: Theme.text
        radius: Theme.radiusCard
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
        Layout.fillWidth: true
        Layout.preferredHeight: 84
        Label { x: 14; y: 12; text: ro.label }
        Mono { id: roValue; x: 14; y: 34; text: ro.value; color: ro.valueColor; font.pixelSize: 26 }
        Mono {
            anchors.left: roValue.right
            anchors.leftMargin: 6
            anchors.baseline: roValue.baseline
            text: ro.unit
            color: Theme.textMuted
        }
    }

    component Btn: Rectangle {
        id: btn
        property string text
        property bool filled: false
        property bool active: false
        signal clicked()
        implicitWidth: caption.implicitWidth + 28
        implicitHeight: 52
        radius: Theme.radiusButton
        color: filled ? Theme.accent : (active ? Theme.accentSoft : (tap.pressed ? Theme.border : Theme.surfaceSubtle))
        border.color: filled ? Theme.accent : (active ? Theme.accent : Theme.border)
        border.width: 1
        Text {
            id: caption
            anchors.centerIn: parent
            text: btn.text
            color: btn.filled ? Theme.onAccent : (btn.active ? Theme.accentSoftText : Theme.text)
            font.family: Theme.uiFont
            font.pixelSize: 15
            font.weight: btn.filled ? Font.DemiBold : Font.Medium
        }
        TapHandler {
            id: tap
            onTapped: btn.clicked()
        }
    }

    component Seg: Row {
        id: seg
        property string title
        property var options: []            // [[value, text], …]
        property string value
        signal picked(string v)
        spacing: 6
        Label { text: seg.title; anchors.verticalCenter: parent.verticalCenter }
        Repeater {
            model: seg.options
            Btn {
                required property var modelData
                text: modelData[1]
                active: seg.value === modelData[0]
                implicitHeight: 44
                onClicked: seg.picked(modelData[0])
            }
        }
    }

    // ---------------------------------------------------------------- layout
    GridLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        columns: root.wide ? 2 : 1
        columnSpacing: Theme.gap
        rowSpacing: Theme.gap

        // pendulum view
        Card {
            id: viewCard
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 220
            clip: true

            Loader {
                id: pendulum
                anchors.fill: parent
                anchors.margins: 1
                source: sim.pendulumMode === "node" ? "PendulumNodeView.qml" : "PendulumItems.qml"
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
                    Mono { text: "SIM" ; color: Theme.textMuted }
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
                Mono { anchors.right: parent.right; text: "LQR · " + sim.segments + " SEGMENTS"; color: Theme.textMuted }
                Mono {
                    anchors.right: parent.right
                    color: Theme.textMuted
                    text: "PHYSICS " + sim.physics.toUpperCase() + " · VIEW " + sim.pendulumMode.toUpperCase()
                }
            }
        }

        // read-outs and trace
        ColumnLayout {
            Layout.preferredWidth: root.wide ? 340 : -1
            Layout.maximumWidth: root.wide ? 340 : Number.POSITIVE_INFINITY
            Layout.fillWidth: !root.wide
            Layout.fillHeight: root.wide
            spacing: Theme.gap

            GridLayout {
                columns: root.wide ? 2 : 4
                columnSpacing: Theme.gap
                rowSpacing: Theme.gap
                Layout.fillWidth: true
                Readout { label: "Angle θ"; value: sim.angleText; unit: "°"; valueColor: Theme.angle }
                Readout { label: "Rate ω"; value: sim.rateText; unit: "°/s" }
                Readout { label: "Cart x"; value: sim.cartText; unit: "m"; valueColor: Theme.accent }
                Readout { label: "Force F"; value: sim.forceText; unit: "N" }
            }

            Card {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 120
                visible: root.wide || graph.mode !== "off"
                Label { x: 14; y: 12; text: "Trace · θ / x · " + graph.mode }
                Loader {
                    id: trace
                    anchors.fill: parent
                    anchors.margins: 14
                    anchors.topMargin: 36
                    active: graph.mode !== "off"
                    source: graph.mode === "qtgraphs" ? "GraphQtGraphs.qml"
                          : graph.mode === "node" ? "GraphNode.qml"
                          : graph.mode === "painted" ? "GraphPainted.qml" : "GraphShape.qml"
                    onStatusChanged: if (status === Loader.Error) graph.reportError("graph variant '" + graph.mode + "' failed to load")
                }
            }
        }

        // dock
        Card {
            Layout.columnSpan: root.wide ? 2 : 1
            Layout.fillWidth: true
            Layout.preferredHeight: dock.implicitHeight + 24
            Flow {
                id: dock
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: 12
                spacing: 10

                Btn { text: sim.running ? "Pause" : "Play"; filled: true; onClicked: sim.toggle() }
                Btn { text: "Push left"; onClicked: sim.push(-1) }
                Btn { text: "Push right"; onClicked: sim.push(1) }
                Btn { text: "Reset"; onClicked: sim.reset() }
                Seg {
                    title: "View"
                    options: [["items", "QML items"], ["node", "Scene graph"]]
                    value: sim.pendulumMode
                    onPicked: (v) => sim.pendulumMode = v
                    height: 52
                }
                Seg {
                    title: "Graph"
                    options: [["off", "Off"], ["shape", "Shape"], ["shapegeo", "Shape geo"], ["qtgraphs", "Qt Graphs"],
                              ["node", "Lines"], ["painted", "Painter"]]
                    value: graph.mode
                    onPicked: (v) => graph.mode = v
                    height: 52
                }
                Seg {
                    title: "Physics"
                    options: sim.processAvailable ? [["thread", "Thread"], ["process", "Process"]] : [["thread", "Thread"]]
                    value: sim.physics
                    onPicked: (v) => sim.physics = v
                    height: 52
                }
                Btn {
                    text: bench.running ? "Cancel benchmark" : "Run benchmark"
                    active: bench.running
                    onClicked: bench.running ? bench.cancel() : bench.start()
                }
                Btn { text: "Report"; visible: bench.report.length > 0; onClicked: sheet.visible = true }
                Mono {
                    text: bench.progress
                    color: Theme.textMuted
                    height: 52
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }

    // ------------------------------------------------------------ report sheet
    Rectangle {
        id: sheet
        anchors.fill: parent
        visible: false
        color: Theme.background
        Connections {
            target: bench
            function onFinished() { sheet.visible = true }
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme.pad
            spacing: Theme.gap
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "Benchmark report"
                    color: Theme.text
                    font.family: Theme.uiFont
                    font.pixelSize: 24
                    font.weight: Font.DemiBold
                    Layout.fillWidth: true
                }
                Btn { text: bench.copied ? "Copied" : "Copy report"; filled: true; onClicked: bench.copy() }
                Btn { text: "Close"; onClicked: sheet.visible = false }
            }
            Card {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                Flickable {
                    anchors.fill: parent
                    anchors.margins: 14
                    contentWidth: reportText.implicitWidth
                    contentHeight: reportText.implicitHeight
                    Mono { id: reportText; text: bench.report; font.pixelSize: 12 }
                }
            }
        }
    }
}
