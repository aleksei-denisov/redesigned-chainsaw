#pragma once
// Application configuration (simulation/config.py). JSON: see pendulum/io/config_json.hpp.
#include <cstdint>
#include <string>

#include "pendulum/control/multiloop.hpp"
#include "pendulum/physics/params.hpp"

namespace pendulum {

inline constexpr int kConfigVersion = 1;

/// Numerical and sensor settings.
struct SimulationSettings {
    double dt = 0.001;                    ///< fixed physics step [s]
    double sensor_noise_angle_deg = 0.0;  ///< std-dev of angle noise [deg]
    double sensor_noise_rate_dps = 0.0;   ///< std-dev of angular-rate noise [deg/s]
    double sensor_noise_position = 0.0;   ///< std-dev of cart position noise [m]
    std::int64_t noise_seed = 1;          ///< same seed → identical run (on every platform)
    double telemetry_rate_hz = 200.0;
    void validate() const;
};

/// Horizontal force pulse at the pendulum tip during experiments.
struct DisturbanceSettings {
    bool enabled = false;
    double time = 2.0;
    double force = 2.0;
    double duration = 0.1;
};

struct ExperimentSettings {
    double duration = 8.0;
    double settle_tolerance_deg = 1.0;
    DisturbanceSettings disturbance{};
    void validate() const;
};

struct AppConfig {
    std::string name = "Custom configuration";
    std::string description;
    PlantParams plant{};
    ControllerConfig controller{};
    SimulationSettings simulation{};
    ExperimentSettings experiment{};
    double push_force = 1.0;  ///< N, default push of the Push buttons (0.1 s pulse)
    void validate() const;    ///< throws std::invalid_argument
};

}  // namespace pendulum
