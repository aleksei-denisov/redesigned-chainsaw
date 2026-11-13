import QtQuick
import Instrument 1.0

// A small static chart (Canvas) for data that changes a few times a second at most –
// auto-tune progress, before/after responses. `series`: [{xs, ys, color, dots, width}]
// (xs omitted: 0, 1, 2 …; NaN values leave gaps); nice ticks on y, monospace labels.
Item {
    id: chart
    property var series: []
    property string unit: ""
    property string xLabel: ""
    property bool zeroLine: false

    function niceStep(span, n) {
        var raw = span / Math.max(n, 1)
        var e = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10))
        var m = [1, 2, 2.5, 5, 10].find(f => f * e >= raw)
        return (m || 10) * e
    }
    function range() {
        var lo = Infinity, hi = -Infinity, xmax = 1, xmin = 0
        for (var s of series) {
            var n = s.ys.length
            for (var i = 0; i < n; ++i) {
                var y = s.ys[i]
                if (y === null || isNaN(y))
                    continue
                lo = Math.min(lo, y)
                hi = Math.max(hi, y)
            }
            if (s.xs && s.xs.length) {
                xmin = Math.min(xmin, s.xs[0])
                xmax = Math.max(xmax, s.xs[s.xs.length - 1])
            } else {
                xmax = Math.max(xmax, n - 1)
            }
        }
        if (!isFinite(lo)) { lo = 0; hi = 1 }
        if (chart.zeroLine) { lo = Math.min(lo, 0); hi = Math.max(hi, 0) }
        if (hi - lo < 1e-9) { lo -= 1; hi += 1 }
        var step = niceStep(hi - lo, 4)
        return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step: step, xmin: xmin, xmax: xmax }
    }
    onSeriesChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()
    Connections { target: Theme; function onDarkChanged() { canvas.requestPaint() } }

    Canvas {
        id: canvas
        anchors.fill: parent
        renderStrategy: Canvas.Cooperative
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var r = chart.range()
            var left = 52, right = 8, top = 8, bottom = chart.xLabel.length ? 24 : 8
            var w = width - left - right, h = height - top - bottom
            if (w < 10 || h < 10)
                return
            function X(x) { return left + (x - r.xmin) / Math.max(r.xmax - r.xmin, 1e-9) * w }
            function Y(y) { return top + (r.hi - y) / (r.hi - r.lo) * h }
            ctx.font = "11px '" + Theme.monoFont + "'"
            ctx.textAlign = "right"
            ctx.textBaseline = "middle"
            for (var v = r.lo; v <= r.hi + r.step * 0.01; v += r.step) {
                ctx.strokeStyle = Theme.grid
                ctx.lineWidth = 1
                ctx.beginPath()
                ctx.moveTo(left, Math.round(Y(v)) + 0.5)
                ctx.lineTo(left + w, Math.round(Y(v)) + 0.5)
                ctx.stroke()
                ctx.fillStyle = Theme.textMuted
                var digits = r.step >= 1 ? 0 : Math.min(3, Math.ceil(-Math.log(r.step) / Math.LN10))
                ctx.fillText(v.toFixed(digits), left - 6, Y(v))
            }
            if (chart.unit.length) {
                ctx.textAlign = "left"
                ctx.textBaseline = "top"
                ctx.fillText(chart.unit, left + 4, top)
            }
            if (chart.xLabel.length) {
                ctx.textAlign = "right"
                ctx.textBaseline = "bottom"
                ctx.fillText(chart.xLabel, left + w, height)
            }
            for (var s of chart.series) {
                var n = s.ys.length
                ctx.strokeStyle = s.color
                ctx.fillStyle = s.color
                ctx.lineWidth = s.width || 2
                var drawing = false
                ctx.beginPath()
                for (var i = 0; i < n; ++i) {
                    var y = s.ys[i]
                    if (y === null || isNaN(y)) { drawing = false; continue }
                    var x = s.xs ? s.xs[i] : i
                    if (s.dots) {
                        ctx.moveTo(X(x) + 3.5, Y(y))
                        ctx.arc(X(x), Y(y), 3.5, 0, 2 * Math.PI)
                        continue
                    }
                    if (drawing) ctx.lineTo(X(x), Y(y)); else ctx.moveTo(X(x), Y(y))
                    drawing = true
                }
                if (s.dots) ctx.fill(); else ctx.stroke()
            }
        }
    }
}
