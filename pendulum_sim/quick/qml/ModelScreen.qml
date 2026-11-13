import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Model screen: the pendulum (base, segments, gravity), cart, actuator and each segment
// (length, mass, centre of mass, inertia, joint friction, initial state); on the side
// the simulation settings (time step, telemetry, sensor noise). Structural changes
// (base, number of segments) rebuild the controller's loops as in the Widgets app.
Item {
    id: screen
    property bool shown: false
    readonly property bool wide: width >= height
    // the context object `model`, read here: inside a FormView (a ListView) `model` is its own property
    readonly property QtObject plantForm: model.plantModel
    readonly property QtObject simulationForm: model.simulationModel

    GridLayout {
        anchors.fill: parent
        columns: screen.wide ? 2 : 1
        rowSpacing: Theme.gap
        columnSpacing: Theme.gap

        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredHeight: screen.wide ? -1 : screen.height * 0.6
            radius: Theme.radiusPanel
            FormView {
                anchors.fill: parent
                anchors.margins: 12
                form: screen.plantForm
            }
        }
        ColumnLayout {
            Layout.preferredWidth: screen.wide ? 380 : -1
            Layout.fillWidth: !screen.wide
            Layout.fillHeight: true
            spacing: Theme.gap
            EditBar {
                Layout.fillWidth: true
                target: model
                onReset: model.reset()
            }
            Card {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Theme.radiusPanel
                FormView {
                    anchors.fill: parent
                    anchors.margins: 12
                    form: screen.simulationForm
                }
            }
        }
    }
}
