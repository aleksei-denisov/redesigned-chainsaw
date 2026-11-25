// Auto-tune and Experiments bridges (backend/autotune, backend/experiments): a short search
// from set-up to Apply, and experiment runs with plots, gains, removal. Headless.
#include <gtest/gtest.h>

#include <QCoreApplication>
#include <QGuiApplication>
#include <QThread>
#include <functional>
#include <memory>

#include "backend/autotune.h"
#include "backend/experiments.h"
#include "backend/parameters.h"
#include "backend/simulation.h"
#include "pendulum/simulation/scenarios.hpp"

using namespace app;

namespace {

QGuiApplication& qapp() {
    static int argc = 1;
    static char name[] = "pendulum_app_tests";
    static char* argv[] = {name, nullptr};
    static QGuiApplication a(argc, argv);
    return a;
}

bool waitFor(const std::function<bool()>& done, int timeout_ms = 60000) {
    for (int t = 0; t < timeout_ms && !done(); t += 20) {
        QThread::msleep(20);
        QCoreApplication::processEvents();
    }
    return done();
}

struct Jobs : ::testing::Test {
    void SetUp() override {
        qapp();
        QCoreApplication::setOrganizationName("adenisov-tests");
        sim = std::make_unique<SimulationBridge>(pendulum::scenario("single_cart_full"));
        controller = std::make_unique<ControllerBridge>(sim.get());
        autotune = std::make_unique<AutotuneBridge>(sim.get(), controller.get());
        experiments = std::make_unique<ExperimentsBridge>(sim.get(), controller.get());
        QObject::connect(sim.get(), &SimulationBridge::message,
                         [this](const QString& k, const QString& t) { messages.push_back((k + ": " + t).toStdString()); });
    }
    void TearDown() override {
        experiments.reset();
        autotune.reset();
        controller.reset();
        sim.reset();
    }
    std::unique_ptr<SimulationBridge> sim;
    std::unique_ptr<ControllerBridge> controller;
    std::unique_ptr<AutotuneBridge> autotune;
    std::unique_ptr<ExperimentsBridge> experiments;
    std::vector<std::string> messages;
};

pendulum::TuningSettings shortSearch() {
    auto s = pendulum::TuningSettings::preset("quick");
    s.samples = 4;
    s.refine_rounds = 1;
    s.refine_samples = 2;
    s.seed = 7;
    return s;
}

}  // namespace

TEST_F(Jobs, AutotuneFromSetupToApply) {
    autotune->setOptions({{"mode", "full"}, {"integral", false}});
    autotune->open();
    EXPECT_EQ(autotune->page(), "setup");
    ASSERT_TRUE(waitFor([&] { return autotune->pushTestSeconds().has_value(); }));
    EXPECT_TRUE(autotune->presets()[0].toMap()["estimate"].toString().startsWith("about"));

    sim->start();
    const double push = sim->pushForce();
    autotune->setSettingsOverride(shortSearch());
    autotune->start();
    EXPECT_TRUE(autotune->busy());
    EXPECT_EQ(autotune->page(), "running");
    EXPECT_FALSE(sim->running());  // paused for the search
    ASSERT_TRUE(waitFor([&] { return !autotune->busy(); }, 120000));
    EXPECT_EQ(autotune->page(), "result");
    ASSERT_TRUE(autotune->canApply()) << autotune->notes().toStdString();
    EXPECT_GE(autotune->result()->max_push, autotune->result()->baseline_max_push);  // never worse
    EXPECT_FALSE(autotune->metrics().isEmpty());
    EXPECT_EQ(autotune->preview().size(), 2);
    EXPECT_TRUE(autotune->notes().contains("push force is not changed"));

    const auto tuned = autotune->result()->controller;
    autotune->apply();
    EXPECT_DOUBLE_EQ(sim->session.config.controller.loops[0].kp, tuned.loops[0].kp);
    EXPECT_DOUBLE_EQ(sim->pushForce(), push);  // kept
    EXPECT_TRUE(controller->canUndo());
    EXPECT_EQ(autotune->page(), "setup");
    EXPECT_TRUE(sim->running());  // resumed
    sim->pause();
}

