#pragma once
// The configurable side panel of the Simulate screen (quick/backend/panel_model.py, panel.py):
// tiles (value / graph / status), validation, named layouts in QSettings (same keys and JSON
// as the Python app), live values at 15 Hz and the status tiles' rows.
#include <QObject>
#include <QSettings>
#include <QTimer>
#include <QVariantList>
#include <QVariantMap>

#include "graphs.h"

namespace app {

class SimulationBridge;

inline constexpr int kMaxTiles = 16;

class PanelModel {
public:
    explicit PanelModel(QSettings& store);
    QVariantList tiles;
    QString name;
    int index(const QString& id) const;
    QString add(const QString& kind, const QString& smoothing);
    void remove(const QString& id);
    void move(const QString& id, int to);
    void update(const QString& id, const QVariantMap& changes);
    void reset();
    QVariantMap saved() const;
    QStringList names() const;
    bool saveAs(const QString& name);
    bool load(const QString& name);
    void remove_layout(const QString& name);

    static QVariantMap defaults(const QString& kind, const QString& smoothing = "off");
    static std::optional<QVariantMap> cleanTile(const QVariant& tile, const QString& smoothing = "off");
    static QVariantList clean(const QVariant& tiles, const QString& smoothing = "off");
    static QVariantList defaultLayout();
    static std::vector<std::vector<int>> rows(const QVariantList& tiles, int columns);

private:
    void save();
    QVariant loadJson(const QString& key) const;
    QSettings& store_;
};

class PanelBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList tiles READ tiles NOTIFY tilesChanged)
    Q_PROPERTY(QVariantList rows READ rows NOTIFY tilesChanged)
    Q_PROPERTY(QVariantList rowsCompact READ rowsCompact NOTIFY tilesChanged)
    Q_PROPERTY(QVariantMap values READ values NOTIFY valuesChanged)
    Q_PROPERTY(QVariantMap status READ status NOTIFY statusChanged)
    Q_PROPERTY(QString layoutName READ layoutName NOTIFY layoutsChanged)
    Q_PROPERTY(QVariantList layoutNames READ layoutNames NOTIFY layoutsChanged)
    Q_PROPERTY(bool editing READ editing WRITE setEditing NOTIFY editingChanged)
    Q_PROPERTY(QString selected READ selected WRITE setSelected NOTIFY selectedChanged)
    Q_PROPERTY(int maxTiles READ maxTiles CONSTANT)
    Q_PROPERTY(bool full READ full NOTIFY tilesChanged)
public:
    PanelBridge(SimulationBridge* sim, GraphsBridge* graphs, QObject* parent = nullptr);

    QVariantList tiles() const;
    QVariantList rows() const { return rowsOf(2); }
    QVariantList rowsCompact() const { return rowsOf(4); }
    QVariantMap values() const { return values_; }
    QVariantMap status() const { return status_; }
    QString layoutName() const { return model_.name; }
    QVariantList layoutNames() const;
    bool editing() const { return editing_; }
    void setEditing(bool on);
    QString selected() const { return selected_; }
    void setSelected(const QString& id);
    int maxTiles() const { return kMaxTiles; }
    bool full() const { return model_.tiles.size() >= kMaxTiles; }

    Q_INVOKABLE QString add(const QString& kind);
    Q_INVOKABLE void remove(const QString& id);
    Q_INVOKABLE void move(const QString& id, int to);
    Q_INVOKABLE void update(const QString& id, const QVariantMap& changes);
    Q_INVOKABLE QVariantMap tile(const QString& id) const;
    Q_INVOKABLE void reset();
    Q_INVOKABLE bool saveAs(const QString& name);
    Q_INVOKABLE void load(const QString& name);
    Q_INVOKABLE void deleteLayout(const QString& name);
    Q_INVOKABLE QObject* view(const QString& id);

    void onFrame(double now);  ///< refresh the value tiles at 15 Hz

Q_SIGNALS:
    void tilesChanged();
    void valuesChanged();
    void statusChanged();
    void editingChanged();
    void selectedChanged();
    void layoutsChanged();

private:
    QVariantList rowsOf(int columns) const;
    static graph::Spec spec(const QVariantMap& t);
    void tilesEdited();
    void updateValues();
    void updateStatus();

    SimulationBridge* sim_;
    GraphsBridge* graphs_;
    QSettings store_;
    PanelModel model_;
    bool editing_ = false;
    QString selected_;
    QVariantMap values_, status_;
    double due_ = 0.0;
    QTimer notify_;
};

/// Context object `trail`: the trajectory of the tip (or every joint), drawn by a GraphLines.
class TrailBridge : public LineSource {
    Q_OBJECT
    Q_PROPERTY(QString mode READ mode WRITE setMode NOTIFY modeChanged)
    Q_PROPERTY(int seconds READ seconds WRITE setSeconds NOTIFY secondsChanged)
    Q_PROPERTY(QVariantList modes READ modes CONSTANT)
    Q_PROPERTY(QVariantList secondsOptions READ secondsOptions CONSTANT)
public:
    explicit TrailBridge(SimulationBridge* sim, QObject* parent = nullptr);
    QString mode() const { return mode_; }
    void setMode(const QString& m);
    int seconds() const { return seconds_; }
    void setSeconds(int s);
    QVariantList modes() const { return {"off", "tip", "all"}; }
    QVariantList secondsOptions() const { return {1, 3, 5, 10}; }
    Q_INVOKABLE void clear();
    Q_INVOKABLE void setMapping(double px_per_m, double cx, double origin_y);
    void setStyle(const graph::Style& s) override {
        style_ = s;
        has_style_ = true;
        draw();
    }
    void onFrame(double now);
Q_SIGNALS:
    void modeChanged();
    void secondsChanged();
protected:
    void activeChanged() override { draw(); }
private:
    void draw();
    SimulationBridge* sim_;
    QSettings store_;
    QString mode_ = "tip";
    int seconds_ = 3;
    graph::Style style_;
    bool has_style_ = false;
    double px_ = 1.0, cx_ = 0.0, oy_ = 0.0;
    long epoch_ = -1;
    std::vector<double> t_;
    std::vector<std::vector<std::pair<double, double>>> points_;  ///< per sample: joint positions
};

}  // namespace app
