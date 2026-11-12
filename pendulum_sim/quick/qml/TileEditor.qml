import QtQuick
import QtQuick.Layouts
import Instrument 1.0

// Editor sheet of one side-panel tile (panel.selected): what it shows and how.
// Every change applies at once (panel.update); Done or Back closes the sheet.
Rectangle {
    id: editor
    color: Theme.background
    visible: tile !== null

    readonly property var tile: {
        var ts = panel.tiles
        for (var i = 0; i < ts.length; ++i)
            if (ts[i].id === panel.selected)
                return ts[i]
        return null
    }
    readonly property string kind: tile ? tile.type : ""
    readonly property var signals: graphs.signals
    property string hint: ""

    function sig(key) {
        for (var i = 0; i < signals.length; ++i)
            if (signals[i].key === key)
                return signals[i]
        return null
    }
    function set(changes) { panel.update(panel.selected, changes) }
    function groups(graphable) {
        var out = []
        for (var i = 0; i < signals.length; ++i) {
            var s = signals[i]
            if (graphable && !s.graphable)
                continue
            if (out.length === 0 || out[out.length - 1].name !== s.group)
                out.push({ name: s.group, items: [] })
            out[out.length - 1].items.push(s)
        }
        return out
    }
    function graphUnits(keys) {
        var units = []
        for (var i = 0; i < keys.length; ++i) {
            var s = sig(keys[i])
            if (s && units.indexOf(s.unit) < 0)
                units.push(s.unit)
        }
        return units
    }
    function toggleCurve(key) {
        var keys = tile.signals.slice()
        var at = keys.indexOf(key)
        hint = ""
        if (at >= 0) {
            if (keys.length === 1) {
                hint = "A graph needs at least one curve."
                return
            }
            keys.splice(at, 1)
        } else {
            if (keys.length >= 4) {
                hint = "At most four curves per graph."
                return
            }
            var s = sig(key)
            var units = graphUnits(keys)
            if (s && units.indexOf(s.unit) < 0 && units.length >= 2) {
                hint = "At most two units per graph (one axis left, one right)."
                return
            }
            keys.push(key)
        }
        set({ signals: keys })
    }
    function setScale(mode) {
        var changes = { scale: mode }
        if (mode === "fixed" && (!tile.fixed || tile.fixed.length === 0)) {
            var view = panel.view(panel.selected)            // start from what the graph shows now
            var r = view ? view.ranges() : []
            if (r.length)
                changes.fixed = r.map(p => [Number(p[0].toPrecision(3)), Number(p[1].toPrecision(3))])
        }
        set(changes)
    }
    function setFixed(axis, lo, hi) {
        var a = parseFloat(lo), b = parseFloat(hi)
        if (isNaN(a) || isNaN(b))
            return
        var fixed = (tile.fixed || []).map(p => [p[0], p[1]])
        var n = graphUnits(tile.signals).length
        while (fixed.length < n)
            fixed.push([-1, 1])
        fixed[axis] = [a, b]
        set({ fixed: fixed })
    }

    onTileChanged: if (!tile) hint = ""
    TapHandler {}                                           // nothing under the sheet reacts

    component Group: Column {
        id: group
        property string title
        property string note
        default property alias content: body.data
        width: parent ? parent.width : 0
        spacing: 8
        Caption { text: group.title }
        Flow {
            id: body
            width: group.width
            spacing: 6
        }
        Text {
            visible: group.note.length > 0
            width: group.width
            wrapMode: Text.Wrap
            text: group.note
            color: Theme.textMuted
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
    }

    component SignalPicker: Column {
        id: picker
        property bool graphable: false
        property var isActive: function (key) { return false }
        signal picked(string key)
        width: parent ? parent.width : 0
        spacing: 10
        Repeater {
            model: editor.groups(picker.graphable)
            Column {
                required property var modelData
                width: picker.width
                spacing: 6
                Text {
                    text: modelData.name
                    color: Theme.textMuted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Flow {
                    width: parent.width
                    spacing: 6
                    Repeater {
                        model: modelData.items
                        Btn {
                            required property var modelData
                            text: modelData.name + "  " + modelData.unit
                            textSize: 14
                            implicitHeight: 44
                            active: picker.isActive(modelData.key)
                            onClicked: picker.picked(modelData.key)
                        }
                    }
                }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: editor.kind === "value" ? "Value tile" : editor.kind === "graph" ? "Graph tile" : "Status tile"
                elide: Text.ElideRight
                color: Theme.text
                font.family: Theme.uiFont
                font.pixelSize: Theme.title
                font.weight: Font.DemiBold
            }
            Btn {
                text: "Move up"
                enabled: editor.tile !== null && panel.tiles.length > 0 && panel.tiles[0].id !== panel.selected
                onClicked: {
                    var ts = panel.tiles
                    for (var i = 0; i < ts.length; ++i)
                        if (ts[i].id === panel.selected) { panel.move(panel.selected, i - 1); break }
                }
            }
            Btn {
                text: "Move down"
                enabled: editor.tile !== null && panel.tiles.length > 0
                         && panel.tiles[panel.tiles.length - 1].id !== panel.selected
                onClicked: {
                    var ts = panel.tiles
                    for (var i = 0; i < ts.length; ++i)
                        if (ts[i].id === panel.selected) { panel.move(panel.selected, i + 1); break }
                }
            }
            Btn { text: "Remove"; onClicked: panel.remove(panel.selected) }
            Btn { text: "Done"; filled: true; onClicked: panel.selected = "" }
        }

        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            Flickable {
                id: flick
                anchors.fill: parent
                anchors.margins: Theme.pad
                contentHeight: body.implicitHeight
                flickableDirection: Flickable.VerticalFlick
                boundsBehavior: Flickable.StopAtBounds

                Column {
                    id: body
                    width: flick.width
                    spacing: 18

                    // ------------------------------------------------- value
                    Column {
                        visible: editor.kind === "value"
                        width: parent.width
                        spacing: 18
                        Group {
                            title: "Shows"
                            SignalPicker {
                                isActive: (key) => editor.tile && editor.tile.signal === key
                                onPicked: (key) => editor.set({ signal: key })
                            }
                        }
                        Group {
                            title: "Size"
                            Seg {
                                options: [["half", "Half width"], ["full", "Full width"]]
                                value: editor.tile ? editor.tile.size : ""
                                onPicked: (v) => editor.set({ size: v })
                            }
                        }
                        Group {
                            title: "Colour"
                            Repeater {
                                model: Theme.tokens
                                Rectangle {
                                    required property string modelData
                                    readonly property bool chosen: editor.tile !== null && editor.tile.color === modelData
                                    width: 44
                                    height: 44
                                    radius: 22
                                    color: Theme.surfaceSubtle
                                    border.color: chosen ? Theme.accent : Theme.border
                                    border.width: chosen ? 3 : 1
                                    Rectangle {
                                        anchors.centerIn: parent
                                        width: 26; height: 26; radius: 13
                                        color: Theme.token(modelData)
                                    }
                                    TapHandler { onTapped: editor.set({ color: modelData }) }
                                }
                            }
                        }
                        Group {
                            title: "Angles in"
                            visible: editor.tile !== null && editor.kind === "value"
                                     && (editor.sig(editor.tile.signal) || {}).angular === true
                            Seg {
                                options: [["deg", "Degrees"], ["rad", "Radians"]]
                                value: editor.tile ? editor.tile.angle_unit : ""
                                onPicked: (v) => editor.set({ angle_unit: v })
                            }
                        }
                    }

                    // ------------------------------------------------- graph
                    Column {
                        visible: editor.kind === "graph"
                        width: parent.width
                        spacing: 18
                        Group {
                            title: "Curves"
                            note: editor.hint.length ? editor.hint
                                                     : "Up to four curves with at most two units: the first unit "
                                                       + "on the left axis, the second on the right."
                            SignalPicker {
                                graphable: true
                                isActive: (key) => editor.tile !== null && editor.kind === "graph"
                                                   && editor.tile.signals.indexOf(key) >= 0
                                onPicked: (key) => editor.toggleCurve(key)
                            }
                        }
                        Group {
                            title: "Time window"
                            Seg {
                                options: graphs.windows.map(w => [w, w + " s"])
                                value: editor.tile && editor.kind === "graph" ? editor.tile.window : ""
                                onPicked: (v) => editor.set({ window: v })
                            }
                        }
                        Group {
                            title: "Scale"
                            note: "Auto: grows at once, shrinks only when the curves have used less than 40 % "
                                  + "of it for 3 s. Symmetric: the same, centred on zero. Fixed: your limits."
                            Seg {
                                options: [["auto", "Auto"], ["symmetric", "Symmetric ±"], ["fixed", "Fixed"]]
                                value: editor.tile && editor.kind === "graph" ? editor.tile.scale : ""
                                onPicked: (v) => editor.setScale(v)
                            }
                        }
                        Column {
                            visible: editor.tile !== null && editor.kind === "graph" && editor.tile.scale === "fixed"
                            width: parent.width
                            spacing: 8
                            Repeater {
                                model: editor.tile && editor.kind === "graph" ? editor.graphUnits(editor.tile.signals) : []
                                Row {
                                    id: limits
                                    required property int index
                                    required property string modelData
                                    readonly property var pair: editor.tile && editor.tile.fixed
                                                                && index < editor.tile.fixed.length
                                                                ? editor.tile.fixed[index] : null
                                    spacing: 8
                                    Mono {
                                        width: 90
                                        height: Theme.touch
                                        verticalAlignment: Text.AlignVCenter
                                        text: (limits.index === 0 ? "left " : "right ") + limits.modelData
                                        color: Theme.textMuted
                                    }
                                    Field {
                                        id: lo
                                        width: 120
                                        mono: true
                                        placeholder: "min"
                                        text: limits.pair ? String(limits.pair[0]) : ""
                                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                                        onAccepted: editor.setFixed(limits.index, lo.text, hi.text)
                                    }
                                    Field {
                                        id: hi
                                        width: 120
                                        mono: true
                                        placeholder: "max"
                                        text: limits.pair ? String(limits.pair[1]) : ""
                                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                                        onAccepted: editor.setFixed(limits.index, lo.text, hi.text)
                                    }
                                }
                            }
                        }
                        Group {
                            title: "Smoothing"
                            note: "For display only – a centred moving average over the curve; the simulation, "
                                  + "the controller and recorded data are not changed."
                            Seg {
                                options: [["off", "Off"], ["light", "Light"], ["medium", "Medium"], ["strong", "Strong"]]
                                value: editor.tile && editor.kind === "graph" ? editor.tile.smoothing : ""
                                onPicked: (v) => editor.set({ smoothing: v })
                            }
                        }
                        Group {
                            title: "Height"
                            Seg {
                                options: [["small", "Small"], ["medium", "Medium"], ["large", "Large"], ["fill", "Fill"]]
                                value: editor.tile && editor.kind === "graph" ? editor.tile.height : ""
                                onPicked: (v) => editor.set({ height: v })
                            }
                        }
                        Group {
                            title: "Angles in"
                            Seg {
                                options: [["deg", "Degrees"], ["rad", "Radians"]]
                                value: editor.tile && editor.kind === "graph" ? editor.tile.angle_unit : ""
                                onPicked: (v) => editor.set({ angle_unit: v, fixed: [] })
                            }
                        }
                    }

                    // ------------------------------------------------ status
                    Column {
                        visible: editor.kind === "status"
                        width: parent.width
                        spacing: 18
                        Repeater {
                            model: [["show_scenario", "Scenario name"], ["show_controller", "Controller type"],
                                    ["show_gains", "Gains of each loop"]]
                            Group {
                                required property var modelData
                                title: modelData[1]
                                Seg {
                                    options: [[true, "Show"], [false, "Hide"]]
                                    value: editor.tile && editor.kind === "status" ? editor.tile[modelData[0]] : ""
                                    onPicked: (v) => { var c = {}; c[modelData[0]] = v; editor.set(c) }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
