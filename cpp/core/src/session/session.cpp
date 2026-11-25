#include "pendulum/session/session.hpp"

#include <chrono>

#include "pendulum/session/rules.hpp"

namespace pendulum::session {

const char* operationName(const Operation& o) {
    static const char* names[] = {"config", "structure", "plant", "controller", "simulation", "reset_integrators"};
    return names[o.index()];
}

std::vector<Operation> Session::load(AppConfig c) {
    prepareConfig(c);
    config = c;
    original_controller = c.controller;
    original_plant = c.plant;
    original_simulation = c.simulation;
    return {op::Config{c, std::nullopt}};
}

PlantChange Session::changePlant(const PlantParams& plant, bool structural, const ControllerConfig* edited) {
    plant.validate();
    PlantChange out;
    if (structural) {
        ControllerConfig ctrl = edited ? *edited : config.controller;
        controllerForStructure(ctrl, plant, config.plant);
        config.plant = plant;
        config.controller = ctrl;
        out.operations.push_back(op::Structure{plant, ctrl});
        out.structural = true;
        out.controller = ctrl;
        return out;
    }
    out.reset_if_idle = initialConditionsChanged(plant, config.plant);
    config.plant = plant;
    out.operations.push_back(op::Plant{plant});
    return out;
}

PlantChange Session::restoreModel(const PlantParams& plant, const SimulationSettings& simulation,
                                  const ControllerConfig& controller) {
    plant.validate();
    simulation.validate();
    PlantChange out;
    const auto& s = config.simulation;
    const bool sim_changed = simulation.dt != s.dt || simulation.sensor_noise_angle_deg != s.sensor_noise_angle_deg ||
                             simulation.sensor_noise_rate_dps != s.sensor_noise_rate_dps ||
                             simulation.sensor_noise_position != s.sensor_noise_position ||
                             simulation.noise_seed != s.noise_seed || simulation.telemetry_rate_hz != s.telemetry_rate_hz;
    if (sim_changed) {
        config.simulation = simulation;
        out.operations.push_back(op::Simulation{simulation});
    }
    if (!sameStructure(plant, config.plant)) {
        const ControllerConfig ctrl = restoredController(controller, plant);
        config.plant = plant;
        config.controller = ctrl;
        out.operations.push_back(op::Structure{plant, ctrl});
        out.structural = true;
        out.controller = ctrl;
        return out;
    }
    out.reset_if_idle = initialConditionsChanged(plant, config.plant);
    config.plant = plant;
    out.operations.push_back(op::Plant{plant});
    return out;
}

std::vector<Operation> Session::restoreController(const ControllerConfig& controller) {
    return changeController(restoredController(controller, config.plant));
}

std::vector<Operation> Session::changeController(const ControllerConfig& controller) {
    controller.validate();
    config.controller = controller;
    return {op::Controller{controller}};
}

std::pair<ControllerConfig, std::vector<Operation>> Session::seedLqr(const LQRWeights& weights,
                                                                     const ControllerConfig* base) {
    const ControllerConfig ctrl = lqrController(config.plant, weights, base ? base : &config.controller);
    return {ctrl, changeController(ctrl)};
}

std::pair<ControllerConfig, std::vector<Operation>> Session::restoreGains() {
    const ControllerConfig ctrl = restoredController(original_controller, config.plant);
    return {ctrl, changeController(ctrl)};
}

std::pair<ControllerConfig, std::vector<Operation>> Session::useRunGains(const AppConfig& run) {
    const ControllerConfig ctrl = controllerFromRun(run, config.plant);
    return {ctrl, changeController(ctrl)};
}

std::vector<Operation> Session::applyTuning(const TuningResult& result) {
    auto ops = changeController(result.controller);
    config.push_force = result.recommended_push;
    return ops;
}

std::vector<Operation> Session::changeSimulation(const SimulationSettings& settings) {
    settings.validate();
    config.simulation = settings;
    return {op::Simulation{settings}};
}

void apply(std::unique_ptr<Simulator>& sim, const Operation& operation, std::optional<double> default_window) {
    if (const auto* c = std::get_if<op::Config>(&operation)) {
        sim = std::make_unique<Simulator>(c->config, c->telemetry_window_s ? c->telemetry_window_s : default_window);
        return;
    }
    if (!sim) throw std::logic_error("no simulator to apply the operation to");
    std::visit(
        [&](const auto& o) {
            using T = std::decay_t<decltype(o)>;
            if constexpr (std::is_same_v<T, op::Structure>)
                applyStructure(*sim, o.plant, o.controller);
            else if constexpr (std::is_same_v<T, op::Plant>)
                applyPlant(*sim, o.plant);
            else if constexpr (std::is_same_v<T, op::Controller>)
                applyController(*sim, o.controller);
            else if constexpr (std::is_same_v<T, op::Simulation>)
                applySimulationSettings(*sim, o.settings);
            else if constexpr (std::is_same_v<T, op::ResetIntegrators>)
                resetIntegrators(*sim);
        },
        operation);
}

void applyStructure(Simulator& sim, const PlantParams& plant, const ControllerConfig& controller) {
    sim.mutableConfig().plant = plant;
    sim.mutableConfig().controller = controller;
    sim.updateController(controller);
    sim.reset();
}

bool applyPlant(Simulator& sim, const PlantParams& plant) { return sim.updatePlant(plant); }
void applyController(Simulator& sim, const ControllerConfig& controller) { sim.updateController(controller); }
void applySimulationSettings(Simulator& sim, const SimulationSettings& settings) {
    sim.mutableConfig().simulation = settings;
}
void resetIntegrators(Simulator& sim) { sim.resetIntegrators(); }

StepsResult runSteps(Simulator& sim, int n, std::optional<double> deadline) {
    auto now = [] { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); };
    StepsResult r;
    try {
        while (r.done < n) {
            const int k = std::min(25, n - r.done);
            for (int i = 0; i < k; ++i) sim.step();
            r.done += k;
            if (deadline && now() > *deadline) break;
        }
    } catch (const SimulationDiverged& e) {
        r.diverged = e.what();
    }
    return r;
}

StepsResult singleStep(Simulator& sim) {
    return runSteps(sim, singleStepCount(sim.controller().period(), sim.config().simulation.dt));
}

}  // namespace pendulum::session