TEST_F(Jobs, AutotuneCancelOffersBestSoFar) {
    autotune->setOptions({{"mode", "full"}, {"integral", true}});
    autotune->choose("thorough");
    autotune->start();
    ASSERT_TRUE(autotune->busy());
    QThread::msleep(300);
    QCoreApplication::processEvents();
    autotune->cancel();
    ASSERT_TRUE(waitFor([&] { return !autotune->busy(); }));
    EXPECT_EQ(autotune->page(), "result");
    EXPECT_FALSE(autotune->headline().isEmpty());
    autotune->close();
    EXPECT_EQ(autotune->page(), "setup");
}

TEST_F(Jobs, AutotuneOptionsAndLoops) {
    EXPECT_EQ(autotune->loopList().size(), static_cast<int>(sim->session.config.controller.loops.size()));
    EXPECT_TRUE(autotune->hasCart());
    autotune->setOptions({{"mode", "loops"}, {"terms", QVariantList{"kp"}}});
    EXPECT_EQ(autotune->options()["mode"].toString(), "loops");
    EXPECT_TRUE(autotune->options().contains("limits"));  // defaults filled in
}

TEST_F(Jobs, ExperimentRunsPlotsAndGains) {
    experiments->form()->apply("duration", 3.0);
    experiments->run();
    ASSERT_TRUE(experiments->running());
    ASSERT_TRUE(waitFor([&] { return !experiments->running(); }));
    ASSERT_EQ(experiments->results().size(), 1u);
    EXPECT_EQ(experiments->placeholder(), "Run 2");
    const auto runs = experiments->runs();
    EXPECT_EQ(runs[0].toMap()["name"].toString(), "Run 1");
    EXPECT_TRUE(runs[0].toMap().contains("note"));
    const auto plots = experiments->plots();
    ASSERT_EQ(plots.size(), 4);  // angle, error, effort, cart
    const auto series = plots[0].toMap()["series"].toList();
    ASSERT_EQ(series.size(), 1);
    EXPECT_LE(series[0].toMap()["xs"].toList().size(), 600);
    EXPECT_EQ(plots[1].toMap()["hlines"].toList().size(), 2);

    // a second run with other gains, from the same start; then use the first run's gains
    const double kp = sim->session.config.controller.loops[0].kp;
    controller->form()->apply("loop0.kp", kp * 0.5);
    experiments->setName("Softer");
    experiments->run();
    ASSERT_TRUE(waitFor([&] { return !experiments->running(); }));
    ASSERT_EQ(experiments->results().size(), 2u);
    EXPECT_EQ(experiments->results()[1].name, "Softer");
    EXPECT_DOUBLE_EQ(experiments->results()[1].config.plant.gravity, experiments->results()[0].config.plant.gravity);
    experiments->useGains(0);
    EXPECT_DOUBLE_EQ(sim->session.config.controller.loops[0].kp, kp);

    experiments->toggleVisible(0);
    EXPECT_EQ(experiments->plots()[0].toMap()["series"].toList().size(), 1);
    experiments->remove(0);
    EXPECT_EQ(experiments->results().size(), 1u);
    EXPECT_EQ(experiments->runs()[0].toMap()["color"].toInt(), 1);  // colours stay with their runs
    experiments->clear();
    EXPECT_TRUE(experiments->plots().isEmpty());
    EXPECT_EQ(experiments->placeholder(), "Run 1");
}

TEST_F(Jobs, ThinKeepsPeaks) {
    std::vector<double> t(10000), y(10000, 0.0);
    for (std::size_t i = 0; i < t.size(); ++i) t[i] = static_cast<double>(i);
    y[4321] = 5.0;
    y[777] = -3.0;
    QVariantList ts, ys;
    thin(t, y, ts, ys, 600);
    EXPECT_LE(ys.size(), 600);
    double lo = 0, hi = 0;
    for (const auto& v : ys) {
        lo = std::min(lo, v.toDouble());
        hi = std::max(hi, v.toDouble());
    }
    EXPECT_EQ(hi, 5.0);
    EXPECT_EQ(lo, -3.0);
}
