#pragma once
// Live graphs (quick/backend/curves.py, graphs.py):
//   GraphState – one graph from telemetry to triangles (Qt-free). Unlike the Python version
//     it recomputes the visible window on every refresh (cheap in C++), with decimation
//     columns fixed in time, so curves never shimmer while scrolling.
//   LineSource – what a GraphLines item draws (graph views and the trajectory).
//   GraphView – one graph card or side-panel tile; GraphsBridge – context object `graphs`.
//   GraphWorker – computes every visible graph on its own thread: the GUI thread only
//     collects a snapshot of the views and swaps in the finished triangles, so a frame's cost
//     does not depend on how many graphs are shown or how wide they are.
#include <QObject>
#include <QPointer>
#include <QTimer>
#include <condition_variable>
#include <functional>
#include <optional>
#include <mutex>
#include <thread>
#include <QSettings>
#include <QVariantList>
#include <map>
#include <memory>
#include <set>

#include "graphmath.h"

namespace app {

class SimulationBridge;

namespace graph {

inline const std::map<std::string, double> kSmoothingS = {{"off", 0.0}, {"light", 0.010}, {"medium", 0.030},
                                                          {"strong", 0.100}};
inline const std::vector<int> kWindowsS = {5, 10, 20, 30};
inline constexpr int kMaxSignals = 4;
inline constexpr int kMaxAxes = 2;
inline constexpr double kBandAlpha = 0.35;

struct Spec {
    std::vector<std::string> signals;
    double window_s = 10.0;
    std::string scale = "auto";
    std::vector<std::pair<double, double>> fixed;
    std::string smoothing = "off";
    std::string angle_unit = "deg";
    bool operator==(const Spec&) const = default;
};

struct Style {
    std::vector<Rgba> colors{{0, 0, 0, 255}};
    double width = 2.0;
    double feather = 0.5;
};

struct Axis {
    std::string unit;
    double lo = 0.0, hi = 1.0;
    std::vector<std::pair<double, std::string>> ticks;  ///< (y pixel, label)
};

struct LegendEntry {
    int index, axis;
    std::string key, label, unit;
    bool dashed;
};

struct Output {
    std::vector<Mesh> curves;
    double dx = 0.0;
    std::vector<Axis> axes;
};

int columnsFor(double width, double dpr);

class GraphState {
public:
    GraphState(const Spec& spec, const std::map<std::string, Signal>& catalogue, double rate_hz);
    void configure(const Spec& spec, const std::map<std::string, Signal>& catalogue);
    void setStyle(const Style& s) { style_ = s; }
    const Spec& spec() const { return spec_; }
    std::vector<int> columns() const;  ///< telemetry column indices of the series
    std::vector<LegendEntry> legend() const;
    /// data: telemetry column index → values (kT included).
    Output update(const std::map<int, std::vector<double>>& data, double width, double height, double dpr, double now);
    double rate_hz;

private:
    Spec spec_;
    Style style_;
    std::vector<Signal> series_;
    std::vector<int> cols_;
    std::vector<std::string> units_;
    std::vector<int> axis_of_;
    std::vector<double> conv_;
    std::vector<StableScale> scales_;
};

}  // namespace graph

/// What a GraphLines item draws.
class LineSource : public QObject {
    Q_OBJECT
public:
    using QObject::QObject;
    Q_INVOKABLE virtual void setSize(double width, double height) {
        width_ = width;
        height_ = height;
    }
    /// Shown or not by `owner` (an item); computed while any item shows it.
    virtual void setActive(bool on, const void* owner);
    virtual void setStyle(const graph::Style& style) = 0;
    void setDpr(double dpr) { dpr_ = dpr; }
    double dpr() const { return dpr_; }
    bool active() const { return active_; }
    const std::vector<graph::Mesh>& curves() const { return curves_; }  ///< read in the scene-graph sync
    double dx() const { return dx_; }
Q_SIGNALS:
    void updated();
protected:
    virtual void activeChanged() {}
    double width_ = 0, height_ = 0, dpr_ = 1.0;
    bool active_ = false;
    std::set<const void*> wanted_;
    std::vector<graph::Mesh> curves_;
    double dx_ = 0.0;
};

class GraphsBridge;

class GraphView : public LineSource {
    Q_OBJECT
    Q_PROPERTY(QString title READ title CONSTANT)
    Q_PROPERTY(QVariantList legend READ legend NOTIFY legendChanged)
    Q_PROPERTY(QVariantList units READ units NOTIFY axesChanged)
    Q_PROPERTY(QVariantList ticks READ ticks NOTIFY axesChanged)
    Q_PROPERTY(QVariantList ticksRight READ ticksRight NOTIFY axesChanged)
    Q_PROPERTY(QString unit READ unit NOTIFY axesChanged)
    Q_PROPERTY(QString unitRight READ unitRight NOTIFY axesChanged)
    Q_PROPERTY(double window READ window NOTIFY legendChanged)
public:
    GraphView(const QString& key, const graph::Spec& spec, GraphsBridge* owner, const QString& title);
    QString key() const { return key_; }
    QString title() const { return title_; }
    QVariantList legend() const;
    QVariantList units() const;
    QVariantList ticks() const { return ticks_[0]; }
    QVariantList ticksRight() const { return ticks_[1]; }
    QString unit() const { return units_.empty() ? QString() : QString::fromStdString(units_[0]); }
    QString unitRight() const { return units_.size() > 1 ? QString::fromStdString(units_[1]) : QString(); }
    double window() const { return state.spec().window_s; }
    Q_INVOKABLE QVariantList ranges() const;

    void setStyle(const graph::Style& style) override { style_ = style; }
    const graph::Style& style() const { return style_; }
    double width() const { return width_; }
    double height() const { return height_; }
    void configure(const graph::Spec& spec);
    /// Finished triangles and axes from the worker (GUI thread).
    void apply(graph::Output out);

