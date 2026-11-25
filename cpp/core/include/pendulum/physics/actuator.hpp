#pragma once
// First-order actuator with saturation, slew-rate limit and deadband (physics/actuator.py).
#include "pendulum/physics/params.hpp"

namespace pendulum {

class Actuator {
public:
    explicit Actuator(const ActuatorParams& p = {}) : params(p) {}
    void reset() { output = 0.0; }
    double target(double command) const;     ///< deadband, then saturation
    double step(double command, double dt);  ///< advance one physics step, returns the output

    ActuatorParams params;
    double output = 0.0;
};

}  // namespace pendulum
