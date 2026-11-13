import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Auto-tune screen: the robustness search of the shared worker, in three steps.
// Set-up: preset (with this device's time estimate) and Start. Running: progress, the
// best result so far with its gains, the best push per round and the candidates of the
// last round; Cancel keeps the best found so far. Result: headline, old → new gains,
// response metrics before / after and both responses to the same push; Apply (undoable
// on the Controller screen), Run again or Discard. The simulation pauses while it runs
// and the dock's screen buttons are locked (a foreground task, as agreed).
Item {
    id: screen
    property bool shown: false
    readonly property bool wide: width >= height
    onShownChanged: if (shown && autotune.page === "setup" && !autotune.busy) autotune.open()

    component Panel: Card {
        default property alias content: inner.data
        property string title
        radius: Theme.radiusCard
        implicitHeight: inner.implicitHeight + 28 + (title.length ? 22 : 0)
        Caption { x: 14; y: 14; text: parent.title; visible: parent.title.length > 0 }
        Column {
            id: inner
            x: 14
            y: 14 + (parent.title.length ? 22 : 0)
            width: parent.width - 28
            spacing: 8
        }
    }
    component Note: Text {
        width: parent ? parent.width : 0
        wrapMode: Text.Wrap
        color: Theme.textMuted
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    // ================================================================ set-up
    Flickable {
        anchors.fill: parent
        visible: autotune.page === "setup"
        contentHeight: setupCol.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        Column {
            id: setupCol
            width: Math.min(parent.width, 820)
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Theme.gap
            Panel {
                width: parent.width
                title: "Auto-tune"
                Body {
                    width: parent.width
                    text: "Searches for the controller that keeps the pendulum up after the largest possible "
                          + "push at the tip – all loops by LQR design plus integral gains, or only the loops and "
                          + "gains you choose, optionally within gain limits. The simulation pauses while it runs; "
                          + "you can cancel at any time and keep the best gains found so far."
                }
                Note { text: autotune.coresText }
            }
            Repeater {
                model: autotune.presets
                Card {
                    required property var modelData
                    readonly property bool chosen: autotune.preset === modelData.key
                    width: setupCol.width
                    implicitHeight: presetCol.implicitHeight + 28
                    radius: Theme.radiusCard
                    border.color: chosen ? Theme.accent : Theme.border
                    border.width: chosen ? 2 : 1
                    color: chosen ? Theme.accentSoft : Theme.surface
                    Column {
                        id: presetCol
                        x: 16
                        y: 14
                        width: parent.width - 32
                        spacing: 4
                        Row {
                            spacing: 10
                            Text {
                                text: modelData.name
                                color: Theme.text
                                font.family: Theme.uiFont
                                font.pixelSize: 18
                                font.weight: Font.DemiBold
                            }
                            Text {
                                text: modelData.description
                                color: Theme.textMuted
                                font.family: Theme.uiFont
                                font.pixelSize: 14
                            }
                        }
                        Mono { text: modelData.estimate; color: Theme.textMuted; font.pixelSize: 13 }
                    }
                    TapHandler { onTapped: autotune.choose(modelData.key) }
                }
            }
            Panel {
                width: parent.width
                title: "Options"
                TuneOptions { width: parent.width }
            }
            Btn {
                width: parent.width
                text: "Start auto-tune"
                filled: true
                onClicked: autotune.start()
            }
        }
    }

    // =============================================================== running
    GridLayout {
        anchors.fill: parent
        visible: autotune.page === "running"
        columns: screen.wide ? 2 : 1
        rowSpacing: Theme.gap
        columnSpacing: Theme.gap

        ColumnLayout {
            Layout.preferredWidth: screen.wide ? 380 : -1
            Layout.fillWidth: !screen.wide
            Layout.fillHeight: screen.wide
            spacing: Theme.gap
            Panel {
                Layout.fillWidth: true
                title: "Progress"
                Text {
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: autotune.phaseText
                    color: Theme.text
                    font.family: Theme.uiFont
                    font.pixelSize: Theme.body
                }
                Rectangle {
                    width: parent.width
                    height: 8
                    radius: 4
                    color: Theme.border
                    Rectangle {
                        width: parent.width * Math.max(0, Math.min(1, autotune.fraction))
                        height: parent.height
                        radius: 4
                        color: Theme.accent
                        Behavior on width { NumberAnimation { duration: 90 } }
                    }
                }
                Mono { width: parent.width; wrapMode: Text.Wrap; text: autotune.timesText; font.pixelSize: 13 }
                Note { text: autotune.activityText }
            }
            Panel {
                Layout.fillWidth: true
                title: "Best so far"
                Text {
                    width: parent.width
                    wrapMode: Text.Wrap
                    textFormat: Text.StyledText
                    text: autotune.bestText
                    color: Theme.text
                    font.family: Theme.uiFont
                    font.pixelSize: Theme.body
                }
                Mono {
                    width: parent.width
                    wrapMode: Text.Wrap
                    visible: text.length > 0
                    text: autotune.bestGains
                    color: Theme.accentSoftText
                    font.pixelSize: 13
                }
            }
            Item { Layout.fillHeight: true; visible: screen.wide }
            Btn {
                Layout.fillWidth: true
                text: "Cancel – keep the best so far"
                onClicked: autotune.cancel()
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.gap
            Card {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: 2
                radius: Theme.radiusCard
                Caption { x: 14; y: 12; text: "Best recoverable push per round" }
                MiniChart {
                    anchors.fill: parent
                    anchors.margins: 12
                    anchors.topMargin: 34
                    unit: "N"
                    xLabel: "round"
                    series: [{ ys: autotune.history, color: Theme.accent, width: 2.5 }]
                }
            }
            Card {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: 1
                radius: Theme.radiusCard
                Caption { x: 14; y: 12; text: "Candidates of this round (recoverable push)" }
                MiniChart {
                    anchors.fill: parent
                    anchors.margins: 12
                    anchors.topMargin: 34
                    unit: "N"
                    zeroLine: true
                    series: [{ ys: autotune.candidates, color: Theme.angle, dots: true }]
                }
            }
        }
    }

    // ================================================================ result
    GridLayout {
        anchors.fill: parent
        visible: autotune.page === "result"
        columns: screen.wide ? 2 : 1
        rowSpacing: Theme.gap
        columnSpacing: Theme.gap

        Flickable {
            Layout.preferredWidth: screen.wide ? 420 : -1
            Layout.fillWidth: !screen.wide
            Layout.fillHeight: true
            Layout.preferredHeight: screen.wide ? -1 : screen.height * 0.55
            contentHeight: resultCol.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            Column {
                id: resultCol
                width: parent.width
                spacing: Theme.gap
                Panel {
                    width: parent.width
                    Text {
                        width: parent.width
                        wrapMode: Text.Wrap
                        textFormat: Text.StyledText
                        text: autotune.headline
                        color: Theme.text
                        font.family: Theme.uiFont
                        font.pixelSize: 19
                        font.weight: Font.DemiBold
                    }
                    Note { text: autotune.notes; textFormat: Text.StyledText }
                }
                Panel {
                    width: parent.width
                    title: "Gains"
                    Caption { text: "Before"; color: Theme.textMuted }
                    Mono { width: parent.width; wrapMode: Text.Wrap; text: autotune.oldGains; color: Theme.textMuted; font.pixelSize: 13 }
                    Caption { text: "After"; color: Theme.accentSoftText }
                    Mono { width: parent.width; wrapMode: Text.Wrap; text: autotune.newGains; font.pixelSize: 13 }
                }
                Panel {
                    width: parent.width
                    title: "Response to the same push"
                    visible: autotune.metrics.length > 0
                    Repeater {
                        model: autotune.metrics
                        Row {
                            required property var modelData
                            width: parent.width
                            Text {
                                width: parent.width * 0.42
                                wrapMode: Text.Wrap
                                text: modelData.name
                                color: Theme.textMuted
                                font.family: Theme.uiFont
                                font.pixelSize: 13
                            }
                            Mono { width: parent.width * 0.29; text: modelData.before; color: Theme.textMuted; font.pixelSize: 13 }
                            Mono { width: parent.width * 0.29; text: modelData.after; font.pixelSize: 13 }
                        }
                    }
                }
                Btn { width: parent.width; text: "Apply these gains"; filled: true; enabled: autotune.canApply; onClicked: autotune.apply() }
                Row {
                    width: parent.width
                    spacing: 8
                    Btn { width: (parent.width - 8) / 2; text: "Run again"; onClicked: autotune.rerun() }
                    Btn { width: (parent.width - 8) / 2; text: "Discard"; onClicked: autotune.close() }
                }
            }
        }
        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusCard
            visible: autotune.preview.length > 0
            Caption { x: 14; y: 12; text: "θ1 after the test push · before / after" }
            Row {
                anchors.right: parent.right
                anchors.rightMargin: 14
                y: 10
                spacing: 12
                Row { spacing: 5; Rectangle { width: 14; height: 2; color: Theme.textMuted; anchors.verticalCenter: parent.verticalCenter }
                      Mono { text: "before"; color: Theme.textMuted; font.pixelSize: 12 } }
                Row { spacing: 5; Rectangle { width: 14; height: 2; color: Theme.accent; anchors.verticalCenter: parent.verticalCenter }
                      Mono { text: "after"; color: Theme.textMuted; font.pixelSize: 12 } }
            }
            MiniChart {
                anchors.fill: parent
                anchors.margins: 12
                anchors.topMargin: 36
                unit: "°"
                xLabel: "s"
                zeroLine: true
                series: autotune.preview.map((p, i) => ({ xs: p.t, ys: p.y, color: i === 0 ? Theme.textMuted : Theme.accent,
                                                          width: i === 0 ? 1.5 : 2.5 }))
            }
        }
    }
}
