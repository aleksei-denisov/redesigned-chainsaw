#pragma once
// Multibody dynamics of an n-link (n = 1..3) pendulum on a cart or a fixed pivot.
// Mirrors pendulum_sim/physics/model.py (reference equations, see its docstring) and
// fast_dynamics.py (the real-time path, same operation order).
//
// q = [x, θ1..θn] (θ absolute from the upward vertical, positive towards +x), s = [q, q̇].
// M(q) q̈ + C(q, q̇) = G(q) + Q.
#include <array>
#include <optional>

#include "pendulum/physics/params.hpp"
#include "pendulum/types.hpp"

namespace pendulum {

/// Velocity scale of the smooth Coulomb friction tanh(φ̇/ε) [rad/s].
inline constexpr double kCoulombSmoothing = 0.02;

/// Positions of cart/pivot, joints (lower joint of each segment, then the tip) and centres of mass.
struct Kinematics {
    Eigen::Vector2d cart;
    std::array<Eigen::Vector2d, kMaxCoords> joints;
    std::array<Eigen::Vector2d, kMaxSegments> coms;
    int n = 0;
    const Eigen::Vector2d& tip() const { return joints[n]; }
};

/// Per-angle Σ F·a_j of pushes acting below the tip (see pushArms); empty = push at the tip.
using PushArms = std::optional<std::array<double, kMaxSegments>>;

class MultiLinkPendulum {
public:
    explicit MultiLinkPendulum(const PlantParams& params);

    void setParams(const PlantParams& params);  ///< validates; throws std::invalid_argument
    const PlantParams& params() const { return params_; }

    int n() const { return n_; }
    bool hasCart() const { return params_.hasCart(); }
    int stateSize() const { return 2 * (n_ + 1); }
    double totalMass() const { return total_mass_; }
    double stopStiffness() const { return stop_stiffness_; }
    double stopDamping() const { return stop_damping_; }

    Vec initialState() const;

    // ---- reference equations (clear, used for linearisation, energies and checks)
    MatQ massMatrix(const Vec& q) const;
    Vec biasForces(const Vec& q, const Vec& qd) const;         ///< C(q, q̇) − G(q)
    Vec jointFrictionTorques(const Vec& qd) const;             ///< length n
    /// Lever arms of a horizontal force at `ratio` (0..1) along `segment` (0-based; −1 or out of range: tip).
    std::array<double, kMaxSegments> pushArms(int segment, double ratio) const;
    Vec generalizedForces(const Vec& q, const Vec& qd, double actuator, double tip_force,
                          const PushArms& arms = std::nullopt) const;
    Vec accelerations(const Vec& state, double actuator = 0.0, double tip_force = 0.0,
                      const PushArms& arms = std::nullopt) const;
    Vec derivative(const Vec& state, double actuator = 0.0, double tip_force = 0.0,
                   const PushArms& arms = std::nullopt) const;

    // ---- fast path for the simulation loop (same equations, scalar code, Cholesky)
    /// q̈ written to `qdd` (size n+1). Throws std::domain_error if M is not positive definite.
    void accelerationsFast(const double* s, double u, double f, const PushArms& arms, double* qdd) const;
    /// One classical RK4 step with inputs held constant.
    Vec rk4Step(const Vec& state, double actuator, double tip_force, double dt,
                const PushArms& arms = std::nullopt) const;

    // ---- energetics and kinematics
    double kineticEnergy(const Vec& state) const;
    double potentialEnergy(const Vec& state) const;            ///< relative to the base height [J]
    double totalEnergy(const Vec& state) const { return kineticEnergy(state) + potentialEnergy(state); }
    double horizontalMomentum(const Vec& state) const;
    Kinematics kinematics(const Vec& state) const;

private:
    double endStopForce(double x, double xd) const;

    PlantParams params_;
    int n_ = 1;
    std::array<double, kMaxSegments> L_{}, m_{}, c_{}, I_{}, visc_{}, coul_{}, h_{};
    std::array<std::array<double, kMaxSegments>, kMaxSegments> W_{};
    double total_mass_ = 0.0;
    double stop_stiffness_ = 0.0;
    double stop_damping_ = 0.0;
};

}  // namespace pendulum
