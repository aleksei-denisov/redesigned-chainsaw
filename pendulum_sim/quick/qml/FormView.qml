import QtQuick
import Instrument 1.0

// A parameter form (backend FormModel: rows of sections and fields) as a touch list.
// Section header: tap to fold; its note (e.g. a loop's live output) in monospace.
// Number: label, value (tap to type), − / slider / +; the slider follows the shared
// mapping (session/sliders.py through form.position / form.valueAt), moves only with
// a deliberate sideways drag (vertical movement scrolls the list, a tap does nothing),
// and applies on release, so the simulation is not rebuilt for every pixel of a drag.
// Switch, choice (chips) and stepper (− n +) rows. Rows that cannot be edited are dimmed.
// Folding a section keeps the scroll position (the model inserts / removes its rows).
ListView {
    id: list
    property QtObject form
    // the sections this list shows (a sub-page of a screen; "loop*" matches by prefix), [] = all
    property var sections: []
    model: form
    function showSections() { if (form) form.setSections(sections) }
    onFormChanged: showSections()
    onSectionsChanged: showSections()
    clip: true
    spacing: 0
    boundsBehavior: Flickable.StopAtBounds
    reuseItems: false
    cacheBuffer: 600

    function fmt(v, decimals) {
        if (v === undefined || v === null || isNaN(v))
            return "—"
        return Number(v).toFixed(Math.max(0, decimals))
    }

    delegate: Item {
        id: row
        required property int index
        required property string rowType
        required property string key
        required property string label
        required property var kind
        required property var unit
        required property var decimals
        required property var minimum
        required property var maximum
        required property var hint
        required property var options
        required property var value
        required property var editable
        required property var section
        required property var expanded
        required property var note
        width: ListView.view.width
        height: loader.item ? loader.item.implicitHeight : 0

        Loader {
            id: loader
            width: parent.width
            sourceComponent: row.rowType === "section" ? sectionRow
                           : row.kind === "number" ? numberRow
                           : row.kind === "switch" ? switchRow
                           : row.kind === "choice" ? choiceRow
                           : row.kind === "stepper" ? stepperRow : null
        }

        // ------------------------------------------------------- section
        Component {
            id: sectionRow
            Item {
                implicitHeight: header.height + (noteText.visible ? noteText.implicitHeight + 8 : 0) + (row.index > 0 ? 14 : 0)
                Rectangle {
                    id: header
                    y: row.index > 0 ? 14 : 0
                    width: parent.width
                    height: 52
                    radius: Theme.radiusButton
                    color: tap.pressed ? Theme.border : Theme.surfaceSubtle
                    border.color: Theme.border
                    Text {
                        x: 14
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - 60
                        elide: Text.ElideRight
                        text: row.label
                        color: Theme.text
                        font.family: Theme.uiFont
                        font.pixelSize: 17
                        font.weight: Font.DemiBold
                    }
                    Text {
                        anchors.right: parent.right
                        anchors.rightMargin: 16
                        anchors.verticalCenter: parent.verticalCenter
                        text: row.expanded ? "▾" : "▸"
                        color: Theme.textMuted
                        font.pixelSize: 18
                    }
                    TapHandler { id: tap; onTapped: list.form.setExpanded(row.section, !row.expanded) }
                }
                Mono {
                    id: noteText
                    visible: text.length > 0
                    x: 14
                    anchors.top: header.bottom
                    anchors.topMargin: 6
                    width: parent.width - 28
                    wrapMode: Text.Wrap
                    text: row.note || ""
                    color: Theme.textMuted
                    font.pixelSize: 13
                }
            }
        }

        // -------------------------------------------------------- number
        Component {
            id: numberRow
            Item {
                id: num
                property bool dragging: false
                property real dragT: 0
                property bool typing: false
                readonly property real shown: dragging ? list.form.valueAt(row.key, dragT) : Number(row.value)
                implicitHeight: col.implicitHeight + 16
                opacity: row.editable ? 1.0 : 0.45
                Column {
                    id: col
                    x: 14
                    y: 8
                    width: parent.width - 28
                    spacing: 4
                    Item {
                        width: parent.width
                        height: 34
                        Text {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - valueBox.width - 12
                            elide: Text.ElideRight
                            text: row.label
                            color: Theme.text
                            font.family: Theme.uiFont
                            font.pixelSize: Theme.body
                        }
                        Item {
                            id: valueBox
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            width: Math.max(140, valueText.implicitWidth + unitText.implicitWidth + 30)
                            height: 34
                            Rectangle {
                                anchors.fill: parent
                                radius: 8
                                visible: !num.typing
                                color: valueTap.pressed ? Theme.border : "transparent"
                                border.color: Theme.border
                            }
                            Mono {
                                id: valueText
                                visible: !num.typing
                                anchors.right: unitText.left
                                anchors.rightMargin: 6
                                anchors.verticalCenter: parent.verticalCenter
                                text: list.fmt(num.shown, row.decimals)
                                color: num.dragging ? Theme.accentSoftText : Theme.text
                                font.pixelSize: 16
                            }
                            Mono {
                                id: unitText
                                visible: !num.typing
                                anchors.right: parent.right
                                anchors.rightMargin: 10
                                anchors.verticalCenter: parent.verticalCenter
                                text: row.unit || ""
                                color: Theme.textMuted
                                font.pixelSize: 13
                            }
                            TapHandler {
                                id: valueTap
                                enabled: row.editable
                                onTapped: {
                                    num.typing = true
                                    typed.text = list.fmt(row.value, row.decimals)
                                    typed.edit()
                                }
                            }
                            Field {
                                id: typed
                                visible: num.typing
                                anchors.fill: parent
                                mono: true
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                onAccepted: {
                                    if (!num.typing)
                                        return
                                    num.typing = false
                                    var v = parseFloat(text.replace(",", "."))
                                    if (!isNaN(v))
                                        list.form.setValue(row.key, Math.min(Math.max(v, row.minimum), row.maximum))
                                }
                            }
                        }
                    }
                    Row {
                        width: parent.width
                        spacing: 8
                        Btn {
                            text: "−"
                            implicitWidth: 44
                            implicitHeight: 40
                            enabled: row.editable
                            onClicked: list.form.setValue(row.key, list.form.nudged(row.key, -1))
                        }
                        Item {
                            id: track
                            width: parent.width - 2 * 44 - 16
                            height: 40
                            readonly property real t: num.dragging ? num.dragT : list.form.position(row.key, Number(row.value))
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width
                                height: 4
                                radius: 2
                                color: Theme.border
                            }
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: track.t * parent.width
                                height: 4
                                radius: 2
                                color: Theme.accent
                            }
                            Rectangle {
                                x: track.t * (track.width - width)
                                anchors.verticalCenter: parent.verticalCenter
                                width: 24
                                height: 24
                                radius: 12
                                color: num.dragging ? Theme.accent : Theme.surface
                                border.color: Theme.accent
                                border.width: 2
                            }
                            // A slider moves only with a deliberate sideways drag: a touch that
                            // starts moving up or down scrolls the list instead (the list takes
                            // the touch over), and a tap changes nothing. Once the drag is
                            // sideways it keeps the touch, and the value follows the finger's x.
                            MouseArea {
                                id: dragArea
                                anchors.fill: parent
                                enabled: row.editable
                                preventStealing: num.dragging
                                property real startX: 0
                                property real startY: 0
                                readonly property real threshold: 14
                                function at(mx) { return Math.min(Math.max((mx - 12) / Math.max(track.width - 24, 1), 0), 1) }
                                onPressed: (m) => { startX = m.x; startY = m.y }
                                onPositionChanged: (m) => {
                                    if (!num.dragging) {
                                        var dx = Math.abs(m.x - startX), dy = Math.abs(m.y - startY)
                                        // vertical first: the list scrolls (it takes the touch over)
                                        if (dx < threshold || dx < 1.5 * dy)
                                            return
                                        num.dragT = list.form.position(row.key, Number(row.value))
                                        num.dragging = true
                                        grabOffset = (num.dragT * Math.max(track.width - 24, 1) + 12) - m.x
                                    }
                                    num.dragT = at(m.x + grabOffset)
                                }
                                property real grabOffset: 0                 // the handle does not jump to the finger
                                onReleased: {
                                    if (!num.dragging)
                                        return
                                    var v = list.form.valueAt(row.key, num.dragT)
                                    num.dragging = false
                                    list.form.setValue(row.key, v)
                                }
                                onCanceled: num.dragging = false
                            }
                        }
                        Btn {
                            text: "+"
                            implicitWidth: 44
                            implicitHeight: 40
                            enabled: row.editable
                            onClicked: list.form.setValue(row.key, list.form.nudged(row.key, 1))
                        }
                    }
                    Text {
                        visible: (row.hint || "").length > 0
                        width: parent.width
                        wrapMode: Text.Wrap
                        text: row.hint || ""
                        color: Theme.textMuted
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                }
                Rectangle { anchors.bottom: parent.bottom; x: 14; width: parent.width - 28; height: 1; color: Theme.grid }
            }
        }

        // -------------------------------------------------------- switch
        Component {
            id: switchRow
            Item {
                implicitHeight: Math.max(56, labelCol.implicitHeight + 16)
                opacity: row.editable ? 1.0 : 0.45
                Column {
                    id: labelCol
                    x: 14
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - 28 - 70
                    spacing: 2
                    Text {
                        width: parent.width
                        elide: Text.ElideRight
                        text: row.label
                        color: Theme.text
                        font.family: Theme.uiFont
                        font.pixelSize: Theme.body
                    }
                    Text {
                        visible: (row.hint || "").length > 0
                        width: parent.width
                        wrapMode: Text.Wrap
                        text: row.hint || ""
                        color: Theme.textMuted
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                }
                Rectangle {                                         // the switch
                    anchors.right: parent.right
                    anchors.rightMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    width: 52
                    height: 30
                    radius: 15
                    color: row.value ? Theme.accent : Theme.border
                    Rectangle {
                        x: row.value ? parent.width - width - 3 : 3
                        anchors.verticalCenter: parent.verticalCenter
                        width: 24
                        height: 24
                        radius: 12
                        color: Theme.surface
                        Behavior on x { NumberAnimation { duration: 120 } }
                    }
                }
                TapHandler { enabled: row.editable; onTapped: list.form.setValue(row.key, !row.value) }
                Rectangle { anchors.bottom: parent.bottom; x: 14; width: parent.width - 28; height: 1; color: Theme.grid }
            }
        }

        // -------------------------------------------------------- choice
        Component {
            id: choiceRow
            Item {
                implicitHeight: choiceCol.implicitHeight + 16
                opacity: row.editable ? 1.0 : 0.45
                Column {
                    id: choiceCol
                    x: 14
                    y: 8
                    width: parent.width - 28
                    spacing: 6
                    Text {
                        text: row.label
                        color: Theme.text
                        font.family: Theme.uiFont
                        font.pixelSize: Theme.body
                    }
                    Flow {
                        width: parent.width
                        spacing: 6
                        Repeater {
                            model: row.options || []
                            Btn {
                                required property var modelData
                                text: modelData.text
                                textSize: 14
                                implicitHeight: 42
                                active: String(row.value) === String(modelData.value)
                                enabled: row.editable
                                onClicked: list.form.setValue(row.key, modelData.value)
                            }
                        }
                    }
                }
                Rectangle { anchors.bottom: parent.bottom; x: 14; width: parent.width - 28; height: 1; color: Theme.grid }
            }
        }

        // ------------------------------------------------------- stepper
        Component {
            id: stepperRow
            Item {
                implicitHeight: 60
                opacity: row.editable ? 1.0 : 0.45
                Text {
                    x: 14
                    anchors.verticalCenter: parent.verticalCenter
                    text: row.label
                    color: Theme.text
                    font.family: Theme.uiFont
                    font.pixelSize: Theme.body
                }
                Row {
                    anchors.right: parent.right
                    anchors.rightMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8
                    Btn {
                        text: "−"
                        implicitWidth: 44
                        implicitHeight: 40
                        enabled: row.editable && Number(row.value) > row.minimum
                        onClicked: list.form.setValue(row.key, Number(row.value) - 1)
                    }
                    Mono {
                        width: 40
                        height: 40
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        text: list.fmt(row.value, 0)
                        font.pixelSize: 17
                    }
                    Btn {
                        text: "+"
                        implicitWidth: 44
                        implicitHeight: 40
                        enabled: row.editable && Number(row.value) < row.maximum
                        onClicked: list.form.setValue(row.key, Number(row.value) + 1)
                    }
                }
                Rectangle { anchors.bottom: parent.bottom; x: 14; width: parent.width - 28; height: 1; color: Theme.grid }
            }
        }
    }
}
