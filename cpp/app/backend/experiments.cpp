#include "experiments.h"

#include <algorithm>
#include <cmath>

#include "parameters.h"
#include "pendulum/session/rules.hpp"
#include "pendulum/session/texts.hpp"
#include "simulation.h"

namespace app {

namespace ps = pendulum::session;

namespace {
constexpr int kPollMs = 50;
struct PlotDef {
    const char *key, *title, *unit;
};
constexpr PlotDef kPlots[] = {{"angle", "Angle", "°"},
                              {"error", "Control error", "°"},
                              {"effort", "Actuator output", "N · N·m"},
                              {"cart", "Cart position", "m"}};
QString qs(const std::string& s) { return QString::fromStdString(s); }
}  // namespace

void thin(const std::vector<double>& t, const std::vector<double>& y, QVariantList& ts, QVariantList& ys,
          std::size_t points) {
    const std::size_t n = std::min(t.size(), y.size());
    if (n <= points) {
        for (std::size_t i = 0; i < n; ++i) {
            ts.push_back(t[i]);
            ys.push_back(y[i]);
        }
        return;
    }
    const std::size_t buckets = points / 2;
    for (std::size_t b = 0; b < buckets; ++b) {
        const std::size_t lo = b * n / buckets, hi = (b + 1) * n / buckets;
        if (hi <= lo) continue;
        std::size_t imin = lo, imax = lo;
        for (std::size_t k = lo; k < hi; ++k) {  // NaN-skipping argmin / argmax
            if (std::isnan(y[k])) continue;
            if (std::isnan(y[imin]) || y[k] < y[imin]) imin = k;
            if (std::isnan(y[imax]) || y[k] > y[imax]) imax = k;
        }
        const std::size_t first = std::min(imin, imax), second = std::max(imin, imax);
        ts.push_back(t[first]);
        ys.push_back(y[first]);
        if (second != first) {
            ts.push_back(t[second]);
            ys.push_back(y[second]);
        }
    }
}

ExperimentsBridge::ExperimentsBridge(SimulationBridge* sim, ControllerBridge* controller, QObject* parent)
    : QObject(parent), sim_(sim), controller_(controller) {
    form_.setSettings(sim_->session.config.experiment);
    model_ = new FormModel(form_, [this](const auto& k, const auto& v) { edit(k, v); }, this);
    model_->setEditable([this](const std::string& k) { return form_.editable(k); });
    model_->setExpandedDefault("disturbance", true);
    model_->refresh(true);
    timer_.setInterval(kPollMs);
    connect(&timer_, &QTimer::timeout, this, &ExperimentsBridge::poll);
    // A scenario was loaded: its experiment settings (the runs stay for comparison).
    connect(sim_, &SimulationBridge::configChanged, this, [this] {
        form_.setSettings(sim_->session.config.experiment);
        model_->updateValues();
    });
}

ExperimentsBridge::~ExperimentsBridge() { shutdown(); }

void ExperimentsBridge::setName(const QString& n) {
    if (n != name_) {
        name_ = n;
        Q_EMIT nameChanged();
    }
}

int ExperimentsBridge::segmentCount() const {
    int n = 1;
    for (const auto& r : results_) n = std::max(n, r.nSegments());
    return n;
}

void ExperimentsBridge::setSegment(int k) {
    k = std::clamp(k, 0, segmentCount() - 1);
    if (k != segment_) {
        segment_ = k;
        plots_ = makePlots();
        Q_EMIT segmentChanged();
        Q_EMIT runsChanged();
    }
}

QVariantList ExperimentsBridge::runs() const {
    QVariantList out;
    for (std::size_t i = 0; i < results_.size(); ++i) {
        const auto& r = results_[i];
        const QString status = qs(ps::texts::statusText(r));
        QVariantList metrics;
        for (const auto& [k, v] : ps::texts::metricPairs(r)) metrics.push_back(QVariantMap{{"name", qs(k)}, {"value", qs(v)}});
        out.push_back(QVariantMap{{"index", static_cast<int>(i)},
                                  {"name", qs(r.name)},
                                  {"color", colors_[i]},
                                  {"status", status},
                                  {"stable", status == "stable"},
                                  {"visible", r.visible},
                                  {"metrics", metrics},
                                  {"gains", qs(r.gainsSummary())},
                                  {"note", qs(ps::texts::saturationNote(r.metrics))}});
    }
    return out;
}

void ExperimentsBridge::edit(const std::string& key, const ps::Value& value) {
    form_.set(key, value);
    model_->updateValues();
    if (key == "tolerance") {
        plots_ = makePlots();
        Q_EMIT runsChanged();
    }
}

void ExperimentsBridge::run() {
    if (worker_) return;
    const pendulum::AppConfig* reuse = form_.flag("reuse") && !results_.empty() ? &results_.front().config : nullptr;
    pendulum::AppConfig cfg;
    try {
        cfg = ps::experimentConfig(sim_->session.config, form_.settings(), reuse);
        cfg.validate();
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("error", QStringLiteral("Invalid configuration: ") + QString::fromUtf8(e.what()));
        return;
    }
    const QString name = name_.trimmed().isEmpty() ? placeholder() : name_.trimmed();
    ++counter_;
    worker_ = std::make_unique<ps::ExperimentWorker>(cfg, name.toStdString());
    worker_->start();
    timer_.start();
    Q_EMIT sim_->message("info", QStringLiteral("Running experiment '%1'…").arg(name));
    Q_EMIT runningChanged();
    Q_EMIT progressChanged();
    Q_EMIT runsChanged();  // the placeholder
}

void ExperimentsBridge::cancel() {
    if (worker_) worker_->cancel();
}

void ExperimentsBridge::poll() {
    if (!worker_) {
        timer_.stop();
        return;
    }
    Q_EMIT progressChanged();
    if (!worker_->done()) return;
    timer_.stop();
    auto w = std::move(worker_);
    Q_EMIT runningChanged();
    Q_EMIT progressChanged();
    if (w->error) {
        Q_EMIT sim_->message("error", QStringLiteral("Experiment failed: ") + qs(*w->error));
        return;
    }
    if (!w->result) return;
    pendulum::ExperimentResult r = std::move(*w->result);
    if (r.message == "cancelled") {
        Q_EMIT sim_->message("info", QStringLiteral("Experiment '%1' cancelled").arg(qs(r.name)));
        return;
    }
    const QString status = qs(ps::texts::statusText(r));
    const QString note = qs(ps::texts::saturationNote(r.metrics));
    const QString name = qs(r.name);
    results_.push_back(std::move(r));
    colors_.push_back(next_color_++);
    setName({});
    changed();
    Q_EMIT sim_->message("info", QStringLiteral("Experiment '%1' finished (%2)").arg(name, status));
    if (!note.isEmpty()) Q_EMIT sim_->message("warning", QStringLiteral("⚠ '%1': ").arg(name) + note);
}

void ExperimentsBridge::changed() {
    if (segment_ >= segmentCount()) {
        segment_ = 0;
        Q_EMIT segmentChanged();
    }
    plots_ = makePlots();
    Q_EMIT runsChanged();
}

void ExperimentsBridge::toggleVisible(int i) {
    if (i < 0 || i >= static_cast<int>(results_.size())) return;
    results_[static_cast<std::size_t>(i)].visible = !results_[static_cast<std::size_t>(i)].visible;
    changed();
}

void ExperimentsBridge::remove(int i) {
    if (i < 0 || i >= static_cast<int>(results_.size())) return;
    results_.erase(results_.begin() + i);
    colors_.erase(colors_.begin() + i);
    changed();
}

void ExperimentsBridge::clear() {
    results_.clear();
    colors_.clear();
    counter_ = 1;
    next_color_ = 0;
    changed();
}

void ExperimentsBridge::useGains(int i) {
    if (i < 0 || i >= static_cast<int>(results_.size())) return;
    const auto& r = results_[static_cast<std::size_t>(i)];
    if (controller_->useRunGains(r.config))
        Q_EMIT sim_->message("info", QStringLiteral("Gains of '%1' applied (undo on Controller)").arg(qs(r.name)));
}

QVariantList ExperimentsBridge::makePlots() const {
    QVariantList out;
    if (results_.empty()) return out;
    const double tol = form_.number("tolerance");
    const bool has_cart = std::any_of(results_.begin(), results_.end(), [](const auto& r) { return r.config.plant.hasCart(); });
    for (const auto& p : kPlots) {
        const std::string key = p.key;
        if (key == "cart" && !has_cart) continue;
        QVariantList series;
        for (std::size_t i = 0; i < results_.size(); ++i) {
            const auto& r = results_[i];
            if (!r.visible) continue;
            std::vector<double> y;
            if (key == "angle" && segment_ < r.nSegments())
                y = r.angleDeg(segment_);
            else if (key == "error" && segment_ < r.nSegments())
                y = r.errorDeg(segment_);
            else if (key == "effort" && r.data.count("actuator"))
                y = r.data.at("actuator");
            else if (key == "cart" && r.config.plant.hasCart() && r.data.count("x"))
                y = r.data.at("x");
            else
                continue;
            QVariantList ts, ys;
            thin(r.data.at("t"), y, ts, ys);
            series.push_back(QVariantMap{{"xs", ts}, {"ys", ys}, {"color", colors_[i]}});
        }
        const QString suffix = key == "angle" || key == "error" ? QStringLiteral(" θ%1").arg(segment_ + 1) : QString();
        out.push_back(QVariantMap{{"key", p.key},
                                  {"title", QString::fromUtf8(p.title) + suffix},
                                  {"unit", QString::fromUtf8(p.unit)},
                                  {"series", series},
                                  {"hlines", key == "error" ? QVariantList{tol, -tol} : QVariantList{}}});
    }
    return out;
}

void ExperimentsBridge::shutdown() {
    if (worker_) {
        worker_->cancel();
        worker_->wait();
    }
}

}  // namespace app
