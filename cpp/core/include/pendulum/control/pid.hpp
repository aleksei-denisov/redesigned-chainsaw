#pragma once
// Discrete PID loop with anti-windup, derivative filtering and component switches
// (control/pid.py). u = Kp·e + I + Kd·ė; the integral is stored as the integral *term*.
#include <optional>
#include <string>

namespace pendulum {

enum class ControlAction { Direct, Reverse };  ///< e = y − r  /  e = r − y
enum class AntiWindup { None, Clamp, BackCalculation };
enum class DerivativeMode { MeasuredRate, FilteredDifference };

std::string toString(ControlAction a);
std::string toString(AntiWindup a);
std::string toString(DerivativeMode m);
ControlAction controlActionFromString(const std::string& s);
AntiWindup antiWindupFromString(const std::string& s);
DerivativeMode derivativeModeFromString(const std::string& s);

struct PIDLoopConfig {
    std::string name = "Segment 1 angle";
    std::string signal = "theta1";  ///< "cart" or "theta1".."theta3"
    bool enabled = true;
    double setpoint = 0.0;          ///< rad for angles, m for the cart
    double kp = 40.0, ki = 0.0, kd = 4.0;
    bool p_enabled = true, i_enabled = true, d_enabled = true;
    double integral_limit = 0.0;    ///< |integral term| limit, 0 = unlimited
    AntiWindup anti_windup = AntiWindup::Clamp;
    double back_calculation_gain = 10.0;
    DerivativeMode derivative_mode = DerivativeMode::MeasuredRate;
    double derivative_filter_hz = 50.0;

    bool isAngle() const { return signal.rfind("theta", 0) == 0; }
    std::optional<int> segmentIndex() const;  ///< 0-based for angle loops
};

struct LoopOutput {
    double error = 0.0, p = 0.0, i = 0.0, d = 0.0;
    double total() const { return p + i + d; }
};

/// Wrap an angle to (−π, π].
double wrapAngle(double angle);

class PIDLoop {
public:
    explicit PIDLoop(PIDLoopConfig c = {}) : config(std::move(c)) {}
    void reset();
    double error(double measurement, double direction = 1.0) const;
    LoopOutput compute(double measurement, double rate, double ts, double direction = 1.0);
    void updateIntegral(double ts, double unsaturated, double saturated);

    PIDLoopConfig config;
    double integral = 0.0;
    LoopOutput last;

private:
    void limitIntegral();
    std::optional<double> prev_error_;
    double filtered_derivative_ = 0.0;
};

}  // namespace pendulum
