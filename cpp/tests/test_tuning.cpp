// Phase 3: auto-tune against the Python reference data, plus the C++ improvements.
#include <gtest/gtest.h>

#include "pendulum/io/config_json.hpp"
#include "pendulum/simulation/scenarios.hpp"
#include "pendulum/simulation/tuning.hpp"
#include "reference.hpp"

using namespace pendulum;
using reference::json;

namespace {

TuningSettings settingsFrom(const json& d) {
    TuningSettings s;
    s.push_duration = d.at("push_duration");
    s.push_time = d.at("push_time");
    s.horizon = d.at("horizon");
    s.fall_deg = d.at("fall_deg");
    s.settle_deg = d.at("settle_deg");
    s.resolution = d.at("resolution");
    s.samples = d.at("samples");
    s.refine_rounds = d.at("refine_rounds");
    s.refine_samples = d.at("refine_samples");
    s.workers = d.at("workers");
    s.seed = d.at("seed");
    s.prune = d.at("prune");
    s.early_stop = d.at("early_stop");
    s.still_deg = d.at("still_deg");
    s.still_rate_dps = d.at("still_rate_dps");
    s.still_cart_mps = d.at("still_cart_mps");
    return s;
}

void expectEvaluation(const Evaluation& e, const json& r, const reference::Tolerance& tol) {
    EXPECT_TRUE(tol.close(e.push, reference::number(r[0]))) << e.push << " vs " << r[0];
    // The recovery cost integrates a whole run: compare at 1e-7.
    const double cost = reference::number(r[1]);
    if (std::isinf(cost))
        EXPECT_TRUE(std::isinf(e.cost));
    else
        EXPECT_NEAR(e.cost, cost, 1e-7 * std::abs(cost) + 1e-12);
    EXPECT_EQ(e.simulations, r[2].get<int>());
    const double res = reference::number(r[3]);
    if (std::isnan(res))
        EXPECT_TRUE(std::isnan(e.residual));
    else
        EXPECT_NEAR(e.residual, res, 1e-7);
}

class TuningReference : public ::testing::Test {
protected:
    static void SetUpTestSuite() { data = new json(reference::load("tuning")); }
    static void TearDownTestSuite() { delete data; }
    static inline json* data = nullptr;
};

TEST_F(TuningReference, MaxRecoverablePushMatches) {
    const reference::Tolerance tol(*data);
    for (const auto& c : data->at("max_push")) {
        SCOPED_TRACE(c.at("scenario").get<std::string>());
        int sims = 0;
        const double push =
            maxRecoverablePush(io::appConfigFromJson(c.at("config")), settingsFrom(c.at("settings")), 100.0, &sims);
        EXPECT_TRUE(tol.close(push, reference::number(c.at("max_recoverable_push"))))
            << push << " vs " << c.at("max_recoverable_push");
    }
}

TEST_F(TuningReference, EvaluateControllerMatches) {
    const reference::Tolerance tol(*data);
    for (const auto& c : data->at("evaluate_controller")) {
        SCOPED_TRACE(c.at("scenario").get<std::string>());
        const auto e = evaluateController(io::appConfigFromJson(c.at("config")), settingsFrom(c.at("settings")));
        expectEvaluation(e, c.at("result"), tol);
    }
}

TEST_F(TuningReference, EveryEvaluationOfAPythonSearchMatches) {
    const reference::Tolerance tol(*data);
    const auto& run = data->at("tune_run");
    const TuningSettings s = settingsFrom(run.at("settings"));
    int k = 0;
    for (const auto& e : run.at("evaluations")) {
        SCOPED_TRACE("evaluation " + std::to_string(k++));
        const auto got = evaluateController(io::appConfigFromJson(e.at("config")), s, e.at("min_push"), e.at("hold"));
        expectEvaluation(got, e.at("result"), tol);
    }
    EXPECT_EQ(k, 26);
}

TuningSettings quick(int workers) {
    TuningSettings s = TuningSettings::preset("quick");
    s.workers = workers;
    return s;
}

TEST(Tuning, NeverWorseThanBeforeAndIndependentOfWorkers) {
    const AppConfig cfg = scenario("single_cart_full");
    const auto serial = tune(cfg, quick(1));
    const auto parallel = tune(cfg, quick(4));
    // Improvement #1: the current controller is a candidate.
    EXPECT_GE(serial.max_push, serial.baseline_max_push * (1.0 - quick(1).resolution));
    // Same candidates in the same order: identical outcome on 1 or 4 threads.
    EXPECT_EQ(serial.max_push, parallel.max_push);
    EXPECT_EQ(serial.history, parallel.history);
    EXPECT_EQ(io::toJson(serial.controller), io::toJson(parallel.controller));
}

TEST(Tuning, ClassicSearchFindsARobustDoublePendulumController) {
    AppConfig cfg = scenario("double_cart_lqr");
    TuningSettings s = quick(0);
    const auto r = tune(cfg, s, TuneOptions::classic());
    EXPECT_GT(r.max_push, 0.1);
    for (const auto& l : r.controller.loops) EXPECT_EQ(l.ki, 0.0);
}

TEST(Tuning, LoopsModeRespectsLimitsAndKeepsOtherLoops) {
    AppConfig cfg = scenario("single_cart_full");
    TuneOptions o;
    o.mode = TuneMode::Loops;
    o.loops = {"theta1"};
    o.terms = {"kd"};
    o.limits.general.kd = std::make_pair(-1e9, 1e9);
    o.limits.per_loop["theta1"].kd = std::make_pair(0.0, 40.0);  // current Kd ≈ 48: must come down
    const auto r = tune(cfg, quick(0), o);
    for (std::size_t i = 0; i < r.controller.loops.size(); ++i) {
        const auto& l = r.controller.loops[i];
        if (l.signal == "theta1") {
            EXPECT_LE(l.kd, 40.0 + 1e-9);
            EXPECT_DOUBLE_EQ(l.kp, cfg.controller.loops[i].kp);
        } else {
            EXPECT_DOUBLE_EQ(l.kd, cfg.controller.loops[i].kd);
        }
    }
}

TEST(Tuning, ImpossibleLimitsAreReported) {
    TuneOptions o;
    o.mode = TuneMode::Loops;
    o.loops = {"theta1"};
    o.terms = {"kd"};
    o.limits.per_loop["theta1"].kd = std::make_pair(0.0, 3.0);  // far too little damping
    EXPECT_THROW(tune(scenario("single_cart_full"), quick(0), o), std::invalid_argument);
}

TEST(Tuning, CancelReturnsBestSoFar) {
    int updates = 0;
    CancelToken token;
    const auto r = tune(scenario("single_cart_full"), quick(2), {}, [&](const TuneProgress& p) {
        return ++updates < 6 || p.phase == "done";
    }, &token);
    EXPECT_TRUE(r.cancelled);
    EXPECT_GT(r.max_push, 0.0);
}

TEST(Tuning, OptionsFromJson) {
    const json d = {{"mode", "loops"},
                    {"loops", {"theta1", "nope"}},
                    {"terms", {"kp", "kd"}},
                    {"off_terms", {"ki"}},
                    {"limit_mode", "per_loop"},
                    {"limits", {{"general", {{"kp", {0, nullptr}}}}, {"per_loop", {{"theta1", {{"kd", {1, 2}}}}}}}}};
    const auto o = optionsFromJson(d, {"cart", "theta1"});
    EXPECT_EQ(o.mode, TuneMode::Loops);
    EXPECT_EQ(o.loops, std::vector<std::string>{"theta1"});
    EXPECT_EQ(o.terms, (std::vector<std::string>{"kp", "kd"}));
    EXPECT_EQ(o.off_terms, std::vector<std::string>{"ki"});
    ASSERT_TRUE(o.limits.general.kp);
    EXPECT_EQ(o.limits.general.kp->first, 0.0);
    EXPECT_TRUE(std::isinf(o.limits.general.kp->second));
    EXPECT_FALSE(o.limits.forLoop("theta1").kp);  // own limits replace the general ones
    EXPECT_EQ(o.limits.forLoop("theta1").kd->second, 2.0);
}

TEST(Tuning, RoundNice) {
    EXPECT_DOUBLE_EQ(roundNice(2.4789), 2.4);
    EXPECT_DOUBLE_EQ(roundNice(0.0567), 0.056);
    EXPECT_DOUBLE_EQ(roundNice(153.0), 150.0);
    EXPECT_DOUBLE_EQ(roundNice(0.0), 0.0);
}

}  // namespace
