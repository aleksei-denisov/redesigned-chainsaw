// Phase 2: controllers against control.json (pendulum_sim/control).
#include <gtest/gtest.h>

#include "pendulum/control/lqr.hpp"
#include "pendulum/control/multiloop.hpp"
#include "pendulum/control/riccati.hpp"
#include "pendulum/io/config_json.hpp"
#include "reference.hpp"

using namespace pendulum;
using reference::allClose;

namespace {

class ControlReference : public ::testing::Test {
protected:
    static void SetUpTestSuite() { data = new reference::json(reference::load("control")); }
    static void TearDownTestSuite() { delete data; }
    static inline reference::json* data = nullptr;
};

TEST_F(ControlReference, LinearisationMatches) {
    const reference::Tolerance tol(*data);
    for (const auto& p : data->at("plants")) {
        SCOPED_TRACE(p.at("scenario").get<std::string>());
        const auto lin = linearize(io::plantFromJson(p.at("plant")));
        EXPECT_TRUE(allClose(lin.A, reference::mat(p.at("A")), tol)) << "A";
        EXPECT_TRUE(allClose(lin.B, reference::mat(p.at("B")), tol)) << "B";
        EXPECT_EQ(lin.kept, p.at("state_index").get<std::vector<int>>());
    }
}

TEST_F(ControlReference, RiccatiMatchesBothPythonBackends) {
    const reference::Tolerance tol(*data);
    for (const auto& p : data->at("plants")) {
        SCOPED_TRACE(p.at("scenario").get<std::string>());
        const auto& care = p.at("care");
        const Eigen::MatrixXd A = reference::mat(p.at("A")), B = reference::mat(p.at("B"));
        const Eigen::MatrixXd Q = reference::mat(care.at("Q")), R = reference::mat(care.at("R"));
        if (care.contains("error")) {
            EXPECT_THROW(solveCare(A, B, Q, R), std::runtime_error);
            continue;
        }
        const Eigen::MatrixXd P = solveCare(A, B, Q, R);
        EXPECT_LT(careResidual(A, B, Q, R, P), 1e-10);
        // P entries span many magnitudes: compare relative to ‖P‖.
        const Eigen::MatrixXd Pn = reference::mat(care.at("P_numpy"));
        EXPECT_LT((P - Pn).norm() / Pn.norm(), 1e-9);
        if (!care.at("P_scipy").is_null()) {
            const Eigen::MatrixXd Ps = reference::mat(care.at("P_scipy"));
            EXPECT_LT((P - Ps).norm() / Ps.norm(), 1e-9);
        }
        (void)tol;
    }
}

TEST_F(ControlReference, LqrGainMatches) {
    for (const auto& p : data->at("plants")) {
        SCOPED_TRACE(p.at("scenario").get<std::string>());
        const auto& l = p.at("lqr");
        LQRWeights w;
        const auto& wj = l.at("weights");
        w.cart_position = wj.at("cart_position");
        w.angle = wj.at("angle");
        w.cart_velocity = wj.at("cart_velocity");
        w.angular_velocity = wj.at("angular_velocity");
        w.effort = wj.at("effort");
        const auto g = lqrGain(io::plantFromJson(p.at("plant")), w);
        const Eigen::VectorXd K = reference::vec(l.at("K"));
        EXPECT_LT((g.K - K).norm() / K.norm(), 1e-8);
        EXPECT_EQ(g.kept, l.at("index").get<std::vector<int>>());
    }
}

TEST_F(ControlReference, PidLoopsMatchStepByStep) {
    const reference::Tolerance tol(*data);
    for (const auto& c : data->at("pid")) {
        SCOPED_TRACE(c.at("scenario").get<std::string>() + " / " + c.at("loop").at("name").get<std::string>());
        // The Python dataclass dump stores the setpoint in radians: read it as such.
        auto lj = c.at("loop");
        PIDLoopConfig cfg = io::loopFromJson(lj);
        cfg.setpoint = reference::number(lj.at("setpoint"));
        PIDLoop loop(cfg);
        const double ts = reference::number(c.at("ts"));
        int k = 0;
        for (const auto& row : c.at("steps")) {
            const double m = reference::number(row[0]), r = reference::number(row[1]);
            const auto out = loop.compute(m, r, ts, 1.0);
            const double sat = reference::number(row[7]);
            ASSERT_TRUE(tol.close(out.error, reference::number(row[2]))) << "step " << k;
            ASSERT_TRUE(tol.close(out.p, reference::number(row[3]))) << "step " << k;
            ASSERT_TRUE(tol.close(out.i, reference::number(row[4]))) << "step " << k;
            ASSERT_TRUE(tol.close(out.d, reference::number(row[5]))) << "step " << k;
            ASSERT_TRUE(tol.close(out.total(), reference::number(row[6]))) << "step " << k;
            loop.updateIntegral(ts, out.total(), sat);
            ++k;
        }
    }
}

TEST(Control, WrapAngle) {
    EXPECT_NEAR(wrapAngle(3.5), 3.5 - 2 * M_PI, 1e-15);
    EXPECT_DOUBLE_EQ(wrapAngle(M_PI), M_PI);
    EXPECT_NEAR(wrapAngle(-M_PI), M_PI, 1e-15);
}

TEST(Control, ControllerHoldsBetweenSamples) {
    ControllerConfig cfg;
    cfg.frequency_hz = 100.0;
    MultiLoopController c(cfg);
    Vec q = Vec::Zero(2), qd = Vec::Zero(2);
    q[1] = 0.1;
    EXPECT_TRUE(c.step(0.0, q, qd).updated);
    EXPECT_FALSE(c.step(0.005, q, qd).updated);
    EXPECT_TRUE(c.step(0.01, q, qd).updated);
}

TEST(Control, SetConfigKeepsIntegratorState) {
    ControllerConfig cfg;
    cfg.loops[0].ki = 10.0;
    MultiLoopController c(cfg);
    Vec q = Vec::Zero(2), qd = Vec::Zero(2);
    q[1] = 0.1;
    for (int i = 0; i < 10; ++i) c.step(i * 0.002, q, qd);
    const double before = c.loops()[0].integral;
    ASSERT_NE(before, 0.0);
    cfg.loops[0].kp = 1.0;
    c.setConfig(cfg);
    EXPECT_DOUBLE_EQ(c.loops()[0].integral, before);
}

TEST(Control, LqrControllerSignsFollowTheAction) {
    PlantParams p;
    const auto c = lqrController(p);
    ASSERT_EQ(c.loops.size(), 2u);
    EXPECT_EQ(c.loops[0].signal, "cart");
    EXPECT_EQ(c.action, ControlAction::Direct);
    EXPECT_GT(c.loops[1].kp, 0.0);  // drive towards the lean
}

TEST(Control, ConfigJsonRoundTrip) {
    PlantParams p;
    p.segments[0].initial_angle = 0.1;
    const auto back = io::plantFromJson(io::toJson(p));
    EXPECT_NEAR(back.segments[0].initial_angle, 0.1, 1e-16);
    const auto c = lqrController(p);
    const auto c2 = io::controllerFromJson(io::toJson(c), p);
    EXPECT_DOUBLE_EQ(c2.loops[1].kd, c.loops[1].kd);
}

}  // namespace
