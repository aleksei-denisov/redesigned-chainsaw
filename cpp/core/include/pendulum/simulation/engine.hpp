#pragma once
// Fixed-step simulation engine (simulation/engine.py). One step() advances exactly dt:
//   sensors (+noise) → controller (own rate, ZOH) → actuator dynamics → RK4 of the plant.
#include <optional>
#include <stdexcept>
#include <vector>

#include "pendulum/control/multiloop.hpp"
#include "pendulum/physics/actuator.hpp"
#include "pendulum/physics/model.hpp"
#include "pendulum/simulation/config.hpp"
#include "pendulum/simulation/rng.hpp"
#include "pendulum/simulation/telemetry.hpp"

namespace pendulum {

/// |state| beyond this (or a non-finite state) counts as numerical divergence.
inline constexpr double kDivergenceLimit = 1e4;

class SimulationDiverged : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct Disturbance {
    double start, end, force;
    int segment = -1;    ///< 0-based segment it acts on; −1 = the tip
    double ratio = 1.0;  ///< position along the segment (0 … 1)
    bool atTip() const { return segment < 0; }
};

class Simulator {
public:
    /// `telemetry_window_s`: seconds of telemetry kept for live display; nullopt keeps all.
    explicit Simulator(const AppConfig& config, std::optional<double> telemetry_window_s = 30.0);

    void reset();  ///< back to the configured initial conditions (time 0, clean telemetry)
    /// New physical parameters while running; false (nothing changed) if the number of
    /// segments or the base type differ – those need reset().
    bool updatePlant(const PlantParams& plant);
    void updateController(const ControllerConfig& controller);
    void resetIntegrators() { controller_.resetIntegrators(); }
    /// Horizontal force pulse [N] at the tip, or at `ratio` along `segment` (0-based) when ≥ 0.
    void applyDisturbance(double force, double duration = 0.1, std::optional<double> start = std::nullopt,
                          int segment = -1, double ratio = 1.0);

    void step();                                                           ///< throws SimulationDiverged
    int advance(double duration, std::optional<int> max_steps = std::nullopt);  ///< returns steps taken

    const AppConfig& config() const { return config_; }
    AppConfig& mutableConfig() { return config_; }  ///< e.g. dt changes between steps
    const MultiLinkPendulum& model() const { return model_; }
    const MultiLoopController& controller() const { return controller_; }
    const TelemetryRecorder& telemetry() const { return telemetry_; }
    const Vec& state() const { return state_; }
    void setState(const Vec& s) { state_ = s; }
    double time() const { return time_; }
    long steps() const { return steps_; }
    double applied() const { return applied_; }
    double tipForce() const { return tip_force_; }
    const ControllerOutput& lastControl() const { return last_control_; }
    double energy() const { return model_.totalEnergy(state_); }

    double manual_force = 0.0;  ///< added to the controller command (manual drive)

    /// Share of physics steps (0 … 1) since reset() in which the command exceeded the actuator
    /// limit or the controller clipped its output.
    double saturationFraction() const { return steps_ ? static_cast<double>(saturated_steps_) / steps_ : 0.0; }
    bool saturatedNow() const { return saturated_now_; }

private:
    void measure(Vec& q, Vec& qd);
    double tipForceNow();
    void record();

    AppConfig config_;
    TelemetryRecorder telemetry_;
    MultiLinkPendulum model_;
    Actuator actuator_;
    MultiLoopController controller_;
    Vec state_;
    double time_ = 0.0;
    long steps_ = 0;
    std::vector<Disturbance> disturbances_;
    Rng rng_;
    ControllerOutput last_control_;
    double applied_ = 0.0;
    double tip_force_ = 0.0;
    PushArms push_arms_;
    double next_record_ = 0.0;
    long saturated_steps_ = 0;
    bool saturated_now_ = false;
};

}  // namespace pendulum
