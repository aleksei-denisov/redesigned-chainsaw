#pragma once
// Linearisation at the upright and LQR seeding of the multi-loop PID gains (control/lqr_tuning.py).
#include <vector>

#include <Eigen/Dense>

#include "pendulum/control/multiloop.hpp"
#include "pendulum/physics/params.hpp"

namespace pendulum {

struct LQRWeights {
    double cart_position = 10.0;
    double angle = 100.0;
    double cart_velocity = 1.0;
    double angular_velocity = 1.0;
    double effort = 0.1;
};

struct Linearization {
    Eigen::MatrixXd A;           ///< ṡ ≈ A s + B u, cart states removed for a fixed base
    Eigen::MatrixXd B;           ///< column vector
    std::vector<int> kept;       ///< rows of A → indices in the full state
};

/// Central differences of the reference derivative at the upright (eps as in Python).
Linearization linearize(const PlantParams& plant, double eps = 1e-6);

struct LQRGain {
    Eigen::VectorXd K;           ///< u = −K s_kept
    std::vector<int> kept;
};
LQRGain lqrGain(const PlantParams& plant, const LQRWeights& weights = {});

/// Multi-loop controller whose PD gains reproduce the LQR design: Kp = −σ K_pos, Kd = −σ K_vel
/// (σ = +1 direct, −1 reverse). Non-gain settings and Ki come from `base` when given.
ControllerConfig lqrController(const PlantParams& plant, const LQRWeights& weights = {},
                               const ControllerConfig* base = nullptr);

/// Direct action for a cart (drive towards the lean), reverse for a base motor.
ControlAction defaultAction(const PlantParams& plant);

}  // namespace pendulum
