#include "pendulum/control/pid.hpp"

#include <algorithm>
#include <cmath>
#include <numbers>
#include <stdexcept>

namespace pendulum {

std::string toString(ControlAction a) { return a == ControlAction::Direct ? "direct" : "reverse"; }
std::string toString(AntiWindup a) {
    switch (a) {
        case AntiWindup::None: return "none";
        case AntiWindup::Clamp: return "clamp";
        case AntiWindup::BackCalculation: return "back_calculation";
    }
    return "clamp";
}
std::string toString(DerivativeMode m) {
    return m == DerivativeMode::MeasuredRate ? "measured_rate" : "filtered_difference";
}
ControlAction controlActionFromString(const std::string& s) {
    if (s == "direct") return ControlAction::Direct;
    if (s == "reverse") return ControlAction::Reverse;
    throw std::invalid_argument("'" + s + "' is not a valid ControlAction");
}
AntiWindup antiWindupFromString(const std::string& s) {
    if (s == "none") return AntiWindup::None;
    if (s == "clamp") return AntiWindup::Clamp;
    if (s == "back_calculation") return AntiWindup::BackCalculation;
    throw std::invalid_argument("'" + s + "' is not a valid AntiWindup");
}
DerivativeMode derivativeModeFromString(const std::string& s) {
    if (s == "measured_rate") return DerivativeMode::MeasuredRate;
    if (s == "filtered_difference") return DerivativeMode::FilteredDifference;
    throw std::invalid_argument("'" + s + "' is not a valid DerivativeMode");
}

std::optional<int> PIDLoopConfig::segmentIndex() const {
    if (!isAngle()) return std::nullopt;
    try {
        return std::stoi(signal.substr(5)) - 1;
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

double wrapAngle(double angle) {
    constexpr double pi = std::numbers::pi;
    double wrapped = std::fmod(angle + pi, 2.0 * pi);
    if (wrapped <= 0.0) wrapped += 2.0 * pi;
    return wrapped - pi;
}

void PIDLoop::reset() {
    integral = 0.0;
    prev_error_.reset();
    filtered_derivative_ = 0.0;
    last = {};
}

double PIDLoop::error(double measurement, double direction) const {
    double e = measurement - config.setpoint;
    if (config.isAngle()) e = wrapAngle(e);
    return direction * e;
}

LoopOutput PIDLoop::compute(double measurement, double rate, double ts, double direction) {
    const auto& c = config;
    const double e = error(measurement, direction);
    double de;
    if (c.derivative_mode == DerivativeMode::MeasuredRate) {
        de = direction * rate;
    } else {
        double raw = 0.0;
        if (prev_error_) {
            const double diff = e - *prev_error_;
            raw = (c.isAngle() ? wrapAngle(diff) : diff) / ts;
        }
        const double tau = 1.0 / (2.0 * std::numbers::pi * std::max(c.derivative_filter_hz, 1e-6));
        const double alpha = ts / (ts + tau);
        filtered_derivative_ += alpha * (raw - filtered_derivative_);
        de = filtered_derivative_;
    }
    prev_error_ = e;
    if (!c.i_enabled) integral = 0.0;
    limitIntegral();
    LoopOutput out;
    out.error = e;
    out.p = c.p_enabled ? c.kp * e : 0.0;
    out.i = c.i_enabled ? integral : 0.0;
    out.d = c.d_enabled ? c.kd * de : 0.0;
    last = out;
    return out;
}

void PIDLoop::updateIntegral(double ts, double unsaturated, double saturated) {
    const auto& c = config;
    if (!(c.enabled && c.i_enabled) || c.ki == 0.0) return;
    const double e = last.error;
    double increment = c.ki * e * ts;
    const bool saturated_now = unsaturated != saturated;
    if (c.anti_windup == AntiWindup::Clamp) {
        // Stop integrating if this would drive the output further into saturation.
        if (saturated_now && increment * unsaturated > 0.0) increment = 0.0;
    } else if (c.anti_windup == AntiWindup::BackCalculation) {
        increment += c.back_calculation_gain * (saturated - unsaturated) * ts;
    }
    integral += increment;
    limitIntegral();
}

void PIDLoop::limitIntegral() {
    const double lim = config.integral_limit;
    if (lim > 0.0) integral = std::max(-lim, std::min(lim, integral));
}

}  // namespace pendulum
