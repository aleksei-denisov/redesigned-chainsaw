// Phase 4: session layer (forms, edit rules, operations, history, sliders, texts, jobs)
// against session.json from the Python package.
#include <gtest/gtest.h>

#include <chrono>
#include <thread>

#include "pendulum/io/config_json.hpp"
#include "pendulum/session/forms.hpp"
#include "pendulum/session/history.hpp"
#include "pendulum/session/jobs.hpp"
#include "pendulum/session/rules.hpp"
#include "pendulum/session/session.hpp"
#include "pendulum/session/sliders.hpp"
#include "pendulum/session/texts.hpp"
#include "pendulum/simulation/scenarios.hpp"
#include "reference.hpp"

using namespace pendulum;
using namespace pendulum::session;
using reference::json;

namespace {

json valueJson(const Value& v) {
    if (const auto* d = std::get_if<double>(&v)) return *d;
    if (const auto* b = std::get_if<bool>(&v)) return *b;
    return std::get<std::string>(v);
}

/// Sections, fields and values of a C++ form must equal the Python dump.
void expectSameForm(const Form& form, const json& ref) {
    const auto& secs = ref.at("sections");
    ASSERT_EQ(form.sections().size(), secs.size());
    for (std::size_t i = 0; i < secs.size(); ++i) {
        const auto& s = form.sections()[i];
        const auto& r = secs[i];
        SCOPED_TRACE(s.key);
        EXPECT_EQ(json(s.key), r.at("key"));
        EXPECT_EQ(json(s.title), r.at("title"));
        EXPECT_EQ(json(s.expanded), r.at("expanded"));
        EXPECT_EQ(json(s.note), r.at("note"));
        ASSERT_EQ(s.fields.size(), r.at("fields").size());
        for (std::size_t k = 0; k < s.fields.size(); ++k) {
            const auto& f = s.fields[k];
            const auto& rf = r.at("fields")[k];
            SCOPED_TRACE(f.key);
            EXPECT_EQ(json(f.key), rf.at("key"));
            EXPECT_EQ(json(f.label), rf.at("label"));
            EXPECT_EQ(json(f.kind), rf.at("kind"));
            EXPECT_DOUBLE_EQ(f.minimum, rf.at("minimum").get<double>());
            EXPECT_DOUBLE_EQ(f.maximum, rf.at("maximum").get<double>());
            EXPECT_EQ(json(f.decimals), rf.at("decimals"));
            EXPECT_EQ(json(f.unit), rf.at("unit"));
            EXPECT_EQ(json(f.mapping), rf.at("mapping"));
            EXPECT_EQ(f.step.has_value(), !rf.at("step").is_null());
            EXPECT_EQ(json(f.hint), rf.at("hint"));
            ASSERT_EQ(f.options.size(), rf.at("options").size());
            for (std::size_t o = 0; o < f.options.size(); ++o) {
                EXPECT_EQ(json(f.options[o].first), rf.at("options")[o][0]);
                EXPECT_EQ(json(f.options[o].second), rf.at("options")[o][1]);
            }
        }
    }
    const auto& values = ref.at("values");
    EXPECT_EQ(form.values().size(), values.size());
    for (const auto& [key, v] : values.items()) {
        SCOPED_TRACE(key);
        const json got = valueJson(form.get(key));
        if (v.is_number())
            // LQR-seeded gains differ from LAPACK by ~1e-12 (relative).
            EXPECT_NEAR(got.get<double>(), v.get<double>(), 1e-9 * std::max(1.0, std::abs(v.get<double>())));
        else
            EXPECT_EQ(got, v);
    }
}

class SessionReference : public ::testing::Test {
protected:
    static void SetUpTestSuite() { data = new json(reference::load("session")); }
    static void TearDownTestSuite() { delete data; }
    static inline json* data = nullptr;
};

TEST_F(SessionReference, FormsMatchFieldByField) {
    const auto& forms = data->at("forms");
    const AppConfig base = scenario("single_cart_full");
    PlantForm pf;
    pf.setPlant(base.plant);
    expectSameForm(pf, forms.at("plant"));
    SimulationForm sf;
    sf.setSimulation(base.simulation);
    expectSameForm(sf, forms.at("simulation"));
    ControllerForm cf;
    EXPECT_TRUE(cf.setConfig(scenario("double_cart_lqr").controller));
    expectSameForm(cf, forms.at("controller"));
    ExperimentForm ef;
    expectSameForm(ef, forms.at("experiment"));
}

TEST_F(SessionReference, ScriptedSessionMatchesStepByStep) {
    const reference::Tolerance tol(*data);
    Session s(scenario("single_cart_full"));
    std::unique_ptr<Simulator> sim = std::make_unique<Simulator>(s.config, std::nullopt);
    PlantForm pf;
    ControllerForm cf;
    SimulationForm sf;
    pf.setPlant(s.config.plant);
    cf.setConfig(s.config.controller);
    sf.setSimulation(s.config.simulation);
    const auto& steps = data->at("steps");
    std::size_t k = 0;

    auto record = [&](const std::string& action, const std::vector<Operation>& ops, const json& extra = json::object()) {
        ASSERT_LT(k, steps.size());
        const auto& ref = steps[k++];
        SCOPED_TRACE(action);
        EXPECT_EQ(json(action), ref.at("action"));
        std::vector<std::string> names;
        for (const auto& o : ops) {
            names.emplace_back(operationName(o));
            apply(sim, o, std::nullopt);
        }
        EXPECT_EQ(names, ref.at("operations").get<std::vector<std::string>>());
        for (int i = 0; i < 200; ++i) sim->step();
        for (const auto& [key, v] : extra.items()) EXPECT_EQ(v, ref.at(key)) << key;
        // Configuration: same JSON (numbers to 1e-12 relative).
        const json got = io::toJson(s.config);
        const json& want = ref.at("config");
        std::function<void(const json&, const json&, const std::string&)> same = [&](const json& a, const json& b,
                                                                                     const std::string& path) {
            if (b.is_object()) {
                for (const auto& [kk, vv] : b.items()) same(a.at(kk), vv, path + "." + kk);
            } else if (b.is_array()) {
                ASSERT_EQ(a.size(), b.size()) << path;
                for (std::size_t i = 0; i < b.size(); ++i) same(a[i], b[i], path + "[" + std::to_string(i) + "]");
            } else if (b.is_number()) {
                EXPECT_NEAR(a.get<double>(), b.get<double>(), 1e-12 * std::max(1.0, std::abs(b.get<double>()))) << path;
            } else {
                EXPECT_EQ(a, b) << path;
            }
        };
        same(got, want, "config");
        EXPECT_TRUE(reference::allClose(Eigen::VectorXd(sim->state()), reference::vec(ref.at("state")), tol));
        EXPECT_NEAR(sim->time(), ref.at("time").get<double>(), 1e-12);
    };

    auto plantEdit = [&](const std::string& key, const Value& value, const std::string& shown) {
        auto [plant, structural] = pf.set(key, value);
        const PlantChange ch = s.changePlant(plant, structural);
        if (ch.controller) cf.setConfig(*ch.controller);
        record("plant " + key + "=" + shown, ch.operations,
               {{"structural", ch.structural}, {"reset_if_idle", ch.reset_if_idle}});
    };

    plantEdit("seg0.mass", 0.3, "0.3");
    plantEdit("seg0.rod", false, "False");
    plantEdit("seg0.inertia", 0.01, "0.01");
    plantEdit("seg0.angle", 5.0, "5.0");
    plantEdit("segments", 2.0, "2");
    {
        auto [ctrl, ops] = s.seedLqr();
        cf.setConfig(ctrl);
        record("seed_lqr", ops);
    }
    record("controller loop1.kp=12.5", s.changeController(*cf.set("loop1.kp", 12.5)));
    record("controller loop0.anti=back_calculation",
           s.changeController(*cf.set("loop0.anti", std::string("back_calculation"))));
    plantEdit("base", std::string("fixed"), "fixed");
    record("restore_gains", s.restoreGains().second);
    bool incompatible = false;
    try {
        s.useRunGains(scenario("double_cart_lqr"));
    } catch (const IncompatibleRun&) {
        incompatible = true;
    }
    record("use_run_gains double_cart_lqr", {}, {{"incompatible", incompatible}});
    {
        const PlantChange ch = s.restoreModel(s.original_plant, s.original_simulation, s.original_controller);
        record("restore_model original", ch.operations, {{"structural", ch.structural}});
    }
    record("simulation seed=7.6", s.changeSimulation(sf.set("seed", 7.6)));
    record("simulation dt=0.5ms", s.changeSimulation(sf.set("dt", 0.5)));
    record("use_run_gains single_cart_pid", s.useRunGains(scenario("single_cart_pid")).second);
    record("reset_integrators", {op::ResetIntegrators{}});
    EXPECT_EQ(k, steps.size());
}

TEST_F(SessionReference, SlidersMatch) {
    for (const auto& c : data->at("sliders")) {
        const double lo = c.at("lo"), hi = c.at("hi");
        const std::string m = c.at("mapping");
        const int dec = c.at("decimals");
        SCOPED_TRACE(m);
        const double ts[] = {0.0, 0.1, 0.37, 0.5, 0.9, 1.0};
        for (int i = 0; i < 6; ++i) {
            const double want = c.at("from_position")[i];
            EXPECT_NEAR(fromPosition(ts[i], lo, hi, m, dec), want, 1e-12 * std::max(1.0, std::abs(want)));
        }
        for (std::size_t i = 0; i < c.at("values").size(); ++i) {
            const double v = c.at("values")[i];
            EXPECT_NEAR(toPosition(v, lo, hi, m, dec), c.at("to_position")[i].get<double>(), 1e-12);
            EXPECT_NEAR(nudge(v, 1, lo, hi, m, dec), c.at("nudge_up")[i].get<double>(), 1e-9 * std::max(1.0, std::abs(v)));
            EXPECT_NEAR(nudge(v, -1, lo, hi, m, dec), c.at("nudge_down")[i].get<double>(), 1e-9 * std::max(1.0, std::abs(v)));
        }
    }
}

TEST_F(SessionReference, TextsMatch) {
    const auto& t = data->at("texts");
    for (const auto& d : t.at("durations")) {
        const std::optional<double> v = d[0].is_null() ? std::nullopt : std::optional<double>(reference::number(d[0]));
        EXPECT_EQ(json(texts::fmtDuration(v)), d[1]);
    }
    for (const auto& [key, text] : t.at("gains").items()) {
        const auto c = scenario(key).controller;
        EXPECT_EQ(json(texts::gainsText(&c)), text) << key;
    }
    const auto run = runExperiment(io::appConfigFromJson(data->at("run_config")), "run");
    const auto pairs = texts::metricPairs(run);
    const auto& want = t.at("metric_pairs");
    ASSERT_EQ(pairs.size(), want.size() + 1);  // + "Saturated" (new in C++)
    for (std::size_t i = 0; i < want.size(); ++i) {
        EXPECT_EQ(json(pairs[i].first), want[i][0]);
        EXPECT_EQ(json(pairs[i].second), want[i][1]);
    }
    EXPECT_EQ(json(texts::statusText(run)), t.at("status"));
    TuningResult fake;
    fake.baseline_max_push = 0.42;
    fake.max_push = 1.234;
    fake.recommended_push = 0.61;
    fake.evaluations = 37;
    fake.elapsed = 83.4;
    fake.cancelled = true;
    EXPECT_EQ(json(texts::tuneHeadline(fake)), t.at("headline"));
    EXPECT_EQ(json(texts::tuneNotes(fake)), t.at("notes"));
    EXPECT_EQ(json(texts::tuningApplied(fake)), t.at("applied"));
    EXPECT_EQ(json(texts::tuneCores(3, 8)), t.at("cores"));
}

TEST_F(SessionReference, RulesMatch) {
    const auto& r = data->at("rules");
    for (const auto& c : r.at("drag")) EXPECT_DOUBLE_EQ(dragForce(c[0], c[1]), c[2].get<double>());
    for (const auto& c : r.at("point_drag")) EXPECT_DOUBLE_EQ(pointDragForce(c[0], c[1]), c[2].get<double>());
    for (const auto& c : r.at("single_step")) EXPECT_EQ(singleStepCount(c[0], c[1]), c[2].get<int>());
    EXPECT_EQ(loopLabels(scenario("triple_cart_lqr").controller), r.at("labels").get<std::vector<std::string>>());
    EXPECT_DOUBLE_EQ(outputLimit(scenario("windup_demo")), r.at("output_limit").get<double>());
}

TEST(Session, HistoryUndoRedo) {
    EditHistory<int> h(3);
    EXPECT_FALSE(h.canUndo());
    for (int v = 1; v <= 5; ++v) h.record(v);  // keeps the last 3
    auto u = h.undo(6);
    ASSERT_TRUE(u);
    EXPECT_EQ(*u, 5);
    EXPECT_EQ(*h.redo(5), 6);
    EXPECT_EQ(*h.undo(6), 5);
    EXPECT_EQ(*h.undo(5), 4);
    EXPECT_EQ(*h.undo(4), 3);
    EXPECT_FALSE(h.undo(3));
    h.record(10);
    EXPECT_FALSE(h.canRedo());
}

TEST(Session, ExperimentWorkerRunsAndCancels) {
    ExperimentWorker w(scenario("single_fixed_pid"), "bg");
    w.start();
    w.wait();
    ASSERT_TRUE(w.done());
    ASSERT_TRUE(w.result);
    EXPECT_DOUBLE_EQ(w.progress(), 1.0);
    ReplayData replay(*w.result);
    EXPECT_NEAR(replay.duration(), 5.0, 1e-9);
    EXPECT_EQ(replay.index(2.5), replay.index(2.5 + 1e-6));
    EXPECT_EQ(replay.state(0).size(), 4);
}

TEST(Session, TuneWorkerReportsProgressAndResult) {
    TuningSettings s = TuningSettings::preset("quick");
    s.workers = 2;
    TuneWorker w(scenario("single_cart_full"), s, TuneOptions{});
    w.start();
    int snapshots = 0;
    while (!w.done()) {
        if (w.takeProgress()) ++snapshots;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    w.wait();
    ASSERT_TRUE(w.outcome) << w.error.value_or("");
    EXPECT_GT(w.outcome->first.max_push, 0.0);
    EXPECT_TRUE(w.outcome->second.has_value());  // before / after preview
    EXPECT_GE(snapshots, 1);
    TuneWorker cal(scenario("single_cart_full"), s, TuneOptions{}, true);
    cal.start();
    cal.wait();
    EXPECT_TRUE(cal.calibration && *cal.calibration > 0.0);
}

TEST(Session, StructureChangeKeepsLoopsAndClearsGainsOnNewBase) {
    Session s(scenario("single_cart_full"));
    PlantParams p = s.config.plant;
    p.base_type = BaseType::Fixed;
    const auto ch = s.changePlant(p, true);
    ASSERT_TRUE(ch.controller);
    EXPECT_EQ(ch.controller->action, ControlAction::Reverse);
    EXPECT_EQ(ch.controller->loops.size(), 1u);  // no cart loop on a fixed pivot
    EXPECT_EQ(ch.controller->loops[0].kp, 0.0);
}

TEST(Session, SaturationNoteOnlyWhenHigh) {
    RunMetrics m;
    m.saturation_pct = 3.0;
    EXPECT_TRUE(texts::saturationNote(m).empty());
    m.saturation_pct = 95.8;
    EXPECT_NE(texts::saturationNote(m).find("96 %"), std::string::npos);
}

}  // namespace
