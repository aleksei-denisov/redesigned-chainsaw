#include "pendulum/session/sliders.hpp"

#include <algorithm>
#include <cmath>

namespace pendulum::session {

double symlogScale(double minimum, double maximum, int decimals) {
    return std::max(std::pow(10.0, -decimals), 1e-3 * std::max(std::abs(minimum), std::abs(maximum)));
}

double toPosition(double v, double lo, double hi, const std::string& mapping, int decimals) {
    if (mapping == "log" && lo > 0)
        return (std::log10(std::max(v, lo)) - std::log10(lo)) / (std::log10(hi) - std::log10(lo));
    if (mapping == "symlog") {
        const double f = symlogScale(lo, hi, decimals);
        const double a = std::asinh(lo / f), b = std::asinh(hi / f);
        return (std::asinh(v / f) - a) / (b - a);
    }
    return hi > lo ? (v - lo) / (hi - lo) : 0.0;
}

double fromPosition(double t, double lo, double hi, const std::string& mapping, int decimals) {
    if (mapping == "log" && lo > 0) return std::pow(10.0, std::log10(lo) + t * (std::log10(hi) - std::log10(lo)));
    if (mapping == "symlog") {
        const double f = symlogScale(lo, hi, decimals);
        const double a = std::asinh(lo / f), b = std::asinh(hi / f);
        return f * std::sinh(a + t * (b - a));
    }
    return lo + t * (hi - lo);
}

double nudge(double v, int direction, double minimum, double maximum, const std::string& mapping, int decimals,
             std::optional<double> step) {
    if (step) return v + direction * *step;
    if (mapping == "log" || mapping == "symlog") {
        const double mag = std::abs(v);
        const double unit = std::pow(10.0, -decimals);
        if (mag < 10 * unit) return v + direction * unit;
        const double raw = 0.02 * mag;  // ~2 % of the value, as a "nice" 1/2/5 step
        const double e = std::pow(10.0, std::floor(std::log10(raw)));
        double nice = raw;
        for (double m : {1.0, 2.0, 5.0, 10.0})
            if (m * e >= raw) {
                nice = m * e;
                break;
            }
        return v + direction * nice;
    }
    return v + direction * (maximum - minimum) / 100.0;
}

}  // namespace pendulum::session
