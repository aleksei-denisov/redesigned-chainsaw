import QtQuick
import Instrument 1.0

// Flat card: surface colour, 1 px border, large radius.
Rectangle {
    radius: Theme.radiusPanel
    color: Theme.surface
    border.color: Theme.border
    border.width: 1
}
