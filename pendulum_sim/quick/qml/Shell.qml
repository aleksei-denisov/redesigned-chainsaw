import QtQuick
import QtQuick.Layouts
import PendulumApp 1.0
import Instrument 1.0

// App shell: the screens (each built when first opened) and the bottom dock with the
// navigation tabs and the run controls. Every vsync goes to frames.vsync() (frame
// pacing decides whether a new frame is shown).
Item {
    id: shell
    focus: true
    readonly property bool wide: width >= height
    property bool reportOpen: false

    Keys.onBackPressed: (event) => {
        event.accepted = true
        if (reportOpen)
            reportOpen = false
        else if (panel.selected.length)
            panel.selected = ""                 // close the tile editor …
        else if (panel.editing)
            panel.editing = false               // … then leave the panel's edit mode
        else
            App.back()
    }

    // the frame loop (Main.qml's FrameAnimation calls these)
    readonly property bool framesContinuous: frames.continuous     // at the display rate
    function vsync() { frames.vsync() }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.gap

        Item {
            id: screens
            Layout.fillWidth: true
            Layout.fillHeight: true

            Repeater {
                model: App.screens
                Loader {
                    id: screenLoader
                    required property string modelData
                    readonly property bool current: App.screen === modelData
                    property bool visited: false
                    anchors.fill: parent
                    active: current || visited
                    visible: current
                    source: modelData === "simulate" ? "SimulateScreen.qml"
                          : modelData === "graphs" ? "GraphsScreen.qml"
                          : modelData === "controller" ? "ControllerScreen.qml"
                          : modelData === "model" ? "ModelScreen.qml"
                          : modelData === "autotune" ? "AutotuneScreen.qml"
                          : modelData === "experiments" ? "ExperimentsScreen.qml" : "SettingsScreen.qml"
                    onCurrentChanged: if (current) visited = true
                    Component.onCompleted: if (current) visited = true
                    onLoaded: item.shown = Qt.binding(() => screenLoader.current && shell.visible)
                }
            }
        }

        // ------------------------------------------------------------------ dock
        Card {
            id: dock
            Layout.fillWidth: true
            Layout.preferredHeight: dockGrid.implicitHeight + 24
            GridLayout {
                id: dockGrid
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: 12
                columns: shell.wide ? 3 : 1
                rowSpacing: 10
                columnSpacing: 10

                Flickable {                     // screen buttons; scroll sideways if they do not fit
                    id: navFlick
                    Layout.fillWidth: true
                    Layout.preferredWidth: navRow.implicitWidth
                    Layout.maximumWidth: navRow.implicitWidth
                    implicitHeight: navRow.implicitHeight
                    contentWidth: navRow.implicitWidth
                    flickableDirection: Flickable.HorizontalFlick
                    boundsBehavior: Flickable.StopAtBounds
                    interactive: contentWidth > width + 1
                    clip: true
                    Row {
                        id: navRow
                        spacing: 6
                        Repeater {
                            model: [["simulate", "Simulate"], ["graphs", "Graphs"], ["controller", "Controller"],
                                    ["model", "Model"], ["autotune", "Auto-tune"], ["experiments", "Experiments"],
                                    ["settings", "Settings"]]
                            Btn {
                                required property var modelData
                                text: modelData[1]
                                active: App.screen === modelData[0]
                                enabled: !bench.running && !autotune.busy
                                onClicked: App.screen = modelData[0]
                            }
                        }
                    }
                }
                Item { Layout.fillWidth: true; visible: shell.wide; implicitHeight: 1 }
                Row {
                    spacing: 6
                    Layout.alignment: shell.wide ? Qt.AlignRight : Qt.AlignLeft
                    // pushes only where the pendulum or its graphs are in view
                    readonly property bool pushHere: App.screen === "simulate" || App.screen === "graphs"
                    Caption { text: "Push"; visible: parent.pushHere; anchors.verticalCenter: parent.verticalCenter; rightPadding: 2 }
                    Btn { text: "Left"; visible: parent.pushHere; onClicked: sim.push(-1) }
                    Btn { text: "Right"; visible: parent.pushHere; onClicked: sim.push(1) }
                    Item { width: 6; height: 1; visible: parent.pushHere }
                    Btn { text: "Reset"; onClicked: sim.reset() }
                    Btn { text: "Step"; onClicked: sim.step() }
                    Btn {
                        text: sim.speed + "×"
                        onClicked: {
                            var s = sim.speeds
                            var i = s.indexOf(sim.speed)
                            sim.speed = s[(i + 1) % s.length]
                        }
                    }
                    Btn {
                        text: sim.running ? "Pause" : "Play"
                        filled: true
                        implicitWidth: 96
                        onClicked: sim.toggle()
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------ toast
    Rectangle {
        id: toast
        property string text
        anchors.horizontalCenter: parent.horizontalCenter
        y: dock.y + Theme.pad - height - Theme.gap
        width: Math.min(toastText.implicitWidth + 32, parent.width - 2 * Theme.pad)
        height: toastText.implicitHeight + 20
        radius: Theme.radiusButton
        color: Theme.text
        opacity: 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.fade } }
        Text {
            id: toastText
            anchors.centerIn: parent
            width: Math.min(implicitWidth, toast.parent.width - 2 * Theme.pad - 32)
            text: toast.text
            color: Theme.surface
            font.family: Theme.uiFont
            font.pixelSize: 14
            wrapMode: Text.Wrap
        }
        Timer { id: toastTimer; onTriggered: toast.opacity = 0 }
        Connections {
            target: sim
            function onMessage(kind, text) {
                toast.text = text
                toast.opacity = 1
                toastTimer.interval = kind === "error" ? 6000 : 2500
                toastTimer.restart()
            }
        }
    }

    // ------------------------------------------------------- debug overlay
    Rectangle {
        visible: Settings.overlay
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Theme.pad + 8
        width: overlayText.implicitWidth + 16
        height: overlayText.implicitHeight + 12
        radius: 8
        color: Theme.surface
        border.color: Theme.border
        opacity: 0.92
        Mono {
            id: overlayText
            x: 8
            y: 6
            font.pixelSize: 11
            text: sim.fpsText + " · " + frames.statsText
        }
    }

    // ------------------------------------------------- side-panel tile editor
    TileEditor {
        anchors.fill: parent
    }

    // -------------------------------------------------------- report sheet
    Connections {
        target: bench
        function onFinished() { shell.reportOpen = true }
        function onShowRequested() { shell.reportOpen = true }
    }
    Rectangle {
        anchors.fill: parent
        visible: shell.reportOpen
        color: Theme.background
        TapHandler {}
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme.pad
            spacing: Theme.gap
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "Performance report"
                    color: Theme.text
                    font.family: Theme.uiFont
                    font.pixelSize: Theme.title
                    font.weight: Font.DemiBold
                    Layout.fillWidth: true
                }
                Btn { text: bench.copied ? "Copied" : "Copy report"; filled: true; onClicked: bench.copy() }
                Btn { text: "Close"; onClicked: shell.reportOpen = false }
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
                    boundsBehavior: Flickable.StopAtBounds
                    Mono { id: reportText; text: bench.report; font.pixelSize: 12 }
                }
            }
        }
    }
}
