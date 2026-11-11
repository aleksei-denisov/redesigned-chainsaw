import QtQuick
import PendulumApp 1.0
import Instrument 1.0

// Root window. Shows the loading screen on the very first frame; the app shell (and
// with it the Simulate screen) is loaded asynchronously once the backend is ready and
// fades in. Only App and Settings exist when this file loads.
Window {
    id: root
    visible: true
    width: 1138
    height: 637
    title: "Pendulum Quick"
    color: Theme.background

    property int swaps: 0                 // frames presented (read by the performance test)
    property bool askExit: false          // set by Python on Back from the Simulate screen
    property bool shellReported: false

    onFrameSwapped: {
        swaps += 1
        if (swaps === 1)
            App.loadingFrame()
        else if (!shellReported && shell.status === Loader.Ready && loading.opacity === 0) {
            shellReported = true
            App.shellFrame()
        }
    }

    // Android Back that no item handled: navigate instead of closing
    onClosing: (close) => {
        if (App.ready && !exitDialog.confirmed) {
            close.accepted = false
            App.back()
        }
    }

    // vsync-driven frame loop; lives here because this file is loaded synchronously
    FrameAnimation {
        running: shell.status === Loader.Ready && shell.item.framesEnabled
        onTriggered: shell.item.vsync()
    }

    Loader {
        id: shell
        anchors.fill: parent
        asynchronous: true
        active: App.ready
        source: "Shell.qml"
        opacity: status === Loader.Ready ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.fade } }
        onLoaded: {
            item.forceActiveFocus()
            App.shellReady()
        }
        onStatusChanged: if (status === Loader.Error) App.shellFailed()
    }

    // ------------------------------------------------------------ loading screen
    Rectangle {
        id: loading
        anchors.fill: parent
        color: Theme.background
        visible: opacity > 0
        opacity: shell.status === Loader.Ready ? 0 : 1
        Behavior on opacity { NumberAnimation { duration: Theme.fade } }

        Column {
            anchors.centerIn: parent
            width: Math.min(parent.width - 2 * Theme.pad, 420)
            spacing: 14
            Text {
                text: "Inverted Pendulum"
                color: Theme.text
                font.family: Theme.uiFont
                font.pixelSize: 28
                font.weight: Font.DemiBold
            }
            Caption { text: "Simulator" }
            Rectangle {
                width: parent.width
                height: 4
                radius: 2
                color: Theme.border
                Rectangle {
                    width: parent.width * App.progress
                    height: parent.height
                    radius: 2
                    color: Theme.accent
                    Behavior on width { NumberAnimation { duration: Theme.fade } }
                }
            }
            Text {
                width: parent.width
                text: App.status
                color: Theme.textMuted
                font.family: Theme.uiFont
                font.pixelSize: 13
                wrapMode: Text.Wrap
            }
        }
    }

    // ------------------------------------------------------------- exit dialog
    Rectangle {
        id: exitDialog
        property bool confirmed: false
        anchors.fill: parent
        visible: root.askExit
        color: Qt.rgba(0, 0, 0, Theme.dark ? 0.55 : 0.35)
        TapHandler { onTapped: root.askExit = false }
        Card {
            anchors.centerIn: parent
            width: Math.min(parent.width - 2 * Theme.pad, 380)
            height: dialogColumn.implicitHeight + 2 * Theme.pad
            TapHandler {}                         // taps on the card do not close it
            Column {
                id: dialogColumn
                x: Theme.pad
                y: Theme.pad
                width: parent.width - 2 * Theme.pad
                spacing: Theme.gap
                Text {
                    text: "Exit the app?"
                    color: Theme.text
                    font.family: Theme.uiFont
                    font.pixelSize: 22
                    font.weight: Font.DemiBold
                }
                Body { width: parent.width; text: "The simulation stops." ; color: Theme.textMuted }
                Row {
                    anchors.right: parent.right
                    spacing: 10
                    Btn { text: "Cancel"; onClicked: root.askExit = false }
                    Btn {
                        text: "Exit"
                        filled: true
                        onClicked: {
                            exitDialog.confirmed = true
                            Qt.quit()
                        }
                    }
                }
            }
        }
    }
}
