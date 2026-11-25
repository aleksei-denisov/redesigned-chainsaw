// Model and Controller bridges (backend/parameters, backend/formmodel): edits reach the
// session, structural edits rebuild the controller's loops, undo / redo / reset, LQR seeding,
// and the list model's rows and slider helpers. Headless (QT_QPA_PLATFORM=offscreen).
#include <gtest/gtest.h>

#include <QGuiApplication>
#include <QThread>
#include <memory>

#include "backend/formmodel.h"
#include "backend/parameters.h"
#include "backend/simulation.h"
#include "pendulum/session/texts.hpp"
#include "pendulum/simulation/scenarios.hpp"

using namespace app;
namespace ps = pendulum::session;

namespace {

QGuiApplication& qapp() {
    static int argc = 1;
    static char name[] = "pendulum_app_tests";
    static char* argv[] = {name, nullptr};
    static QGuiApplication a(argc, argv);
    return a;
}

struct Fixture : ::testing::Test {
    void SetUp() override {
        qapp();
        sim = std::make_unique<SimulationBridge>(pendulum::scenario("single_cart_full"));
        model = std::make_unique<ModelBridge>(sim.get());
        controller = std::make_unique<ControllerBridge>(sim.get());
        connect();
    }
    void connect() {
        QObject::connect(sim.get(), &SimulationBridge::message, [this](const QString&, const QString& t) {
            messages.push_back(t.toStdString());
        });
    }
    void TearDown() override {
        controller.reset();
        model.reset();
        sim.reset();
    }
    std::unique_ptr<SimulationBridge> sim;
    std::unique_ptr<ModelBridge> model;
    std::unique_ptr<ControllerBridge> controller;
    std::vector<std::string> messages;
};

int sectionRows(FormModel* m) {
    int n = 0;
    const auto names = m->roleNames();
    const int role = names.key("rowType");
    for (int r = 0; r < m->rowCount(); ++r) n += m->data(m->index(r), role).toString() == "section";
    return n;
}

QVariant field(FormModel* m, const QString& key) { return m->value(key); }

}  // namespace

TEST_F(Fixture, StructuralEditRebuildsLoopsAndUndoes) {
    const int loops_before = static_cast<int>(sim->session.config.controller.loops.size());
    const int sections_before = sectionRows(model->plantForm());
    model->plantForm()->apply("segments", 2.0);
    EXPECT_EQ(sim->session.config.plant.nSegments(), 2);
    EXPECT_EQ(sim->segments(), 2);
    EXPECT_EQ(static_cast<int>(sim->session.config.controller.loops.size()), loops_before + 1);
    EXPECT_EQ(sectionRows(controller->form()), static_cast<int>(sim->session.config.controller.loops.size()) + 2);
    EXPECT_GT(sectionRows(model->plantForm()), sections_before);  // the second segment's editor
    EXPECT_TRUE(model->canUndo());
    ASSERT_FALSE(messages.empty());
    EXPECT_EQ(messages.back(), ps::texts::kStructureChanged);

    model->undo();
    EXPECT_EQ(sim->session.config.plant.nSegments(), 1);
    EXPECT_EQ(sim->segments(), 1);
    EXPECT_EQ(static_cast<int>(sim->session.config.controller.loops.size()), loops_before);
    EXPECT_EQ(sectionRows(model->plantForm()), sections_before);
    EXPECT_TRUE(model->canRedo());
    model->redo();
    EXPECT_EQ(sim->session.config.plant.nSegments(), 2);
}

TEST_F(Fixture, PlantEditAndReset) {
    const double g0 = sim->session.config.plant.gravity;
    model->plantForm()->apply("gravity", 5.0);
    EXPECT_DOUBLE_EQ(sim->session.config.plant.gravity, 5.0);
    EXPECT_DOUBLE_EQ(field(model->plantForm(), "gravity").toDouble(), 5.0);
    model->reset();
    EXPECT_DOUBLE_EQ(sim->session.config.plant.gravity, g0);
    EXPECT_DOUBLE_EQ(field(model->plantForm(), "gravity").toDouble(), g0);
    model->undo();  // reset can be undone
    EXPECT_DOUBLE_EQ(sim->session.config.plant.gravity, 5.0);
}

