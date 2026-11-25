#include "pendulum/physics/params.hpp"

#include <stdexcept>

namespace pendulum {

std::string toString(BaseType b) { return b == BaseType::Cart ? "cart" : "fixed"; }

BaseType baseTypeFromString(const std::string& s) {
    if (s == "cart") return BaseType::Cart;
    if (s == "fixed") return BaseType::Fixed;
    throw std::invalid_argument("'" + s + "' is not a valid BaseType");
}

void SegmentParams::validate(int index) const {
    const std::string name = "segment " + std::to_string(index + 1);
    if (length <= 0) throw std::invalid_argument(name + ": length must be positive");
    if (mass <= 0) throw std::invalid_argument(name + ": mass must be positive");
    if (!(com_ratio >= 0.0 && com_ratio <= 1.0))
        throw std::invalid_argument(name + ": centre of mass ratio must be within [0, 1]");
    if (inertia < 0) throw std::invalid_argument(name + ": inertia must not be negative");
    if (joint_viscous_friction < 0 || joint_coulomb_friction < 0)
        throw std::invalid_argument(name + ": friction coefficients must not be negative");
}

void ActuatorParams::validate() const {
    if (max_output <= 0) throw std::invalid_argument("actuator: max output must be positive");
    if (rate_limit < 0 || time_constant < 0 || deadband < 0)
        throw std::invalid_argument("actuator: rate limit, time constant and deadband must not be negative");
}

void PlantParams::validate() const {
    if (nSegments() < 1 || nSegments() > kMaxSegments)
        throw std::invalid_argument("number of segments must be between 1 and " + std::to_string(kMaxSegments));
    if (gravity < 0) throw std::invalid_argument("gravity must not be negative");
    if (hasCart()) {
        if (cart_mass <= 0) throw std::invalid_argument("cart mass must be positive");
        if (track_length <= 0) throw std::invalid_argument("track length must be positive");
        if (cart_viscous_friction < 0) throw std::invalid_argument("cart friction must not be negative");
    }
    for (int i = 0; i < nSegments(); ++i) segments[i].validate(i);
    actuator.validate();
}

}  // namespace pendulum
