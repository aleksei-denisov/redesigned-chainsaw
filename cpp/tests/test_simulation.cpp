// Phase 3: simulation engine, scenarios and experiments against the Python reference data.
#include <gtest/gtest.h>

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <map>

#include "pendulum/io/config_json.hpp"
#include "pendulum/simulation/engine.hpp"
#include "pendulum/simulation/experiment.hpp"
#include "pendulum/simulation/realtime.hpp"
#include "pendulum/simulation/rng.hpp"
#include "pendulum/simulation/scenarios.hpp"
#include "reference.hpp"

using namespace pendulum;
using reference::json;

namespace {

/// Compares two JSON trees: numbers within `rel`, everything else exactly.
::testing::AssertionResult sameJson(const json& a, const json& b, double rel, const std::string& path = "") {
    if (b.is_object()) {
        for (const auto& [k, v] : b.items()) {
            if (!a.contains(k)) return ::testing::AssertionFailure() << path << "." << k << " missing";
            auto r = sameJson(a.at(k), v, rel, path + "." + k);
            if (!r) return r;
        }
        return ::testing::AssertionSuccess();
    }
    if (b.is_array()) {
        if (a.size() != b.size()) return ::testing::AssertionFailure() << path << ": size";
        for (std::size_t i = 0; i < b.size(); ++i) {
            auto r = sameJson(a[i], b[i], rel, path + "[" + std::to_string(i) + "]");
            if (!r) return r;
        }
        return ::testing::AssertionSuccess();
    }
    if (b.is_number() && a.is_number()) {
        const double x = a.get<double>(), y = b.get<double>();
        if (std::abs(x - y) <= rel * std::abs(y) + 1e-12) return ::testing::AssertionSuccess();
        return ::testing::AssertionFailure() << path << ": " << std::setprecision(17) << x << " vs " << y;
    }
    if (a == b) return ::testing::AssertionSuccess();
    return ::testing::AssertionFailure() << path << ": " << a.dump() << " vs " << b.dump();
}

TEST(Scenarios, MatchThePythonScenarios) {
    const auto data = reference::load("experiment");
    std::vector<std::string> keys;
    for (const auto& c : data.at("cases")) {
        const std::string key = c.at("scenario");
        keys.push_back(key);
        // LQR gains come from LAPACK on the Python side: equal to ~1e-12.
        EXPECT_TRUE(sameJson(io::toJson(scenario(key)), c.at("config"), 1e-9)) << key;
    }
    EXPECT_EQ(keys, scenarioKeys());
}

class SimulationReference : public ::testing::Test {
protected:
    static void SetUpTestSuite() { data = new json(reference::load("simulation")); }
    static void TearDownTestSuite() { delete data; }
    static inline json* data = nullptr;
};

TEST_F(SimulationReference, TrajectoriesAndTelemetryMatch) {
    const reference::Tolerance tol(*data);
    std::map<std::string, double> sensitive_from;
    for (const auto& c : data->at("cases"))
        if (c.at("case") == "plain" && !c.at("sensitive_from_s").is_null())
            sensitive_from[c.at("scenario")] = c.at("sensitive_from_s").get<double>();
    const auto& names = columnNames();
    int compared_rows = 0;
    for (const auto& c : data->at("cases")) {
        const std::string key = c.at("scenario");
        SCOPED_TRACE(key + " / " + c.at("case").get<std::string>());
        const AppConfig cfg = io::appConfigFromJson(c.at("config"));
        Simulator sim(cfg, std::nullopt);
        if (c.contains("push")) {
            const auto& p = c.at("push");
            sim.applyDisturbance(p.at("force"), p.at("duration"), p.at("start").get<double>(), p.at("segment"),
                                 p.at("ratio"));
        }
        // Rounding-sensitive runs: compare only before the time a 1e-11 change shows up.
        const double until = sensitive_from.count(key) ? sensitive_from[key] - 0.05 : 1e9;
        const auto& states = c.at("states");
        const int every = static_cast<int>(std::lround(0.05 / cfg.simulation.dt));
        std::size_t row = 0;
        for (long step = 0; step < std::lround(4.0 / cfg.simulation.dt) && row < states.size(); ++step) {
            sim.step();
            if ((step + 1) % every) continue;
            if (sim.time() <= until) {
                const Eigen::VectorXd expected = reference::vec(states[row]);
                ASSERT_TRUE(reference::allClose(Eigen::VectorXd(sim.state()), expected, tol)) << "t = " << sim.time();
                ++compared_rows;
            }
            ++row;
        }
        // Telemetry: every 10th recorded row (20 Hz), compared by column name.
        const auto& tnames = c.at("telemetry_columns");
        const auto& trows = c.at("telemetry");
        for (std::size_t r = 0; r < trows.size(); ++r) {
            const auto& got = sim.telemetry().row(10 * r);
            if (got[kT] > until) break;
            for (std::size_t k = 0; k < tnames.size(); ++k) {
                const auto idx = columnIndex(tnames[k].get<std::string>());
                ASSERT_TRUE(idx.has_value()) << tnames[k];
                ASSERT_TRUE(tol.close(got[*idx], reference::number(trows[r][k])))
                    << names[*idx] << " at row " << 10 * r << ": " << got[*idx] << " vs " << trows[r][k];
            }
        }
    }
    EXPECT_GT(compared_rows, 1000);
}

TEST(ExperimentReference, MetricsMatch) {
    const auto data = reference::load("experiment");
    const reference::Tolerance tol(data);
    int compared = 0;
    for (const auto& c : data.at("cases")) {
        const std::string key = c.at("scenario");
        SCOPED_TRACE(key);
        const auto r = runExperiment(io::appConfigFromJson(c.at("config")), key);
        if (c.at("sensitive").get<bool>()) continue;  // chaotic long run: rounding decides
        json got = io::toJson(r.metrics);
        got.erase("saturation_pct");
        EXPECT_TRUE(sameJson(got, c.at("metrics"), tol.rel));
        EXPECT_EQ(r.diverged, c.at("diverged").get<bool>());
        ++compared;
    }
    EXPECT_EQ(compared, 7);
}

TEST(Simulation, SaturationIsReported) {
    // double_fixed_lqr drives its 2 N·m motor into the limit (improvement #2).
    const auto sat = runExperiment(scenario("double_fixed_lqr")).metrics.saturation_pct;
    const auto calm = runExperiment(scenario("single_cart_full")).metrics.saturation_pct;
    EXPECT_GT(sat, 5.0);
    EXPECT_LT(calm, sat);
}

TEST(Simulation, NoiseIsReproducibleAndSeeded) {
    AppConfig c = scenario("single_cart_full");
    c.simulation.sensor_noise_angle_deg = 0.2;
    Simulator a(c), b(c);
    a.advance(1.0);
    b.advance(1.0);
    EXPECT_EQ(a.state(), b.state());
    c.simulation.noise_seed = 2;
    Simulator d(c);
    d.advance(1.0);
    EXPECT_NE(a.state(), d.state());
}

TEST(Simulation, RngStatistics) {
    Rng r(42);
    double sum = 0, sq = 0;
    const int n = 200000;
    for (int i = 0; i < n; ++i) {
        const double x = r.normal();
        sum += x;
        sq += x * x;
    }
    EXPECT_NEAR(sum / n, 0.0, 0.01);
    EXPECT_NEAR(sq / n, 1.0, 0.01);
    Rng a(7), b(7);
    for (int i = 0; i < 10; ++i) EXPECT_EQ(a.next(), b.next());
}

TEST(Simulation, DivergenceIsDetected) {
    Simulator sim(scenario("single_cart_full"));
    Vec s = sim.state();
    s[1] = std::numeric_limits<double>::quiet_NaN();
    sim.setState(s);
    EXPECT_THROW(sim.step(), SimulationDiverged);
}

TEST(Simulation, TelemetryWindowKeepsRecentSamples) {
    AppConfig c = scenario("single_cart_full");
    Simulator sim(c, 1.0);  // 200 samples
    sim.advance(5.0);
    EXPECT_LE(sim.telemetry().size(), 400u);
    EXPECT_GE(sim.telemetry().size(), 200u);
    EXPECT_NEAR(sim.telemetry().rows().back()[kT], 5.0, 1e-9);
}

TEST(Simulation, RealtimeStepperPacesAndDropsBacklog) {
    double now = 0.0;
    RealtimeStepper rt(0.010, 0.1, [&] { return now; });
    Simulator sim(scenario("single_cart_full"));
    EXPECT_EQ(rt.tick(sim, 0.016), 16);  // 16 ms at dt = 1 ms
    EXPECT_EQ(rt.tick(sim, 1.0), 100);   // a stall is clamped to 100 ms
    RealtimeStepper slow(0.010, 0.1, [&] { return now += 0.02; });  // each check overruns
    EXPECT_EQ(slow.tick(sim, 0.05), 25);  // one chunk, then the backlog is dropped
}

TEST(Simulation, ExperimentFilesRoundTrip) {
    const auto dir = std::filesystem::temp_directory_path();
    const auto r = runExperiment(scenario("single_fixed_pid"), "run A");
    const std::string json_path = (dir / "pendulum_experiments.json").string();
    saveExperiments({r}, json_path);
    const auto back = loadExperiments(json_path);
    ASSERT_EQ(back.size(), 1u);
    EXPECT_EQ(back[0].name, "run A");
    EXPECT_DOUBLE_EQ(back[0].metrics.rmse, r.metrics.rmse);
    EXPECT_EQ(back[0].data.at("t").size(), r.data.at("t").size());
    const std::string csv = (dir / "pendulum_run.csv").string();
    exportCsv(r, csv);
    std::ifstream in(csv);
    std::string header;
    std::getline(in, header);
    EXPECT_EQ(header, "t,x,xd,theta1_deg,omega1_dps,command,actuator,tip_force,energy\r");
    const std::string cfg_path = (dir / "pendulum_config.json").string();
    io::saveConfig(scenario("double_cart_lqr"), cfg_path);
    EXPECT_TRUE(sameJson(io::toJson(io::loadConfig(cfg_path)), io::toJson(scenario("double_cart_lqr")), 1e-15));
}

TEST(Simulation, ConfigValidationMessages) {
    json bad = io::toJson(scenario("single_cart_pid"));
    bad["simulation"]["dt"] = 0.5;
    EXPECT_THROW(io::appConfigFromJson(bad), std::invalid_argument);
    bad = io::toJson(scenario("single_cart_pid"));
    bad["version"] = 2;
    EXPECT_THROW(io::appConfigFromJson(bad), std::invalid_argument);
}

}  // namespace
