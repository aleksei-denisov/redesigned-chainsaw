#pragma once
// Real-time pacing of the fixed-step simulator (simulation/realtime.py): converts
// wall time × speed into whole dt steps, carries the remainder, clamps long frames and
// limits physics work per frame (drops the backlog instead of freezing the display).
#include <chrono>
#include <functional>

#include "pendulum/simulation/engine.hpp"

namespace pendulum {

class RealtimeStepper {
public:
    using Clock = std::function<double()>;  ///< seconds
    static constexpr int kStepChunk = 25;   ///< physics steps between deadline checks

    explicit RealtimeStepper(double budget_s = 0.010, double max_frame_s = 0.1, Clock clock = {});

    void resetClock() { accum_ = 0.0; }
    /// Advance `sim` for one displayed frame; returns the number of physics steps.
    int tick(Simulator& sim, double wall_elapsed, double speed = 1.0);

    double budget_s, max_frame_s;
    double realtime_factor = 0.0;  ///< smoothed simulated / wall time
    double physics_ms = 0.0;       ///< smoothed physics time per frame
    double last_ms = 0.0;

private:
    Clock clock_;
    double accum_ = 0.0;
};

}  // namespace pendulum
