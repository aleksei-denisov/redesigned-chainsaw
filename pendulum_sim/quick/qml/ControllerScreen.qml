import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Controller screen in sub-pages: the PID loops (each with its live error and P/I/D
// terms), the controller's global settings (with its live output and Reset integrators)
// and the LQR weights used for seeding (with Seed gains from LQR). Every edit goes to
// the running simulation at once. Portrait: edits, tabs, the page. Landscape: tabs and
// the page; edits, live output and the gain actions on the side.
Item {
    id: screen
    property bool shown: false
    readonly property bool wide: width >= height
    onShownChanged: controller.setActive(shown)

    property int page: 0
    readonly property var pages: ["Loops", "Controller", "LQR seeding"]
    readonly property var pageSections: [["loop*"], ["controller"], ["lqr"]]

    component LiveCard: Card {
        radius: Theme.radiusCard
        implicitHeight: liveCol.implicitHeight + 28
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
    component GainsCard: Card {
        id: gains
        property bool seed: true
        property bool integrators: true
        radius: Theme.radiusCard
        implicitHeight: actCol.implicitHeight + 28
        Column {
            id: actCol
            x: 14
            y: 14
            width: parent.width - 28
            spacing: 8
            Caption { text: "Gains" }
            Btn {
                width: parent.width
                visible: gains.seed
                text: "Seed gains from LQR"
                filled: true
                onClicked: controller.seedLqr()
            }
            Text {
                width: parent.width
                visible: gains.seed
                wrapMode: Text.Wrap
                text: "Designs a state-feedback (LQR) controller for the current plant with the "
                      + "“LQR weights for seeding” and sets every loop's Kp and Kd from it."
                color: Theme.textMuted
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
            Btn {
                width: parent.width
                visible: gains.integrators
                text: "Reset integrators"
                onClicked: controller.resetIntegrators()
            }
        }
    }

    component Page: ColumnLayout {
        id: pg
        property bool active
        property bool extras: false          // portrait: the page's cards above its list
        spacing: Theme.gap
        SubPages {
            Layout.fillWidth: true
            pages: screen.pages
            current: screen.page
            onChosen: (i) => screen.page = i
        }
        LiveCard {
            Layout.fillWidth: true
            visible: pg.extras && screen.page === 1
        }
        GainsCard {
            Layout.fillWidth: true
            visible: pg.extras && screen.page > 0
            seed: screen.page === 2
            integrators: screen.page === 1
        }
        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusPanel
            FormView {
                anchors.fill: parent
                anchors.margins: 12
                sections: screen.pageSections[screen.page]
                form: pg.active ? controller.model : null
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: Theme.gap
        visible: screen.wide
        Page {
            Layout.fillWidth: true
            Layout.fillHeight: true
            active: screen.wide
        }
        Flickable {
            Layout.preferredWidth: 340
            Layout.fillHeight: true
            contentHeight: side.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            Column {
                id: side
                width: parent.width
                spacing: Theme.gap
                EditBar {
                    width: parent.width
                    target: controller
                    onReset: controller.restoreGains()
                }
                LiveCard { width: parent.width }
                GainsCard { width: parent.width }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gap
        visible: !screen.wide
        EditBar {
            Layout.fillWidth: true
            target: controller
            onReset: controller.restoreGains()
        }
        Page {
            Layout.fillWidth: true
            Layout.fillHeight: true
            active: !screen.wide
            extras: true
        }
    }
}
