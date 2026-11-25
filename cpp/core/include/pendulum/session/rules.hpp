#pragma once
// Rules for editing the configuration of an interactive session (session/rules.py): the
// decisions every interface makes the same way. Functions taking a reference change it in
// place and return it, as the Python functions do.
#include <stdexcept>
#include <string>
#include <vector>

#include "pendulum/simulation/config.hpp"

namespace pendulum::session {

inline const std::vector<double> kSpeeds = {0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0};  ///< × real time
inline constexpr double kDragGain = 2.0;        ///< drag force = gain · push force · offset / length …
inline constexpr double kDragLimit = 4.0;       ///< … clamped to ±limit · push force
inline constexpr double kPointDragGain = 2.0;   ///< v2 point drag
inline constexpr double kPointDragLimit = 1.0;

/// A run's gains belong to a pendulum with another structure (segments or base).
class IncompatibleRun : public std::invalid_argument {
public:
    IncompatibleRun() : std::invalid_argument("That run used a different pendulum structure.") {}
};

std::vector<std::string> loopSignals(const PlantParams& plant);  ///< cart (if any), theta1, …
/// One loop per available signal (a new one disabled with zero gains); others dropped.
ControllerConfig& completeLoops(ControllerConfig& config, const PlantParams& plant);
AppConfig& prepareConfig(AppConfig& config);  ///< complete loops, validate
/// After the number of segments or the base type changed: a new base type gets the default
/// action and output limits and cleared gains; then the loops are completed.
ControllerConfig& controllerForStructure(ControllerConfig& controller, const PlantParams& plant,
                                         const PlantParams& old_plant);
bool isStructural(const PlantParams& plant, const PlantParams& old_plant);
bool sameStructure(const PlantParams& a, const PlantParams& b);
bool initialConditionsChanged(const PlantParams& plant, const PlantParams& old_plant);
ControllerConfig restoredController(const ControllerConfig& original, const PlantParams& plant);
ControllerConfig controllerFromRun(const AppConfig& run, const PlantParams& plant);  ///< throws IncompatibleRun
int singleStepCount(double controller_period, double dt);  ///< physics steps of one Step
double dragForce(double push_force, double offset);       ///< offset relative to the total length
double pointDragForce(double push_force, double offset);  ///< v2: at most the push force
std::vector<std::string> loopLabels(const ControllerConfig& controller);  ///< cart, θ1, …
double outputLimit(const AppConfig& config);
/// Configuration of a new experiment run; `reuse`: keep the first run's plant and simulation settings.
AppConfig experimentConfig(const AppConfig& config, const ExperimentSettings& settings,
                           const AppConfig* reuse = nullptr);

}  // namespace pendulum::session
