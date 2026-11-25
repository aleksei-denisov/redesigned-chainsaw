#pragma once
// Built-in demonstration scenarios (simulation/scenarios.py), same keys and values.
#include <string>
#include <vector>

#include "pendulum/control/lqr.hpp"
#include "pendulum/simulation/config.hpp"

namespace pendulum {

/// Keys in display order: single_cart_pid, single_cart_full, …, hanging_damped.
const std::vector<std::string>& scenarioKeys();
inline const char* kDefaultScenario = "single_cart_full";
/// Fresh, validated configuration; throws std::out_of_range for an unknown key.
AppConfig scenario(const std::string& key);
/// LQR weights found by the robust auto-tuner for the built-in systems.
LQRWeights tunedWeights(const std::string& system);  ///< single_cart, double_cart, double_fixed, triple_cart

}  // namespace pendulum
