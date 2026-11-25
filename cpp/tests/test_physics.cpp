// Phase 2: physics against physics.json (pendulum_sim/physics).
#include <gtest/gtest.h>

#include "pendulum/io/config_json.hpp"
#include "pendulum/physics/actuator.hpp"
#include "pendulum/physics/model.hpp"
#include "reference.hpp"

using namespace pendulum;
using reference::allClose;

namespace {

Vec toVec(const Eigen::VectorXd& v) { return Vec(v); }

PushArms arms(const reference::json& a) {
    std::array<double, kMaxSegments> out{};
    for (std::size_t i = 0; i < a.size(); ++i) out[i] = reference::number(a[i]);
    return out;
}

class PhysicsReference : public ::testing::Test {
protected:
    static void SetUpTestSuite() { data = new reference::json(reference::load("physics")); }
    static void TearDownTestSuite() { delete data; }
    static inline reference::json* data = nullptr;
};

TEST_F(PhysicsReference, EveryQuantityMatches) {
    const reference::Tolerance tol(*data);
    int checked = 0;
    for (const auto& c : data->at("cases")) {
        SCOPED_TRACE(c.at("scenario").get<std::string>());
        const PlantParams plant = io::plantFromJson(c.at("config").at("plant"));
        const MultiLinkPendulum model(plant);
        const int n1 = model.n() + 1;
        for (const auto& s : c.at("samples")) {
            const Vec state = toVec(reference::vec(s.at("state")));
            const Vec q = state.head(n1), qd = state.segment(n1, n1);
            const double u = reference::number(s.at("actuator"));
            const double f = reference::number(s.at("tip_force"));
            const auto& push = s.at("push");
            const PushArms a = arms(push.at("arms"));
            const auto unit = model.pushArms(push.at("segment").get<int>(), reference::number(push.at("ratio")));
            const Eigen::VectorXd unit_v = Eigen::Map<const Eigen::VectorXd>(unit.data(), model.n());

            EXPECT_TRUE(allClose(unit_v, reference::vec(push.at("arms_unit")), tol)) << "push arms";
            EXPECT_TRUE(allClose(model.massMatrix(q), reference::mat(s.at("mass_matrix")), tol)) << "M";
            EXPECT_TRUE(allClose(model.biasForces(q, qd), reference::vec(s.at("bias_forces")), tol)) << "bias";
            EXPECT_TRUE(allClose(model.jointFrictionTorques(qd), reference::vec(s.at("joint_friction")), tol));
            EXPECT_TRUE(allClose(model.generalizedForces(q, qd, u, f), reference::vec(s.at("generalized_forces")), tol));
            EXPECT_TRUE(allClose(model.accelerations(state), reference::vec(s.at("accelerations_free")), tol));
            EXPECT_TRUE(allClose(model.accelerations(state, u, f), reference::vec(s.at("accelerations")), tol));
            EXPECT_TRUE(allClose(model.accelerations(state, u, 0.0, a), reference::vec(s.at("accelerations_push")), tol));
            EXPECT_TRUE(allClose(model.derivative(state, u, f), reference::vec(s.at("derivative")), tol));
            EXPECT_TRUE(tol.close(model.kineticEnergy(state), reference::number(s.at("kinetic_energy"))));
            EXPECT_TRUE(tol.close(model.potentialEnergy(state), reference::number(s.at("potential_energy"))));
            EXPECT_TRUE(tol.close(model.horizontalMomentum(state), reference::number(s.at("horizontal_momentum"))));
            const double dt = reference::number(s.at("dt"));
            EXPECT_TRUE(allClose(model.rk4Step(state, u, f, dt), reference::vec(s.at("rk4")), tol)) << "rk4";
            EXPECT_TRUE(allClose(model.rk4Step(state, u, 0.0, dt, a), reference::vec(s.at("rk4_push")), tol));
            // Fast path = reference equations.
            Vec fast(n1);
            model.accelerationsFast(state.data(), u, f, std::nullopt, fast.data());
            EXPECT_TRUE(allClose(fast, reference::vec(s.at("accelerations")), reference::Tolerance(*data)));
            ++checked;
        }
        // initial_state comes from degrees in the JSON: compare at 1e-15 rad.
        const Eigen::VectorXd init = reference::vec(c.at("samples")[0].at("initial_state"));
        EXPECT_TRUE(model.initialState().isApprox(Vec(init), 1e-14) || (model.initialState() - Vec(init)).norm() < 1e-15);
    }
    EXPECT_EQ(checked, 54);
}

TEST(Physics, EnergyIsConservedWithoutFriction) {
    PlantParams p;
    p.segments = {SegmentParams{}, SegmentParams{}};
    for (auto& s : p.segments) s.joint_viscous_friction = 0.0;
    p.cart_viscous_friction = 0.0;
    p.segments[0].initial_angle = 0.3;
    p.segments[1].initial_angle = -0.2;
    const MultiLinkPendulum m(p);
    Vec s = m.initialState();
    const double e0 = m.totalEnergy(s);
    for (int i = 0; i < 2000; ++i) s = m.rk4Step(s, 0.0, 0.0, 0.0005);
    EXPECT_NEAR(m.totalEnergy(s), e0, 1e-7 * std::abs(e0) + 1e-9);
}

TEST(Physics, ValidationRejectsBadParameters) {
    PlantParams p;
    p.segments[0].mass = 0.0;
    EXPECT_THROW(MultiLinkPendulum{p}, std::invalid_argument);
    p = PlantParams{};
    p.segments.assign(4, SegmentParams{});
    EXPECT_THROW(MultiLinkPendulum{p}, std::invalid_argument);
}

TEST(Physics, ActuatorLagRateLimitAndSaturation) {
    ActuatorParams ap;
    ap.max_output = 10.0;
    ap.time_constant = 0.0;
    ap.rate_limit = 1000.0;
    Actuator a(ap);
    EXPECT_DOUBLE_EQ(a.step(50.0, 0.001), 1.0);  // slew limited
    ap.rate_limit = 0.0;
    Actuator b(ap);
    EXPECT_DOUBLE_EQ(b.step(50.0, 0.001), 10.0);  // saturated
    ap.deadband = 0.5;
    Actuator c(ap);
    EXPECT_DOUBLE_EQ(c.step(0.4, 0.001), 0.0);
}

TEST(Physics, KinematicsTipOfAStraightPendulum) {
    PlantParams p;
    p.segments = {SegmentParams{}, SegmentParams{}};
    const MultiLinkPendulum m(p);
    Vec s = Vec::Zero(m.stateSize());
    s[0] = 0.25;
    const auto k = m.kinematics(s);
    EXPECT_DOUBLE_EQ(k.tip().x(), 0.25);
    EXPECT_DOUBLE_EQ(k.tip().y(), 1.0);
}

}  // namespace
