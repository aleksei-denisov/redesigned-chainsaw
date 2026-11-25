#pragma once
// Plant and controller ↔ the JSON configuration format of the Python app
// (simulation/config.py: angles and angular velocities in degrees, gains SI).
#include <nlohmann/json.hpp>

#include "pendulum/control/multiloop.hpp"
#include "pendulum/physics/params.hpp"
#include "pendulum/simulation/config.hpp"
#include "pendulum/simulation/metrics.hpp"

namespace pendulum::io {

/// Reads the "plant" object; missing keys take the Python defaults. Throws std::invalid_argument.
PlantParams plantFromJson(const nlohmann::json& plant);
/// Reads the "controller" object (defaults depend on the plant, as in Python).
ControllerConfig controllerFromJson(const nlohmann::json& controller, const PlantParams& plant);
PIDLoopConfig loopFromJson(const nlohmann::json& loop);

nlohmann::json toJson(const PlantParams& plant);
nlohmann::json toJson(const ControllerConfig& controller);
nlohmann::json toJson(const PIDLoopConfig& loop);

/// Whole configuration (config_from_dict / config_to_dict); validates, throws std::invalid_argument.
AppConfig appConfigFromJson(const nlohmann::json& data);
nlohmann::json toJson(const AppConfig& config);
AppConfig loadConfig(const std::string& path);
void saveConfig(const AppConfig& config, const std::string& path);  ///< indented JSON + newline

nlohmann::json toJson(const RunMetrics& metrics);  ///< same keys as RunMetrics.to_dict()
RunMetrics runMetricsFromJson(const nlohmann::json& data);

}  // namespace pendulum::io
