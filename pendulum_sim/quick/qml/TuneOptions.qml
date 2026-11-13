import QtQuick
import Instrument 1.0

// Auto-tune options (quick-core-v2): what to tune, integral gains, methods, gain limits.
// The whole options map lives in the backend (autotune.options, remembered); every
// change here writes a modified copy back with autotune.setOptions().
Column {
    id: opts
    spacing: 14
    readonly property var o: autotune.options
    readonly property var loops: autotune.loopList

    function put(key, value) {
        var c = JSON.parse(JSON.stringify(o))
        c[key] = value
        autotune.setOptions(c)
    }
    function toggleIn(key, item) {
        var list = (o[key] || []).slice()
        var i = list.indexOf(item)
        if (i >= 0) list.splice(i, 1); else list.push(item)
        put(key, list)
    }
    function limitOf(scope, term) {                 // scope: "general" or a loop signal
        var lim = o.limits || {}
        var d = scope === "general" ? (lim.general || {}) : ((lim.per_loop || {})[scope] || {})
        return d[term] || null
    }
    function setLimit(scope, term, index, text) {
        var c = JSON.parse(JSON.stringify(o))
        c.limits = c.limits || { general: {}, per_loop: {} }
        c.limits.per_loop = c.limits.per_loop || {}
        var d = scope === "general" ? (c.limits.general = c.limits.general || {})
                                    : (c.limits.per_loop[scope] = c.limits.per_loop[scope] || {})
        var pair = d[term] ? d[term].slice() : [null, null]
        var v = parseFloat(String(text).replace(",", "."))
        pair[index] = isNaN(v) ? null : v
        d[term] = (pair[0] === null && pair[1] === null) ? null : pair
        autotune.setOptions(c)
    }
    function setTerm(term, state) {                // "tune" | "keep" | "off"
        var c = JSON.parse(JSON.stringify(o))
        var tune = (c.terms || []).filter(t => t !== term)
        var off = (c.off_terms || []).filter(t => t !== term)
        if (state === "tune") tune.push(term)
        if (state === "off") off.push(term)
        c.terms = tune
        c.off_terms = off
        autotune.setOptions(c)
    }
    function hasOwn(signal) { return !!((o.limits || {}).per_loop || {})[signal] }
    function setOwn(signal, on) {
        var c = JSON.parse(JSON.stringify(o))
        c.limits = c.limits || { general: {}, per_loop: {} }
        c.limits.per_loop = c.limits.per_loop || {}
        if (on) c.limits.per_loop[signal] = {}
        else delete c.limits.per_loop[signal]
        autotune.setOptions(c)
    }

    component Note: Text {
        width: parent ? parent.width : 0
        wrapMode: Text.Wrap
        color: Theme.textMuted
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    component Switch: Item {
        id: sw
        property string label
        property string hint
        property bool checked
        signal toggled()
        width: parent ? parent.width : 0
        implicitHeight: Math.max(48, swText.implicitHeight + 8)
        Column {
            id: swText
            width: parent.width - 70
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Text { width: parent.width; wrapMode: Text.Wrap; text: sw.label; color: Theme.text
                   font.family: Theme.uiFont; font.pixelSize: Theme.body }
            Text { width: parent.width; wrapMode: Text.Wrap; visible: sw.hint.length > 0; text: sw.hint
                   color: Theme.textMuted; font.family: Theme.uiFont; font.pixelSize: 13 }
        }
        Rectangle {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 52; height: 30; radius: 15
            color: sw.checked ? Theme.accent : Theme.border
            Rectangle {
                x: sw.checked ? parent.width - width - 3 : 3
                anchors.verticalCenter: parent.verticalCenter
                width: 24; height: 24; radius: 12
                color: Theme.surface
            }
        }
        TapHandler { onTapped: sw.toggled() }
    }
    component LimitRows: Column {                   // Kp / Ki / Kd min … max of one scope
        id: rowsOf
        property string scope: "general"
        width: parent ? parent.width : 0
        spacing: 6
        Repeater {
            model: [["kp", "Kp"], ["ki", "Ki"], ["kd", "Kd"]]
            Row {
                required property var modelData
                readonly property var pair: opts.limitOf(rowsOf.scope, modelData[0])
                spacing: 8
                Mono { width: 40; height: Theme.touch; verticalAlignment: Text.AlignVCenter; text: modelData[1] }
                Field {
                    width: 130
                    mono: true
                    placeholder: "min –∞"
                    text: parent.pair && parent.pair[0] !== null ? String(parent.pair[0]) : ""
                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                    onAccepted: opts.setLimit(rowsOf.scope, modelData[0], 0, text)
                }
                Mono { height: Theme.touch; verticalAlignment: Text.AlignVCenter; text: "…"; color: Theme.textMuted }
                Field {
                    width: 130
                    mono: true
                    placeholder: "max +∞"
                    text: parent.pair && parent.pair[1] !== null ? String(parent.pair[1]) : ""
                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                    onAccepted: opts.setLimit(rowsOf.scope, modelData[0], 1, text)
                }
            }
        }
    }

    // ------------------------------------------------------------ what
    Caption { text: "What to tune" }
    Flow {
        width: parent.width
        spacing: 6
        Btn { text: "All loops (LQR design)"; active: opts.o.mode !== "loops"; onClicked: opts.put("mode", "full") }
        Btn { text: "Selected loops"; active: opts.o.mode === "loops"; onClicked: opts.put("mode", "loops") }
    }
    Note {
        text: opts.o.mode === "loops"
              ? "Only the chosen loops are changed: a gain set to Tune is searched (starting from its current "
                + "value, and it stays on), Keep leaves it as it is, Off sets it to 0. Other loops keep their settings."
              : "Every loop at once: a search over LQR designs (always stabilising), mapped onto Kp and Kd, "
                + "then each loop's integral gain."
    }
    Switch {
        visible: opts.o.mode !== "loops"
        label: "Integral gains (PID)"
        hint: "after the design, search each loop's Ki; kept only where it makes the controller more robust"
        checked: opts.o.integral !== false
        onToggled: opts.put("integral", !checked)
    }
    Column {
        visible: opts.o.mode === "loops"
        width: parent.width
        spacing: 8
        Text { text: "Loops"; color: Theme.textMuted; font.family: Theme.uiFont; font.pixelSize: 13 }
        Flow {
            width: parent.width
            spacing: 6
            Repeater {
                model: opts.loops
                Btn {
                    required property var modelData
                    text: modelData.name
                    textSize: 14
                    active: (opts.o.loops || []).indexOf(modelData.signal) >= 0
                    enabled: modelData.enabled
                    onClicked: opts.toggleIn("loops", modelData.signal)
                }
            }
        }
        Text { text: "Gains of the chosen loops"; color: Theme.textMuted; font.family: Theme.uiFont; font.pixelSize: 13 }
        Repeater {
            model: [["kp", "Kp"], ["ki", "Ki"], ["kd", "Kd"]]
            Row {
                required property var modelData
                readonly property string termState: (opts.o.off_terms || []).indexOf(modelData[0]) >= 0 ? "off"
                                              : (opts.o.terms || []).indexOf(modelData[0]) >= 0 ? "tune" : "keep"
                spacing: 6
                Mono { width: 40; height: Theme.touch; verticalAlignment: Text.AlignVCenter; text: modelData[1] }
                Repeater {
                    model: [["tune", "Tune"], ["keep", "Keep as is"], ["off", "Off (0)"]]
                    Btn {
                        required property var modelData
                        text: modelData[1]
                        textSize: 14
                        active: parent.termState === modelData[0]
                        onClicked: opts.setTerm(parent.modelData[0], modelData[0])
                    }
                }
            }
        }
        Note { text: (opts.o.loops || []).length === 0 ? "No loop chosen: all loops are searched." : "" ; visible: text.length > 0 }
    }
    Switch {
        label: "Also try the methods"
        hint: "derivative from the rate sensor or from the filtered error, and the anti-windup strategy"
        checked: opts.o.methods === true
        onToggled: opts.put("methods", !checked)
    }

    // ---------------------------------------------------------- limits
    Caption { text: "Gain limits" }
    Flow {
        width: parent.width
        spacing: 6
        Btn { text: "No limits"; active: opts.o.limit_mode === "none" || !opts.o.limit_mode; onClicked: opts.put("limit_mode", "none") }
        Btn { text: "Same for all loops"; active: opts.o.limit_mode === "general"; onClicked: opts.put("limit_mode", "general") }
        Btn { text: "Per loop"; active: opts.o.limit_mode === "per_loop"; onClicked: opts.put("limit_mode", "per_loop") }
    }
    Note {
        text: opts.o.limit_mode === "per_loop"
              ? "A loop with its own limits uses those; the others use the general limits. Empty = no limit."
              : opts.o.limit_mode === "general" ? "Signed bounds for every loop's gains. Empty = no limit."
              : "Gains may take any value (designs for 2–3 segments can reach 10⁴–10⁶)."
    }
    Text {
        visible: opts.o.limit_mode === "per_loop"
        text: "General (loops without their own)"
        color: Theme.textMuted
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    LimitRows { scope: "general"; visible: opts.o.limit_mode === "general" || opts.o.limit_mode === "per_loop" }
    Repeater {
        model: opts.o.limit_mode === "per_loop" ? opts.loops : []
        Column {
            required property var modelData
            width: opts.width
            spacing: 6
            Switch {
                label: modelData.name + ": own limits"
                checked: opts.hasOwn(modelData.signal)
                onToggled: opts.setOwn(modelData.signal, !checked)
            }
            LimitRows { scope: modelData.signal; visible: opts.hasOwn(modelData.signal) }
        }
    }
}
