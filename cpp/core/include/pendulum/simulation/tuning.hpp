#pragma once
// Robust auto-tune (simulation/tuning.py + tuning2.py).
//
// Metric: the largest tip push (0.1 s pulse) the controller recovers from, found by doubling
// and bisection on the full nonlinear simulator; among equally robust controllers the one
// with the smaller recovery cost (ISE of the angles after half that push).
//
// Search modes:
//   Full  – every loop through the five LQR weights (log scale), then optionally the
//           integral rates log10(Ki/Kp) per loop. With integral = false this is the desktop
//           app's robust_tune.
//   Loops – a direct search over the selected loops' gains (log10 |K|, sign kept).
// Options: gain limits (per loop / general / none), hold-position test, methods.
//
// Differences from the Python version (docs/CPP_PORT.md, "Improvements"):
//   * Full mode: the current controller is itself a candidate, so the result is never
//     worse than before tuning (TuningResult::unchanged tells when it stayed best).
//   * Candidates run on a thread pool on every platform; results are applied in candidate
//     order, so the outcome does not depend on the number of workers.
//   * Sampling uses the core's Rng: a seed reproduces a search on every platform.
#include <atomic>
#include <functional>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "pendulum/control/lqr.hpp"
#include "pendulum/simulation/config.hpp"
#include "pendulum/simulation/experiment.hpp"

namespace pendulum {

struct TuningSettings {
    double push_duration = 0.1;  ///< s, length of the tip force pulse
    double push_time = 0.2;      ///< s, when the pulse starts
    double horizon = 4.0;        ///< s, simulated time per test
    double fall_deg = 60.0;
    double settle_deg = 3.0;
    double resolution = 0.03;    ///< relative bisection accuracy of the push force
    int samples = 24;            ///< stage 1 random candidates
    int refine_rounds = 3;
    int refine_samples = 8;
    int workers = 0;             ///< 0 = automatic (cores − 1, 1 … 8); 1 = serial
    std::uint64_t seed = 0;
    bool prune = true;           ///< one-shot rejection of candidates that cannot win
    bool early_stop = true;      ///< end clearly recovered push tests early
    double still_deg = 0.5;
    double still_rate_dps = 3.0;
    double still_cart_mps = 0.03;

    int candidates() const { return 1 + samples + refine_rounds * refine_samples; }
    static TuningSettings preset(const std::string& name);  ///< "quick", "thorough", else default
};

class TuningCancelled : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

/// Thread-safe cancellation flag.
class CancelToken {
public:
    void cancel() { flag_.store(true); }
    bool cancelled() const { return flag_.load(); }

private:
    std::atomic<bool> flag_{false};
};

// ------------------------------------------------------------------ the metric
inline constexpr double kMinPush = 1e-3;  ///< N; below this a "recovery" is meaningless

struct PushTestResult {
    bool recovered = false;
    double ise = 0.0;  ///< ∫|θ|² dt [rad²·s], inf after a fall
};

/// Upright at rest (segment 1 tilted 0.2° so an unstable controller cannot balance by accident).
AppConfig uprightConfig(const AppConfig& cfg);
PushTestResult pushTest(const AppConfig& cfg, double force, const TuningSettings& s, bool allow_early_stop = true,
                        const CancelToken* cancel = nullptr);
/// Largest recoverable tip force [N]; `counter` (optional) counts the simulations run.
double maxRecoverablePush(const AppConfig& cfg, const TuningSettings& s, double upper = 100.0,
                          int* counter = nullptr, const CancelToken* cancel = nullptr);

// --------------------------------------------------------------- options
enum class TuneMode { Full, Loops };

/// Signed (min, max) per gain; nullopt = no limit (±inf allowed for open ends).
struct TermLimits {
    std::optional<std::pair<double, double>> kp, ki, kd;
    const std::optional<std::pair<double, double>>& get(const std::string& term) const;
    bool allows(const std::string& term, double value) const;
};

struct GainLimits {
    TermLimits general;
    std::map<std::string, TermLimits> per_loop;  ///< by loop signal
    const TermLimits& forLoop(const std::string& signal) const;
    bool allows(const ControllerConfig& ctrl) const;
};

struct TuneOptions {
    TuneMode mode = TuneMode::Full;
    std::vector<std::string> loops;                         ///< Loops mode: signals to tune (empty: all)
    std::vector<std::string> terms = {"kp", "ki", "kd"};    ///< Loops mode: gains to tune …
    std::vector<std::string> off_terms;                     ///< … and gains to switch off in those loops
    bool integral = true;                                   ///< Full mode: integral search after the design
    bool methods = false;                                   ///< try derivative source / anti-windup
    GainLimits limits;
    bool hold = false;                                      ///< also score holding position under a steady push

