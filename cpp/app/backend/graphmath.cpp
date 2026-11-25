#include "graphmath.h"

#include <algorithm>
#include <cstdio>
#include <numbers>

namespace app::graph {

constexpr double kDeg = 180.0 / std::numbers::pi;

// ---------------------------------------------------------------- signals
Signal::Display Signal::display(const std::string& angle_unit) const {
    if (angular && angle_unit == "deg") return {kDeg, unit == "rad" ? "°" : "°/s", min_span, decimals};
    if (angular) return {1.0, unit, min_span / kDeg, decimals + 2};
    return {1.0, unit, min_span, decimals};
}

std::vector<Signal> catalogue(int n, bool has_cart, const std::vector<std::string>& labels) {
    std::vector<Signal> out;
    auto add = [&](Signal s) { out.push_back(std::move(s)); };
    for (int i = 0; i < n; ++i) {
        const auto k = std::to_string(i + 1);
        add({"theta" + k, "θ" + k, "Angles", "rad", "Angle θ" + k, true, true, 2.0, false, true, 2});
    }
    for (int i = 0; i < n; ++i) {
        const auto k = std::to_string(i + 1);
        add({"omega" + k, "ω" + k, "Angular velocities", "rad/s", "Rate ω" + k, true, false, 20.0, false, true, 1});
    }
    if (has_cart) {
        add({"x", "x", "Cart", "m", "Cart x", false, false, 0.05, false, true, 3});
        add({"xd", "ẋ", "Cart", "m/s", "Cart velocity ẋ", false, false, 0.1, false, true, 3});
    }
    add({"actuator", "F", "Control", "N", "Force F", false, false, 1.0, false, true, 2});
    add({"command", "F cmd", "Control", "N", "Force command", false, false, 1.0, false, true, 2});
    add({"unsaturated", "F unsat", "Control", "N", "Force unsaturated", false, false, 1.0, false, true, 2});
    add({"tip_force", "push", "Control", "N", "Push force", false, false, 1.0, true, true, 2});
    add({"saturated", "sat", "Control", "", "Actuator at its limit", false, false, 1.0, false, true, 0});  // new in C++
    for (std::size_t k = 0; k < labels.size(); ++k) {
        const std::string name = labels[k].empty() ? "loop " + std::to_string(k + 1) : labels[k];
        const bool cart = name == "cart";
        add({"error" + std::to_string(k), "e " + name, "Control error", cart ? "m" : "rad", "Error " + name, !cart, false,
             cart ? 0.05 : 2.0, false, true, cart ? 3 : 2});
    }
    for (std::size_t k = 0; k < labels.size(); ++k) {
        const std::string name = labels[k].empty() ? "loop " + std::to_string(k + 1) : labels[k];
        for (const char* term : {"p", "i", "d"}) {
            std::string up(1, static_cast<char>(std::toupper(term[0])));
            add({term + std::to_string(k), up + " " + name, "PID terms", "N", up + " term " + name, false, false, 0.5,
                 false, true, 2});
        }
    }
    add({"ke", "E kin", "Energy", "J", "Kinetic energy", false, false, 0.02, false, true, 3});
    add({"pe", "E pot", "Energy", "J", "Potential energy", false, false, 0.02, false, true, 3});
    add({"energy", "E total", "Energy", "J", "Total energy", false, false, 0.02, false, true, 3});
    add({"time", "T", "Simulation", "s", "Time T", false, false, 1.0, false, false, 2});
    add({"rtf", "speed", "Simulation", "×", "Speed (real time)", false, false, 1.0, false, false, 2});
    add({"fps", "fps", "Simulation", "fps", "Frame rate", false, false, 1.0, false, false, 0});
    return out;
}

std::string formatValue(double v, int decimals, bool is_signed) {
    if (!std::isfinite(v)) return "—";
    char buf[64];
    std::snprintf(buf, sizeof buf, is_signed ? "%+.*f" : "%.*f", decimals, v);
    return buf;
}

// ------------------------------------------------------------- plot maths
double niceStep(double span, int max_ticks) {
    if (!std::isfinite(span) || span <= 0) return 1.0;
    const double raw = span / std::max(max_ticks, 1);
    const double mag = std::pow(10.0, std::floor(std::log10(raw)));
    for (double m : {1.0, 2.0, 2.5, 5.0, 10.0})
        if (raw <= m * mag + 1e-12 * mag) return m * mag;
    return 10.0 * mag;
}

std::pair<std::vector<double>, double> niceTicks(double lo, double hi, int max_ticks) {
    const double step = niceStep(hi - lo, max_ticks);
    std::vector<double> ticks;
    double v = std::ceil(lo / step - 1e-9) * step;
    while (v <= hi + 1e-9 * step && ticks.size() < 50) {
        ticks.push_back(std::abs(v) < 1e-9 * step ? 0.0 : v);
        v += step;
    }
    return {ticks, step};
}

namespace {
int tickDecimals(double step) {
    if (step <= 0 || !std::isfinite(step)) return 0;
    int d = std::max(0, -static_cast<int>(std::floor(std::log10(step) + 1e-9)));
    const double p = std::pow(10.0, d);
    if (std::abs(step * p - std::round(step * p)) > 1e-6) ++d;  // 2.5 → one more digit
    return std::min(d, 6);
}
}  // namespace

std::string formatTick(double value, double step) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.*f", tickDecimals(step), value);
    std::string s = buf;
    if (s.find_first_not_of("-0.") == std::string::npos) return "0";
    return s;
}

