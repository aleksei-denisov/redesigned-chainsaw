#pragma once
// Physics off the GUI thread (quick/backend/sim_source.py, thread variant): the core's
// Simulator advanced against the wall clock by a RealtimeStepper on its own thread. It
// publishes time-stamped state samples (for interpolation per displayed frame) and the
// telemetry into buffers the GUI thread reads under a short lock. Edits arrive as
// commands; configuration edits are the session's operations.
#include <array>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <variant>
#include <vector>

#include "pendulum/session/session.hpp"
#include "pendulum/simulation/engine.hpp"
#include "pendulum/simulation/realtime.hpp"

namespace app {

inline constexpr double kPeriodS = 0.002;        ///< physics tick (the thread sleeps in between)
inline constexpr double kBudgetS = 0.006;        ///< physics work per tick before the backlog is dropped
inline constexpr int kHistory = 128;             ///< samples kept for interpolation
inline constexpr double kRenderDelayS = 0.006;   ///< the GUI shows the state this long ago
inline constexpr double kTelemetryWindowS = 30.0;

double monotonic();  ///< steady clock [s]

namespace cmd {
struct Run { bool on; };
struct Reset {};
struct Restart {};
struct Step {};
struct Steps { int n; };
struct Push { double force, duration; int segment; double ratio; };
struct Drag { double force; int segment; double ratio; };
struct Speed { double value; };
struct ResetIfIdle {};
struct Op { pendulum::session::Operation op; };
}  // namespace cmd
using Command = std::variant<cmd::Run, cmd::Reset, cmd::Restart, cmd::Step, cmd::Steps, cmd::Push, cmd::Drag,
                             cmd::Speed, cmd::ResetIfIdle, cmd::Op>;

/// One published state sample.
struct Sample {
    double wall = 0, t = 0;
    long steps = 0;
    double applied = 0, tip = 0;
    long epoch = 0;
    std::array<double, pendulum::kMaxState> state{};
    int n_state = 0;
};

struct HostStats {
    long steps = 0;
    double busy_s = 0;
    bool running = false;
    double realtime_factor = 0;
    long epoch = 0;
    double t = 0;
    double speed = 1;
    double physics_ms = 0;
};

enum class MessageKind { Diverged = 1, Rejected = 2 };

class SimHost {
public:
    explicit SimHost(const pendulum::AppConfig& config);
    ~SimHost();
    SimHost(const SimHost&) = delete;
    SimHost& operator=(const SimHost&) = delete;

    void send(Command c);
    void sendOps(const std::vector<pendulum::session::Operation>& ops);

    /// The state at `wall` (default now − render delay), interpolated; false before the first sample.
    bool read(Sample& out, std::optional<double> wall = std::nullopt) const;
    /// The last `seconds` of this epoch's telemetry for `columns` (Column indices); "t" always first.
    std::map<int, std::vector<double>> tail(const std::vector<int>& columns, double seconds) const;
    double latest(int column) const;  ///< newest telemetry value (NaN before the first row)
    HostStats stats() const;
    std::optional<std::pair<MessageKind, std::string>> message();  ///< a new message, once

private:
    struct Loop;
    void run();
    void publish(Loop& loop);

    mutable std::mutex mutex_;  // guards everything below that the GUI reads
    std::deque<Command> commands_;
    std::array<Sample, kHistory> history_{};
    long hist_written_ = 0;
    std::vector<pendulum::TelemetryRow> tel_;
    long tel_written_ = 0, tel_start_ = 0;
    double tel_rate_ = 200.0;
    HostStats stats_;
    long msg_seq_ = 0, msg_seen_ = 0;
    std::pair<MessageKind, std::string> msg_;

    std::unique_ptr<Loop> loop_;  // physics thread only
    std::atomic<bool> stop_{false};
    std::thread thread_;
};

}  // namespace app
