import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Model screen in sub-pages: the model (pendulum: base, segments, gravity; cart;
// actuator), the segments (length, mass, centre of mass, inertia, joint friction, initial
// state) and the simulation settings (time step, telemetry, sensor noise). Structural
// changes (base, number of segments) rebuild the controller's loops as in the Widgets
// app. Portrait: edits, tabs, the page. Landscape: tabs and the page, edits on the side.
Item {
    id: screen
    property bool shown: false
    readonly property bool wide: width >= height
    // the context object `model`, read here: inside a FormView (a ListView) `model` is its own property
    readonly property QtObject plantForm: model.plantModel
    readonly property QtObject simulationForm: model.simulationModel

    property int page: 0
    readonly property var pages: ["Model", "Segments", "Simulation settings"]
    readonly property var pageSections: [["pendulum", "cart", "actuator"], ["seg*"], []]
    readonly property QtObject pageForm: page === 2 ? simulationForm : plantForm

    component Page: ColumnLayout {
        id: pg
        property bool active
        spacing: Theme.gap
        SubPages {
            Layout.fillWidth: true
            pages: screen.pages
            current: screen.page
            onChosen: (i) => screen.page = i
        }
        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusPanel
            FormView {
                anchors.fill: parent
                anchors.margins: 12
                sections: screen.pageSections[screen.page]
                form: pg.active ? screen.pageForm : null
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
        ColumnLayout {
            Layout.preferredWidth: 340
            Layout.fillWidth: false            // (EditBar's fillWidth would make the column take everything)
            Layout.fillHeight: true
            spacing: Theme.gap
            EditBar {
                Layout.fillWidth: true
                target: model
                onReset: model.reset()
            }
            Item { Layout.fillHeight: true }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gap
        visible: !screen.wide
        EditBar {
            Layout.fillWidth: true
            target: model
            onReset: model.reset()
        }
        Page {
            Layout.fillWidth: true
            Layout.fillHeight: true
            active: !screen.wide
        }
    }
}