TEST_F(Fixture, SimulationSettingEdit) {
    model->simulationForm()->apply("dt", 0.5);  // milliseconds on screen
    EXPECT_DOUBLE_EQ(sim->session.config.simulation.dt, 0.0005);
    EXPECT_TRUE(model->canUndo());
    model->undo();
    EXPECT_DOUBLE_EQ(model->simulationForm()->value("dt").toDouble(), sim->session.config.simulation.dt * 1000.0);
}

TEST_F(Fixture, ControllerEditUndoRedo) {
    const double kp0 = sim->session.config.controller.loops[0].kp;
    controller->form()->apply("loop0.kp", kp0 + 1.0);
    EXPECT_DOUBLE_EQ(sim->session.config.controller.loops[0].kp, kp0 + 1.0);
    EXPECT_TRUE(controller->canUndo());
    controller->undo();
    EXPECT_DOUBLE_EQ(sim->session.config.controller.loops[0].kp, kp0);
    EXPECT_DOUBLE_EQ(controller->form()->value("loop0.kp").toDouble(), kp0);
    controller->redo();
    EXPECT_DOUBLE_EQ(sim->session.config.controller.loops[0].kp, kp0 + 1.0);
}

TEST_F(Fixture, SeedLqrThenRestoreGains) {
    const auto original = sim->session.config.controller;
    for (std::size_t k = 0; k < original.loops.size(); ++k)
        controller->form()->apply("loop" + std::to_string(k) + ".kp", 1.0);
    controller->seedLqr();
    EXPECT_EQ(messages.back(), ps::texts::kLqrSeeded);
    bool changed = false;
    for (const auto& l : sim->session.config.controller.loops) changed |= l.kp != 1.0;
    EXPECT_TRUE(changed);
    controller->restoreGains();
    EXPECT_EQ(messages.back(), ps::texts::kGainsRestored);
    for (std::size_t k = 0; k < original.loops.size(); ++k)
        EXPECT_DOUBLE_EQ(sim->session.config.controller.loops[k].kp, original.loops[k].kp);
    controller->undo();
    EXPECT_NE(sim->session.config.controller.loops[0].kp, original.loops[0].kp);
}

TEST_F(Fixture, NewConfigurationClearsHistory) {
    model->plantForm()->apply("gravity", 5.0);
    controller->form()->apply("loop0.kp", 3.0);
    ASSERT_TRUE(model->canUndo() && controller->canUndo());
    ASSERT_TRUE(sim->load(pendulum::scenario("double_cart_lqr")));
    EXPECT_FALSE(model->canUndo());
    EXPECT_FALSE(controller->canUndo());
    EXPECT_EQ(sim->session.config.plant.nSegments(), 2);
    EXPECT_DOUBLE_EQ(controller->form()->value("loop0.kp").toDouble(), sim->session.config.controller.loops[0].kp);
}

TEST_F(Fixture, SlidersAndPages) {
    FormModel* m = model->plantForm();
    const double t = m->position("gravity", 9.81);
    EXPECT_GT(t, 0.0);
    EXPECT_LT(t, 1.0);
    EXPECT_NEAR(m->valueAt("gravity", t), 9.81, 1e-3);
    EXPECT_GT(m->nudged("gravity", 1), 9.81);
    EXPECT_LT(m->nudged("gravity", -1), 9.81);
    const int all = m->rowCount();
    m->setSections({QStringLiteral("pendulum")});
    EXPECT_LT(m->rowCount(), all);
    EXPECT_EQ(sectionRows(m), 1);
    m->setSections({});
    EXPECT_EQ(m->rowCount(), all);
}

TEST_F(Fixture, LiveOutputs) {
    controller->setActive(true);
    sim->start();
    for (int i = 0; i < 50 && controller->outputText().isEmpty(); ++i) {
        QThread::msleep(20);
        QCoreApplication::processEvents();
    }
    EXPECT_TRUE(controller->outputText().startsWith("Output: command"));
    controller->setActive(false);
    sim->pause();
}
