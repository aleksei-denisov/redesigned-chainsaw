#include "pendulum/simulation/realtime.hpp"

#include <algorithm>

namespace pendulum {

RealtimeStepper::RealtimeStepper(double budget, double max_frame, Clock clock)
    : budget_s(budget), max_frame_s(max_frame), clock_(std::move(clock)) {
    if (!clock_)
        clock_ = [] {
            return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
        };
}

int RealtimeStepper::tick(Simulator& sim, double wall_elapsed, double speed) {
    const double wall = std::min(std::max(wall_elapsed, 0.0), max_frame_s);
    if (wall <= 0.0) return 0;
    const double started = clock_();
    const double deadline = started + budget_s;
    const double dt = sim.config().simulation.dt;
    accum_ += wall * speed;
    const int wanted = static_cast<int>(accum_ / dt);
    int done = 0;
    while (done < wanted) {
        const int k = std::min(kStepChunk, wanted - done);
        for (int i = 0; i < k; ++i) sim.step();
        done += k;
        if (clock_() > deadline) break;
    }
    if (done < wanted)
        accum_ = 0.0;  // cannot keep up: drop the backlog
    else
        accum_ -= done * dt;
    realtime_factor = 0.9 * realtime_factor + 0.1 * (done * dt / wall);
    last_ms = (clock_() - started) * 1000.0;
    physics_ms = 0.9 * physics_ms + 0.1 * last_ms;
    return done;
}

}  // namespace pendulum
