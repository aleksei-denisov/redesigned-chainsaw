// Speed of the physics step and the controller (Release build): steps per second.
#include <chrono>
#include <cstdio>

#include "pendulum/control/lqr.hpp"
#include "pendulum/control/multiloop.hpp"
#include "pendulum/physics/model.hpp"

using namespace pendulum;

int main() {
    for (int n = 1; n <= 3; ++n) {
        PlantParams p;
        p.segments.assign(n, SegmentParams{});
        p.segments[0].initial_angle = 0.05;
        const MultiLinkPendulum m(p);
        MultiLoopController c(lqrController(p));
        Vec s = m.initialState();
        const int steps = 2'000'000;
        const auto t0 = std::chrono::steady_clock::now();
        double t = 0.0;
        for (int i = 0; i < steps; ++i) {
            const auto& out = c.step(t, s.head(n + 1), s.tail(n + 1));
            s = m.rk4Step(s, out.command, 0.0, 0.001);
            t += 0.001;
        }
        const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        std::printf("%d segment(s): %.2f M steps/s (%.0f× real time at dt = 1 ms)  [x=%.3g]\n", n,
                    steps / sec / 1e6, steps / sec / 1000.0, s[0]);
    }
}
