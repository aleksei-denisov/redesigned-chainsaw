#include "frames.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <ctime>

#include "simhost.h"
#include "simulation.h"

namespace app {

namespace {
constexpr double kWindowS = 1.0, kDownFraction = 1.0, kMissFraction = 0.10, kMissed = 1.8;
constexpr double kUpHoldS = 3.0, kMaxHoldS = 60.0, kFailedTryS = 5.0;
constexpr int kMaxFailedTries = 4, kMinSamples = 20;

std::vector<int> supportedRates(double refresh) {
    std::vector<int> r;
    for (int k = 1; k <= 4; ++k)
        if (refresh / k >= 29.0) r.push_back(static_cast<int>(std::nearbyint(refresh / k)));
    if (r.empty()) r.push_back(static_cast<int>(std::nearbyint(refresh)));
    return r;
}

/// CPU time of the calling thread [s] (the GUI thread's work per frame, rendering included
/// with Qt's basic render loop).
double threadCpu() {
#if defined(CLOCK_THREAD_CPUTIME_ID)
    timespec ts{};
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts) == 0) return ts.tv_sec + ts.tv_nsec * 1e-9;
#endif
    return monotonic();
}
}  // namespace

double p95(std::vector<double> v) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    return v[std::min(v.size() - 1, static_cast<std::size_t>(0.95 * (v.size() - 1) + 0.5))];
}

// ---------------------------------------------------------------------- Pacer
Pacer::Pacer(double refresh, const QString& m) : refresh_hz(refresh > 1 ? refresh : 60.0), started_(monotonic()) {
    rates = supportedRates(refresh_hz);
    rate = rates[0];
    setMode(m);
}

void Pacer::setMode(const QString& m) {
    QString mm = m;
    if (mm != "auto") {
        bool ok = false;
        const int wanted = mm.toInt(&ok);
        if (!ok) {
            mm = "auto";
        } else {
            // fixed rate chosen in Settings
            int r = rates.back();
            for (int c : rates)
                if (c <= wanted) {
                    r = c;
                    break;
                }
            setRate(r, "fixed " + std::to_string(r) + " fps");
        }
    }
    if (mm == "auto" && mode != "auto") {
        setRate(rates[0], "auto");
        failures_.clear();
        hold_ = kUpHoldS;
    }
    mode = mm;
}

void Pacer::setRefresh(double hz) {
    if (hz > 0 && std::abs(hz - refresh_hz) > 0.5) {
        refresh_hz = hz;
        rates = supportedRates(hz);
        const QString m = mode;
        mode = "auto";
        setRate(rates[0], "display " + std::to_string(static_cast<int>(hz)) + " Hz");
        setMode(m);
    }
}

int Pacer::divisor() const { return std::max(1, static_cast<int>(std::nearbyint(refresh_hz / rate))); }

void Pacer::setRate(int r, const std::string& why) {
    if (r != rate) {
        if (log.size() < 64) log.push_back({monotonic() - started_, rate, r, why});
        rate = r;
        ++changes;
    }
    samples_.clear();
    good_since_.reset();
    phase_ = 0;
}

bool Pacer::vsync() {
    const bool show = phase_ == 0;
    phase_ = (phase_ + 1) % divisor();
    return show;
}

void Pacer::frameDone(double now, double cost) {
    bool missed = false;
    if (last_shown_) missed = (now - *last_shown_) * 1000.0 > kMissed * intervalMs();
    last_shown_ = now;
    if (mode != "auto") return;
    samples_.push_back({now, cost, missed});
    while (!samples_.empty() && now - samples_.front().t > kWindowS) samples_.pop_front();
    if (static_cast<int>(samples_.size()) < kMinSamples) return;
    std::vector<double> costs;
    int misses_n = 0;
    for (const auto& s : samples_) {
        costs.push_back(s.cost);
        misses_n += s.missed;
    }
    const double cost95 = p95(costs);
    const double misses = static_cast<double>(misses_n) / samples_.size();
    const auto index = std::find(rates.begin(), rates.end(), rate) - rates.begin();
    if ((misses > kMissFraction || cost95 > kDownFraction * intervalMs()) && index + 1 < static_cast<long>(rates.size())) {
        if (stepped_up_at_ && now - *stepped_up_at_ < kFailedTryS) {
            hold_ = std::min(2.0 * hold_, kMaxHoldS);
            ++failures_[rate];
        }
        stepped_up_at_.reset();
        char why[96];
        std::snprintf(why, sizeof why, "down: %.0f %% missed, work p95 %.1f ms", misses * 100, cost95);
        setRate(rates[index + 1], why);
        return;
    }
    if (index > 0 && misses == 0 && failures_[rates[index - 1]] < kMaxFailedTries) {
        if (!good_since_)
            good_since_ = now;
        else if (now - *good_since_ >= hold_) {
            stepped_up_at_ = now;
            setRate(rates[index - 1], "up: smooth for " + std::to_string(static_cast<int>(hold_)) + " s");
        }
    } else {
        good_since_.reset();
    }
}

