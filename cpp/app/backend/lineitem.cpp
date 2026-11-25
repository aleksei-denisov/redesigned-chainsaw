#include "lineitem.h"

#include <QGuiApplication>
#include <QQuickWindow>
#include <QSGFlatColorMaterial>
#include <QSGGeometryNode>
#include <QSGTransformNode>
#include <QSGVertexColorMaterial>
#include <QScreen>
#include <cstring>

namespace app {

namespace {
constexpr int kMaxSeries = 6;

QSGGeometry* newGeometry(bool wide) {
    auto* g = new QSGGeometry(QSGGeometry::defaultAttributes_ColoredPoint2D(), 0, 0,
                              wide ? QSGGeometry::UnsignedIntType : QSGGeometry::UnsignedShortType);
    g->setDrawingMode(QSGGeometry::DrawTriangles);
    return g;
}
}  // namespace

GraphLines::GraphLines(QQuickItem* parent) : QQuickItem(parent) {
    setFlag(ItemHasContents, true);
    setClip(true);  // curves scroll in from the right, out at the left
}

GraphLines::~GraphLines() {
    if (view_) view_->setActive(false, this);
}

void GraphLines::setView(QObject* v) {
    auto* src = qobject_cast<LineSource*>(v);
    if (src == view_) return;
    if (view_) {
        disconnect(view_, nullptr, this, nullptr);
        view_->setActive(false, this);
    }
    view_ = src;
    if (view_) {
        connect(view_, &LineSource::updated, this, [this] {
            pending_ = true;
            update();
        });
        styleChanged();
    }
    sync();
    Q_EMIT viewChanged();
}

QVariantList GraphLines::colors() const {
    QVariantList l;
    for (const auto& c : colors_) l << c;
    return l;
}

void GraphLines::setColors(const QVariantList& c) {
    colors_.clear();
    for (const auto& v : c) colors_.push_back(v.value<QColor>());
    Q_EMIT colorsChanged();
    styleChanged();
}

void GraphLines::setLineWidth(double w) {
    width_ = w;
    Q_EMIT lineWidthChanged();
    styleChanged();
}

void GraphLines::setActive(bool on) {
    if (on != active_) {
        active_ = on;
        sync();
        Q_EMIT activeChanged();
    }
}

double GraphLines::dpr() const {
    if (window()) return window()->effectiveDevicePixelRatio();
    const QScreen* s = QGuiApplication::primaryScreen();
    return s ? s->devicePixelRatio() : 1.0;
}

void GraphLines::sync() {
    if (!view_) return;
    const double w = width(), h = height();
    view_->setSize(w, h);
    if (view_->dpr() != dpr()) {
        view_->setDpr(dpr());
        styleChanged();
    }
    view_->setActive(active_ && isVisible() && w > 2 && h > 2, this);
}

void GraphLines::styleChanged() {
    if (!view_) return;
    graph::Style s;
    s.colors.clear();
    for (const auto& c : colors_)
        s.colors.push_back({static_cast<std::uint8_t>(c.red()), static_cast<std::uint8_t>(c.green()),
                            static_cast<std::uint8_t>(c.blue()), static_cast<std::uint8_t>(c.alpha())});
    if (s.colors.empty()) s.colors.push_back({0, 0, 0, 255});
    s.width = width_;
    s.feather = 1.0 / std::max(dpr(), 1.0);
    view_->setStyle(s);
}

void GraphLines::itemChange(ItemChange change, const ItemChangeData& value) {
    QQuickItem::itemChange(change, value);
    if (change == ItemVisibleHasChanged || change == ItemSceneChange || change == ItemDevicePixelRatioHasChanged) sync();
}

void GraphLines::geometryChange(const QRectF& n, const QRectF& o) {
    QQuickItem::geometryChange(n, o);
    if (n.size() != o.size()) sync();
}

QSGNode* GraphLines::updatePaintNode(QSGNode* old, UpdatePaintNodeData*) {
    auto* root = static_cast<QSGTransformNode*>(old);
    if (!root) {
        root = new QSGTransformNode;
        for (int k = 0; k < kMaxSeries; ++k) {
            auto* node = new QSGGeometryNode;
            node->setGeometry(newGeometry(false));
            node->setFlag(QSGNode::OwnsGeometry);
            node->setMaterial(new QSGVertexColorMaterial);
            node->setFlag(QSGNode::OwnsMaterial);
            root->appendChildNode(node);
        }
        pending_ = true;
    }
    if (!pending_) return root;
    pending_ = false;
    static const std::vector<graph::Mesh> none;
    const auto& curves = view_ ? view_->curves() : none;
    QMatrix4x4 m;
    m.translate(view_ ? static_cast<float>(view_->dx()) : 0.f, 0.f);
    root->setMatrix(m);
    int k = 0;
    for (QSGNode* child = root->firstChild(); child; child = child->nextSibling(), ++k) {
        auto* node = static_cast<QSGGeometryNode*>(child);
        QSGGeometry* g = node->geometry();
        if (k >= static_cast<int>(curves.size()) || curves[k].empty()) {
            if (g->indexCount()) {
                g->allocate(0, 0);
                node->markDirty(QSGNode::DirtyGeometry);
            }
            continue;
        }
        const auto& mesh = curves[k];
        const bool wide = mesh.vertices.size() > 65535;
        if (wide != (g->indexType() == QSGGeometry::UnsignedIntType)) {
            g = newGeometry(wide);
            node->setGeometry(g);  // the old one is deleted (OwnsGeometry)
        }
        g->allocate(static_cast<int>(mesh.vertices.size()), static_cast<int>(mesh.indices.size()));
        std::memcpy(g->vertexData(), mesh.vertices.data(), mesh.vertices.size() * sizeof(graph::Vertex));
        if (wide) {
            std::memcpy(g->indexDataAsUInt(), mesh.indices.data(), mesh.indices.size() * sizeof(std::uint32_t));
        } else {
            auto* dst = g->indexDataAsUShort();
            for (std::size_t i = 0; i < mesh.indices.size(); ++i) dst[i] = static_cast<quint16>(mesh.indices[i]);
        }
        node->markDirty(QSGNode::DirtyGeometry);
    }
    return root;
}

}  // namespace app
