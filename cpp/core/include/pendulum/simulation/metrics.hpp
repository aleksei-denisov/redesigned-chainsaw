#pragma once
// Performance metrics of a recorded run (simulation/metrics.py), angle errors in degrees.
#include <optional>
#include <vector>

namespace pendulum {

inline constexpr double kFallAngleDeg = 90.0;

struct SignalMetrics {
    double max_deviation = 0.0;           ///< max |e|
    double rmse = 0.0;                    ///< sqrt(mean e²)
    std::optional<double> settling_time;  ///< earliest t with |e| ≤ tol from then on; none if never
    double overshoot_pct = 0.0;           ///< opposite-side excursion after the peak, % of the peak
};

struct RunMetrics {
    std::vector<SignalMetrics> segments;
    double max_deviation = 0.0;           ///< worst segment [deg]
    double rmse = 0.0;                    ///< combined over segments [deg]
    std::optional<double> settling_time;  ///< slowest segment [s]
    double overshoot_pct = 0.0;           ///< worst segment [%]
    double control_rms = 0.0;
    double control_peak = 0.0;
    double control_energy = 0.0;          ///< ∫u² dt
    double max_cart_displacement = 0.0;
    bool fell = false;
    double saturation_pct = 0.0;          ///< time with the actuator at its limit [%] (new in C++)
};

std::optional<double> settlingTime(const std::vector<double>& t, const std::vector<double>& e, double tolerance);
double overshootPct(const std::vector<double>& e);
SignalMetrics signalMetrics(const std::vector<double>& t, const std::vector<double>& e_deg, double tolerance_deg);
RunMetrics runMetrics(const std::vector<double>& t, const std::vector<std::vector<double>>& angle_errors_deg,
                      const std::vector<double>& control, const std::vector<double>& cart_position,
                      double tolerance_deg, bool diverged = false);

}  // namespace pendulum
