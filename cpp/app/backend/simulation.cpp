#include "simulation.h"

#include <cmath>
#include <numbers>

#include "pendulum/session/rules.hpp"

namespace app {

using namespace pendulum;

namespace {
constexpr double kReadoutHz = 15.0;
constexpr double kPushDurationS = 0.1;
double degrees(double r) { return r * (180.0 / std::numbers::pi); }
double wrap(double a) {
    double w = std::fmod(a + std::numbers::pi, 2.0 * std::numbers::pi);
    if (w < 0) w += 2.0 * std::numbers::pi;
    return w - std::numbers::pi;
}
QString fmt(const char* f, double v) { return QString::asprintf(f, v); }
}  // namespace

SimulationBridge::SimulationBridge(const AppConfig& config, QObject* parent)
    : QObject(parent), session(config), host_(std::make_unique<SimHost>(session.config)) {
    texts_[0] = "+0.00";
    texts_[1] = "+0.0";
    texts_[2] = "+0.000";
    texts_[3] = "+0.00";
    texts_[4] = "T 000.00 s";
    texts_[5] = "0 FPS";
    updateStructure();
}

void SimulationBridge::updateStructure() {
    const auto& p = session.config.plant;
    n_ = p.nSegments();
    lens_.clear();
    for (const auto& s : p.segments) lens_.push_back(s.length);
}

QVariantList SimulationBridge::lengths() const {
    QVariantList l;
    for (double v : lens_) l << v;
    return l;
}

QVariantList SimulationBridge::speeds() const {
    QVariantList l;
    for (double v : session::kSpeeds) l << v;
    return l;
}

void SimulationBridge::advance() {
    const double started = monotonic();
    if (host_->read(sample_)) {
        cart_ = sample_.state[0];
        for (int i = 0; i < 3; ++i) th_[i] = i < n_ ? sample_.state[1 + i] : 0.0;
        omega_ = sample_.n_state > n_ + 2 ? sample_.state[n_ + 2] : 0.0;
        applied_ = sample_.applied;
        tip_ = sample_.tip;
        t_ = sample_.t;
        epoch_ = sample_.epoch;
    }
    if (last_frame_ > 0) gap_ms_ = (started - last_frame_) * 1000.0;
    last_frame_ = started;
    ++fps_count_;
    if (fps_second_ == 0.0) fps_second_ = started;
    if (started - fps_second_ >= 0.5) {
        fps_ = fps_count_ / (started - fps_second_);
        fps_count_ = 0;
        fps_second_ = started;
    }
    Q_EMIT frame();  // QML places its items in this call
    if (started >= readout_due_) {
        readout_due_ = started + 1.0 / kReadoutHz;
        readouts();
    }
}

void SimulationBridge::readouts() {
    texts_[0] = fmt("%+.2f", degrees(wrap(th_[0])));
    texts_[1] = fmt("%+.1f", degrees(omega_));
    texts_[2] = fmt("%+.3f", cart_);
    texts_[3] = fmt("%+.2f", applied_);
    texts_[4] = fmt("T %06.2f s", t_);
    texts_[5] = fmt("%.0f FPS", fps_);
    const HostStats st = host_->stats();
    if (running_) {
        const double rtf = st.realtime_factor;
        texts_[6] = fmt("%.2f×", rtf) + (rtf < 0.95 * st.speed ? " (slower than real time)" : "");
    } else {
        texts_[6].clear();
    }
    const double energy = host_->latest(kEnergy);
    texts_[7] = std::isnan(energy) ? QString() : fmt("E %.4f J", energy);
    if (st.running != running_) {  // stopped by the physics (divergence)
        running_ = st.running;
        Q_EMIT runningChanged();
    }
    Q_EMIT readoutsChanged();
    if (auto msg = host_->message()) {
        const QString text = QString::fromStdString(msg->second);
        if (msg->first == MessageKind::Diverged)
            Q_EMIT message("error", text + "\n\nThe simulation was paused. Reset it, or reduce the time step / gains.");
        else
            Q_EMIT message("warning", "⚠ " + text);
    }
}

void SimulationBridge::setRunning(bool on) {
    if (on != running_) {
        running_ = on;
        host_->send(cmd::Run{on});
        Q_EMIT runningChanged();
    }
}

void SimulationBridge::reset() {
    host_->send(cmd::Reset{});
    if (running_) {
        running_ = false;
        Q_EMIT runningChanged();
    }
}

void SimulationBridge::restart() {
    host_->send(cmd::Restart{});
    if (!running_) {
        running_ = true;
        Q_EMIT runningChanged();
    }
}

void SimulationBridge::step() {
    host_->send(cmd::Step{});
    if (running_) {
        running_ = false;
        Q_EMIT runningChanged();
    }
}

void SimulationBridge::push(int direction) {
    const double force = (direction >= 0 ? 1.0 : -1.0) * session.config.push_force;
    host_->send(cmd::Push{force, kPushDurationS, -1, 1.0});
    Q_EMIT message("info", QString::asprintf("Tip push %+.2f N for %g s", force, kPushDurationS));
}

void SimulationBridge::pushAt(int direction, int segment, double ratio) {
    const double force = (direction >= 0 ? 1.0 : -1.0) * session.config.push_force;
    host_->send(cmd::Push{force, kPushDurationS, segment, ratio});
    const QString where = segment < 0 || (segment == n_ - 1 && ratio >= 0.999)
                              ? QStringLiteral("tip")
                              : QString::asprintf("segment %d at %.0f %%", segment + 1, ratio * 100);
    Q_EMIT message("info", QString::asprintf("Push %+.2f N for %g s at the ", force, kPushDurationS) + where);
}

void SimulationBridge::drag(double offset) {
    host_->send(cmd::Drag{session::dragForce(session.config.push_force, offset), -1, 1.0});
}

void SimulationBridge::dragAt(double offset, int segment, double ratio) {
    host_->send(cmd::Drag{session::pointDragForce(session.config.push_force, offset), segment, ratio});
}

void SimulationBridge::endDrag() { host_->send(cmd::Drag{0.0, -1, 1.0}); }

void SimulationBridge::setSpeed(double v) {
    host_->send(cmd::Speed{v});
    speed_ = v;
    Q_EMIT speedChanged();
}

void SimulationBridge::setPushForce(double v) {
    session.setPushForce(v);
    Q_EMIT pushForceChanged();
}

bool SimulationBridge::load(const AppConfig& config) {
    std::vector<session::Operation> ops;
    try {
        ops = session.load(config);
    } catch (const std::exception& e) {
        Q_EMIT message("error", QString("Invalid configuration: ") + e.what());
        return false;
    }
    running_ = false;
    applyOperations(ops);
    host_->send(cmd::Run{false});
    updateStructure();
    Q_EMIT runningChanged();
    Q_EMIT structureChanged();
    Q_EMIT configChanged();
    Q_EMIT controllerChanged();
    Q_EMIT pushForceChanged();
    if (!config.description.empty()) Q_EMIT message("info", QString::fromStdString(config.description));
    return true;
}

void SimulationBridge::structureEdited() {
    updateStructure();
    Q_EMIT structureChanged();
}

void SimulationBridge::suspend() {
    suspended_running_ = running_;
    setRunning(false);
    host_->send(cmd::Drag{0.0, -1, 1.0});
}

void SimulationBridge::resume() {
    if (suspended_running_) {
        suspended_running_ = false;
        setRunning(true);
    }
}

}  // namespace app
