#pragma once
// Physical parameters (mirrors pendulum_sim/physics/params.py). SI units, radians.
#include <string>
#include <vector>

#include "pendulum/types.hpp"

namespace pendulum {

/// How the first joint is supported and actuated.
enum class BaseType {
    Cart,   ///< joint on a cart on a horizontal track; the actuator pushes the cart [N]
    Fixed,  ///< joint fixed in space; the actuator is a base torque motor [N·m]
};

std::string toString(BaseType b);
BaseType baseTypeFromString(const std::string& s);  ///< throws std::invalid_argument

/// One rigid segment (link).
struct SegmentParams {
    double length = 0.5;                       ///< lower to upper joint [m]
    double mass = 0.2;                         ///< [kg]
    double com_ratio = 0.5;                    ///< centre of mass as a fraction of length, from the lower joint
    double inertia = 0.2 * 0.5 * 0.5 / 12.0;   ///< about the centre of mass [kg·m²]
    double joint_viscous_friction = 0.0005;    ///< joint below this segment [N·m·s/rad]
    double joint_coulomb_friction = 0.0;       ///< [N·m]
    double initial_angle = 0.0;                ///< absolute, from the upward vertical [rad]
    double initial_velocity = 0.0;             ///< [rad/s]

    double comDistance() const { return com_ratio * length; }
    static double rodInertia(double mass, double length) { return mass * length * length / 12.0; }
    void validate(int index = 0) const;        ///< throws std::invalid_argument
};

/// Actuator: force [N] for a cart base, torque [N·m] for a fixed base.
struct ActuatorParams {
    double max_output = 30.0;     ///< |u| <= max_output
    double rate_limit = 0.0;      ///< slew rate [units/s], 0 = off
    double time_constant = 0.005; ///< first-order lag [s], 0 = ideal
    double deadband = 0.0;        ///< |command| below this gives no output
    void validate() const;
};

struct PlantParams {
    BaseType base_type = BaseType::Cart;
    double gravity = 9.81;
    double cart_mass = 1.0;
    double cart_viscous_friction = 0.1;
    double track_length = 4.0;
    std::vector<SegmentParams> segments{SegmentParams{}};
    ActuatorParams actuator{};

    int nSegments() const { return static_cast<int>(segments.size()); }
    bool hasCart() const { return base_type == BaseType::Cart; }
    void validate() const;
};

}  // namespace pendulum
