// Phase 1: the golden data is present, readable and complete. The module tests of
// Phases 2–4 compare the C++ results against these files.
#include <gtest/gtest.h>

#include "reference.hpp"

namespace {

constexpr std::size_t kScenarios = 9;

TEST(ReferenceData, PhysicsCoversEveryScenario) {
    const auto d = reference::load("physics");
    ASSERT_EQ(d.at("cases").size(), kScenarios);
    for (const auto& c : d.at("cases")) {
        EXPECT_FALSE(c.at("samples").empty()) << c.at("scenario");
        const auto& s = c.at("samples").front();
        const auto n = s.at("state").size();
        EXPECT_EQ(s.at("rk4").size(), n);
        EXPECT_EQ(s.at("mass_matrix").size() * 2, n);
    }
}

TEST(ReferenceData, ControlHasLinearisationAndLqr) {
    const auto d = reference::load("control");
    ASSERT_EQ(d.at("plants").size(), kScenarios);
    for (const auto& p : d.at("plants")) {
        EXPECT_EQ(p.at("A").size(), p.at("B").size());
        EXPECT_TRUE(p.at("lqr").contains("K"));
    }
    EXPECT_FALSE(d.at("pid").empty());
}

TEST(ReferenceData, SimulationHasBothCasesPerScenario) {
    const auto d = reference::load("simulation");
    ASSERT_EQ(d.at("cases").size(), 2 * kScenarios);
    for (const auto& c : d.at("cases")) EXPECT_FALSE(c.at("states").empty());
}

TEST(ReferenceData, ExperimentAndTuning) {
    EXPECT_EQ(reference::load("experiment").at("cases").size(), kScenarios);
    const auto t = reference::load("tuning");
    EXPECT_FALSE(t.at("max_push").empty());
    EXPECT_FALSE(t.at("evaluate_controller").empty());
    EXPECT_EQ(t.at("tune_run").at("evaluations").size(),
              t.at("tune_run").at("evaluations_count").get<std::size_t>());
}

TEST(ReferenceData, ToleranceHandlesSpecialValues) {
    const auto d = reference::load("tuning");
    const reference::Tolerance tol(d);
    EXPECT_TRUE(tol.close(reference::number("nan"), reference::number("nan")));
    EXPECT_TRUE(tol.close(1.0 + 1e-12, 1.0));
    EXPECT_FALSE(tol.close(1.001, 1.0));
}

}  // namespace
