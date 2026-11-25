#include "pendulum/control/multiloop.hpp"

#include <algorithm>
#include <stdexcept>

namespace pendulum {

void ControllerConfig::validate() const {
    if (!(frequency_hz >= 1.0 && frequency_hz <= 10000.0))
        throw std::invalid_argument("controller frequency must be between 1 Hz and 10 kHz");
    if (output_min >= output_max)
        throw std::invalid_argument("controller output minimum must be below the maximum");
    for (const auto& loop : loops) {
        const auto idx = loop.segmentIndex();
        const bool ok_angle = loop.isAngle() && idx && *idx >= 0 && *idx <= 2;
        if (loop.signal != "cart" && !ok_angle)
            throw std::invalid_argument("loop '" + loop.name + "': unknown signal '" + loop.signal + "'");
    }
}

MultiLoopController::MultiLoopController(const ControllerConfig& config) {
    setConfig(config);
    reset();
}

void MultiLoopController::setConfig(const ControllerConfig& config) {
    config.validate();
    std::vector<PIDLoop> next;
    next.reserve(config.loops.size());
    for (std::size_t i = 0; i < config.loops.size(); ++i) {
        const auto& cfg = config.loops[i];
        const PIDLoop* keep = nullptr;
        if (i < loops_.size() && loops_[i].config.signal == cfg.signal) {
            keep = &loops_[i];
        } else {
            for (const auto& l : loops_)
                if (l.config.signal == cfg.signal) keep = &l;  // last match, like the Python dict
        }
        PIDLoop loop = keep ? *keep : PIDLoop(cfg);
        loop.config = cfg;
        next.push_back(std::move(loop));
    }
    loops_ = std::move(next);
    config_ = config;
}

void MultiLoopController::reset() {
    for (auto& l : loops_) l.reset();
    next_sample_ = 0.0;
    output_ = ControllerOutput{};
    output_.loops.assign(loops_.size(), LoopOutput{});
}

void MultiLoopController::resetIntegrators() {
    for (auto& l : loops_) l.integral = 0.0;
}

const ControllerOutput& MultiLoopController::step(double t, const Vec& q, const Vec& qd) {
    if (t + 1e-12 < next_sample_) {
        output_.updated = false;
        return output_;
    }
    const double ts = period();
    next_sample_ += ts;
    if (next_sample_ <= t) next_sample_ = t + ts;  // e.g. after a frequency change

    const auto& c = config_;
    std::vector<LoopOutput> outputs;
    outputs.reserve(loops_.size());
    double total = 0.0;
    for (auto& loop : loops_) {
        double y = 0.0, yd = 0.0;
        bool have = false;
        if (loop.config.signal == "cart") {
            y = q[0];
            yd = qd[0];
            have = true;
        } else if (const auto idx = loop.config.segmentIndex(); idx && *idx + 1 < q.size()) {
            y = q[*idx + 1];
            yd = qd[*idx + 1];
            have = true;
        }
        if (!have || !loop.config.enabled) {
            loop.last = LoopOutput{};
            outputs.push_back(loop.last);
            continue;
        }
        const LoopOutput out = loop.compute(y, yd, ts, direction());
        outputs.push_back(out);
        total += out.total();
    }
    if (!c.enabled) total = 0.0;
    const double saturated = std::min(c.output_max, std::max(c.output_min, total));
    if (c.enabled)
        for (auto& loop : loops_) loop.updateIntegral(ts, total, saturated);
    output_.command = saturated;
    output_.unsaturated = total;
    output_.loops = std::move(outputs);
    output_.updated = true;
    return output_;
}

}  // namespace pendulum
