#include "pendulum/session/jobs.hpp"

#include <algorithm>
#include <cmath>

namespace pendulum::session {

// ------------------------------------------------------------------- TuneWorker
TuneWorker::TuneWorker(AppConfig config, TuningSettings settings, TuneOptions options, bool calibrate_only)
    : config_(std::move(config)), settings_(settings), options_(std::move(options)), calibrate_only_(calibrate_only) {}

TuneWorker::~TuneWorker() {
    cancel();
    if (thread_.joinable()) thread_.join();
}

void TuneWorker::start() { thread_ = std::thread([this] { run(); }); }

void TuneWorker::wait() {
    if (thread_.joinable()) thread_.join();
}

std::optional<TuneProgress> TuneWorker::takeProgress() {
    std::lock_guard lock(mutex_);
    auto u = std::move(latest_);
    latest_.reset();
    return u;
}

void TuneWorker::run() {
    try {
        if (calibrate_only_) {
            calibration = calibratePushTestSeconds(config_, settings_);
        } else {
            auto update = [this](const TuneProgress& u) {
                std::lock_guard lock(mutex_);
                latest_ = u;
                return !token_.cancelled();
            };
            TuningResult result = tune(config_, settings_, options_, update, &token_);
            std::optional<std::pair<ExperimentResult, ExperimentResult>> comparison;
            try {
                comparison = compareResponses(config_, config_.controller, result.controller,
                                              std::max(result.recommended_push, 1e-3), settings_);
            } catch (const std::exception&) {  // the summary works without the preview
            }
            outcome.emplace(std::move(result), std::move(comparison));
        }
    } catch (const TuningCancelled&) {
        error = "Cancelled before any candidate was finished – nothing to apply.";
    } catch (const std::exception& e) {
        error = e.what();
    }
    finished_.store(true);
}

// ------------------------------------------------------------- ExperimentWorker
ExperimentWorker::ExperimentWorker(AppConfig config, std::string name)
    : config_(std::move(config)), name_(std::move(name)) {}

ExperimentWorker::~ExperimentWorker() {
    cancel();
    if (thread_.joinable()) thread_.join();
}

void ExperimentWorker::start() {
    thread_ = std::thread([this] {
        try {
            result = runExperiment(config_, name_, [this](double f) {
                progress_.store(f);
                return !cancel_.load();
            });
        } catch (const std::exception& e) {
            error = e.what();
        }
        finished_.store(true);
    });
}

void ExperimentWorker::wait() {
    if (thread_.joinable()) thread_.join();
}

// ------------------------------------------------------------------- ReplayData
namespace {
std::vector<double> clean(const std::map<std::string, std::vector<double>>& d, const std::string& name,
                          std::size_t size) {
    std::vector<double> out(size, 0.0);
    if (const auto it = d.find(name); it != d.end())
        for (std::size_t i = 0; i < size && i < it->second.size(); ++i)
            out[i] = std::isfinite(it->second[i]) ? it->second[i] : 0.0;  // np.nan_to_num (finite part)
    return out;
}
}  // namespace

ReplayData::ReplayData(ExperimentResult result) : result_(std::move(result)), model_(result_.config.plant) {
    const auto& d = result_.data;
    const std::size_t n = d.count("t") ? d.at("t").size() : 0;
    t_ = clean(d, "t", n);
    x_ = clean(d, "x", n);
    xd_ = clean(d, "xd", n);
    actuator_ = clean(d, "actuator", n);
    tip_ = clean(d, "tip_force", n);
    for (int i = 0; i < model_.n(); ++i) {
        th_.push_back(clean(d, "theta" + std::to_string(i + 1), n));
        om_.push_back(clean(d, "omega" + std::to_string(i + 1), n));
    }
}

std::size_t ReplayData::index(double t) const {
    if (t_.empty()) return 0;
    const auto it = std::upper_bound(t_.begin(), t_.end(), t);
    const auto i = static_cast<std::ptrdiff_t>(it - t_.begin()) - 1;
    return static_cast<std::size_t>(std::clamp<std::ptrdiff_t>(i, 0, static_cast<std::ptrdiff_t>(t_.size()) - 1));
}

Vec ReplayData::state(std::size_t i) const {
    const int n = model_.n();
    Vec s(2 * (n + 1));
    s[0] = x_[i];
    s[n + 1] = xd_[i];
    for (int k = 0; k < n; ++k) {
        s[1 + k] = th_[k][i];
        s[n + 2 + k] = om_[k][i];
    }
    return s;
}

}  // namespace pendulum::session
