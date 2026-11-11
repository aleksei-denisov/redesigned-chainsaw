import QtQuick
import Instrument 1.0

// Uppercase label (12 sp, 0.08 em letter spacing).
Text {
    color: Theme.textMuted
    font.family: Theme.uiFont
    font.pixelSize: Theme.label
    font.letterSpacing: 0.08 * Theme.label
    font.capitalization: Font.AllUppercase
}
