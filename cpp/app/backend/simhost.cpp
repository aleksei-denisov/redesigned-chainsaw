#include "simhost.h"

#include <chrono>
#include <cmath>
#include <limits>

namespace app {

using namespace pendulum;

double monotonic() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

namespace {
std::size_t telemetryRows(const AppConfig& c) {
    return static_cast<std::size_t>(kTelemetryWindowS * std::max(c.simulation.telemetry_rate_hz, 400.0)) + 64;
}
}  // namespace

/// The physics thread's state (sim_source.SimLoop).
struct SimHost::Loop {
    std::unique_ptr<Simulator> sim;
    RealtimeStepper stepper{kBudgetS, 0.1};
    bool running = false;
    double speed = 1.0;
    double drag = 0.0;
    int drag_segment = -1;
    double drag_ratio = 1.0;
    long epoch = 0;
    long steps_total = 0;
    double busy_s = 0.0;
    std::optional<double> last_wall;
    std::optional<std::pair<MessageKind, std::string>> message;
    std::uint64_t copied = 0;  // telemetry rows of this epoch already published
    long published_epoch = -1;

    void jumped() {
        ++epoch;
        stepper.resetClock();
    }
    void diverged(const std::string& what) {
        running = false;
        drag = 0.0;
        message = std::make_pair(MessageKind::Diverged, what);
    }
    void advance(const session::StepsResult& r, double started) {
        busy_s += monotonic() - started;
        steps_total += r.done;
        if (r.diverged) diverged(*r.diverged);
    }

    void handle(const Command& c) {
        std::visit(
            [&](const auto& x) {
                using T = std::decay_t<decltype(x)>;
                if constexpr (std::is_same_v<T, cmd::Run>) {
                    running = x.on;
                    if (!running) last_wall.reset();
                } else if constexpr (std::is_same_v<T, cmd::Reset> || std::is_same_v<T, cmd::Restart>) {
                    sim->reset();
                    jumped();
                    running = std::is_same_v<T, cmd::Restart>;
                    last_wall.reset();
                } else if constexpr (std::is_same_v<T, cmd::Push>) {
                    sim->applyDisturbance(x.force, x.duration, std::nullopt, x.segment, x.ratio);
                } else if constexpr (std::is_same_v<T, cmd::Drag>) {
                    drag = x.force;
                    drag_segment = x.segment;
                    drag_ratio = x.ratio;
                } else if constexpr (std::is_same_v<T, cmd::Speed>) {
                    speed = x.value;
                } else if constexpr (std::is_same_v<T, cmd::Step>) {
                    running = false;
                    const double t0 = monotonic();
                    advance(session::singleStep(*sim), t0);
                } else if constexpr (std::is_same_v<T, cmd::Steps>) {
                    const double t0 = monotonic();
                    advance(session::runSteps(*sim, x.n), t0);
                } else if constexpr (std::is_same_v<T, cmd::ResetIfIdle>) {
                    if (!running && sim->time() == 0.0) {
                        sim->reset();
                        jumped();
                    }
                } else if constexpr (std::is_same_v<T, cmd::Op>) {
                    try {
                        const bool is_config = std::holds_alternative<session::op::Config>(x.op);
                        session::apply(sim, x.op, kTelemetryWindowS);
                        if (is_config || std::holds_alternative<session::op::Structure>(x.op)) jumped();
                    } catch (const std::exception& e) {
                        message = std::make_pair(MessageKind::Rejected, std::string(e.what()));
                    }
                }
            },
            c);
    }