double wrapDegrees(double a) {
    double w = std::fmod(a + 180.0, 360.0);
    if (w < 0) w += 360.0;
    return w - 180.0;
}

const std::vector<GraphDef>& graphDefs() {
    static const std::vector<GraphDef> d = {
        {"angle", "Angle", "deg", 2.0},          {"angular_velocity", "Angular velocity", "deg/s", 20.0},
        {"cart_position", "Cart position", "m", 0.05}, {"cart_velocity", "Cart velocity", "m/s", 0.1},
        {"control", "Control force", "N / N·m", 1.0},  {"error", "Control error", "deg", 2.0},
        {"pid", "PID components", "output", 0.5},      {"energy", "Energy", "J", 0.02}};
    return d;
}

const GraphDef* graphDef(const std::string& key) {
    for (const auto& g : graphDefs())
        if (g.key == key) return &g;
    return nullptr;
}

bool graphAvailable(const std::string& key, int, bool has_cart) {
    return has_cart || (key != "cart_position" && key != "cart_velocity");
}

std::vector<std::string> graphColumns(const std::string& key, int n, const std::vector<std::string>& labels, int loop) {
    std::vector<std::string> out;
    if (key == "angle")
        for (int i = 0; i < n; ++i) out.push_back("theta" + std::to_string(i + 1));
    else if (key == "angular_velocity")
        for (int i = 0; i < n; ++i) out.push_back("omega" + std::to_string(i + 1));
    else if (key == "cart_position")
        out = {"x"};
    else if (key == "cart_velocity")
        out = {"xd"};
    else if (key == "control")
        out = {"command", "actuator", "tip_force"};
    else if (key == "error") {
        for (std::size_t k = 0; k < labels.size(); ++k)
            if (labels[k] != "cart") out.push_back("error" + std::to_string(k));
    } else if (key == "pid") {
        if (!labels.empty()) {
            const int k = std::max(0, std::min(loop, static_cast<int>(labels.size()) - 1));
            out = {"p" + std::to_string(k), "i" + std::to_string(k), "d" + std::to_string(k)};
        }
    } else if (key == "energy") {
        out = {"ke", "pe", "energy"};
    }
    return out;
}

// ------------------------------------------------------------ stable scale
namespace {
constexpr double kPad = 0.08, kShrinkRatio = 0.4, kShrinkDelayS = 3.0, kEaseS = 0.3;
}

StableScale::StableScale(std::string mode, double min_span, std::optional<std::pair<double, double>> fixed)
    : mode_(mode == "symmetric" || mode == "fixed" ? mode : "auto"), min_span_(min_span), fixed_(fixed) {}

void StableScale::reset() {
    lo = hi = NAN;
    target_ = from_ = {NAN, NAN};
    small_since_.reset();
    moving = false;
}

