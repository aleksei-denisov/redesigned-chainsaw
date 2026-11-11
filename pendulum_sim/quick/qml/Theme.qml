pragma Singleton
import QtQuick

// "Instrument" design tokens. One set of names, two themes; no colour is written
// anywhere else in the QML.
QtObject {
    property bool dark: false

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
    readonly property color segment2: dark ? "#A9BCFF" : "#1D3FCC"
    readonly property color segment3: dark ? "#2ED3B7" : "#0E9384"

    readonly property string uiFont: "Space Grotesk"
    readonly property string monoFont: "JetBrains Mono"

    readonly property int pad: 16
    readonly property int gap: 12
    readonly property int radiusPanel: 18
    readonly property int radiusCard: 14
    readonly property int radiusButton: 12
}
