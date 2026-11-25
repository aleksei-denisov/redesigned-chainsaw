#include "pendulum/simulation/experiment.hpp"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <limits>
#include <numbers>
#include <sstream>
#include <stdexcept>

#include <nlohmann/json.hpp>

#include "pendulum/control/pid.hpp"
#include "pendulum/io/config_json.hpp"
#include "pendulum/simulation/engine.hpp"

namespace pendulum {

namespace {
double degrees(double r) { return r * (180.0 / std::numbers::pi); }
double unixNow() {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
}
std::string fmtG(double v, const char* spec = "%g") {
    char buf[64];
    std::snprintf(buf, sizeof buf, spec, v);
    return buf;
}
}  // namespace

std::vector<double> angleError(const AppConfig& config, const std::map<std::string, std::vector<double>>& data,
                               int segment) {
    double setpoint = 0.0;
    const std::string sig = "theta" + std::to_string(segment + 1);
    for (const auto& l : config.controller.loops)
        if (l.signal == sig && l.enabled) setpoint = l.setpoint;
    std::vector<double> out;
    const auto& theta = data.at(sig);
    out.reserve(theta.size());
    for (double v : theta) out.push_back(wrapAngle(v - setpoint));
    return out;
}

std::vector<double> ExperimentResult::angleDeg(int segment) const {
    std::vector<double> out;
    for (double v : data.at("theta" + std::to_string(segment + 1))) out.push_back(degrees(v));
    return out;
}

std::vector<double> ExperimentResult::errorDeg(int segment) const {
    auto e = angleError(config, data, segment);
    for (double& v : e) v = degrees(v);
    return e;
}

std::string ExperimentResult::gainsSummary() const {
    if (!config.controller.enabled) return "controller off";
    std::string out;
    for (const auto& l : config.controller.loops) {
        if (!l.enabled) continue;
        if (!out.empty()) out += "; ";
        out += (l.signal == "cart" ? std::string("x") : "θ" + l.signal.substr(5)) + ": P=" + fmtG(l.kp) +
               " I=" + fmtG(l.ki) + " D=" + fmtG(l.kd);
    }
    return out;
}

ExperimentResult runExperiment(const AppConfig& config, const std::string& name, const ExperimentProgress& progress) {
    AppConfig cfg = config;
    cfg.validate();
    Simulator sim(cfg, std::nullopt);
    const auto& dist = cfg.experiment.disturbance;
    if (dist.enabled) sim.applyDisturbance(dist.force, dist.duration, dist.time);
    const double dt = cfg.simulation.dt;
    const long chunk = std::max(1L, std::lround(std::nearbyint(0.1 / dt)));
    const long total = std::lround(std::nearbyint(cfg.experiment.duration / dt));
    ExperimentResult r;
    r.name = name;
    r.config = cfg;
    r.created = unixNow();
    try {
        long done = 0;
        while (done < total) {
            const long n = std::min(chunk, total - done);
            for (long i = 0; i < n; ++i) sim.step();
            done += n;
            if (progress && !progress(static_cast<double>(done) / total)) {
                r.message = "cancelled";
                break;
            }
        }
    } catch (const SimulationDiverged& e) {
        r.diverged = true;
        r.message = e.what();
    }
    const auto& names = columnNames();
    for (int c = 0; c < kColumnCount; ++c) r.data[names[c]] = sim.telemetry().column(c);
    std::vector<std::vector<double>> errors;
    for (int i = 0; i < cfg.plant.nSegments(); ++i) {
        auto e = angleError(cfg, r.data, i);
        for (double& v : e) v = degrees(v);
        errors.push_back(std::move(e));
    }
    r.metrics = runMetrics(r.data.at("t"), errors, r.data.at("actuator"), r.data.at("x"),
                           cfg.experiment.settle_tolerance_deg, r.diverged);
    r.metrics.saturation_pct = 100.0 * sim.saturationFraction();
    return r;
}

void exportCsv(const ExperimentResult& result, const std::string& path) {
    std::vector<std::string> keys = {"t", "x", "xd"};
    for (int i = 0; i < result.nSegments(); ++i) {
        keys.push_back("theta" + std::to_string(i + 1));
        keys.push_back("omega" + std::to_string(i + 1));
    }
    for (const char* k : {"command", "actuator", "tip_force", "energy"}) keys.emplace_back(k);
    std::ofstream out(path);
    if (!out) throw std::invalid_argument("cannot write " + path);
    auto angular = [](const std::string& k) { return k.rfind("theta", 0) == 0 || k.rfind("omega", 0) == 0; };
    for (std::size_t i = 0; i < keys.size(); ++i) {
        const auto& k = keys[i];
        out << (i ? "," : "") << k
            << (k.rfind("theta", 0) == 0 ? "_deg" : k.rfind("omega", 0) == 0 ? "_dps" : "");
    }
    out << "\r\n";  // csv.writer's line terminator
    const std::size_t rows = result.data.at("t").size();
    for (std::size_t r = 0; r < rows; ++r) {
        for (std::size_t i = 0; i < keys.size(); ++i) {
            double v = result.data.at(keys[i])[r];
            if (angular(keys[i])) v = degrees(v);
            out << (i ? "," : "") << (std::isnan(v) ? std::string("nan") : fmtG(v, "%.6g"));
        }
        out << "\r\n";
    }
}

void saveExperiments(const std::vector<ExperimentResult>& results, const std::string& path) {
    using nlohmann::json;
    json list = json::array();
    for (const auto& r : results) {
        json data = json::object();
        for (const auto& [k, v] : r.data) {
            bool all_nan = true;
            json arr = json::array();
            for (double x : v) {
                if (std::isnan(x)) {
                    arr.push_back(nullptr);
                } else {
                    arr.push_back(x);
                    all_nan = false;
                }
            }
            if (!all_nan) data[k] = std::move(arr);
        }
        list.push_back({{"name", r.name},
                        {"created", r.created},
                        {"diverged", r.diverged},
                        {"message", r.message},
                        {"config", io::toJson(r.config)},
                        {"metrics", io::toJson(r.metrics)},
                        {"data", std::move(data)}});
    }
    std::ofstream out(path);
    if (!out) throw std::invalid_argument("cannot write " + path);
    out << json{{"version", 1}, {"experiments", list}}.dump();
}

std::vector<ExperimentResult> loadExperiments(const std::string& path) {
    using nlohmann::json;
    std::ifstream in(path);
    if (!in) throw std::invalid_argument("cannot open " + path);
    try {
        const json payload = json::parse(in);
        std::vector<ExperimentResult> out;
        for (const auto& e : payload.at("experiments")) {
            ExperimentResult r;
            r.config = io::appConfigFromJson(e.at("config"));
            for (const auto& [k, v] : e.at("data").items()) {
                std::vector<double> col;
                col.reserve(v.size());
                for (const auto& x : v) col.push_back(x.is_null() ? std::numeric_limits<double>::quiet_NaN() : x.get<double>());
                r.data[k] = std::move(col);
            }
            const std::size_t length = r.data.at("t").size();
            for (const auto& name : columnNames())
                if (!r.data.count(name)) r.data[name] = std::vector<double>(length, std::numeric_limits<double>::quiet_NaN());
            r.metrics = io::runMetricsFromJson(e.at("metrics"));
            r.name = e.at("name").get<std::string>();
            r.diverged = e.value("diverged", false);
            r.message = e.value("message", std::string());
            r.created = e.value("created", unixNow());
            out.push_back(std::move(r));
        }
        return out;
    } catch (const json::exception& ex) {
        throw std::invalid_argument(std::string("not a valid experiment file: ") + ex.what());
    }
}

}  // namespace pendulum
