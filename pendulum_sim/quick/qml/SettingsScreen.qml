import QtQuick
import QtQuick.Layouts
import PendulumApp 1.0
import Instrument 1.0

// Settings (Phase 2 subset): theme, scenario, performance options and the performance
// test. Vertical touch scrolling only.
Item {
    id: screen
    property bool shown: false

    component Section: Card {
        id: section
        default property alias content: body.data
        property string title
        Layout.fillWidth: true
        implicitHeight: body.implicitHeight + 52
        Caption { x: Theme.pad; y: 14; text: section.title }
        Column {
            id: body
            x: Theme.pad
            y: 40
            width: section.width - 2 * Theme.pad
            spacing: 10
        }
    }

    Flickable {
        id: flick
        anchors.fill: parent
        contentHeight: column.implicitHeight
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        clip: true

        ColumnLayout {
            id: column
            width: flick.width
            spacing: Theme.gap

            Section {
                title: "Appearance"
                Seg {
                    title: "Theme"
                    options: [["system", "System"], ["light", "Light"], ["dark", "Dark"]]
                    value: Settings.theme
                    onPicked: (v) => Settings.theme = v
                }
                Seg {
                    title: "Trajectory"
                    options: [["off", "Off"], ["tip", "Tip"], ["all", "All joints"]]
                    value: trail.mode
                    onPicked: (v) => trail.mode = v
                }
                Seg {
                    title: "Trajectory length"
                    visible: trail.mode !== "off"
                    options: trail.secondsOptions.map(s => [s, s + " s"])
                    value: trail.seconds
                    onPicked: (v) => trail.seconds = v
                }
            }

            Section {
                title: "Side panel (Simulate screen)"
                Body {
                    width: parent.width
                    color: Theme.textMuted
                    text: "Long-press any tile on the right of the Simulate screen (under the pendulum in "
                          + "portrait) to edit the panel. While editing:\n"
                          + "•  tap a tile to choose what it shows – a value, a graph or the controller status – "
                          + "and how (size, colour, curves, time window, scale, smoothing, height);\n"
                          + "•  drag a tile by its ≡ grip onto another tile to move it there;\n"
                          + "•  tap × to remove a tile, + Value / + Graph / + Status to add one;\n"
                          + "•  tap Done or press Back to finish. Changes are kept at once."
                }
                Flow {
                    width: parent.width
                    spacing: 10
                    Mono {
                        height: Theme.touch
                        verticalAlignment: Text.AlignVCenter
                        text: "Layout: " + panel.layoutName
                    }
                    Btn { text: "Edit panel now"; onClicked: { panel.editing = true; App.screen = "simulate" } }
                    Btn { text: "Reset to default"; onClicked: panel.reset() }
                }
                Flow {
                    width: parent.width
                    spacing: 10
                    Field {
                        id: layoutName
                        width: 220
                        placeholder: "Layout name"
                        onAccepted: {}
                    }
                    Btn {
                        text: "Save current as"
                        filled: true
                        enabled: layoutName.text.trim().length > 0
                        onClicked: if (panel.saveAs(layoutName.text)) layoutName.text = ""
                    }
                }
                Caption { text: "Saved layouts"; visible: panel.layoutNames.length > 0 }
                Repeater {
                    model: panel.layoutNames
                    Flow {
                        required property string modelData
                        width: parent.width
                        spacing: 8
                        Mono {
                            width: 180
                            height: Theme.touch
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                            text: modelData
                            color: modelData === panel.layoutName ? Theme.accentSoftText : Theme.text
                        }
                        Btn { text: "Load"; active: modelData === panel.layoutName; onClicked: panel.load(modelData) }
                        Btn { text: "Delete"; onClicked: panel.deleteLayout(modelData) }
                    }
                }
                Seg {
                    title: "Smoothing of new graphs"
                    options: [["off", "Off"], ["light", "Light"], ["medium", "Medium"], ["strong", "Strong"]]
                    value: graphs.smoothing
                    onPicked: (v) => graphs.smoothing = v
                }
                Body {
                    width: parent.width
                    color: Theme.textMuted
                    font.pixelSize: 13
                    text: "Smoothing is for display only (a centred moving average over 10, 30 or 100 ms); "
                          + "it also applies to the Graphs screen. Each graph tile keeps its own setting."
                }
            }

            Section {
                title: "Scenario"
                Body { width: parent.width; text: sim.scenarioName; color: Theme.textMuted }
                Flow {
                    width: parent.width
                    spacing: 6
                    Repeater {
                        model: scenarios.items
                        Btn {
                            required property var modelData
                            text: modelData.name
                            textSize: 14
                            implicitHeight: Theme.touch
                            active: modelData.name === sim.scenarioName
                            onClicked: scenarios.load(modelData.key)
                        }
                    }
                }
            }

            Section {
                title: "Performance"
                Flow {
                    width: parent.width
                    spacing: 10
                    Seg {
                        title: "Frame rate"
                        options: Settings.frameRates.map(r => [r, r === "auto" ? "Auto" : r])
                        value: Settings.frameRate
                        onPicked: (v) => Settings.frameRate = v
                    }
                    Mono {
                        height: Theme.touch
                        verticalAlignment: Text.AlignVCenter
                        text: "now " + frames.rate + " fps"
                        color: Theme.textMuted
                    }
                }
                Seg {
                    title: "Graph refresh"
                    options: Settings.graphRates.map(r => [r, r + " Hz"])
                    value: Settings.graphRate
                    onPicked: (v) => Settings.graphRate = v
                }
                Seg {
                    title: "Physics"
                    options: sim.processAvailable ? [["process", "Separate process"], ["thread", "Thread"]]
                                                  : [["thread", "Thread"]]
                    value: sim.physics
                    onPicked: (v) => Settings.physics = v
                }
                Seg {
                    title: "Debug overlay"
                    options: [[true, "On"], [false, "Off"]]
                    value: Settings.overlay
                    onPicked: (v) => Settings.overlay = v
                }
            }

            Section {
                title: "Performance test"
                Body {
                    width: parent.width
                    color: Theme.textMuted
                    text: "Runs the app through a few situations (about 45 s; leave the device alone meanwhile) "
                          + "and writes a report to copy."
                }
                Row {
                    spacing: 10
                    Btn {
                        text: bench.running ? "Cancel" : "Run performance test"
                        filled: !bench.running
                        onClicked: bench.running ? bench.cancel() : bench.start()
                    }
                    Btn {
                        text: "Show report"
                        visible: bench.report.length > 0 && !bench.running
                        onClicked: bench.show()
                    }
                }
                Mono { text: bench.progress; color: Theme.textMuted; visible: bench.running }
            }
        }
    }
}