std::pair<double, double> StableScale::rangeFor(double dmin, double dmax) const {
    if (mode_ == "symmetric") {
        double m = std::max({std::abs(dmin), std::abs(dmax), 0.5 * min_span_}) * (1.0 + kPad);
        const double step = niceStep(2.0 * m, 4);
        m = std::ceil(m / step - 1e-9) * step;
        return {-m, m};
    }
    const double centre = 0.5 * (dmin + dmax);
    const double span = std::max(dmax - dmin, min_span_) * (1.0 + 2.0 * kPad);
    const double l = centre - 0.5 * span, h = centre + 0.5 * span;
    const double step = niceStep(h - l, 4);
    return {std::floor(l / step + 1e-9) * step, std::ceil(h / step - 1e-9) * step};
}

std::pair<double, double> StableScale::newTarget(double dmin, double dmax, double now) {
    auto [l, h] = target_;
    if (std::isnan(l)) return rangeFor(dmin, dmax);
    if (dmin < l || dmax > h) {  // data left the range: expand now
        const auto [nl, nh] = mode_ == "symmetric" ? rangeFor(std::min(dmin, l), std::max(dmax, h)) : rangeFor(dmin, dmax);
        small_since_.reset();
        return {std::min(nl, l), std::max(nh, h)};
    }
    const double used = mode_ == "auto" ? std::max(dmax - dmin, min_span_)
                                        : std::max(2.0 * std::max(std::abs(dmin), std::abs(dmax)), min_span_);
    if (used < kShrinkRatio * (h - l)) {
        if (!small_since_)
            small_since_ = now;
        else if (now - *small_since_ >= kShrinkDelayS) {
            small_since_.reset();
            return rangeFor(dmin, dmax);
        }
    } else {
        small_since_.reset();
    }
    return {l, h};
}

std::pair<double, double> StableScale::update(double dmin, double dmax, double now) {
    std::pair<double, double> target;
    if (mode_ == "fixed" && fixed_ && fixed_->second > fixed_->first)
        target = *fixed_;
    else if (!(std::isfinite(dmin) && std::isfinite(dmax)))
        target = std::isnan(target_.first) ? std::make_pair(-0.5 * min_span_, 0.5 * min_span_) : target_;
    else
        target = newTarget(dmin, dmax, now);
    if (std::isnan(lo)) {  // first range: no glide
        lo = target.first;
        hi = target.second;
        target_ = from_ = target;
        moving = true;
        return {lo, hi};
    }
    if (target != target_) {
        from_ = {lo, hi};
        target_ = target;
        ease_start_ = now;
    }
    if (lo == target_.first && hi == target_.second) {
        moving = false;
        return {lo, hi};
    }
    const double u = std::min(std::max((now - ease_start_) / kEaseS, 0.0), 1.0);
    const double s = u * u * (3.0 - 2.0 * u);
    if (u >= 1.0) {
        lo = target_.first;
        hi = target_.second;
    } else {
        lo = from_.first + (target_.first - from_.first) * s;
        hi = from_.second + (target_.second - from_.second) * s;
    }
    moving = true;
    return {lo, hi};
}

// ----------------------------------------------------------- line geometry
void Mesh::append(const Mesh& o) {
    const auto offset = static_cast<std::uint32_t>(vertices.size());
    vertices.insert(vertices.end(), o.vertices.begin(), o.vertices.end());
    for (auto i : o.indices) indices.push_back(i + offset);
}

namespace {
constexpr double kMiterLimit = 2.0;
const std::uint32_t kQuads[18] = {0, 1, 4, 1, 5, 4, 1, 2, 5, 2, 6, 5, 2, 3, 6, 3, 7, 6};

Rgba premultiplied(Rgba c) {
    return {static_cast<std::uint8_t>(c[0] * c[3] / 255), static_cast<std::uint8_t>(c[1] * c[3] / 255),
            static_cast<std::uint8_t>(c[2] * c[3] / 255), c[3]};
}
}  // namespace