// ---------------------------------------------------------------- FrameDriver
FrameDriver::FrameDriver(SimulationBridge* sim, double refresh, const QString& mode, QObject* parent)
    : QObject(parent), pacer(refresh, mode), sim_(sim) {
    stats_timer_.setInterval(250);
    connect(&stats_timer_, &QTimer::timeout, this, &FrameDriver::updateStats);
    stats_timer_.start();
    tick_.setSingleShot(true);
    tick_.setTimerType(Qt::PreciseTimer);
    connect(&tick_, &QTimer::timeout, this, &FrameDriver::pacedFrame);
    keepalive_.setSingleShot(true);
    connect(&keepalive_, &QTimer::timeout, this, &FrameDriver::idleFrame);
    if (!continuous()) keepalive_.start(static_cast<int>(pacer.intervalMs()));
}

void FrameDriver::setMode(const QString& m) {
    const int before = pacer.rate;
    pacer.setMode(m);
    if (pacer.rate != before) rateWasChanged();
}

void FrameDriver::setRefresh(double hz) {
    const int before = pacer.rate;
    pacer.setRefresh(hz);
    if (pacer.rate != before) rateWasChanged();
}

void FrameDriver::rateWasChanged() {
    Q_EMIT rateChanged();
    Q_EMIT continuousChanged();
    if (!continuous() && enabled_) keepalive_.start(static_cast<int>(pacer.intervalMs()));
}

void FrameDriver::swapped() {
    if (enabled_ && pacer.divisor() > 1) {
        keepalive_.stop();
        const double vsync_ms = 1000.0 / pacer.refresh_hz;
        tick_.start(std::max(0, static_cast<int>((pacer.divisor() - 1) * vsync_ms - 3.0)));
    }
}

void FrameDriver::idleFrame() {
    pacer.paused();
    wall_last_ = 0.0;
    pacedFrame();
}

void FrameDriver::pacedFrame() {
    if (!enabled_ || pacer.divisor() == 1) return;
    tick_.stop();
    keepalive_.start(static_cast<int>(2 * pacer.intervalMs()));
    showFrame();
}

void FrameDriver::setEnabled(bool on) {
    if (on == enabled_) return;
    enabled_ = on;
    if (!on) {
        pacer.paused();
        wall_last_ = 0.0;
        tick_.stop();
        keepalive_.stop();
    } else if (pacer.divisor() > 1) {
        keepalive_.start(static_cast<int>(pacer.intervalMs()));
    }
    Q_EMIT enabledChanged();
    Q_EMIT continuousChanged();
}

void FrameDriver::vsync() {
    if (pacer.vsync()) showFrame();
}

void FrameDriver::showFrame() {
    const double now = monotonic();
    const double cpu = threadCpu();
    if (wall_last_ > 0) {
        const double work = (cpu - cpu_last_) * 1000.0;
        const int rate = pacer.rate;
        pacer.frameDone(now, work);
        if (pacer.rate != rate) rateWasChanged();
        const double gap = (now - wall_last_) * 1000.0;
        if (recent_.size() < 512) {
            recent_.push_back(work);
            gaps_.push_back(gap);
        }
        if (recording) recording->emplace_back(gap, work);
    }
    cpu_last_ = cpu;
    wall_last_ = now;
    sim_->advance();
    for (auto& l : listeners) l(now);
}

void FrameDriver::updateStats() {
    QStringList s;
    s << QString("%1 fps%2").arg(pacer.rate).arg(pacer.mode == "auto" ? " auto" : "");
    if (!recent_.empty()) {
        double sum = 0;
        for (double w : recent_) sum += w;
        s << QString::asprintf("work %4.1f / p95 %4.1f ms", sum / recent_.size(), p95(recent_));
        s << QString::asprintf("gap p95 %4.1f max %4.1f ms", p95(gaps_), *std::max_element(gaps_.begin(), gaps_.end()));
    }
    s << "C++ core";
    recent_.clear();
    gaps_.clear();
    stats_ = s.join("\n");
    Q_EMIT statsChanged();
}

QString FrameDriver::describe() const {
    QString s = QString("pacing %1 at %2 fps of %3 Hz (rate changes so far: %4)")
                    .arg(pacer.mode)
                    .arg(pacer.rate)
                    .arg(pacer.refresh_hz)
                    .arg(pacer.changes);
    for (const auto& c : pacer.log)
        s += QString::asprintf("\n   t %6.1f s  %3d → %3d fps  %s", c.t, c.from, c.to, c.why.c_str());
    return s;
}

}  // namespace app
