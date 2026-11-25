#pragma once
// Plant and controller ↔ the JSON configuration format of the Python app
// (simulation/config.py: angles and angular velocities in degrees, gains SI).
// The full AppConfig (simulation, view, experiment settings) follows in Phase 4.
#include <nlohmann/json.hpp>

#include "pendulum/control/multiloop.hpp"
#include "pendulum/physics/params.hpp"

namespace pendulum::io {

/// Reads the "plant" object; missing keys take the Python defaults. Throws std::invalid_argument.
PlantParams plantFromJson(const nlohmann::json& plant);
/// Reads the "controller" object (defaults depend on the plant, as in Python).
ControllerConfig controllerFromJson(const nlohmann::json& controller, const PlantParams& plant);
PIDLoopConfig loopFromJson(const nlohmann::json& loop);

nlohmann::json toJson(const PlantParams& plant);
nlohmann::json toJson(const ControllerConfig& controller);
nlohmann::json toJson(const PIDLoopConfig& loop);

}  // namespace pendulum::io
