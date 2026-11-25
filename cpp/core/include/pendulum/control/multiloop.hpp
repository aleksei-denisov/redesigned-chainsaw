#pragma once
// Multi-loop (parallel) PID controller with one actuator (control/multiloop.py):
// u = sat(Σ_k PID_k(e_k)), sampled at frequency_hz with zero-order hold.
#include <vector>

#include "pendulum/control/pid.hpp"
#include "pendulum/types.hpp"

namespace pendulum {

struct ControllerConfig {
    bool enabled = true;
    double frequency_hz = 500.0;
    double output_min = -30.0;
    double output_max = 30.0;
    ControlAction action = ControlAction::Direct;
    std::vector<PIDLoopConfig> loops{PIDLoopConfig{}};
    void validate() const;  ///< throws std::invalid_argument
};

struct ControllerOutput {
    double command = 0.0;
    double unsaturated = 0.0;
    std::vector<LoopOutput> loops;
    bool updated = false;
};

class MultiLoopController {
public:
    explicit MultiLoopController(const ControllerConfig& config);

    /// Apply a new configuration keeping integrator states (bumpless). A loop keeps its
    /// state when the loop at the same position, or else any loop, has the same signal.
    void setConfig(const ControllerConfig& config);
    const ControllerConfig& config() const { return config_; }

    double direction() const { return config_.action == ControlAction::Direct ? 1.0 : -1.0; }
    double period() const { return 1.0 / config_.frequency_hz; }

    void reset();
    void resetIntegrators();
    bool sampleDue(double t) const { return t + 1e-12 >= next_sample_; }
    /// Held command; a new one is computed when a sample is due. q, qd: generalised coordinates.
    const ControllerOutput& step(double t, const Vec& q, const Vec& qd);

    std::vector<PIDLoop>& loops() { return loops_; }
    const std::vector<PIDLoop>& loops() const { return loops_; }
    const ControllerOutput& output() const { return output_; }

private:
    ControllerConfig config_;
    std::vector<PIDLoop> loops_;
    double next_sample_ = 0.0;
    ControllerOutput output_;
};

}  // namespace pendulum
