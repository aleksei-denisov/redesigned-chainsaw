#pragma once
// Headless experiment runs for controller comparison (simulation/experiment.py).
// Deterministic: the same configuration (incl. the noise seed) always gives the same result.
#include <functional>
#include <map>
#include <string>
#include <vector>

#include "pendulum/simulation/config.hpp"
#include "pendulum/simulation/metrics.hpp"
#include "pendulum/simulation/telemetry.hpp"

namespace pendulum {

using ExperimentProgress = std::function<bool(double)>;  ///< fraction done → false cancels

struct ExperimentResult {
    std::string name;
    AppConfig config;
    std::map<std::string, std::vector<double>> data;  ///< telemetry by column name
    RunMetrics metrics;
    bool diverged = false;
    std::string message;
    double created = 0.0;  ///< Unix time [s]
    bool visible = true;   ///< shown in comparison plots (UI state)

    int nSegments() const { return config.plant.nSegments(); }
    std::vector<double> angleDeg(int segment) const;
    std::vector<double> errorDeg(int segment) const;
    std::string gainsSummary() const;
};

/// Angle minus the setpoint of the matching enabled loop (0 if none), wrapped [rad].
std::vector<double> angleError(const AppConfig& config, const std::map<std::string, std::vector<double>>& data,
                               int segment);

ExperimentResult runExperiment(const AppConfig& config, const std::string& name = "Experiment",
                               const ExperimentProgress& progress = {});

/// CSV in the app's format (angles in degrees, 6 significant digits).
void exportCsv(const ExperimentResult& result, const std::string& path);
/// Experiments (configuration, metrics and time series) as JSON, compatible with the Python app.
void saveExperiments(const std::vector<ExperimentResult>& results, const std::string& path);
std::vector<ExperimentResult> loadExperiments(const std::string& path);  ///< throws std::invalid_argument

}  // namespace pendulum
