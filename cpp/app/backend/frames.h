#pragma once
// Context object `frames` (quick/backend/frames.py, pacing.py): the vsync-driven frame loop.
// QML's FrameAnimation calls vsync() on every vsync; the Pacer decides whether this vsync
// shows a new frame (120/60/40/30 fps on a 120 Hz display; "auto" keeps the highest rate
// without dropped frames). Below the display rate each frame is scheduled from the previous
// swap, so Qt really renders less. Per shown frame: sim.advance(), then graphs, panel, trail.
#include <QObject>
#include <QTimer>
#include <deque>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace app {

class SimulationBridge;
class GraphsBridge;

class Pacer {
public:
    explicit Pacer(double refresh_hz = 60.0, const QString& mode = "auto");
    void setMode(const QString& mode);
    void setRefresh(double refresh_hz);
    int divisor() const;
    double intervalMs() const { return 1000.0 / rate; }
    bool vsync();                                  ///< true on the vsyncs that show a frame
    void frameDone(double now, double cost_ms);    ///< a shown frame's work [ms]
    void paused() { last_shown_.reset(); }

    double refresh_hz;
    std::vector<int> rates;
    struct Change {
        double t;  ///< seconds since the pacer started
        int from, to;
        std::string why;
    };
    std::vector<Change> log;  ///< every rate change (the first 64)
    QString mode = "auto";
    int rate = 60;
    int changes = 0;

private:
    void setRate(int r, const std::string& why = "");
    double started_;
    int phase_ = 0;
    struct S {
        double t, cost;
        bool missed;
    };
    std::deque<S> samples_;
    std::optional<double> good_since_, last_shown_, stepped_up_at_;
    double hold_ = 3.0;
    std::map<int, int> failures_;
};

double p95(std::vector<double> v);

class FrameDriver : public QObject {
    Q_OBJECT
    Q_PROPERTY(int rate READ rate NOTIFY rateChanged)
    Q_PROPERTY(QString mode READ mode NOTIFY rateChanged)
    Q_PROPERTY(bool continuous READ continuous NOTIFY continuousChanged)
    Q_PROPERTY(bool enabled READ enabled WRITE setEnabled NOTIFY enabledChanged)
    Q_PROPERTY(QString statsText READ statsText NOTIFY statsChanged)
    Q_PROPERTY(bool py READ py CONSTANT)
public:
    FrameDriver(SimulationBridge* sim, double refresh_hz, const QString& mode, QObject* parent = nullptr);

    int rate() const { return pacer.rate; }
    QString mode() const { return pacer.mode; }
    bool continuous() const { return enabled_ && pacer.divisor() == 1; }
    bool enabled() const { return enabled_; }
    void setEnabled(bool on);
    QString statsText() const { return stats_; }
    bool py() const { return false; }

    Q_INVOKABLE void vsync();
    void swapped();  ///< QQuickWindow::frameSwapped (queued)
    void setMode(const QString& m);
    void setRefresh(double hz);
    std::vector<std::function<void(double)>> listeners;  ///< per shown frame (graphs, panel, trail)

    /// Frame records for the performance test: (gap ms, work ms).
    std::vector<std::pair<double, double>>* recording = nullptr;
    QString describe() const;
    Pacer pacer;

Q_SIGNALS:
    void rateChanged();
    void continuousChanged();
    void enabledChanged();
    void statsChanged();

private:
    void showFrame();
    void pacedFrame();
    void idleFrame();
    void rateWasChanged();
    void updateStats();

    SimulationBridge* sim_;
    bool enabled_ = true;
    double cpu_last_ = 0.0, wall_last_ = 0.0;
    std::vector<double> recent_, gaps_;
    QString stats_;
    QTimer stats_timer_, tick_, keepalive_;
};

}  // namespace app
