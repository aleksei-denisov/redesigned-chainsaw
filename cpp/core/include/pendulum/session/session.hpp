#pragma once
// Configuration state of one interactive session (session/session.py + operations.py).
// Session holds the configuration an interface edits and applies the rules to every edit.
// It owns no simulator: each method returns the operations the simulator must perform,
// which apply() performs (the app's simulator runs on its own thread).
#include <memory>
#include <optional>
#include <variant>
#include <vector>

#include "pendulum/control/lqr.hpp"
#include "pendulum/simulation/engine.hpp"
#include "pendulum/simulation/tuning.hpp"

namespace pendulum::session {

namespace op {
struct Config {
    AppConfig config;
    std::optional<double> telemetry_window_s;  ///< empty: the default window
};
struct Structure {
    PlantParams plant;
    ControllerConfig controller;
};
struct Plant {
    PlantParams plant;
};
struct Controller {
    ControllerConfig controller;
};
struct Simulation {
    SimulationSettings settings;
};
struct ResetIntegrators {};
}  // namespace op

using Operation = std::variant<op::Config, op::Structure, op::Plant, op::Controller, op::Simulation, op::ResetIntegrators>;
const char* operationName(const Operation& o);  ///< "config", "structure", … (as in Python)

struct PlantChange {
    std::vector<Operation> operations;
    bool structural = false;
    std::optional<ControllerConfig> controller;  ///< new controller after a structural change
    bool reset_if_idle = false;                  ///< initial pose edited: reset if stopped at t = 0
};

class Session {
public:
    explicit Session(const AppConfig& config) { load(config); }

    std::vector<Operation> load(AppConfig config);  ///< whole configuration (scenario, file); validates
    /// Physical parameters edited. `edited_controller`: the controller as currently shown,
    /// if the interface keeps unapplied edits; otherwise the session's controller.
    PlantChange changePlant(const PlantParams& plant, bool structural,
                            const ControllerConfig* edited_controller = nullptr);
    /// Snapshot of the Model screen (undo / redo / reset).
    PlantChange restoreModel(const PlantParams& plant, const SimulationSettings& simulation,
                             const ControllerConfig& controller);
    std::vector<Operation> restoreController(const ControllerConfig& controller);
    std::vector<Operation> changeController(const ControllerConfig& controller);
    std::pair<ControllerConfig, std::vector<Operation>> seedLqr(const LQRWeights& weights = {},
                                                                const ControllerConfig* base = nullptr);
    std::pair<ControllerConfig, std::vector<Operation>> restoreGains();
    std::pair<ControllerConfig, std::vector<Operation>> useRunGains(const AppConfig& run);  ///< throws IncompatibleRun
    std::vector<Operation> applyTuning(const TuningResult& result);  ///< gains + recommended push
    std::vector<Operation> changeSimulation(const SimulationSettings& settings);
    void setPushForce(double value) { config.push_force = value; }

    AppConfig config;
    ControllerConfig original_controller;
    PlantParams original_plant;
    SimulationSettings original_simulation;
};

/// Performs one operation; `sim` is replaced after op::Config (and created if empty).
void apply(std::unique_ptr<Simulator>& sim, const Operation& operation, std::optional<double> default_window = 30.0);

// ---- operations on a running simulator (operations.py)
void applyStructure(Simulator& sim, const PlantParams& plant, const ControllerConfig& controller);
bool applyPlant(Simulator& sim, const PlantParams& plant);
void applyController(Simulator& sim, const ControllerConfig& controller);
void applySimulationSettings(Simulator& sim, const SimulationSettings& settings);
void resetIntegrators(Simulator& sim);

struct StepsResult {
    int done = 0;
    std::optional<std::string> diverged;  ///< the divergence message, if any
};
/// Up to n steps in batches of 25, stopping early after `deadline` (steady-clock seconds).
StepsResult runSteps(Simulator& sim, int n, std::optional<double> deadline = std::nullopt);
StepsResult singleStep(Simulator& sim);  ///< one controller period

}  // namespace pendulum::session
