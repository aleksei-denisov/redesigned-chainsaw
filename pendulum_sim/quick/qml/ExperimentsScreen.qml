import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Experiments screen: run the current set-up for a fixed time (computed at full speed,
// deterministic), change gains on the Controller screen, run again and compare. Left:
// settings (duration, settling band, same start, a scheduled tip push), name, Run /
// Cancel with progress. Right: the comparison (angle, control error with the settling
// band, actuator output, cart position; segment chips) and one card per run with its
// status, metrics and gains – Show / Hide, Use gains (undoable), Remove. Portrait: the
// set-up and the runs as sub-pages, the run bar below both; a finished run opens Runs.
Item {
    id: screen
    property bool shown: false
    readonly property bool wide: width >= height
    // the context object, read here: inside a FormView (a ListView) `model` means its own model
    readonly property QtObject settingsForm: experiments.model
    property int page: 0                     // portrait: 0 set-up, 1 runs
    property int runCount: experiments.runs.length
    property int seenRuns: 0
    onRunCountChanged: { if (runCount > seenRuns) page = 1; seenRuns = runCount }    // a run finished: show it

    component RunBar: Column {
        spacing: 8
        Field {
            width: parent.width
            placeholder: "Name – " + experiments.placeholder
            text: experiments.name
            onAccepted: experiments.name = text
        }
        Rectangle {
            width: parent.width
            height: 6
            radius: 3
            color: Theme.border
            visible: experiments.running
            Rectangle {
                width: parent.width * Math.max(0, Math.min(1, experiments.progress))
                height: parent.height
                radius: 3
                color: Theme.accent
            }
        }
        Row {
            width: parent.width
            spacing: 8
            Btn {
                width: (parent.width - 8) * 0.35
                text: "Cancel"
                enabled: experiments.running
                onClicked: experiments.cancel()
            }
            Btn {
                width: (parent.width - 8) * 0.65
                text: experiments.running ? "Running…" : "Run experiment"
                filled: true
                enabled: !experiments.running
                onClicked: experiments.run()
            }
        }
    }

    component Comparison: Column {
        id: cmp
        property bool active: true            // only the shown copy builds its charts and cards
        spacing: Theme.gap
        Card {
            width: parent.width
            visible: experiments.runs.length === 0
            implicitHeight: emptyText.implicitHeight + 32
            radius: Theme.radiusCard
            Body {
                id: emptyText
                x: 16
                y: 16
                width: parent.width - 32
                color: Theme.textMuted
                text: "Run the current configuration, change gains on the Controller screen, run again and "
                      + "compare. Runs are deterministic: with “Same start” every run begins from exactly the "
                      + "same state, so only the controller differs."
            }
        }
        Row {
            visible: experiments.segmentCount > 1
            spacing: 6
            Caption { text: "Segment"; anchors.verticalCenter: parent.verticalCenter; rightPadding: 4 }
            Repeater {
                model: experiments.segmentCount
                Btn {
                    required property int index
                    text: "θ" + (index + 1)
                    active: experiments.segment === index
                    onClicked: experiments.segment = index
                }
            }
        }
        Grid {
            id: plotGrid
            width: parent.width
            columns: width > 640 ? 2 : 1
            spacing: Theme.gap
            Repeater {
                model: cmp.active ? experiments.plots : []
                Card {
                    required property var modelData
                    width: (plotGrid.width - (plotGrid.columns - 1) * plotGrid.spacing) / plotGrid.columns
                    height: 220
                    radius: Theme.radiusCard
                    Caption { x: 14; y: 12; text: modelData.title }
                    MiniChart {
                        anchors.fill: parent
                        anchors.margins: 12
                        anchors.topMargin: 34
                        unit: modelData.unit
                        xLabel: "s"
                        hlines: modelData.hlines
                        series: modelData.series.map(s => ({ xs: s.xs, ys: s.ys, color: Theme.runColor(s.color), width: 2 }))
                    }
                }
            }
        }
        Repeater {
            model: cmp.active ? experiments.runs : []
            Card {
                required property var modelData
                width: parent.width
                implicitHeight: runCol.implicitHeight + 24
                radius: Theme.radiusCard
                opacity: modelData.visible ? 1.0 : 0.6
                Column {
                    id: runCol
                    x: 14
                    y: 12
                    width: parent.width - 28
                    spacing: 6
                    Row {
                        width: parent.width
                        spacing: 10
                        Rectangle {
                            width: 16; height: 16; radius: 4
                            color: Theme.runColor(modelData.color)
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            width: parent.width - 120
                            elide: Text.ElideRight
                            text: modelData.name
                            color: Theme.text
                            font.family: Theme.uiFont
                            font.pixelSize: 17
                            font.weight: Font.DemiBold
                        }
                        Text {
                            text: modelData.status
                            color: modelData.stable ? Theme.success : Theme.danger
                            font.family: Theme.uiFont
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                        }
                    }
                    Flow {
                        width: parent.width
                        spacing: 14
                        Repeater {
                            model: modelData.metrics
                            Row {
                                required property var modelData
                                spacing: 5
                                Text {
                                    text: modelData.name
                                    color: Theme.textMuted
                                    font.family: Theme.uiFont
                                    font.pixelSize: 13
                                }
                                Mono { text: modelData.value; font.pixelSize: 13 }
                            }
                        }
                    }
                    Mono {
                        width: parent.width
                        wrapMode: Text.Wrap
                        text: modelData.gains
                        color: Theme.textMuted
                        font.pixelSize: 12
                    }
                    Flow {
                        width: parent.width
                        spacing: 6
                        Btn {
                            text: modelData.visible ? "Hide" : "Show"
                            textSize: 14
                            implicitHeight: 40
                            onClicked: experiments.toggleVisible(modelData.index)
                        }
                        Btn {
                            text: "Use gains"
                            textSize: 14
                            implicitHeight: 40
                            onClicked: experiments.useGains(modelData.index)
                        }
                        Btn {
                            text: "Remove"
                            textSize: 14
                            implicitHeight: 40
                            onClicked: experiments.remove(modelData.index)
                        }
                    }
                }
            }
        }
        Btn {
            width: parent.width
            visible: experiments.runs.length > 1
            text: "Remove all runs"
            onClicked: experiments.clear()
        }
    }

    // landscape: set-up and run bar on the left, the comparison on the right
    RowLayout {
        anchors.fill: parent
        spacing: Theme.gap
        visible: screen.wide
        ColumnLayout {
            Layout.preferredWidth: 380
            Layout.fillWidth: false            // (the cards' fillWidth would make the column take everything)
            Layout.fillHeight: true
            spacing: Theme.gap
            Card {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Theme.radiusPanel
                FormView {
                    anchors.fill: parent
                    anchors.margins: 12
                    form: screen.wide ? screen.settingsForm : null
                }
            }
            Card {
                Layout.fillWidth: true
                implicitHeight: wideRunBar.implicitHeight + 24
                radius: Theme.radiusCard
                RunBar { id: wideRunBar; x: 12; y: 12; width: parent.width - 24 }
            }
        }
        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentHeight: wideComparison.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            Comparison { id: wideComparison; width: parent.width; active: screen.wide }
        }
    }

    // portrait: sub-pages Set-up / Runs, the run bar always at the bottom
    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gap
        visible: !screen.wide
        SubPages {
            Layout.fillWidth: true
            pages: ["Set-up", experiments.runs.length ? "Runs (" + experiments.runs.length + ")" : "Runs"]
            current: screen.page
            onChosen: (i) => screen.page = i
        }
        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: screen.page === 0
            radius: Theme.radiusPanel
            FormView {
                anchors.fill: parent
                anchors.margins: 12
                form: screen.wide ? null : screen.settingsForm
            }
        }
        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: screen.page === 1
            contentHeight: comparison.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            Comparison { id: comparison; width: parent.width; active: !screen.wide && screen.page === 1 }
        }
        Card {
            Layout.fillWidth: true
            implicitHeight: runBar.implicitHeight + 24
            radius: Theme.radiusCard
            RunBar { id: runBar; x: 12; y: 12; width: parent.width - 24 }
        }
    }
}
