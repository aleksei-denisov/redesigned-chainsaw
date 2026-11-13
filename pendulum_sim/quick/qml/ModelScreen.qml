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

    // landscape: the model on the left, edits and simulation settings on the right
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
                form: screen.wide ? screen.plantForm : null
            }
        }
        ColumnLayout {
            Layout.preferredWidth: 380
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
                    form: screen.wide ? screen.simulationForm : null
                }
            }
        }
    }

    // portrait: edits on top, then one list – the model or the simulation settings
    ColumnLayout {
        id: portrait
        anchors.fill: parent
        spacing: Theme.gap
        visible: !screen.wide
        property bool simulation: false
        EditBar {
            Layout.fillWidth: true
            target: model
            onReset: model.reset()
        }
        Row {
            spacing: 6
            Btn { text: "Model"; active: !portrait.simulation; onClicked: portrait.simulation = false }
            Btn { text: "Simulation settings"; active: portrait.simulation; onClicked: portrait.simulation = true }
        }
        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusPanel
            FormView {
                anchors.fill: parent
                anchors.margins: 12
                form: screen.wide ? null : (portrait.simulation ? screen.simulationForm : screen.plantForm)
            }
        }
    }
}
