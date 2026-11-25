#pragma once
// Background jobs of a session (session/jobs.py): auto-tune and experiment workers on
// std::thread, polled by the interface on a timer; replay data of a recorded run.
#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <utility>

#include "pendulum/physics/model.hpp"
#include "pendulum/simulation/experiment.hpp"
#include "pendulum/simulation/tuning.hpp"

namespace pendulum::session {

/// Calibration (one push test) or the search, on its own thread.
class TuneWorker {
public:
    TuneWorker(AppConfig config, TuningSettings settings, TuneOptions options, bool calibrate_only = false);
    ~TuneWorker();  ///< cancels and joins
    TuneWorker(const TuneWorker&) = delete;
    TuneWorker& operator=(const TuneWorker&) = delete;

    void start();
    void cancel() { token_.cancel(); }
    bool done() const { return finished_.load(); }
    void wait();
    /// The latest progress snapshot since the last call (nullopt if none).
    std::optional<TuneProgress> takeProgress();

    // Valid once done():
    std::optional<double> calibration;
    std::optional<std::pair<TuningResult, std::optional<std::pair<ExperimentResult, ExperimentResult>>>> outcome;
    std::optional<std::string> error;

private:
    void run();
    AppConfig config_;
    TuningSettings settings_;
    TuneOptions options_;
    bool calibrate_only_;
    CancelToken token_;
    std::mutex mutex_;
    std::optional<TuneProgress> latest_;
    std::atomic<bool> finished_{false};
    std::thread thread_;
};

/// One experiment on its own thread; poll done() and progress().
class ExperimentWorker {
public:
    ExperimentWorker(AppConfig config, std::string name);
    ~ExperimentWorker();
    ExperimentWorker(const ExperimentWorker&) = delete;
    ExperimentWorker& operator=(const ExperimentWorker&) = delete;

    void start();
    void cancel() { cancel_.store(true); }
    bool done() const { return finished_.load(); }
    void wait();
    double progress() const { return progress_.load(); }

    std::optional<ExperimentResult> result;
    std::optional<std::string> error;

private:
    AppConfig config_;
    std::string name_;
    std::atomic<double> progress_{0.0};
    std::atomic<bool> cancel_{false}, finished_{false};
    std::thread thread_;
};

/// Recorded states of a run (telemetry columns → model state), for playing it back.
class ReplayData {
public:
    explicit ReplayData(ExperimentResult result);
    double duration() const { return t_.empty() ? 0.0 : t_.back(); }
    std::size_t size() const { return t_.size(); }
    std::size_t index(double t) const;  ///< last sample at or before t
    Vec state(std::size_t i) const;     ///< [x, θ…, ẋ, ω…]
    double time(std::size_t i) const { return t_[i]; }
    double actuator(std::size_t i) const { return actuator_[i]; }
    double tipForce(std::size_t i) const { return tip_[i]; }
    const ExperimentResult& result() const { return result_; }
    const MultiLinkPendulum& model() const { return model_; }

private:
    ExperimentResult result_;
    MultiLinkPendulum model_;
    std::vector<double> t_, x_, xd_, actuator_, tip_;
    std::vector<std::vector<double>> th_, om_;
};

}  // namespace pendulum::session