    int tick(double now) {
        if (!running) {
            last_wall.reset();
            return 0;
        }
        if (!last_wall) {
            last_wall = now;
            return 0;
        }
        const double wall = now - *last_wall;
        last_wall = now;
        if (drag != 0.0) {  // dragging the pendulum: a force over this tick
            const double duration = std::max(std::min(wall, stepper.max_frame_s) * speed, sim->config().simulation.dt);
            sim->applyDisturbance(drag, duration, std::nullopt, drag_segment, drag_ratio);
        }
        const double started = monotonic();
        const long before = sim->steps();
        try {
            stepper.tick(*sim, wall, speed);
        } catch (const SimulationDiverged& e) {
            diverged(e.what());
        }
        busy_s += monotonic() - started;
        const long done = sim->steps() - before;
        steps_total += done;
        return static_cast<int>(done);
    }
};

SimHost::SimHost(const AppConfig& config) : loop_(std::make_unique<Loop>()) {
    loop_->sim = std::make_unique<Simulator>(config, kTelemetryWindowS);
    tel_.resize(telemetryRows(config));
    tel_rate_ = config.simulation.telemetry_rate_hz;
    publish(*loop_);
    thread_ = std::thread([this] { run(); });
}

SimHost::~SimHost() {
    stop_.store(true);
    if (thread_.joinable()) thread_.join();
}

void SimHost::send(Command c) {
    std::lock_guard lock(mutex_);
    commands_.push_back(std::move(c));
}

void SimHost::sendOps(const std::vector<session::Operation>& ops) {
    std::lock_guard lock(mutex_);
    for (const auto& o : ops) commands_.push_back(cmd::Op{o});
}

void SimHost::run() {
    Loop& loop = *loop_;
    while (!stop_.load()) {
        std::deque<Command> pending;
        {
            std::lock_guard lock(mutex_);
            pending.swap(commands_);
        }
        for (const auto& c : pending) loop.handle(c);
        const bool config_changed = !pending.empty();
        if (loop.tick(monotonic()) || config_changed) publish(loop);
        std::this_thread::sleep_for(std::chrono::duration<double>(kPeriodS));
    }
}

void SimHost::publish(Loop& loop) {
    Simulator& sim = *loop.sim;
    const auto& rec = sim.telemetry();
    std::lock_guard lock(mutex_);
    const std::size_t needed = telemetryRows(sim.config());
    if (tel_.size() < needed) {  // a faster telemetry rate: a larger ring, fresh
        tel_.assign(needed, TelemetryRow{});
        tel_written_ = tel_start_ = 0;
        loop.published_epoch = -1;
    }
    if (loop.epoch != loop.published_epoch) {  // reset / new configuration: fresh telemetry
        loop.published_epoch = loop.epoch;
        loop.copied = 0;
        tel_start_ = tel_written_;
    }
    // rows the recorder got since the last publish (it keeps at least its window)
    const std::uint64_t version = rec.version();
    std::uint64_t fresh = version >= loop.copied ? version - loop.copied : version;
    if (loop.copied > version) loop.copied = 0;  // recorder cleared (reset)
    fresh = std::min<std::uint64_t>(fresh, rec.size());
    const long size = static_cast<long>(tel_.size());
    for (std::size_t k = rec.size() - fresh; k < rec.size(); ++k) tel_[tel_written_++ % size] = rec.row(k);
    loop.copied = version;
    tel_rate_ = sim.config().simulation.telemetry_rate_hz;

    Sample& s = history_[hist_written_ % kHistory];
    s.wall = monotonic();
    s.t = sim.time();
    s.steps = sim.steps();
    s.applied = sim.applied();
    s.tip = sim.tipForce();
    s.epoch = loop.epoch;
    s.n_state = static_cast<int>(sim.state().size());
    s.state.fill(0.0);
    for (int i = 0; i < s.n_state; ++i) s.state[i] = sim.state()[i];
    ++hist_written_;

    stats_ = {loop.steps_total, loop.busy_s, loop.running, loop.stepper.realtime_factor, loop.epoch,
              sim.time(),       loop.speed,  loop.stepper.physics_ms};
    if (loop.message) {
        msg_ = *loop.message;
        loop.message.reset();
        ++msg_seq_;
    }
}

bool SimHost::read(Sample& out, std::optional<double> wall_opt) const {
    const double wall = wall_opt.value_or(monotonic() - kRenderDelayS);
    std::lock_guard lock(mutex_);
    if (hist_written_ == 0) return false;
    long i = hist_written_ - 1;
    const long lowest = std::max(hist_written_ - 24, 0L);
    const Sample* b = &history_[i % kHistory];
    if (b->wall <= wall) {
        out = *b;
        return true;
    }
    for (;;) {
        if (i <= lowest) {
            out = history_[lowest % kHistory];
            return true;
        }
        const Sample* a = &history_[(i - 1) % kHistory];
        if (a->wall <= wall) {
            if (a->epoch != b->epoch || b->wall <= a->wall || a->n_state != b->n_state) {
                out = *b;  // a reset in between: no blending
            } else {
                const double u = (wall - a->wall) / (b->wall - a->wall);
                out = *b;
                out.wall = wall;
                out.t = a->t + u * (b->t - a->t);
                out.applied = a->applied + u * (b->applied - a->applied);
                out.tip = a->tip + u * (b->tip - a->tip);
                for (int k = 0; k < b->n_state; ++k) out.state[k] = a->state[k] + u * (b->state[k] - a->state[k]);
            }
            return true;
        }
        --i;
        b = a;
    }
}

std::map<int, std::vector<double>> SimHost::tail(const std::vector<int>& columns, double seconds) const {
    std::map<int, std::vector<double>> out;
    std::lock_guard lock(mutex_);
    const long size = static_cast<long>(tel_.size());
    const long n = std::min({tel_written_ - tel_start_, static_cast<long>(seconds * tel_rate_) + 1, size - 32});
    std::vector<int> cols = {kT};
    for (int c : columns)
        if (c != kT) cols.push_back(c);
    for (int c : cols) out[c].reserve(std::max(n, 0L));
    for (long k = tel_written_ - std::max(n, 0L); k < tel_written_; ++k) {
        const auto& row = tel_[k % size];
        for (int c : cols) out[c].push_back(row[c]);
    }
    return out;
}

double SimHost::latest(int column) const {
    std::lock_guard lock(mutex_);
    if (tel_written_ <= tel_start_) return std::numeric_limits<double>::quiet_NaN();
    return tel_[(tel_written_ - 1) % static_cast<long>(tel_.size())][column];
}

HostStats SimHost::stats() const {
    std::lock_guard lock(mutex_);
    return stats_;
}

std::optional<std::pair<MessageKind, std::string>> SimHost::message() {
    std::lock_guard lock(mutex_);
    if (msg_seq_ == msg_seen_) return std::nullopt;
    msg_seen_ = msg_seq_;
    return msg_;
}

}  // namespace app
