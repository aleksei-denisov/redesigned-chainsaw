#include "pendulum/simulation/telemetry.hpp"

namespace pendulum {

const std::array<std::string, kColumnCount>& columnNames() {
    static const std::array<std::string, kColumnCount> names = [] {
        std::array<std::string, kColumnCount> n;
        const char* fixed[] = {"t", "x", "xd", "theta1", "theta2", "theta3", "omega1", "omega2", "omega3",
                               "command", "unsaturated", "actuator", "tip_force", "ke", "pe", "energy"};
        for (int i = 0; i < kLoop0; ++i) n[i] = fixed[i];
        const char* kinds[] = {"error", "p", "i", "d"};
        for (int k = 0; k < kMaxLoops; ++k)
            for (int j = 0; j < 4; ++j) n[loopColumn(k, j)] = std::string(kinds[j]) + std::to_string(k);
        n[kSaturated] = "saturated";
        return n;
    }();
    return names;
}

std::optional<int> columnIndex(std::string_view name) {
    const auto& n = columnNames();
    for (int i = 0; i < kColumnCount; ++i)
        if (n[i] == name) return i;
    return std::nullopt;
}

void TelemetryRecorder::append(const TelemetryRow& row) {
    if (max_samples_ && rows_.size() >= 2 * *max_samples_) {
        rows_.erase(rows_.begin(), rows_.end() - static_cast<std::ptrdiff_t>(*max_samples_));
    }
    rows_.push_back(row);
    ++version_;
}

std::vector<double> TelemetryRecorder::column(int c) const {
    std::vector<double> out;
    out.reserve(rows_.size());
    for (const auto& r : rows_) out.push_back(r[c]);
    return out;
}

}  // namespace pendulum
