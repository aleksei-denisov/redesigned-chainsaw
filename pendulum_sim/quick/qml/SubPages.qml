import QtQuick
import Instrument 1.0

// Sub-page tabs of a screen (Model, Controller, Auto-tune, Experiments, Settings): one
// chip per page, the current one highlighted. The screen keeps the current page (so it
// survives a rotation) and changes it in onChosen.
Flow {
    id: tabs
    property var pages: []                    // titles
    property int current: 0
    signal chosen(int index)
    spacing: 6
    Repeater {
        model: tabs.pages
        Btn {
            required property int index
            required property var modelData
            text: modelData
            active: tabs.current === index
            onClicked: tabs.chosen(index)
        }
    }
}
