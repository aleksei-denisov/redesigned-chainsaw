#pragma once
// Telemetry recorder (simulation/telemetry.py): one row of named columns per sample.
#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace pendulum {

inline constexpr int kMaxLoops = 4;  ///< cart + three segments

/// Column order: the Python columns first (same names), then the C++ additions.
enum Column : int {
    kT, kX, kXd, kTheta1, kTheta2, kTheta3, kOmega1, kOmega2, kOmega3,
    kCommand, kUnsaturated, kActuator, kTipForce, kKe, kPe, kEnergy,
    kLoop0,  ///< error0, p0, i0, d0, error1, … (4 per loop)
    kSaturated = kLoop0 + 4 * kMaxLoops,  ///< 1 when the command exceeds the actuator limit or the controller clips (new in C++)
    kColumnCount
};

/// Column names ("t", "x", …, "error0", "p0", …, "saturated").
const std::array<std::string, kColumnCount>& columnNames();
std::optional<int> columnIndex(std::string_view name);
inline int loopColumn(int loop, int kind) { return kLoop0 + 4 * loop + kind; }  ///< kind: 0 error, 1 p, 2 i, 3 d

using TelemetryRow = std::array<double, kColumnCount>;

/// Append-only store. With a window (`max_samples`) the oldest samples are dropped once the
/// buffer holds twice that many (amortised O(1)); without one everything is kept (experiments).
class TelemetryRecorder {
public:
    explicit TelemetryRecorder(std::optional<std::size_t> max_samples = std::nullopt) : max_samples_(max_samples) {}

    void clear() {
        rows_.clear();
        version_ = 0;
    }
    void append(const TelemetryRow& row);
    std::size_t size() const { return rows_.size(); }
    bool empty() const { return rows_.empty(); }
    const TelemetryRow& row(std::size_t i) const { return rows_[i]; }
    const std::vector<TelemetryRow>& rows() const { return rows_; }
    std::vector<double> column(int c) const;
    std::uint64_t version() const { return version_; }  ///< incremented on every append

    std::vector<std::string> loop_names;

private:
    std::optional<std::size_t> max_samples_;
    std::vector<TelemetryRow> rows_;
    std::uint64_t version_ = 0;
};

}  // namespace pendulum
