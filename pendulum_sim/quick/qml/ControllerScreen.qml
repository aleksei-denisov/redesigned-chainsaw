import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Controller screen: global settings, one section per PID loop (with its live error and
// P/I/D terms), the LQR weights used for seeding; on the side the controller's live
// output and the tuning actions. Every edit goes to the running simulation at once.
Item {
    id: screen
    property bool shown: false
    readonly property bool wide: width >= height
    onShownChanged: controller.setActive(shown)

    component Actions: Column {
        spacing: Theme.gap
        Card {
            width: parent.width
            implicitHeight: liveCol.implicitHeight + 28
            radius: Theme.radiusCard
            Column {
                id: liveCol
                x: 14
                y: 14
                width: parent.width - 28
                spacing: 6
                Caption { text: "Live output" }
                Mono {
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: controller.outputText.length ? controller.outputText.replace("Output: ", "") : "—"
                    font.pixelSize: 14
                }
            }
        }
        Card {
            width: parent.width
            implicitHeight: actCol.implicitHeight + 28
            radius: Theme.radiusCard
            Column {
                id: actCol
                x: 14
                y: 14
                width: parent.width - 28
                spacing: 8
                Caption { text: "Gains" }
                Btn {
                    width: parent.width
                    text: "Seed gains from LQR"
                    filled: true
                    onClicked: controller.seedLqr()
                }
                Text {
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: "Designs a state-feedback (LQR) controller for the current plant with the weights "
                          + "below and sets every loop's Kp and Kd from it."
                    color: Theme.textMuted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Btn { width: parent.width; text: "Restore previous gains"; onClicked: controller.restoreGains() }
                Btn { width: parent.width; text: "Reset integrators"; onClicked: controller.resetIntegrators() }
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: Theme.gap
        visible: screen.wide

        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusPanel
            FormView {
                anchors.fill: parent
                anchors.margins: 12
                form: screen.wide ? controller.model : null
            }
        }
        Flickable {
            Layout.preferredWidth: 340
            Layout.fillHeight: true
            contentHeight: sideActions.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            Actions { id: sideActions; width: parent.width }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gap
        visible: !screen.wide
        Actions { Layout.fillWidth: true }
        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusPanel
            FormView {
                anchors.fill: parent
                anchors.margins: 12
                form: screen.wide ? null : controller.model
            }
        }
    }
}
