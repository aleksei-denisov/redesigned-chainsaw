#include "pendulum/simulation/tuning.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <numbers>
#include <thread>

#include "pendulum/simulation/engine.hpp"
#include "pendulum/simulation/rng.hpp"

namespace pendulum {

namespace {

constexpr double kInf = std::numeric_limits<double>::infinity();
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
const double kInitialTilt = 0.2 * (std::numbers::pi / 180.0);
constexpr double kKiRatioLo = -2.0, kKiRatioHi = 1.5;  // log10(Ki/Kp): integral time 30 s … 0.03 s
const double kHoldSeeds[] = {-1.0, -0.7, -0.5};
const char* kTerms[] = {"kp", "ki", "kd"};

// WEIGHT_BOUNDS (log10): cart position, angles, cart velocity, angular velocities, effort R.
const double kWeightLo[5] = {-2.0, -1.0, -3.0, -3.0, -4.0};
const double kWeightHi[5] = {3.0, 5.0, 3.0, 3.0, 1.0};

double radians(double d) { return d * (std::numbers::pi / 180.0); }

double nowSeconds() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

double& gain(PIDLoopConfig& l, const std::string& term) { return term == "kp" ? l.kp : term == "ki" ? l.ki : l.kd; }
double gain(const PIDLoopConfig& l, const std::string& term) {
    return term == "kp" ? l.kp : term == "ki" ? l.ki : l.kd;
}
bool& enabledFlag(PIDLoopConfig& l, const std::string& term) {
    return term == "kp" ? l.p_enabled : term == "ki" ? l.i_enabled : l.d_enabled;
}

LQRWeights weightsOf(const std::vector<double>& z) {
    return {std::pow(10.0, z[0]), std::pow(10.0, z[1]), std::pow(10.0, z[2]), std::pow(10.0, z[3]),
            std::pow(10.0, z[4])};
}

std::vector<double> defaultLogWeights() {
    const LQRWeights w;
    return {std::log10(w.cart_position), std::log10(w.angle), std::log10(w.cart_velocity),
            std::log10(w.angular_velocity), std::log10(w.effort)};
}

double maxAbsAngles(const Simulator& sim) {
    double m = 0.0;
    for (int i = 0; i < sim.model().n(); ++i) m = std::max(m, std::abs(sim.state()[1 + i]));
    return m;
}

double maxAbsRates(const Simulator& sim) {
    const int n = sim.model().n();
    double m = 0.0;
    for (int i = 0; i < n; ++i) m = std::max(m, std::abs(sim.state()[n + 2 + i]));
    return m;
}

}  // namespace

// --------------------------------------------------------------------- settings
TuningSettings TuningSettings::preset(const std::string& name) {
    TuningSettings s;
    if (name == "quick") {
        s.samples = 10;
        s.refine_rounds = 1;
        s.refine_samples = 6;
        s.resolution = 0.05;
    } else if (name == "thorough") {
        s.samples = 48;
        s.refine_rounds = 5;
        s.refine_samples = 10;
        s.resolution = 0.02;
    }
    return s;
}

TuneOptions TuneOptions::classic() {
    TuneOptions o;
    o.integral = false;
    return o;
}

// ------------------------------------------------------------------- the metric
AppConfig uprightConfig(const AppConfig& cfg) {
    AppConfig c = cfg;
    for (std::size_t k = 0; k < c.plant.segments.size(); ++k) {
        c.plant.segments[k].initial_angle = k == 0 ? kInitialTilt : 0.0;
        c.plant.segments[k].initial_velocity = 0.0;
    }
    c.simulation.telemetry_rate_hz = 20.0;
    return c;
}

PushTestResult pushTest(const AppConfig& cfg, double force, const TuningSettings& s, bool allow_early_stop,
                        const CancelToken* cancel) {
    Simulator sim(cfg, 0.5);
    sim.applyDisturbance(force, s.push_duration, s.push_time);
    const double dt = cfg.simulation.dt;
    const long steps = std::lround(std::nearbyint(s.horizon / dt));
    const double fall = radians(s.fall_deg);
    const long check_every = std::max(1L, static_cast<long>(0.01 / dt));
    const bool early = s.early_stop && allow_early_stop;
    const double calm_after = s.push_time + s.push_duration + 0.3;
    const double still = radians(s.still_deg);
    const double still_rate = radians(s.still_rate_dps);
    const int cart_index = sim.model().n() + 1;
    const int n = sim.model().n();
    double ise = 0.0;
    try {
        for (long k = 0; k < steps; ++k) {
            sim.step();
            if (k % check_every == 0) {
                double dot = 0.0;
                for (int i = 0; i < n; ++i) dot += sim.state()[1 + i] * sim.state()[1 + i];
                ise += dot * dt * static_cast<double>(check_every);
                const double ang = maxAbsAngles(sim);
                if (ang > fall) return {false, kInf};
                if (early && sim.time() >= calm_after && ang < still && maxAbsRates(sim) < still_rate &&
                    std::abs(sim.state()[cart_index]) < s.still_cart_mps)
                    return {true, ise};
                if (cancel && k % (10 * check_every) == 0 && cancel->cancelled()) throw TuningCancelled("cancelled");
            }
        }
    } catch (const SimulationDiverged&) {
        return {false, kInf};
    } catch (const std::domain_error&) {  // mass matrix lost definiteness: treat as a fall
        return {false, kInf};
    }
    return {maxAbsAngles(sim) < radians(s.settle_deg), ise};
}

double maxRecoverablePush(const AppConfig& cfg, const TuningSettings& s, double upper, int* counter,
                          const CancelToken* cancel) {
    const AppConfig c = uprightConfig(cfg);
    int local = 0;
    int& count = counter ? *counter : local;
    auto test = [&](double force) {
        ++count;
        return pushTest(c, force, s, true, cancel).recovered;
    };
    double lo = 0.0;
    std::optional<double> hi;
    double force = 0.01 * std::max(1.0, c.plant.actuator.max_output / 10.0);
    while (force <= upper) {
        if (!test(force)) {
            hi = force;
            break;
        }
        lo = force;
        force *= 2.0;
    }
    if (!hi) return lo;
    if (lo == 0.0 && !test(kMinPush)) return 0.0;
    lo = std::max(lo, kMinPush);
    double h = *hi;
    while (h - lo > s.resolution * h) {
        const double mid = 0.5 * (lo + h);
        if (test(mid))
            lo = mid;
        else
            h = mid;
    }
    return lo;
}

// ---------------------------------------------------------------------- options
const std::optional<std::pair<double, double>>& TermLimits::get(const std::string& term) const {
    return term == "kp" ? kp : term == "ki" ? ki : kd;
}

bool TermLimits::allows(const std::string& term, double value) const {
    const auto& lim = get(term);
    return !lim || (lim->first - 1e-12 <= value && value <= lim->second + 1e-12);
}

const TermLimits& GainLimits::forLoop(const std::string& signal) const {
    const auto it = per_loop.find(signal);
    return it == per_loop.end() ? general : it->second;
}

bool GainLimits::allows(const ControllerConfig& ctrl) const {
    for (const auto& lp : ctrl.loops)
        for (const char* t : kTerms)
            if (!forLoop(lp.signal).allows(t, gain(lp, t))) return false;
    return true;
}

TuneOptions optionsFromJson(const nlohmann::json& in, const std::vector<std::string>& signals) {
    using nlohmann::json;
    json d = {{"mode", "full"}, {"integral", true}, {"methods", false}, {"hold", false},
              {"loops", json::array()}, {"terms", {"kp", "ki", "kd"}}, {"off_terms", json::array()},
              {"limit_mode", "none"}, {"limits", {{"general", json::object()}, {"per_loop", json::object()}}}};
    if (in.is_object())
        for (const auto& [k, v] : in.items()) d[k] = v;
    auto contains = [](const json& arr, const std::string& s) {
        if (!arr.is_array()) return false;
        for (const auto& x : arr)
            if (x.is_string() && x.get<std::string>() == s) return true;
        return false;
    };
    auto pair = [](const json& v) -> std::optional<std::pair<double, double>> {
        if (!v.is_array() || v.size() != 2) return std::nullopt;
        const double lo = v[0].is_null() ? -kInf : v[0].get<double>();
        const double hi = v[1].is_null() ? kInf : v[1].get<double>();
        if (lo == -kInf && hi == kInf) return std::nullopt;
        return std::make_pair(std::min(lo, hi), std::max(lo, hi));
    };
    auto termLimits = [&](const json& v) {
        TermLimits t;
        if (!v.is_object()) return t;
        if (v.contains("kp")) t.kp = pair(v["kp"]);
        if (v.contains("ki")) t.ki = pair(v["ki"]);
        if (v.contains("kd")) t.kd = pair(v["kd"]);
        return t;
    };
    TuneOptions o;
    o.mode = d["mode"] == "loops" ? TuneMode::Loops : TuneMode::Full;
    o.loops.clear();
    if (d["loops"].is_array())
        for (const auto& s : d["loops"])
            if (s.is_string() && std::find(signals.begin(), signals.end(), s.get<std::string>()) != signals.end())
                o.loops.push_back(s.get<std::string>());
    o.off_terms.clear();
    o.terms.clear();
    for (const char* t : kTerms)
        if (contains(d["off_terms"], t)) o.off_terms.emplace_back(t);
    for (const char* t : kTerms)
        if (contains(d["terms"], t) && !contains(d["off_terms"], t)) o.terms.emplace_back(t);
    o.integral = d.value("integral", true);
    o.methods = d.value("methods", false);
    o.hold = d.value("hold", false);
    const std::string limit_mode = d.value("limit_mode", std::string("none"));
    if (limit_mode != "none") {
        const json lim = d["limits"].is_object() ? d["limits"] : json::object();
        o.limits.general = termLimits(lim.value("general", json::object()));
        if (limit_mode == "per_loop" && lim.contains("per_loop") && lim["per_loop"].is_object())
            for (const auto& [s, v] : lim["per_loop"].items())
                if (!v.is_null() && std::find(signals.begin(), signals.end(), s) != signals.end())
                    o.limits.per_loop[s] = termLimits(v);
    }
    return o;
}

// ------------------------------------------------------------------- work units
ControllerConfig fullController(const AppConfig& cfg, const LQRWeights& weights, const std::vector<double>* ki_ratios) {
    ControllerConfig ctrl = lqrController(cfg.plant, weights, &cfg.controller);
    const double limit = cfg.plant.actuator.max_output;
    ctrl.enabled = true;
    ctrl.output_min = -limit;
    ctrl.output_max = limit;
    for (std::size_t i = 0; i < ctrl.loops.size(); ++i) {
        auto& loop = ctrl.loops[i];
        loop.enabled = true;
        loop.p_enabled = loop.i_enabled = loop.d_enabled = true;
        const double r = ki_ratios && i < ki_ratios->size() ? (*ki_ratios)[i] : kOff;
        loop.ki = r <= kOff / 2 ? 0.0 : loop.kp * std::pow(10.0, r);
        loop.setpoint = 0.0;
        loop.integral_limit = 0.0;
        loop.anti_windup = AntiWindup::Clamp;
        loop.derivative_mode = DerivativeMode::MeasuredRate;
    }
    return ctrl;
}

std::string heldName(const AppConfig& cfg) { return cfg.plant.hasCart() ? "cart position" : "segment 1 angle"; }

double holdResidual(const AppConfig& cfg, double force, const TuningSettings& s, double seconds,
                    const CancelToken* cancel) {
    AppConfig c = cfg;
    for (auto& seg : c.plant.segments) seg.initial_angle = seg.initial_velocity = 0.0;
    c.simulation.telemetry_rate_hz = 20.0;
    Simulator sim(c, 0.5);
    sim.applyDisturbance(force, seconds + 1.0, s.push_time);
    const double dt = c.simulation.dt;
    const long steps = std::lround(std::nearbyint(seconds / dt));
    const long tail = std::lround(std::nearbyint(1.0 / dt));
    const double fall = radians(s.fall_deg);
    const bool cart = sim.model().hasCart();
    double peak = 0.0, tail_sum = 0.0;
    long tail_n = 0;
    try {
        for (long k = 0; k < steps; ++k) {
            sim.step();
            const double q = cart ? std::abs(sim.state()[0]) : std::abs(sim.state()[1]);
            peak = std::max(peak, q);
            if (k >= steps - tail) {
                tail_sum += q;
                ++tail_n;
            }
            if (k % 10 == 0) {
                if (maxAbsAngles(sim) > fall) return 1.0;
                if (cancel && k % 1000 == 0 && cancel->cancelled()) throw TuningCancelled("cancelled");
            }
        }
    } catch (const SimulationDiverged&) {
        return 1.0;
    } catch (const std::domain_error&) {
        return 1.0;
    }
    if (peak < 1e-9) return 0.0;
    return std::min(1.0, std::max(0.0, tail_sum / static_cast<double>(std::max(tail_n, 1L)) / peak));
}

double score(double push, double residual, bool hold) {
    if (!hold || push <= 0.0 || std::isnan(residual)) return push;
    return push * (1.0 - kHoldWeight * residual);
}

Evaluation evaluateController(const AppConfig& cfg, const TuningSettings& s, double min_push, bool hold,
                              const CancelToken* cancel) {
    Evaluation e;
    e.residual = hold ? 1.0 : kNaN;
    try {
        cfg.validate();
    } catch (const std::invalid_argument&) {
        return e;
    }
    if (s.prune && min_push > 0.0) {
        ++e.simulations;
        if (!pushTest(uprightConfig(cfg), min_push, s, true, cancel).recovered) return e;
    }
    const double push = maxRecoverablePush(cfg, s, 100.0, &e.simulations, cancel);
    if (push <= 0.0) return e;
    ++e.simulations;
    e.push = push;
    e.cost = pushTest(uprightConfig(cfg), 0.5 * push, s, false, cancel).ise;
    if (hold) {
        ++e.simulations;
        e.residual = holdResidual(cfg, kHoldForce * push, s, kHoldSeconds, cancel);
    }
    return e;
}

Evaluation evaluateStart(const AppConfig& cfg, const TuningSettings& s, bool hold, const CancelToken* cancel) {
    Evaluation e;
    e.cost = kNaN;
    e.push = maxRecoverablePush(cfg, s, 100.0, &e.simulations, cancel);
    if (hold) {
        ++e.simulations;
        e.residual = e.push > 0 ? holdResidual(cfg, kHoldForce * e.push, s, kHoldSeconds, cancel) : 1.0;
    }
    return e;
}

int budget(const TuningSettings& s, const TuneOptions& o) {
    int n = s.candidates();
    if (o.mode == TuneMode::Full && o.integral) {
        n += 1 + s.samples / 2 + s.refine_rounds * s.refine_samples / 2;
        if (o.hold) n += static_cast<int>(std::size(kHoldSeeds));
    }
    if (o.methods) n += 8;
    return n;
}

int defaultWorkers() {
    const int cores = static_cast<int>(std::thread::hardware_concurrency());
    return std::max(1, std::min(8, (cores > 0 ? cores : 2) - 1));
}

// ---------------------------------------------------------------- thread pool
namespace {

using Task = std::function<Evaluation()>;
using ResultFn = std::function<bool(std::size_t, const Evaluation&)>;

/// Runs tasks on `workers` threads; `on_result` (calling thread, completion order) returning
/// false cancels. Returns false if cancelled before every task finished.
bool runTasks(const std::vector<Task>& tasks, int workers, CancelToken& token, const ResultFn& on_result) {
    if (tasks.empty()) return true;
    if (workers <= 1 || tasks.size() == 1) {
        for (std::size_t i = 0; i < tasks.size(); ++i) {
            if (token.cancelled()) return false;
            Evaluation ev;
            try {
                ev = tasks[i]();
            } catch (const TuningCancelled&) {
                return false;
            }
            if (!on_result(i, ev)) {
                token.cancel();
                return false;
            }
        }
        return true;
    }
    std::atomic<std::size_t> next{0};
    std::mutex m;
    std::condition_variable cv;
    std::deque<std::pair<std::size_t, Evaluation>> finished;
    int running = std::min<int>(workers, static_cast<int>(tasks.size()));
    std::vector<std::thread> threads;
    for (int w = 0, count = running; w < count; ++w)
        threads.emplace_back([&] {
            for (;;) {
                const std::size_t i = next.fetch_add(1);
                if (i >= tasks.size() || token.cancelled()) break;
                Evaluation ev;
                try {
                    ev = tasks[i]();
                } catch (const TuningCancelled&) {
                    break;
                }
                std::lock_guard lock(m);
                finished.emplace_back(i, ev);
                cv.notify_one();
            }
            std::lock_guard lock(m);
            --running;
            cv.notify_one();
        });
    std::size_t received = 0;
    bool keep = true;
    while (received < tasks.size()) {
        std::unique_lock lock(m);
        cv.wait(lock, [&] { return !finished.empty() || running == 0; });
        if (finished.empty()) break;  // all workers stopped (cancelled)
        auto [i, ev] = finished.front();
        finished.pop_front();
        lock.unlock();
        ++received;
        if (!on_result(i, ev)) {
            keep = false;
            token.cancel();
            break;
        }
    }
    for (auto& t : threads) t.join();
    return keep && received == tasks.size();
}

// ------------------------------------------------------------ search spaces
struct Space {
    std::vector<double> z0, lo, hi;
    std::function<std::optional<ControllerConfig>(const std::vector<double>&)> build;
    double sigma = 1.0;
    std::function<std::optional<LQRWeights>(const std::vector<double>&)> weights = [](const std::vector<double>&) {
        return std::optional<LQRWeights>{};
    };
    std::vector<double> clip(std::vector<double> z) const {
        for (std::size_t i = 0; i < z.size(); ++i) z[i] = std::min(std::max(z[i], lo[i]), hi[i]);
        return z;
    }
};

std::optional<std::pair<double, double>> magnitudeBounds(double sign, const std::optional<std::pair<double, double>>& lim,
                                                         double start) {
    double lo = std::log10(start) - 3.0, hi = std::log10(start) + 3.0;
    if (lim) {
        const double a = sign > 0 ? lim->first : -lim->second;
        const double b = sign > 0 ? lim->second : -lim->first;
        if (b <= 0) return std::nullopt;  // this sign is not allowed at all
        if (a > 0) lo = std::max(lo, std::log10(std::max(a, 1e-9)));
        hi = std::min(hi, std::log10(b));
    }
    if (hi > lo) return std::make_pair(lo, hi);
    return std::make_pair(hi - 1e-6, hi);
}

Space loopsSpace(const AppConfig& cfg, const TuneOptions& o) {
    const ControllerConfig& base = cfg.controller;
    std::map<std::string, PIDLoopConfig> ref;
    try {
        for (const auto& lp : lqrController(cfg.plant, LQRWeights{}, &base).loops) ref[lp.signal] = lp;
    } catch (const std::exception&) {  // only a reference for zero gains
    }
    std::set<std::string> chosen(o.loops.begin(), o.loops.end());
    if (chosen.empty())
        for (const auto& lp : base.loops) chosen.insert(lp.signal);
    struct Dim {
        std::size_t loop;
        std::string term;
        double sign;
    };
    std::vector<Dim> dims;
    std::vector<std::size_t> off_loops;
    Space sp;
    auto in = [](const std::vector<std::string>& v, const std::string& s) {
        return std::find(v.begin(), v.end(), s) != v.end();
    };
    for (std::size_t i = 0; i < base.loops.size(); ++i) {
        const auto& lp = base.loops[i];
        if (!chosen.count(lp.signal) || !lp.enabled) continue;
        const TermLimits& lim = o.limits.forLoop(lp.signal);
        off_loops.push_back(i);
        for (const char* tc : kTerms) {
            const std::string term = tc;
            if (!in(o.terms, term) || in(o.off_terms, term)) continue;
            const double cur = gain(lp, term);
            const auto r_it = ref.find(lp.signal);
            double start, sign;
            if (term == "ki") {
                const double ref_kp = r_it != ref.end() ? r_it->second.kp : 1.0;
                double kp = std::abs(lp.kp);
                if (kp == 0.0) kp = std::abs(ref_kp);
                if (kp == 0.0) kp = 1.0;
                start = cur != 0.0 ? std::abs(cur) : 0.1 * kp;
                sign = cur != 0.0 ? std::copysign(1.0, cur) : std::copysign(1.0, lp.kp != 0.0 ? lp.kp : ref_kp);
            } else {
                const double r = r_it != ref.end() ? gain(r_it->second, term) : 0.0;
                start = cur != 0.0 ? std::abs(cur) : (std::abs(r) != 0.0 ? std::abs(r) : 1.0);
                sign = cur != 0.0 ? std::copysign(1.0, cur) : std::copysign(1.0, r != 0.0 ? r : 1.0);
            }
            const auto b = magnitudeBounds(sign, lim.get(term), start);
            if (!b) continue;
            dims.push_back({i, term, sign});
            sp.z0.push_back(std::min(std::max(std::log10(start), b->first), b->second));
            sp.lo.push_back(b->first);
            sp.hi.push_back(b->second);
        }
    }
    if (dims.empty() && !(!off_loops.empty() && !o.off_terms.empty()))
        throw std::invalid_argument("nothing to tune: choose at least one loop and one gain (and check the gain limits)");
    const auto off_terms = o.off_terms;
    const GainLimits limits = o.limits;
    sp.build = [base, dims, off_loops, off_terms, limits](const std::vector<double>& z) -> std::optional<ControllerConfig> {
        ControllerConfig ctrl = base;
        for (std::size_t i : off_loops)
            for (const auto& t : off_terms) {
                gain(ctrl.loops[i], t) = 0.0;
                enabledFlag(ctrl.loops[i], t) = false;
            }
        for (std::size_t k = 0; k < dims.size(); ++k) {
            gain(ctrl.loops[dims[k].loop], dims[k].term) = dims[k].sign * std::pow(10.0, z[k]);
            enabledFlag(ctrl.loops[dims[k].loop], dims[k].term) = true;
        }
        if (!limits.allows(ctrl)) return std::nullopt;
        return ctrl;
    };
    sp.sigma = 0.5;
    return sp;
}

Space weightsSpace(const AppConfig& cfg, const TuneOptions& o) {
    Space sp;
    sp.z0 = defaultLogWeights();
    sp.lo.assign(std::begin(kWeightLo), std::end(kWeightLo));
    sp.hi.assign(std::begin(kWeightHi), std::end(kWeightHi));
    const GainLimits limits = o.limits;
    sp.build = [cfg, limits](const std::vector<double>& z) -> std::optional<ControllerConfig> {
        try {
            ControllerConfig c = fullController(cfg, weightsOf(z));
            if (!limits.allows(c)) return std::nullopt;
            return c;
        } catch (const std::exception&) {
            return std::nullopt;
        }
    };
    sp.sigma = 1.2;
    sp.weights = [](const std::vector<double>& z) { return std::optional<LQRWeights>(weightsOf(z)); };
    return sp;
}

Space integralSpace(const AppConfig& cfg, const TuneOptions& o, const LQRWeights& w) {
    const std::size_t n = fullController(cfg, w).loops.size();
    Space sp;
    sp.z0.assign(n, kKiRatioLo - 0.25);
    sp.lo.assign(n, kKiRatioLo - 0.5);
    sp.hi.assign(n, kKiRatioHi);
    const GainLimits limits = o.limits;
    sp.build = [cfg, limits, w](const std::vector<double>& z) -> std::optional<ControllerConfig> {
        std::vector<double> ratios;
        for (double v : z) ratios.push_back(v < kKiRatioLo + 0.5 ? kOff : v);
        try {
            ControllerConfig c = fullController(cfg, w, &ratios);
            if (!limits.allows(c)) return std::nullopt;
            return c;
        } catch (const std::exception&) {
            return std::nullopt;
        }
    };
    sp.sigma = 1.0;
    sp.weights = [w](const std::vector<double>&) { return std::optional<LQRWeights>(w); };
    return sp;
}

std::vector<ControllerConfig> methodVariants(const ControllerConfig& ctrl, const std::set<std::string>& signals) {
    std::vector<ControllerConfig> out;
    for (std::size_t i = 0; i < ctrl.loops.size(); ++i) {
        const auto& lp = ctrl.loops[i];
        if (!signals.count(lp.signal)) continue;
        for (auto mode : {DerivativeMode::MeasuredRate, DerivativeMode::FilteredDifference})
            if (mode != lp.derivative_mode) {
                auto c = ctrl;
                c.loops[i].derivative_mode = mode;
                out.push_back(c);
            }
        if (lp.ki != 0.0)
            for (auto aw : {AntiWindup::None, AntiWindup::Clamp, AntiWindup::BackCalculation})
                if (aw != lp.anti_windup) {
                    auto c = ctrl;
                    c.loops[i].anti_windup = aw;
                    out.push_back(c);
                }
    }
    return out;
}

struct Best {
    ControllerConfig controller;
    double push = 0.0, cost = kInf;
    std::optional<LQRWeights> weights;
    std::optional<std::vector<double>> z;
    double residual = kNaN;
    double score = 0.0;
    bool is_start = false;  ///< the controller before tuning
    /// `value`: the challenger's score (= its push without the hold test).
    bool beatenBy(double value, double cost_, double tol) const {
        if (value > score * (1.0 + tol)) return true;
        return value >= score * (1.0 - tol) && cost_ < cost;
    }
};

struct Candidate {
    std::optional<ControllerConfig> controller;  ///< nullopt: outside the limits (counted, not run)
    std::optional<LQRWeights> weights;
    std::optional<std::vector<double>> z;
    bool is_start = false;
};

}  // namespace

// ----------------------------------------------------------------------- tune
TuningResult tune(const AppConfig& config, const TuningSettings& s, const TuneOptions& o, const TuneUpdate& on_update,
                  CancelToken* cancel_in) {
    AppConfig cfg = config;
    cfg.validate();
    const double started = nowSeconds();
    Rng rng(s.seed);
    const int total = 1 + budget(s, o);
    CancelToken own_token;
    CancelToken& token = cancel_in ? *cancel_in : own_token;
    const int workers = s.workers > 0 ? s.workers : defaultWorkers();

    TuneProgress state;
    state.total = total;
    state.workers = workers;
    state.candidates_total = total - 1;
    state.message = "Starting…";
    std::optional<Best> best;    // overall best (what the result returns)
    std::optional<Best> center;  // best point of the current search space (refinement centre)
    std::vector<double> history;
    int evaluations = 0;
    double start_residual = kNaN;

    auto emit = [&](const std::string& phase, const std::string& message) {
        state.phase = phase;
        state.message = message;
        state.elapsed = nowSeconds() - started;
        state.eta = state.done ? std::optional<double>(state.elapsed / state.done * (total - state.done)) : std::nullopt;
        bool keep = !token.cancelled();
        if (on_update && !on_update(state)) keep = false;
        if (!keep) token.cancel();
        return keep;
    };

    // Evaluate in input order: the outcome does not depend on the number of workers.
    auto run = [&](const std::vector<Candidate>& cands, const std::string& phase, const std::string& label,
                   bool with_baseline) -> bool {
        const double threshold = (best && s.prune) ? best->score * (1.0 - 2.0 * s.resolution) : 0.0;
        std::vector<Task> tasks;
        std::vector<std::ptrdiff_t> task_cand;  // −1: baseline task
        if (with_baseline) {
            tasks.push_back([&cfg, &s, &o, &token] { return evaluateStart(cfg, s, o.hold, &token); });
            task_cand.push_back(-1);
        }
        for (std::size_t k = 0; k < cands.size(); ++k) {
            if (!cands[k].controller) continue;
            AppConfig c = cfg;
            c.controller = *cands[k].controller;
            const double min_push = cands[k].is_start ? 0.0 : threshold;
            tasks.push_back([c, &s, &o, &token, min_push] { return evaluateController(c, s, min_push, o.hold, &token); });
            task_cand.push_back(static_cast<std::ptrdiff_t>(k));
        }
        std::map<std::size_t, Evaluation> results;
        for (std::size_t k = 0; k < cands.size(); ++k)
            if (!cands[k].controller) results[k] = Evaluation{};  // outside the limits: counted, not run
        std::size_t next_k = 0;
        auto drain = [&] {
            while (results.count(next_k)) {
                const Evaluation ev = results[next_k];
                results.erase(next_k);
                const Candidate& c = cands[next_k];
                ++evaluations;
                const double value = score(ev.push, ev.residual, o.hold);
                if (c.is_start) {
                    state.baseline_push = ev.push;
                    start_residual = ev.residual;
                }
                if (c.controller && ev.push > 0) {
                    Best b{*c.controller, ev.push, ev.cost, c.weights, c.z, ev.residual, value, c.is_start};
                    if (!best || best->beatenBy(value, ev.cost, s.resolution)) {
                        best = b;
                        state.best_push = ev.push;
                        state.best_cost = ev.cost;
                        state.best_controller = b.controller;
                        state.best_weights = b.weights;
                    }
                    if (c.z && (!center || center->beatenBy(value, ev.cost, s.resolution))) center = b;
                }
                history.push_back(best ? best->push : 0.0);
                state.history = history;
                state.candidate_pushes.push_back(ev.push);
                ++next_k;
            }
        };
        const auto rejected = static_cast<int>(results.size());
        state.done += rejected;
        state.candidates_done += rejected;
        drain();
        const bool ok = runTasks(tasks, workers, token, [&](std::size_t ti, const Evaluation& ev) {
            ++state.done;
            state.simulations += ev.simulations;
            if (task_cand[ti] < 0) {
                state.baseline_push = ev.push;
                start_residual = ev.residual;
            } else {
                ++state.candidates_done;
                results[static_cast<std::size_t>(task_cand[ti])] = ev;
            }
            drain();
            const std::string msg = state.candidates_done
                                        ? label + ": tested " + std::to_string(state.candidates_done) + " / " +
                                              std::to_string(total - 1) + " candidate controllers"
                                        : "Measuring the current controller…";
            return emit(phase, msg);
        });
        drain();
        return ok;
    };

    auto sample = [&](const Space& sp, const std::vector<double>& around, double sigma) {
        std::vector<double> z = around;
        for (double& v : z) v += rng.normal(0.0, sigma);
        return sp.clip(z);
    };
    auto candidate = [](const Space& sp, const std::vector<double>& z) {
        return Candidate{sp.build(z), sp.weights(z), z, false};
    };

    auto search = [&](const Space& sp, int samples, int rounds, int per_round, const std::string& phase,
                      const std::string& label, bool with_baseline, bool include_start,
                      const std::vector<std::vector<double>>& seeds, std::vector<Candidate> extra_first) -> bool {
        if (sp.z0.empty()) samples = rounds = 0;  // nothing searched (only gains switched off)
        std::vector<Candidate> cands = std::move(extra_first);
        if (include_start) cands.push_back(candidate(sp, sp.z0));
        for (const auto& z : seeds) cands.push_back(candidate(sp, z));
        for (int i = 0; i < samples; ++i) cands.push_back(candidate(sp, sample(sp, sp.z0, sp.sigma)));
        if (!run(cands, phase, label + " – exploring", with_baseline)) return false;
        double sigma = 0.5 * sp.sigma;
        for (int r = 0; r < rounds; ++r) {
            if (!center || !center->z || center->z->size() != sp.z0.size()) return true;
            std::vector<Candidate> batch;
            for (int i = 0; i < per_round; ++i) batch.push_back(candidate(sp, sample(sp, *center->z, sigma)));
            if (!run(batch, phase, label + " – refining " + std::to_string(r + 1) + "/" + std::to_string(rounds), false))
                return false;
            sigma *= 0.6;
        }
        return true;
    };

    bool cancelled = false;
    try {
        emit("baseline", "Measuring the current controller on " + std::to_string(workers) +
                             (workers == 1 ? " core…" : " cores…"));
        bool ok;
        if (o.mode == TuneMode::Loops) {
            const Space sp = loopsSpace(cfg, o);
            ok = search(sp, s.samples, s.refine_rounds, s.refine_samples, "explore", "Gains", true, true, {}, {});
        } else {
            // The controller before tuning competes (if within the limits): never a worse result.
            std::vector<Candidate> first;
            const bool start_allowed = o.limits.allows(cfg.controller);
            if (start_allowed) first.push_back(Candidate{cfg.controller, std::nullopt, std::nullopt, true});
            const Space ws = weightsSpace(cfg, o);
            center.reset();
            ok = search(ws, s.samples, s.refine_rounds, s.refine_samples, "explore", "LQR design", !start_allowed, true,
                        {}, std::move(first));
            const std::optional<Best> design = center;  // best LQR design
            if (ok && o.integral && design && design->weights) {
                const Space is = integralSpace(cfg, o, *design->weights);
                center = design;
                center->z = is.z0;  // the PD design is the integral search's start
                std::vector<std::vector<double>> seeds;
                if (o.hold)
                    for (double r : kHoldSeeds) {
                        auto z = is.z0;
                        z[0] = r;
                        seeds.push_back(is.clip(z));
                    }
                ok = search(is, s.samples / 2, s.refine_rounds, s.refine_samples / 2, "refine", "Integral gains", false,
                            true, seeds, {});
            }
        }
        if (ok && o.methods && best) {
            std::set<std::string> signals;
            if (o.mode == TuneMode::Loops && !o.loops.empty())
                signals.insert(o.loops.begin(), o.loops.end());
            else
                for (const auto& lp : best->controller.loops) signals.insert(lp.signal);
            auto variants = methodVariants(best->controller, signals);
            if (variants.size() > 8) variants.resize(8);
            std::vector<Candidate> cands;
            for (auto& v : variants) {
                const bool allowed = o.limits.allows(v);
                cands.push_back(Candidate{allowed ? std::optional<ControllerConfig>(v) : std::nullopt, best->weights,
                                          std::nullopt, false});
            }
            ok = run(cands, "refine", "Methods", false);
        }
        cancelled = !ok;
    } catch (const TuningCancelled&) {
        cancelled = true;
    }

    if (!best) {
        if (cancelled || token.cancelled()) throw TuningCancelled("tuning was cancelled before a candidate was evaluated");
        throw std::invalid_argument(
            "no controller within the gain limits recovers from a push – widen the limits, or tune other loops or gains");
    }
    state.phase = "done";
    state.message = cancelled ? "Cancelled – best result so far" : "Done";
    state.elapsed = nowSeconds() - started;
    state.eta = 0.0;
    if (on_update) on_update(state);
    double baseline = state.baseline_push ? *state.baseline_push : maxRecoverablePush(cfg, s);

    TuningResult r;
    r.controller = best->controller;
    r.weights = best->weights.value_or(LQRWeights{});
    r.max_push = best->push;
    r.baseline_max_push = baseline;
    r.recommended_push = roundNice(0.5 * best->push);
    r.evaluations = evaluations;
    r.elapsed = nowSeconds() - started;
    r.cancelled = cancelled;
    r.unchanged = best->is_start;
    r.history = history;
    r.hold = o.hold;
    r.hold_residual = best->residual;
    r.baseline_hold_residual = start_residual;
    r.held = heldName(cfg);
    return r;
}

// ------------------------------------------------------------------- helpers
double estimateSeconds(const TuningSettings& s, double push_test_s, int workers) {
    const double full = 16 * 0.45 * push_test_s;
    const double explore = (2 + s.samples) * full;
    const int refine_n = s.refine_rounds * s.refine_samples;
    const double refine = s.prune ? refine_n * (0.4 * full + 0.6 * push_test_s * 0.6) : refine_n * full;
    const double speedup = workers > 1 ? std::max(1.0, 0.8 * workers) : 1.0;
    return (explore + refine) / speedup;
}

double calibratePushTestSeconds(const AppConfig& config, const TuningSettings& s) {
    const AppConfig cfg = uprightConfig(config);
    const double t0 = nowSeconds();
    pushTest(cfg, 0.0, s, false);
    return nowSeconds() - t0;
}

std::pair<ExperimentResult, ExperimentResult> compareResponses(const AppConfig& config, const ControllerConfig& old_c,
                                                               const ControllerConfig& new_c, double push,
                                                               const TuningSettings& s, double duration) {
    auto one = [&](const char* name, const ControllerConfig& ctrl) {
        AppConfig cfg = uprightConfig(config);
        cfg.controller = ctrl;
        cfg.simulation.telemetry_rate_hz = 100.0;
        cfg.experiment.duration = duration;
        cfg.experiment.disturbance = {true, 1.0, push, s.push_duration};
        return runExperiment(cfg, name);
    };
    return {one("before tuning", old_c), one("after tuning", new_c)};
}

double roundNice(double value) {
    if (value <= 0) return 0.0;
    const int exponent = static_cast<int>(std::floor(std::log10(value))) - 1;
    const double scale = std::pow(10.0, exponent);
    const double v = std::floor(value / scale) * scale;
    const int digits = std::max(0, -exponent);
    const double p = std::pow(10.0, digits);
    return std::round(v * p) / p;
}

}  // namespace pendulum
