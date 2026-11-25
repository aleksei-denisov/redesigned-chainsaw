#include "pendulum/simulation/config.hpp"

#include <algorithm>
#include <stdexcept>

namespace pendulum {

void SimulationSettings::validate() const {
    if (!(dt >= 1e-5 && dt <= 0.01))
        throw std::invalid_argument("physics time step must be between 0.01 ms and 10 ms");
    if (std::min({sensor_noise_angle_deg, sensor_noise_rate_dps, sensor_noise_position}) < 0)
        throw std::invalid_argument("sensor noise must not be negative");
    if (!(telemetry_rate_hz >= 1.0 && telemetry_rate_hz <= 2000.0))
        throw std::invalid_argument("telemetry rate must be between 1 and 2000 Hz");
}

void ExperimentSettings::validate() const {
    if (!(duration >= 0.1 && duration <= 600.0))
        throw std::invalid_argument("experiment duration must be between 0.1 s and 600 s");
    if (settle_tolerance_deg <= 0) throw std::invalid_argument("settling tolerance must be positive");
}

void AppConfig::validate() const {
    plant.validate();
    controller.validate();
    simulation.validate();
    experiment.validate();
    if (!(push_force > 0.0 && push_force <= 10000.0)) throw std::invalid_argument("push force must be positive");
}

}  // namespace pendulum
