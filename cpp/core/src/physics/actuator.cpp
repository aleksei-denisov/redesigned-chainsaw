#include "pendulum/physics/actuator.hpp"

#include <algorithm>
#include <cmath>

namespace pendulum {

double Actuator::target(double command) const {
    if (std::abs(command) < params.deadband) command = 0.0;
    return std::max(-params.max_output, std::min(params.max_output, command));
}

double Actuator::step(double command, double dt) {
    const double tgt = target(command);
    double next;
    if (params.time_constant > 0.0) {
        // Exact discretisation of τ·u̇ = target − u over one step.
        const double alpha = 1.0 - std::exp(-dt / params.time_constant);
        next = output + alpha * (tgt - output);
    } else {
        next = tgt;
    }
    if (params.rate_limit > 0.0) {
        const double max_delta = params.rate_limit * dt;
        next = output + std::max(-max_delta, std::min(max_delta, next - output));
    }
    output = std::max(-params.max_output, std::min(params.max_output, next));
    return output;
}

}  // namespace pendulum