    /// The classic desktop search (robust_tune): LQR design only, no integral gains.
    static TuneOptions classic();
};

/// Options from the screen's plain data (tuning2.options_from_dict, same keys).
TuneOptions optionsFromJson(const nlohmann::json& d, const std::vector<std::string>& signals);

// ------------------------------------------------------------ work units
inline constexpr double kOff = -99.0;           ///< search coordinate meaning "this term off"
inline constexpr double kHoldForce = 0.05;      ///< steady push of the hold test, × the candidate's push
inline constexpr double kHoldSeconds = 12.0;
inline constexpr double kHoldWeight = 0.5;      ///< score = push · (1 − kHoldWeight · residual)

struct Evaluation {
    double push = 0.0;
    double cost = std::numeric_limits<double>::infinity();
    int simulations = 0;
    double residual = std::numeric_limits<double>::quiet_NaN();  ///< hold test, NaN when not run
};

/// LQR design for every loop with the tuner's settings; ki_ratios[i] = log10(Ki/Kp) or kOff.
ControllerConfig fullController(const AppConfig& cfg, const LQRWeights& weights,
                                const std::vector<double>* ki_ratios = nullptr);
double holdResidual(const AppConfig& cfg, double force, const TuningSettings& s, double seconds = kHoldSeconds,
                    const CancelToken* cancel = nullptr);
double score(double push, double residual, bool hold);
/// Push, recovery cost, simulations and hold residual of the configured controller;
/// `min_push` > 0 (with pruning) rejects it after one test if it cannot win.
Evaluation evaluateController(const AppConfig& cfg, const TuningSettings& s, double min_push = 0.0,
                              bool hold = false, const CancelToken* cancel = nullptr);
/// The controller before tuning: push (no recovery cost) and hold residual.
Evaluation evaluateStart(const AppConfig& cfg, const TuningSettings& s, bool hold = false,
                         const CancelToken* cancel = nullptr);
std::string heldName(const AppConfig& cfg);  ///< "cart position" / "segment 1 angle"
/// Candidates evaluated (without the baseline), for progress and estimates.
int budget(const TuningSettings& s, const TuneOptions& o);

// ------------------------------------------------------------------ search
struct TuneProgress {
    std::string phase = "baseline";  ///< "baseline" | "explore" | "refine" | "done"
    std::string message;
    int done = 0, total = 0;
    double elapsed = 0.0;
    std::optional<double> eta;
    int workers = 1;
    int simulations = 0;
    int candidates_done = 0, candidates_total = 0;
    std::optional<double> baseline_push;
    double best_push = 0.0;
    double best_cost = std::numeric_limits<double>::infinity();
    std::optional<LQRWeights> best_weights;
    std::optional<ControllerConfig> best_controller;
    std::vector<double> history;           ///< best push after each candidate
    std::vector<double> candidate_pushes;  ///< each candidate's own push (0 = rejected)
    double fraction() const { return total ? std::min(1.0, static_cast<double>(done) / total) : 0.0; }
};

using TuneUpdate = std::function<bool(const TuneProgress&)>;  ///< false cancels

struct TuningResult {
    ControllerConfig controller;
    LQRWeights weights;
    double max_push = 0.0;           ///< N, recoverable tip force (pulse of push_duration)
    double baseline_max_push = 0.0;  ///< N, same metric before tuning
    double recommended_push = 0.0;   ///< N, half of max_push rounded down to 2 digits
    int evaluations = 0;
    double elapsed = 0.0;
    bool cancelled = false;
    bool unchanged = false;          ///< the controller before tuning stayed the best
    std::vector<double> history;
    bool hold = false;
    double hold_residual = std::numeric_limits<double>::quiet_NaN();
    double baseline_hold_residual = std::numeric_limits<double>::quiet_NaN();
    std::string held;
};

int defaultWorkers();  ///< all cores but one, 1 … 8

/// Runs the search. Throws TuningCancelled if cancelled before any candidate finished, and
/// std::invalid_argument if nothing can be tuned or no candidate recovers from any push.
TuningResult tune(const AppConfig& config, const TuningSettings& settings = {}, const TuneOptions& options = {},
                  const TuneUpdate& on_update = {}, CancelToken* cancel = nullptr);

/// Rough duration of a search from the wall time of one full-horizon push test.
double estimateSeconds(const TuningSettings& s, double push_test_s, int workers);
double calibratePushTestSeconds(const AppConfig& config, const TuningSettings& s = {});
/// Experiments with the old and new controller under the same push at 1 s (before / after).
std::pair<ExperimentResult, ExperimentResult> compareResponses(const AppConfig& config, const ControllerConfig& old_c,
                                                               const ControllerConfig& new_c, double push,
                                                               const TuningSettings& s = {}, double duration = 5.0);
double roundNice(double value);  ///< down to 2 significant digits

}  // namespace pendulum