    /// GUI-side copy: spec, legend and columns only – the worker owns the computing state.
    graph::GraphState state;
Q_SIGNALS:
    void legendChanged();
    void axesChanged();
private:
    QString key_, title_;
    GraphsBridge* owner_;
    graph::Style style_;
    std::vector<graph::Axis> axes_;
    QVariantList ticks_[2];
    std::vector<std::string> units_;
};

class SimHost;

/// The background thread that computes the graphs. One job at a time; a new job replaces a
/// waiting one ("latest wins"), so a slow device skips refreshes instead of queueing them.
class GraphWorker {
public:
    struct ViewJob {
        QPointer<GraphView> view;
        const void* id;
        graph::Spec spec;
        graph::Style style;
        double width, height, dpr;
    };
    struct Job {
        std::vector<ViewJob> views;
        std::vector<int> columns;
        double window = 10.0;
        double rate_hz = 200.0;
        std::shared_ptr<const std::map<std::string, graph::Signal>> catalogue;
    };
    using Deliver = std::function<void(std::vector<std::pair<QPointer<GraphView>, graph::Output>>, double ms)>;

    GraphWorker(SimHost* host, Deliver deliver);
    ~GraphWorker();
    void submit(Job job);
    void stop();  ///< joins the thread (before the physics host goes away)

private:
    void run();
    struct Entry {
        std::unique_ptr<graph::GraphState> state;
        long last_used = 0;
    };
    SimHost* host_;
    Deliver deliver_;
    std::mutex mutex_;
    std::condition_variable cv_;
    std::optional<Job> pending_;
    bool stop_ = false;
    std::map<const void*, Entry> states_;  // worker thread only
    long jobs_ = 0;
    std::thread thread_;
};

class GraphsBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList available READ available NOTIFY availableChanged)
    Q_PROPERTY(QVariantList selection READ selection NOTIFY selectionChanged)
    Q_PROPERTY(int maxVisible READ maxVisible CONSTANT)
    Q_PROPERTY(QVariantList windows READ windows CONSTANT)
    Q_PROPERTY(QString problem READ problem NOTIFY problemChanged)
    Q_PROPERTY(QVariantList smoothings READ smoothings CONSTANT)
    Q_PROPERTY(QVariantList loopLabels READ loopLabels NOTIFY availableChanged)
    Q_PROPERTY(QVariantList signals READ signalList NOTIFY catalogueChanged)
    Q_PROPERTY(int windowS READ windowS WRITE setWindowS NOTIFY windowChanged)
    Q_PROPERTY(QString smoothing READ smoothing WRITE setSmoothing NOTIFY smoothingChanged)
    Q_PROPERTY(int pidLoop READ pidLoop WRITE setPidLoop NOTIFY loopChanged)
    Q_PROPERTY(double rateHz READ rateHz WRITE setRateHz NOTIFY rateChanged)
public:
    explicit GraphsBridge(SimulationBridge* sim, QObject* parent = nullptr);
    ~GraphsBridge() override;
    void shutdown();  ///< stop the worker thread (call before the simulation is destroyed)

    QVariantList available() const;
    QVariantList selection() const;
    int maxVisible() const { return graph::kMaxVisible; }
    QVariantList windows() const;
    QString problem() const { return problem_; }
    QVariantList smoothings() const { return {"off", "light", "medium", "strong"}; }
    QVariantList loopLabels() const;
    QVariantList signalList() const;
    int windowS() const { return window_; }
    void setWindowS(int seconds);
    QString smoothing() const { return QString::fromStdString(smoothing_); }
    void setSmoothing(const QString& level);
    int pidLoop() const { return loop_; }
    void setPidLoop(int k);
    double rateHz() const { return rate_; }
    void setRateHz(double hz);

    Q_INVOKABLE void select(const QString& key, bool on);
    Q_INVOKABLE QObject* view(const QString& key);
    GraphView* tileView(const QString& tile_id, const graph::Spec& spec);
    void dropTiles(const std::set<QString>& keep);

    const std::map<std::string, graph::Signal>& catalogue() const { return catalogue_; }
    double telemetryRate() const { return rate_telemetry_; }
    void driveFromFrames() { timer_.stop(); }
    void onFrame(double now);  ///< a frame is being shown: refresh if due
    void refresh();
    double lastMs() const { return ms_; }          ///< worker time of the last refresh [ms]
    double lastGuiMs() const { return gui_ms_; }   ///< GUI-thread time of the last refresh [ms]

Q_SIGNALS:
    void selectionChanged();
    void availableChanged();
    void windowChanged();
    void loopChanged();
    void rateChanged();
    void smoothingChanged();
    void catalogueChanged();
    void problemChanged();

private:
    std::vector<std::string> labels() const;
    std::vector<std::string> availableKeys() const;
    graph::Spec cardSpec(const std::string& key) const;
    void reconfigureCards();
    void onStructure();

    SimulationBridge* sim_;
    QSettings settings_;
    std::vector<std::string> selection_;
    int window_ = 10;
    std::string smoothing_ = "off";
    int loop_ = 0;
    double rate_ = 25.0;
    double rate_telemetry_ = 200.0;
    std::map<std::string, graph::Signal> catalogue_;
    std::map<QString, GraphView*> views_;
    std::map<QString, GraphView*> tiles_;
    double ms_ = 0.0, gui_ms_ = 0.0, due_ = 0.0;
    std::shared_ptr<const std::map<std::string, graph::Signal>> shared_catalogue_;
    std::unique_ptr<GraphWorker> worker_;
    QString problem_;
    QTimer timer_;
};

}  // namespace app
