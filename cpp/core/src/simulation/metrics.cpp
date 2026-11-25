#include "pendulum/simulation/metrics.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace pendulum {

namespace {
double meanSquare(const std::vector<double>& v) {
    double acc = 0.0;
    for (double x : v) acc += x * x;
    return v.empty() ? 0.0 : acc / static_cast<double>(v.size());
}
double maxAbs(const std::vector<double>& v) {
    double m = 0.0;
    for (double x : v) m = std::max(m, std::abs(x));
    return m;
}
}  // namespace

std::optional<double> settlingTime(const std::vector<double>& t, const std::vector<double>& e, double tolerance) {
    std::ptrdiff_t last = -1;
    for (std::size_t i = 0; i < e.size(); ++i)
        if (std::abs(e[i]) > tolerance) last = static_cast<std::ptrdiff_t>(i);
    if (last < 0) return 0.0;
    if (last == static_cast<std::ptrdiff_t>(e.size()) - 1) return std::nullopt;
    return t[static_cast<std::size_t>(last) + 1];
}

double overshootPct(const std::vector<double>& e) {
    if (e.empty()) return 0.0;
    std::size_t k = 0;
    for (std::size_t i = 1; i < e.size(); ++i)
        if (std::abs(e[i]) > std::abs(e[k])) k = i;  // first maximum, as np.argmax
    const double peak = e[k];
    if (peak == 0.0) return 0.0;
    const double sgn = peak > 0 ? 1.0 : -1.0;
    double opp = -std::numeric_limits<double>::infinity();
    for (std::size_t i = k; i < e.size(); ++i) opp = std::max(opp, -sgn * e[i]);
    return std::max(0.0, opp) / std::abs(peak) * 100.0;
}

SignalMetrics signalMetrics(const std::vector<double>& t, const std::vector<double>& e, double tol) {
    SignalMetrics m;
    m.max_deviation = e.empty() ? 0.0 : maxAbs(e);
    m.rmse = e.empty() ? 0.0 : std::sqrt(meanSquare(e));
    m.settling_time = settlingTime(t, e, tol);
    m.overshoot_pct = overshootPct(e);
    return m;
}

RunMetrics runMetrics(const std::vector<double>& t, const std::vector<std::vector<double>>& errs,
                      const std::vector<double>& control, const std::vector<double>& cart, double tol, bool diverged) {
    RunMetrics r;
    bool any_unsettled = false;
    double settle = 0.0, sum_sq = 0.0;
    std::size_t count = 0;
    bool fell = diverged;
    for (const auto& e : errs) {
        r.segments.push_back(signalMetrics(t, e, tol));
        const auto& s = r.segments.back();
        r.max_deviation = std::max(r.max_deviation, s.max_deviation);
        r.overshoot_pct = std::max(r.overshoot_pct, s.overshoot_pct);
        if (s.settling_time)
            settle = std::max(settle, *s.settling_time);
        else
            any_unsettled = true;
        for (double x : e) {
            sum_sq += x * x;
            if (std::abs(x) > kFallAngleDeg) fell = true;
        }
        count += e.size();
    }
    r.fell = fell;
    r.rmse = count ? std::sqrt(sum_sq / static_cast<double>(count)) : 0.0;
    if (!fell && !any_unsettled && !errs.empty()) r.settling_time = settle;
    r.control_rms = control.empty() ? 0.0 : std::sqrt(meanSquare(control));
    r.control_peak = control.empty() ? 0.0 : maxAbs(control);
    double energy = 0.0;
    for (std::size_t i = 0; i < control.size() && i < t.size(); ++i) {
        const double dt = i == 0 ? 0.0 : t[i] - t[i - 1];
        energy += control[i] * control[i] * dt;
    }
    r.control_energy = energy;
    r.max_cart_displacement = cart.empty() ? 0.0 : maxAbs(cart);
    return r;
}

}  // namespace pendulum