Mesh stroke(const std::vector<double>& x, const std::vector<double>& y, double width, double feather, Rgba rgba,
            double dash, const std::vector<char>* breaks) {
    Mesh m;
    const std::size_t n = x.size();
    if (n == 0) return m;
    auto valid = [&](std::size_t i) {  // segment i → i+1
        if (breaks && (*breaks)[i + 1]) return false;
        return x[i + 1] != x[i] || y[i + 1] != y[i];
    };
    std::vector<double> nx(n > 1 ? n - 1 : 0), ny(nx.size());
    std::vector<char> ok(nx.size());
    for (std::size_t i = 0; i + 1 < n; ++i) {
        ok[i] = valid(i);
        const double dx = x[i + 1] - x[i], dy = y[i + 1] - y[i];
        const double len = ok[i] ? std::hypot(dx, dy) : 1.0;
        nx[i] = ok[i] ? -dy / len : 0.0;
        ny[i] = ok[i] ? dx / len : 0.0;
    }
    const double half = 0.5 * width;
    const double k[4] = {half + feather, half, -half, -(half + feather)};
    const Rgba core = premultiplied(rgba);
    m.vertices.resize(4 * n);
    for (std::size_t i = 0; i < n; ++i) {
        const bool bv = i > 0 && ok[i - 1], av = i + 1 < n && ok[i];
        const double bx = bv ? nx[i - 1] : 0.0, by = bv ? ny[i - 1] : 0.0;
        const double ax = av ? nx[i] : 0.0, ay = av ? ny[i] : 0.0;
        double sx = bx + ax, sy = by + ay;
        double norm = std::hypot(sx, sy);
        const bool both = bv && av;
        const bool flat = both && norm < 1e-6;  // a full reversal: use the segment after
        if (flat) {
            sx = ax;
            sy = ay;
            norm = 1.0;
        }
        double mx = 0.0, my = 1.0;
        if (norm > 1e-12) {
            mx = sx / norm;
            my = sy / norm;
        }
        double scale = 1.0;
        if (both && !flat) scale = 1.0 / std::max(std::abs(mx * ax + my * ay), 1.0 / kMiterLimit);
        for (int j = 0; j < 4; ++j) {
            Vertex& v = m.vertices[4 * i + j];
            v.x = static_cast<float>(x[i] + mx * scale * k[j]);
            v.y = static_cast<float>(y[i] + my * scale * k[j]);
            const Rgba c = (j == 1 || j == 2) ? core : Rgba{0, 0, 0, 0};
            v.r = c[0];
            v.g = c[1];
            v.b = c[2];
            v.a = c[3];
        }
    }
    for (std::size_t i = 0; i + 1 < n; ++i) {
        if (!ok[i]) continue;
        if (dash > 0 && static_cast<long>(std::floor(0.5 * (x[i] + x[i + 1]) / dash)) % 2 != 0) continue;
        for (auto q : kQuads) m.indices.push_back(static_cast<std::uint32_t>(4 * i) + q);
    }
    return m;
}

Mesh band(const std::vector<double>& x, const std::vector<double>& lo, const std::vector<double>& hi, Rgba rgba,
          double min_height, const std::vector<char>* breaks, const std::vector<double>* line) {
    Mesh m;
    const std::size_t n = x.size();
    if (n < 2) return m;
    const Rgba c = premultiplied(rgba);
    m.vertices.resize(2 * n);
    for (std::size_t i = 0; i < n; ++i) {
        m.vertices[2 * i] = {static_cast<float>(x[i]), static_cast<float>(hi[i]), c[0], c[1], c[2], c[3]};
        m.vertices[2 * i + 1] = {static_cast<float>(x[i]), static_cast<float>(lo[i]), c[0], c[1], c[2], c[3]};
    }
    std::vector<double> need(n, min_height);
    if (line) {  // as NumPy: need[:-1] += step, then need[1:] = max(need[1:], min_height + step)
        std::vector<double> step(n - 1);
        for (std::size_t i = 0; i + 1 < n; ++i) step[i] = std::abs((*line)[i + 1] - (*line)[i]);
        for (std::size_t i = 0; i + 1 < n; ++i) need[i] += step[i];
        for (std::size_t i = 0; i + 1 < n; ++i) need[i + 1] = std::max(need[i + 1], min_height + step[i]);
    }
    for (std::size_t i = 0; i + 1 < n; ++i) {
        const bool wide = std::abs(hi[i] - lo[i]) >= need[i] || std::abs(hi[i + 1] - lo[i + 1]) >= need[i + 1];
        if (!wide || (breaks && (*breaks)[i + 1])) continue;
        const auto b = static_cast<std::uint32_t>(2 * i);
        for (std::uint32_t q : {0u, 1u, 2u, 1u, 3u, 2u}) m.indices.push_back(b + q);
    }
    return m;
}

}  // namespace app::graph
