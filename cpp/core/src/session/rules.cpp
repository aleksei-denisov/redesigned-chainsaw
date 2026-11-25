#include "pendulum/session/rules.hpp"

#include <algorithm>
#include <cmath>
#include <map>

#include "pendulum/control/lqr.hpp"

namespace pendulum::session {

std::vector<std::string> loopSignals(const PlantParams& plant) {
    std::vector<std::string> s;
    if (plant.hasCart()) s.emplace_back("cart");
    for (int i = 0; i < plant.nSegments(); ++i) s.push_back("theta" + std::to_string(i + 1));
    return s;
}

ControllerConfig& completeLoops(ControllerConfig& config, const PlantParams& plant) {
    std::map<std::string, PIDLoopConfig> existing;
    for (const auto& l : config.loops) existing[l.signal] = l;  // last loop per signal, as the dict
    std::vector<PIDLoopConfig> loops;
    for (const auto& sig : loopSignals(plant)) {
        if (const auto it = existing.find(sig); it != existing.end()) {
            loops.push_back(it->second);
        } else {
            PIDLoopConfig l;
            l.name = sig == "cart" ? "Cart position" : "Segment " + sig.substr(5) + " angle";
            l.signal = sig;
            l.enabled = false;
            l.kp = l.ki = l.kd = 0.0;
            loops.push_back(l);
        }
    }
    config.loops = std::move(loops);
    return config;
}

AppConfig& prepareConfig(AppConfig& config) {
    completeLoops(config.controller, config.plant);
    config.validate();
    return config;
}

ControllerConfig& controllerForStructure(ControllerConfig& ctrl, const PlantParams& plant, const PlantParams& old_plant) {
    if (plant.base_type != old_plant.base_type) {
        ctrl.action = defaultAction(plant);
        ctrl.output_min = -plant.actuator.max_output;
        ctrl.output_max = plant.actuator.max_output;
        for (auto& l : ctrl.loops) {
            l.kp = l.ki = l.kd = 0.0;
            l.enabled = false;
        }
    }
    return completeLoops(ctrl, plant);
}

bool sameStructure(const PlantParams& a, const PlantParams& b) {
    return a.nSegments() == b.nSegments() && a.base_type == b.base_type;
}

bool isStructural(const PlantParams& plant, const PlantParams& old_plant) { return !sameStructure(plant, old_plant); }

bool initialConditionsChanged(const PlantParams& plant, const PlantParams& old_plant) {
    const auto n = std::min(plant.segments.size(), old_plant.segments.size());
    for (std::size_t i = 0; i < n; ++i)
        if (plant.segments[i].initial_angle != old_plant.segments[i].initial_angle ||
            plant.segments[i].initial_velocity != old_plant.segments[i].initial_velocity)
            return true;
    return false;
}

ControllerConfig restoredController(const ControllerConfig& original, const PlantParams& plant) {
    ControllerConfig c = original;
    completeLoops(c, plant);
    c.validate();
    return c;
}

ControllerConfig controllerFromRun(const AppConfig& run, const PlantParams& plant) {
    if (!sameStructure(run.plant, plant)) throw IncompatibleRun();
    return run.controller;
}

int singleStepCount(double period, double dt) { return std::max(1, static_cast<int>(std::nearbyint(period / dt))); }

double dragForce(double push_force, double offset) {
    const double f = kDragGain * push_force * offset;
    const double limit = kDragLimit * push_force;
    return std::min(std::max(f, -limit), limit);  // np.clip
}

double pointDragForce(double push_force, double offset) {
    const double limit = kPointDragLimit * std::abs(push_force);
    return std::min(std::max(kPointDragGain * std::abs(push_force) * offset, -limit), limit);
}

std::vector<std::string> loopLabels(const ControllerConfig& c) {
    std::vector<std::string> out;
    for (const auto& l : c.loops) out.push_back(l.signal == "cart" ? "cart" : "θ" + l.signal.substr(5));
    return out;
}

double outputLimit(const AppConfig& config) {
    const auto& c = config.controller;
    return std::max({config.plant.actuator.max_output, std::abs(c.output_min), std::abs(c.output_max)});
}

AppConfig experimentConfig(const AppConfig& config, const ExperimentSettings& settings, const AppConfig* reuse) {
    AppConfig cfg = config;
    cfg.experiment = settings;
    if (reuse) {
        cfg.plant = reuse->plant;
        cfg.simulation = reuse->simulation;
        completeLoops(cfg.controller, cfg.plant);
    }
    return cfg;
}

}  // namespace pendulum::session
