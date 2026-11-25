#pragma once
// `GraphLines` (quick/backend/lineitem.py): the curves of a LineSource as a scene-graph item.
// The view prepares the triangles at the graph rate; updatePaintNode only copies the vertex
// and index buffers into the geometry (during the sync, while the GUI thread waits).
#include <QColor>
#include <QPointer>
#include <QQuickItem>
#include <QVariantList>

#include "graphs.h"

namespace app {

class GraphLines : public QQuickItem {
    Q_OBJECT
    Q_PROPERTY(QObject* view READ view WRITE setView NOTIFY viewChanged)
    Q_PROPERTY(QVariantList colors READ colors WRITE setColors NOTIFY colorsChanged)
    Q_PROPERTY(QVariantList dashed READ dashed WRITE setDashed NOTIFY dashedChanged)
    Q_PROPERTY(double lineWidth READ lineWidth WRITE setLineWidth NOTIFY lineWidthChanged)
    Q_PROPERTY(bool active READ active WRITE setActive NOTIFY activeChanged)
public:
    explicit GraphLines(QQuickItem* parent = nullptr);
    ~GraphLines() override;

    QObject* view() const { return view_; }
    void setView(QObject* v);
    QVariantList colors() const;
    void setColors(const QVariantList& c);
    QVariantList dashed() const { return dashed_; }
    void setDashed(const QVariantList& d) {
        dashed_ = d;
        Q_EMIT dashedChanged();
    }
    double lineWidth() const { return width_; }
    void setLineWidth(double w);
    bool active() const { return active_; }
    void setActive(bool on);

Q_SIGNALS:
    void viewChanged();
    void colorsChanged();
    void dashedChanged();
    void lineWidthChanged();
    void activeChanged();

protected:
    QSGNode* updatePaintNode(QSGNode* old, UpdatePaintNodeData*) override;
    void itemChange(ItemChange change, const ItemChangeData& value) override;
    void geometryChange(const QRectF& n, const QRectF& o) override;

private:
    void sync();
    void styleChanged();
    double dpr() const;

    QPointer<LineSource> view_;
    std::vector<QColor> colors_;
    QVariantList dashed_;
    double width_ = 2.0;
    bool active_ = false;
    bool pending_ = false;
};

}  // namespace app
