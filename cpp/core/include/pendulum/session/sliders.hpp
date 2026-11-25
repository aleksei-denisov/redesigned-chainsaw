#pragma once
// Slider mappings and − / + steps of the parameter editors (session/sliders.py):
// linear; log (positive, decades); symlog (signed, linear near zero, log beyond).
#include <optional>
#include <string>

namespace pendulum::session {

double symlogScale(double minimum, double maximum, int decimals);
double toPosition(double v, double minimum, double maximum, const std::string& mapping = "linear", int decimals = 3);
double fromPosition(double t, double minimum, double maximum, const std::string& mapping = "linear", int decimals = 3);
/// The value after one tap on − (direction −1) or + (+1), not yet clamped.
double nudge(double v, int direction, double minimum, double maximum, const std::string& mapping = "linear",
             int decimals = 3, std::optional<double> step = std::nullopt);

}  // namespace pendulum::session
