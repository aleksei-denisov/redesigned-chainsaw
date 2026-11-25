#include "benchmark.h"

#include <QClipboard>
#include <QGuiApplication>
#include <QScreen>
#include <QSysInfo>
#include <cmath>

#include "frames.h"
#include "graphs.h"
#include "settings.h"
#include "simhost.h"
#include "simulation.h"

namespace app {

namespace {
struct Phase {
    const char* name;
    const char* screen;
    double speed;
    bool pushes;
};
const Phase kPhases[] = {{"Simulate, 1×", "simulate", 1.0, false},
                         {"Simulate, pushes every second", "simulate", 1.0, true},
                         {"Simulate, 4×", "simulate", 4.0, false},
                         {"Graphs screen, pushes", "graphs", 1.0, true}};
constexpr int kPhaseMs = 8000;
constexpr int kSettleMs = 1000;  // not measured: the screen builds, the rate settles
}  // namespace

Benchmark::Benchmark(AppBridge* app, SimulationBridge* sim, GraphsBridge* graphs, FrameDriver* frames, QObject* parent)
    : QObject(parent), app_(app), sim_(sim), graphs_(graphs), frames_(frames) {
    settle_.setSingleShot(true);
    connect(&settle_, &QTimer::timeout, this, &Benchmark::beginMeasuring);
    measure_.setSingleShot(true);
    connect(&measure_, &QTimer::timeout, this, &Benchmark::endPhase);
    push_timer_.setInterval(1000);
    connect(&push_timer_, &QTimer::timeout, this, [this] {
        sim_->push(push_dir_);
        push_dir_ = -push_dir_;
    });
    graph_timer_.setInterval(40);
    connect(&graph_timer_, &QTimer::timeout, this, [this] {
        graph_ms_.push_back(graphs_->lastMs());
        graph_gui_ms_.push_back(graphs_->lastGuiMs());
    });
}

void Benchmark::start() {
    if (running()) return;
    const QScreen* s = window && window->screen() ? window->screen() : QGuiApplication::primaryScreen();
    lines_.clear();
    lines_ << "Pendulum C++ – performance report"
           << QString("device: %1 · %2").arg(QSysInfo::prettyProductName(), QSysInfo::currentCpuArchitecture())
           << QString("Qt %1 · window %2×%3 px · pixel ratio %4 · display %5 Hz")
                  .arg(qVersion())
                  .arg(window ? window->width() : 0)
                  .arg(window ? window->height() : 0)
                  .arg(window ? window->effectiveDevicePixelRatio() : 1.0)
                  .arg(s ? s->refreshRate() : 0.0)
           << QString("scenario: %1 · %2").arg(sim_->scenarioName(), frames_->describe()) << "";
    screen0_ = app_->screen();
    speed0_ = sim_->speed();
    copied_ = false;
    sim_->restart();
    startPhase(0);
}

void Benchmark::startPhase(int i) {
    phase_ = i;
    const Phase& p = kPhases[i];
    app_->setScreen(p.screen);
    sim_->setSpeed(p.speed);
    if (p.pushes)
        push_timer_.start();
    else
        push_timer_.stop();
    progress_ = QString("Phase %1 / %2: %3").arg(i + 1).arg(std::size(kPhases)).arg(p.name);
    Q_EMIT changed();
    settle_.start(kSettleMs);
}

void Benchmark::beginMeasuring() {
    const HostStats st = sim_->host().stats();
    steps0_ = st.steps;
    busy0_ = st.busy_s;
    t0_ = st.t;
    wall0_ = monotonic();
    changes0_ = frames_->pacer.changes;
    frames_rec_.clear();
    graph_ms_.clear();
    graph_gui_ms_.clear();
    frames_->recording = &frames_rec_;
    graph_timer_.start();
    measure_.start(kPhaseMs);
}

void Benchmark::endPhase() {
    frames_->recording = nullptr;
    graph_timer_.stop();
    const Phase& p = kPhases[phase_];
    const HostStats st = sim_->host().stats();
    const double wall = monotonic() - wall0_;
    std::vector<double> gaps, work;
    int dropped = 0;
    double gsum = 0, wsum = 0;
    for (const auto& f : frames_rec_) {
        gaps.push_back(f.gap_ms);
        work.push_back(f.work_ms);
        dropped += f.gap_ms > 1.8 * f.interval_ms;  // late for the rate this frame was paced at
        gsum += f.gap_ms;
        wsum += f.work_ms;
    }
    std::vector<double> sorted = gaps;
    std::sort(sorted.begin(), sorted.end());
    const double p50 = sorted.empty() ? 0 : sorted[sorted.size() / 2];
    const double gmax = sorted.empty() ? 0 : sorted.back();
    const long steps = st.steps - steps0_;
    lines_ << QString("%1. %2").arg(phase_ + 1).arg(p.name);
    if (frames_->pacer.changes != changes0_)
        lines_ << QString("   frame rate changed %1× during this phase").arg(frames_->pacer.changes - changes0_);
    lines_ << QString::asprintf("   frames %zu in %.1f s = %.1f fps (rate %d, %s)", gaps.size(), wall,
                                gsum > 0 ? 1000.0 * gaps.size() / gsum : 0.0, frames_->pacer.rate,
                                frames_->pacer.mode.toUtf8().constData());
    lines_ << QString::asprintf("   frame gap p50 %.1f · p95 %.1f · max %.1f ms · dropped %d (%.1f %%)", p50, p95(gaps),
                                gmax, dropped, gaps.empty() ? 0.0 : 100.0 * dropped / gaps.size());
    lines_ << QString::asprintf("   GUI work per frame mean %.2f · p95 %.2f ms", work.empty() ? 0.0 : wsum / work.size(),
                                p95(work));
    lines_ << QString::asprintf("   physics %.2f× real time (speed %g×), %ld steps, %.4f ms CPU per step",
                                (st.t - t0_) / wall, p.speed, steps, steps ? 1000.0 * (st.busy_s - busy0_) / steps : 0.0);
    if (!graph_ms_.empty())
        lines_ << QString::asprintf("   graphs: background thread p95 %.2f ms · GUI thread p95 %.3f ms", p95(graph_ms_),
                                    p95(graph_gui_ms_));
    if (phase_ + 1 < static_cast<int>(std::size(kPhases)))
        startPhase(phase_ + 1);
    else
        finish(false);
}

void Benchmark::finish(bool cancelled) {
    settle_.stop();
    measure_.stop();
    push_timer_.stop();
    graph_timer_.stop();
    frames_->recording = nullptr;
    if (cancelled) lines_ << "" << "Cancelled.";
    lines_ << "" << "Frame-rate log: " + frames_->describe();
    report_ = lines_.join("\n");
    phase_ = -1;
    progress_.clear();
    sim_->setSpeed(speed0_);
    app_->setScreen(screen0_);
    Q_EMIT changed();
    Q_EMIT finished();
}

void Benchmark::cancel() {
    if (running()) finish(true);
}

void Benchmark::copy() {
    QGuiApplication::clipboard()->setText(report_);
    copied_ = true;
    Q_EMIT changed();
}

}  // namespace app
