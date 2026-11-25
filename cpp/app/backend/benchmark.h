#pragma once
// Context object `bench`: the performance test (Settings → Run performance test, or
// --benchmark on the command line). Phases of a few seconds each on the Simulate and Graphs
// screens; per phase the frame rate, frame gaps, dropped frames, GUI work per frame, the
// physics' real-time factor and the graph refresh time. The report is plain text.
#include <QObject>
#include <QTimer>
#include <QPointer>
#include <QQuickWindow>

#include "frames.h"

namespace app {

class SimulationBridge;
class GraphsBridge;
class FrameDriver;
class AppBridge;

class Benchmark : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool running READ running NOTIFY changed)
    Q_PROPERTY(QString progress READ progress NOTIFY changed)
    Q_PROPERTY(QString report READ report NOTIFY changed)
    Q_PROPERTY(bool copied READ copied NOTIFY changed)
public:
    Benchmark(AppBridge* app, SimulationBridge* sim, GraphsBridge* graphs, FrameDriver* frames, QObject* parent = nullptr);
    bool running() const { return phase_ >= 0; }
    QString progress() const { return progress_; }
    QString report() const { return report_; }
    bool copied() const { return copied_; }
    Q_INVOKABLE void start();
    Q_INVOKABLE void cancel();
    Q_INVOKABLE void copy();
    Q_INVOKABLE void show() { Q_EMIT showRequested(); }
    QPointer<QQuickWindow> window;
Q_SIGNALS:
    void changed();
    void finished();
    void showRequested();
private:
    void startPhase(int i);   ///< set up phase i, measure after the settling time
    void beginMeasuring();
    void endPhase();
    void finish(bool cancelled);
    AppBridge* app_;
    SimulationBridge* sim_;
    GraphsBridge* graphs_;
    FrameDriver* frames_;
    int phase_ = -1;
    QString progress_, report_;
    bool copied_ = false;
    QStringList lines_;
    std::vector<FrameDriver::FrameRecord> frames_rec_;
    std::vector<double> graph_ms_, graph_gui_ms_;
    int changes0_ = 0;
    QTimer settle_, measure_, push_timer_, graph_timer_;
    int push_dir_ = 1;
    long steps0_ = 0;
    double busy0_ = 0, t0_ = 0, wall0_ = 0;
    QString screen0_;
    double speed0_ = 1.0;
};

}  // namespace app
