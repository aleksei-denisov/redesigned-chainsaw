#include "pendulum/session/texts.hpp"

#include <cmath>
#include <cstdio>

namespace pendulum::session::texts {

const std::vector<Preset>& tunePresets() {
    static const std::vector<Preset> p = {{"quick", "Quick", "Fewest candidates · fastest"},
                                          {"normal", "Balanced", "Recommended"},
                                          {"thorough", "Thorough", "Most candidates · best result"}};
    return p;
}

std::string fmt(double value, const char* spec) {
    char format[16];
    std::snprintf(format, sizeof format, "%%%s", spec);
    char buf[64];
    std::snprintf(buf, sizeof buf, format, value);
    return buf;
}

std::string tuningApplied(const TuningResult& r) {
    return "Robust gains applied: recoverable push " + fmt(r.baseline_max_push, ".3g") + " N → " +
           fmt(r.max_push, ".3g") + " N. Restart to try them; Push uses " + fmt(r.recommended_push, "g") + " N.";
}

std::string fmtDuration(std::optional<double> seconds) {
    if (!seconds || !std::isfinite(*seconds)) return "–";
    const long s = std::max(0L, std::lround(std::nearbyint(*seconds)));
    char buf[64];
    if (s >= 3600)
        std::snprintf(buf, sizeof buf, "%ld h %02ld min", s / 3600, s % 3600 / 60);
    else if (s >= 60)
        std::snprintf(buf, sizeof buf, "%ld min %02ld s", s / 60, s % 60);
    else
        std::snprintf(buf, sizeof buf, "%ld s", s);
    return buf;
}

std::string loopName(const std::string& signal) { return signal == "cart" ? "Cart x" : "θ" + signal.substr(5); }

std::string gainsText(const ControllerConfig* ctrl) {
    if (!ctrl) return "–";
    std::string out;
    for (const auto& lp : ctrl->loops) {
        if (!lp.enabled) continue;
        if (!out.empty()) out += "\n";
        out += loopName(lp.signal) + ":  Kp " + fmt(lp.kp, ".4g") + " · Ki " + fmt(lp.ki, ".4g") + " · Kd " +
               fmt(lp.kd, ".4g");
    }
    return out;
}

std::string statusText(const ExperimentResult& r) {
    return r.diverged ? "diverged" : r.metrics.fell ? "fell" : "stable";
}

std::vector<std::pair<std::string, std::string>> metricPairs(const ExperimentResult& r) {
    const auto& m = r.metrics;
    const std::string settle = m.settling_time ? fmt(*m.settling_time, ".3f") + " s" : "–";
    std::vector<std::pair<std::string, std::string>> p = {
        {"Settling", settle},
        {"Max dev", fmt(m.max_deviation, ".2f") + "°"},
        {"Overshoot", fmt(m.overshoot_pct, ".1f") + " %"},
        {"RMSE", fmt(m.rmse, ".3f") + "°"},
        {"u RMS", fmt(m.control_rms, ".3f")},
        {"u peak", fmt(m.control_peak, ".3f")},
        {"∫u² dt", fmt(m.control_energy, ".3f")}};
    if (r.config.plant.hasCart()) p.emplace_back("Max |x|", fmt(m.max_cart_displacement, ".3f") + " m");
    p.emplace_back("Saturated", fmt(m.saturation_pct, ".1f") + " %");  // new in C++
    return p;
}

std::string tuneEstimate(const std::string& preset, std::optional<double> push_test_s, int workers) {
    if (!push_test_s) return "measuring this device…";
    return "about " + fmtDuration(estimateSeconds(TuningSettings::preset(preset), *push_test_s, workers));
}

std::string tuneCores(int workers, int available) {
    return "Uses " + std::to_string(workers) + " CPU core" + (workers != 1 ? "s" : "") + " in parallel (" +
           std::to_string(available) + " available; one stays free for the display).";
}

std::string tuneActivity(const TuneProgress& u) {
    return "Working on " + std::to_string(u.workers) + " CPU core" + (u.workers != 1 ? "s" : "") + " · " +
           std::to_string(u.simulations) + " simulations so far";
}

std::pair<std::string, std::string> tuneBest(const TuneProgress& u) {
    if (u.best_push > 0) {
        const std::string before =
            u.baseline_push ? " (current controller: " + fmt(*u.baseline_push, ".3g") + " N)" : std::string();
        const ControllerConfig* c = u.best_controller ? &*u.best_controller : nullptr;
        return {"Recovers from a tip push of <b>" + fmt(u.best_push, ".3g") + " N</b>" + before, gainsText(c)};
    }
    if (u.baseline_push) return {"Current controller recovers from " + fmt(*u.baseline_push, ".3g") + " N", ""};
    return {"–", ""};
}

double tuneFraction(const TuneProgress& u, double since_update) {
    double frac = u.fraction();
    if (u.eta && *u.eta > 0) {
        const double est = (u.elapsed + since_update) / (u.elapsed + *u.eta);
        frac = std::max(frac, std::min(est, static_cast<double>(u.done + 1) / u.total * 0.999));
    }
    return frac;
}

std::string tuneTimes(double elapsed, const TuneProgress* u, double since_update) {
    if (!u || u->total == 0) return "Elapsed " + fmtDuration(elapsed);
    const std::string rest = u->eta ? " · about " + fmtDuration(std::max(0.0, *u->eta - since_update)) + " left"
                                    : " · estimating time left…";
    return "Elapsed " + fmtDuration(elapsed) + rest;
}

std::string tuneHeadline(const TuningResult& r) {
    const double before = r.baseline_max_push, after = r.max_push;
    const std::string gain =
        before > 0 && after > before ? " (+" + fmt((after / before - 1) * 100, ".0f") + " %)" : std::string();
    return "Recoverable tip push: " + fmt(before, ".3g") + " N → " + fmt(after, ".3g") + " N" + gain;
}

std::string tuneNotes(const TuningResult& r) {
    std::vector<std::string> notes = {std::to_string(r.evaluations) + " candidates in " + fmtDuration(r.elapsed) + "."};
    if (r.cancelled) notes.insert(notes.begin(), "Cancelled – this is the best result found so far.");
    if (r.unchanged)
        notes.push_back("No candidate beat the current gains – they are kept; applying changes nothing.");
    else if (r.max_push <= r.baseline_max_push * 1.02)
        notes.push_back("The current gains are already about as robust as this search can find; "
                        "applying gives the normalised recommended settings.");
    notes.push_back("Applying also sets the push force to " + fmt(r.recommended_push, "g") + " N for testing.");
    std::string out;
    for (const auto& n : notes) out += (out.empty() ? "" : " ") + n;
    return out;
}

std::vector<std::tuple<std::string, std::string, std::string>> tuneMetricRows(
    const std::pair<ExperimentResult, ExperimentResult>& c) {
    const auto& o = c.first.metrics;
    const auto& n = c.second.metrics;
    auto st = [](const RunMetrics& m) { return m.settling_time ? fmt(*m.settling_time, ".2f") + " s" : "did not settle"; };
    return {{"Settling time", st(o), st(n)},
            {"Overshoot", fmt(o.overshoot_pct, ".0f") + " %", fmt(n.overshoot_pct, ".0f") + " %"},
            {"Max angle", fmt(o.max_deviation, ".2f") + "°", fmt(n.max_deviation, ".2f") + "°"},
            {"Peak force", fmt(o.control_peak, ".3g"), fmt(n.control_peak, ".3g")},
            {"Control energy ∫u²", fmt(o.control_energy, ".3g"), fmt(n.control_energy, ".3g")},
            {"Fell over", o.fell ? "yes" : "no", n.fell ? "yes" : "no"}};
}

std::string saturationNote(const RunMetrics& m) {
    if (m.saturation_pct < 10.0) return "";
    return "The actuator was at its limit " + fmt(m.saturation_pct, ".0f") +
           " % of the time: the controller is effectively switching (bang-bang), so small changes can alter "
           "the response a lot. Lower the gains or allow more force/torque.";
}

}  // namespace pendulum::session::texts
