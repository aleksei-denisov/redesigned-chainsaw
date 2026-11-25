#include <gtest/gtest.h>
#include <Eigen/Dense>

#include "pendulum/version.hpp"

TEST(Smoke, CoreVersion) { EXPECT_EQ(pendulum::coreVersion(), "0.1.0"); }

TEST(Smoke, DefaultModeIsV2) {
    EXPECT_EQ(pendulum::kDefaultCoreMode, pendulum::CoreMode::V2);
}

TEST(Smoke, EigenSolvesSpdSystem) {
    Eigen::Matrix2d m;
    m << 4, 1, 1, 3;
    Eigen::Vector2d x = m.llt().solve(Eigen::Vector2d(1, 2));
    EXPECT_NEAR((m * x - Eigen::Vector2d(1, 2)).norm(), 0.0, 1e-14);
}
