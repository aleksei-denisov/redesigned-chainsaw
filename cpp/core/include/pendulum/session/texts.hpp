#pragma once
// Texts every interface shows the same way (session/texts.py).
#include <optional>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "pendulum/simulation/experiment.hpp"
#include "pendulum/simulation/tuning.hpp"

namespace pendulum::session::texts {

struct Preset {
    std::string key, title, note;
};
const std::vector<Preset>& tunePresets();  ///< quick / normal / thorough

inline const char* kStructureChanged =
    "Structure changed – simulation reset. Use 'Seed gains from LQR' in the PID panel to obtain stabilising gains.";
inline const char* kLqrSeeded = "Gains seeded from LQR. Reset/Restart to try them from the initial conditions.";
inline const char* kGainsRestored = "Original gains restored";

/// Python-style number formatting: fmt(1.23456, ".3g") → "1.23".
std::string fmt(double value, const char* spec);

std::string tuningApplied(const TuningResult& result);
std::string fmtDuration(std::optional<double> seconds);
std::string loopName(const std::string& signal);
std::string gainsText(const ControllerConfig* ctrl);
std::string statusText(const ExperimentResult& r);  ///< diverged / fell / stable
std::vector<std::pair<std::string, std::string>> metricPairs(const ExperimentResult& r);

std::string tuneEstimate(const std::string& preset, std::optional<double> push_test_s, int workers);
std::string tuneCores(int workers, int available);
std::string tuneActivity(const TuneProgress& u);
std::pair<std::string, std::string> tuneBest(const TuneProgress& u);  ///< (line, may contain <b>; gains)
double tuneFraction(const TuneProgress& u, double since_update);
std::string tuneTimes(double elapsed, const TuneProgress* u, double since_update);
std::string tuneHeadline(const TuningResult& result);
std::string tuneNotes(const TuningResult& result);
std::vector<std::tuple<std::string, std::string, std::string>> tuneMetricRows(
    const std::pair<ExperimentResult, ExperimentResult>& comparison);
/// Saturation note for an experiment (improvement 2); empty when below 10 %.
std::string saturationNote(const RunMetrics& m);

}  // namespace pendulum::session::texts
