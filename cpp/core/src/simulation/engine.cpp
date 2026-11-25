#include "pendulum/simulation/engine.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>
#include <numbers>

namespace pendulum {

namespace {
double radians(double deg) { return deg * (std::numbers::pi / 180.0); }

std::optional<std::size_t> windowSamples(const AppConfig& c, std::optional<double> window_s) {
    if (!window_s) return std::nullopt;
    return static_cast<std::size_t>(*window_s * c.simulation.telemetry_rate_hz);
}
}  // namespace

Simulator::Simulator(const AppConfig& config, std::optional<double> telemetry_window_s)
    : config_((config.validate(), config)),
      telemetry_(windowSamples(config, telemetry_window_s)),
      model_(config.plant),
      actuator_(config.plant.actuator),
      controller_(config.controller) {
    reset();
}

void Simulator::reset() {
    model_.setParams(config_.plant);
    actuator_.params = config_.plant.actuator;
    actuator_.reset();
    controller_.setConfig(config_.controller);
    controller_.reset();
    state_ = model_.initialState();
    time_ = 0.0;
    steps_ = 0;
    disturbances_.clear();
    rng_.reseed(static_cast<std::uint64_t>(config_.simulation.noise_seed));
    last_control_ = ControllerOutput{};
    applied_ = 0.0;
    tip_force_ = 0.0;
    push_arms_.reset();
    next_record_ = 0.0;
    saturated_steps_ = 0;
    saturated_now_ = false;
    telemetry_.clear();
    telemetry_.loop_names.clear();
    for (const auto& l : config_.controller.loops) telemetry_.loop_names.push_back(l.name);
    record();
}

bool Simulator::updatePlant(const PlantParams& plant) {
    plant.validate();
    if (plant.nSegments() != model_.n() || plant.base_type != model_.params().base_type) return false;
    config_.plant = plant;
    model_.setParams(plant);
    actuator_.params = plant.actuator;
    return true;
}

void Simulator::updateController(const ControllerConfig& controller) {
    controller.validate();
    config_.controller = controller;
    controller_.setConfig(controller);
    telemetry_.loop_names.clear();
    for (const auto& l : controller.loops) telemetry_.loop_names.push_back(l.name);
}

void Simulator::applyDisturbance(double force, double duration, std::optional<double> start, int segment,
                                 double ratio) {
    const double t0 = start.value_or(time_);
    if (segment >= model_.n() || (segment == model_.n() - 1 && ratio >= 1.0)) segment = -1;  // the tip
    disturbances_.push_back({t0, t0 + duration, force, segment, ratio});
}

void Simulator::measure(Vec& q, Vec& qd) {
    const int n1 = model_.n() + 1;
    q = state_.head(n1);
    qd = state_.segment(n1, n1);
    const auto& s = config_.simulation;
    if (s.sensor_noise_angle_deg > 0)
        for (int i = 1; i < n1; ++i) q[i] += rng_.normal(0.0, radians(s.sensor_noise_angle_deg));
    if (s.sensor_noise_rate_dps > 0)
        for (int i = 1; i < n1; ++i) qd[i] += rng_.normal(0.0, radians(s.sensor_noise_rate_dps));
    if (s.sensor_noise_position > 0 && model_.hasCart()) q[0] += rng_.normal(0.0, s.sensor_noise_position);
}

double Simulator::tipForceNow() {
    push_arms_.reset();
    if (disturbances_.empty()) return 0.0;
    double force = 0.0;
    bool below_tip = false;
    for (const auto& d : disturbances_)
        if (d.start <= time_ && time_ < d.end) {
            force += d.force;
            below_tip = below_tip || !d.atTip();
        }
    if (below_tip) {
        std::array<double, kMaxSegments> arms{};
        for (const auto& d : disturbances_)
            if (d.start <= time_ && time_ < d.end) {
                const auto a = model_.pushArms(d.segment, d.ratio);
                for (int j = 0; j < model_.n(); ++j) arms[j] += d.force * a[j];
            }
        push_arms_ = arms;
    }
    std::erase_if(disturbances_, [&](const Disturbance& d) { return !(d.end > time_); });
    return force;
}

void Simulator::step() {
    const double dt = config_.simulation.dt;
    if (controller_.sampleDue(time_)) {
        // Sensors are only read when the controller samples.
        Vec q, qd;
        measure(q, qd);
        last_control_ = controller_.step(time_, q, qd);
    }
    const double command = last_control_.command + manual_force;
    applied_ = actuator_.step(command, dt);
    // Saturated: the command asks for more than the actuator can give, or the controller
    // clipped its own output (the lag means the output itself only approaches the limit).
    const double limit = config_.plant.actuator.max_output;
    saturated_now_ = std::abs(command) >= limit * (1.0 - 1e-9) ||
                     (config_.controller.enabled && last_control_.unsaturated != last_control_.command);
    if (saturated_now_) ++saturated_steps_;
    tip_force_ = tipForceNow();
    state_ = model_.rk4Step(state_, applied_, tip_force_, dt, push_arms_);
    ++steps_;
    time_ += dt;
    // "not <=" is also true for NaN: one comparison catches non-finite states.
    if (!(state_.cwiseAbs().maxCoeff() <= kDivergenceLimit)) {
        char msg[128];
        std::snprintf(msg, sizeof msg, "simulation diverged at t = %.3f s – reduce the time step or the gains", time_);
        throw SimulationDiverged(msg);
    }
    if (time_ + 1e-12 >= next_record_) record();
}

int Simulator::advance(double duration, std::optional<int> max_steps) {
    int n = static_cast<int>(std::nearbyint(duration / config_.simulation.dt));
    if (max_steps) n = std::min(n, *max_steps);
    for (int i = 0; i < n; ++i) step();
    return n;
}

void Simulator::record() {
    next_record_ += 1.0 / config_.simulation.telemetry_rate_hz;
    const int n = model_.n(), n1 = n + 1;
    TelemetryRow row;
    row.fill(std::numeric_limits<double>::quiet_NaN());
    row[kT] = time_;
    row[kX] = state_[0];
    row[kXd] = state_[n1];
    for (int i = 0; i < n; ++i) {
        row[kTheta1 + i] = state_[1 + i];  // continuous (unwrapped)
        row[kOmega1 + i] = state_[n1 + 1 + i];
    }
    row[kCommand] = last_control_.command;
    row[kUnsaturated] = last_control_.unsaturated;
    row[kActuator] = applied_;
    row[kTipForce] = tip_force_;
    const double ke = model_.kineticEnergy(state_), pe = model_.potentialEnergy(state_);
    row[kKe] = ke;
    row[kPe] = pe;
    row[kEnergy] = ke + pe;
    const auto& loops = controller_.loops();
    for (std::size_t k = 0; k < loops.size() && k < static_cast<std::size_t>(kMaxLoops); ++k) {
        const auto& out = loops[k].last;
        const int kk = static_cast<int>(k);
        row[loopColumn(kk, 0)] = out.error;
        row[loopColumn(kk, 1)] = out.p;
        row[loopColumn(kk, 2)] = out.i;
        row[loopColumn(kk, 3)] = out.d;
    }
    row[kSaturated] = saturated_now_ ? 1.0 : 0.0;
    telemetry_.append(row);
}

}  // namespace pendulum
