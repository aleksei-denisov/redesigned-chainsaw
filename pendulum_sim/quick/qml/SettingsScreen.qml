import QtQuick
import QtQuick.Layouts
import Pendulum 1.0
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
            }

            Section {
                title: "Scenario"
                Body { width: parent.width; text: Sim.scenarioName; color: Theme.textMuted }
                Flow {
                    width: parent.width
                    spacing: 6
                    Repeater {
                        model: Scenarios.items
                        Btn {
                            required property var modelData
                            text: modelData.name
                            textSize: 14
                            implicitHeight: Theme.touch
                            active: modelData.name === Sim.scenarioName
                            onClicked: Scenarios.load(modelData.key)
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
                        text: "now " + Frames.rate + " fps"
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
                    options: Sim.processAvailable ? [["process", "Separate process"], ["thread", "Thread"]]
                                                  : [["thread", "Thread"]]
                    value: Sim.physics
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
                        text: Bench.running ? "Cancel" : "Run performance test"
                        filled: !Bench.running
                        onClicked: Bench.running ? Bench.cancel() : Bench.start()
                    }
                    Btn {
                        text: "Show report"
                        visible: Bench.report.length > 0 && !Bench.running
                        onClicked: Bench.show()
                    }
                }
                Mono { text: Bench.progress; color: Theme.textMuted; visible: Bench.running }
            }
        }
    }
}
