pragma Singleton
import QtQuick
import PendulumApp 1.0

// "Instrument" design tokens. One set of names, two themes; no colour is written
// anywhere else in the QML. The theme follows Settings (System / Light / Dark) and
// switches instantly.
QtObject {
    readonly property bool dark: Settings.isDark

    readonly property color background: dark ? "#0F131A" : "#F4F5F7"
    readonly property color surface: dark ? "#171C25" : "#FFFFFF"
    readonly property color surfaceSubtle: dark ? "#1D2330" : "#F9FAFB"
    readonly property color border: dark ? "#2A3140" : "#E4E7EC"
    readonly property color grid: dark ? "#1F2532" : "#EEF0F3"
    readonly property color gridCenter: dark ? "#2C3445" : "#DDE2EA"
    readonly property color text: dark ? "#E6EAF0" : "#101828"
    readonly property color textMuted: dark ? "#98A2B3" : "#5B6474"
    readonly property color track: dark ? "#C3CAD5" : "#101828"
    readonly property color tick: dark ? "#5B6474" : "#98A2B3"
    readonly property color accent: dark ? "#7C97FF" : "#2F5BFF"
    readonly property color onAccent: dark ? "#0B1020" : "#FFFFFF"
    readonly property color accentSoft: dark ? "#1E2A4D" : "#EEF2FF"
    readonly property color accentSoftText: dark ? "#A9BCFF" : "#2F5BFF"
    readonly property color angle: dark ? "#F5A524" : "#B54708"
    readonly property color success: dark ? "#47CD89" : "#067647"
    readonly property color danger: dark ? "#F97066" : "#D92D20"
    readonly property color segment2: dark ? "#A9BCFF" : "#1D3FCC"
    readonly property color segment3: dark ? "#2ED3B7" : "#0E9384"
    readonly property var palette: dark ? ["#7C97FF", "#F5A524", "#2ED3B7", "#A48AFB", "#F670C7"]
                                        : ["#2F5BFF", "#B54708", "#0E9384", "#7A5AF8", "#C11574"]
    readonly property var segments: [accent, segment2, segment3]
    // curve colours of side-panel graph tiles, in signal order (θ amber, x blue as before)
    readonly property var tilePalette: [angle, accent, segment3, palette[3], palette[4]]
    // colour tokens a value tile can use (panel_model.COLORS)
    readonly property var tokens: ["text", "accent", "angle", "segment2", "segment3", "success",
                                   "palette0", "palette1", "palette2", "palette3", "palette4"]
    // experiment runs (as the Widgets app's run colours; darker on the light theme)
    readonly property var runColors: dark
        ? ["#4FA3FF", "#FF6B6B", "#4CD07D", "#C792EA", "#FFA14A", "#7FDBCA", "#F78C6C", "#E0E0E0", "#FFCB6B", "#89DDFF"]
        : ["#1F6FD1", "#D93F3F", "#1E9E50", "#8E52C9", "#D9771A", "#1F9C8A", "#C2552F", "#5B6474", "#B88A00", "#1A8FC2"]
    function runColor(i) { return runColors[((i % runColors.length) + runColors.length) % runColors.length] }
    function token(name) {
        if (name && name.indexOf("palette") === 0)
            return palette[parseInt(name.substring(7))] || text
        switch (name) {
        case "accent": return accent
        case "angle": return angle
        case "segment2": return segment2
        case "segment3": return segment3
        case "success": return success
        default: return text
        }
    }

    readonly property string uiFont: "Space Grotesk"
    readonly property string monoFont: "JetBrains Mono"
    readonly property int body: 15
    readonly property int label: 12
    readonly property int title: 26
    readonly property int readout: 26
    readonly property int inline: 14

    readonly property int pad: 16
    readonly property int gap: 12
    readonly property int radiusPanel: 18
    readonly property int radiusCard: 14
    readonly property int radiusButton: 12
    readonly property int touch: 48
    readonly property int dockButton: 52
    readonly property int fade: 180
}
