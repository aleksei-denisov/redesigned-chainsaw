#include "autotune.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QSettings>
#include <algorithm>
#include <cmath>
#include <numbers>
#include <thread>

#include "parameters.h"
#include "pendulum/session/texts.hpp"
#include "simulation.h"

namespace app {

namespace texts = pendulum::session::texts;

namespace {
constexpr int kPollMs = 100;
constexpr double kHoldCost = 2.4;  ///< rough factor on the search time with the hold test (tuning2.HOLD_COST)
const char* kOptionsKey = "quick/autotune/options";

QString qs(const std::string& s) { return QString::fromStdString(s); }

QVariantMap defaultOptions() {
    return QVariantMap{{"mode", "full"},
                       {"integral", true},
                       {"methods", false},
                       {"hold", false},
                       {"loops", QVariantList{}},
                       {"terms", QVariantList{"kp", "ki", "kd"}},
                       {"off_terms", QVariantList{}},
                       {"limit_mode", "none"},
                       {"limits", QVariantMap{{"general", QVariantMap{}}, {"per_loop", QVariantMap{}}}}};
}

QVariantMap merged(const QVariantMap& over) {
    QVariantMap m = defaultOptions();
    for (auto it = over.begin(); it != over.end(); ++it) m[it.key()] = it.value();
    return m;
}

nlohmann::json toJson(const QVariantMap& m) {
    const QByteArray bytes = QJsonDocument(QJsonObject::fromVariantMap(m)).toJson(QJsonDocument::Compact);
    return nlohmann::json::parse(bytes.constData(), bytes.constData() + bytes.size(), nullptr, false);
}

QString fmtPush(double v) { return qs(texts::fmt(v, "g")); }
}  // namespace

AutotuneBridge::AutotuneBridge(SimulationBridge* sim, ControllerBridge* controller, QObject* parent)
    : QObject(parent), sim_(sim), controller_(controller), workers_(pendulum::defaultWorkers()) {
    QSettings store;
    const QJsonDocument doc = QJsonDocument::fromJson(store.value(kOptionsKey).toString().toUtf8());
    options_ = merged(doc.isObject() ? doc.object().toVariantMap() : QVariantMap{});
    connect(sim_, &SimulationBridge::controllerChanged, this, &AutotuneBridge::optionsChanged);  // the loop list
    connect(sim_, &SimulationBridge::configChanged, this, &AutotuneBridge::optionsChanged);      // loops, cart or not
    timer_.setInterval(kPollMs);
    connect(&timer_, &QTimer::timeout, this, &AutotuneBridge::poll);
}

AutotuneBridge::~AutotuneBridge() { shutdown(); }

// ------------------------------------------------------------------ properties
QString AutotuneBridge::estimate(const std::string& preset) const {
    if (!push_test_s_) return QStringLiteral("measuring this device…");
    const auto settings = pendulum::TuningSettings::preset(preset);
    const auto options = tuneOptions();
    const double ratio = static_cast<double>(pendulum::budget(settings, options)) / std::max(settings.candidates(), 1) *
                         (options.hold ? kHoldCost : 1.0);
    return QStringLiteral("about ") +
           qs(texts::fmtDuration(pendulum::estimateSeconds(settings, *push_test_s_, workers_) * ratio));
}

QVariantList AutotuneBridge::presets() const {
    QVariantList out;
    for (const auto& p : texts::tunePresets())
        out.push_back(QVariantMap{
            {"key", qs(p.key)}, {"name", qs(p.title)}, {"description", qs(p.note)}, {"estimate", estimate(p.key)}});
    return out;
}

bool AutotuneBridge::hasCart() const { return sim_->session.config.plant.hasCart(); }

QVariantList AutotuneBridge::loopList() const {
    QVariantList out;
    for (const auto& l : sim_->session.config.controller.loops)
        out.push_back(QVariantMap{{"signal", qs(l.signal)}, {"name", qs(l.name)}, {"enabled", l.enabled}});
    return out;
}

QString AutotuneBridge::coresText() const {
    return qs(texts::tuneCores(workers_, static_cast<int>(std::max(1u, std::thread::hardware_concurrency()))));
}

std::vector<std::string> AutotuneBridge::signalList() const {
    std::vector<std::string> s;
    for (const auto& l : sim_->session.config.controller.loops) s.push_back(l.signal);
    return s;
}

pendulum::TuneOptions AutotuneBridge::tuneOptions() const { return pendulum::optionsFromJson(toJson(options_), signalList()); }

void AutotuneBridge::setOptions(const QVariantMap& options) {
    options_ = merged(options);
    QSettings().setValue(kOptionsKey,
                         QString::fromUtf8(QJsonDocument(QJsonObject::fromVariantMap(options_)).toJson(QJsonDocument::Compact)));
    Q_EMIT optionsChanged();
    Q_EMIT changed();  // the estimates
}

// ---------------------------------------------------------------------- set-up
void AutotuneBridge::open() {
    if (worker_) return;
    config_ = sim_->session.config;
    page_ = QStringLiteral("setup");
    if (!push_test_s_ && !calibration_) {
        calibration_ = std::make_unique<pendulum::session::TuneWorker>(*config_, pendulum::TuningSettings{},
                                                                      pendulum::TuneOptions{}, true);
        calibration_->start();
        timer_.start();
    }
    Q_EMIT changed();
}

void AutotuneBridge::choose(const QString& key) {
    for (const auto& p : texts::tunePresets())
        if (qs(p.key) == key) {
            preset_ = key;
            Q_EMIT changed();
            return;
        }
}

void AutotuneBridge::start() {
    if (worker_) return;
    if (!config_) config_ = sim_->session.config;
    auto settings = override_ ? *override_ : pendulum::TuningSettings::preset(preset_.toStdString());
    settings.workers = workers_;
    result_.reset();
    comparison_.reset();
    last_.reset();
    fraction_ = 0.0;
    history_.clear();
    candidates_.clear();
    phase_ = QStringLiteral("Starting the search…");
    best_ = QStringLiteral("–");
    gains_.clear();
    activity_ = QStringLiteral("Working on %1 CPU core%2").arg(workers_).arg(workers_ != 1 ? "s" : "");
    pendulum::TuneOptions options;
    try {
        options = tuneOptions();
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("warning", QStringLiteral("⚠ ") + QString::fromUtf8(e.what()));
        return;
    }
    was_running_ = sim_->running();
    sim_->pause();  // all CPU cores for the search
    started_ = monotonic();
    worker_ = std::make_unique<pendulum::session::TuneWorker>(*config_, settings, options);
    worker_->start();
    timer_.start();
    page_ = QStringLiteral("running");
    Q_EMIT busyChanged();
    Q_EMIT changed();
    Q_EMIT progressChanged();
}

void AutotuneBridge::cancel() {
    if (!worker_) return;
    phase_ = QStringLiteral("Stopping the search…");
    worker_->cancel();
    Q_EMIT progressChanged();
}

// ---------------------------------------------------------------------- result
void AutotuneBridge::apply() {
    if (result_) {
        const double push = sim_->session.config.push_force;
        controller_->applyTuning(*result_);
        sim_->setPushForce(push);  // the push force stays the user's choice
        Q_EMIT applied();
    }
    close();
}

QString AutotuneBridge::resultNotes(const pendulum::TuningResult& r) const {
    // The core's notes, without "applying sets the push force" (this app keeps it).
    QStringList notes{QStringLiteral("%1 candidates in %2.").arg(r.evaluations).arg(qs(texts::fmtDuration(r.elapsed)))};
    if (r.cancelled) notes.prepend(QStringLiteral("Cancelled – this is the best result found so far."));
    if (r.unchanged)
        notes << QStringLiteral("No candidate beat the current gains – they are kept; applying changes nothing.");
    else if (r.max_push <= r.baseline_max_push * 1.02)
        notes << QStringLiteral("The current gains are already about as robust as this search can find.");
    if (r.hold && !std::isnan(r.hold_residual)) {
        auto left = [](double v) { return std::isnan(v) ? QStringLiteral("–") : QStringLiteral("%1 %").arg(std::lround(100 * v)); };
        notes << QStringLiteral("Steady push: %1 → %2 of the %3's offset left after %4 s (0 % = back at its setpoint).")
                     .arg(left(r.baseline_hold_residual), left(r.hold_residual), qs(r.held), fmtPush(pendulum::kHoldSeconds));
    }
    notes << QStringLiteral("For testing, a tip push of about %1 N is half of what it recovers from (the push force "
                            "is not changed).")
                 .arg(fmtPush(r.recommended_push));
    return notes.join(' ');
}

void AutotuneBridge::close() {
    if (worker_) {
        worker_->cancel();
        worker_->wait();
        workerDone();
    }
    config_.reset();
    page_ = QStringLiteral("setup");
    if (was_running_) {
        was_running_ = false;
        sim_->start();
    }
    Q_EMIT changed();
}

void AutotuneBridge::rerun() {
    config_ = sim_->session.config;
    start();
}

// --------------------------------------------------------------------- polling
void AutotuneBridge::poll() {
    if (calibration_ && calibration_->done()) {
        auto w = std::move(calibration_);
        if (w->calibration) {
            push_test_s_ = w->calibration;
            Q_EMIT changed();
        }
    }
    if (worker_) {
        if (auto u = worker_->takeProgress()) onProgress(*u);
        tick();
        if (worker_->done()) workerDone();
    }
    if (!calibration_ && !worker_) timer_.stop();
}

void AutotuneBridge::onProgress(const pendulum::TuneProgress& u) {
    last_ = u;
    last_update_ = monotonic();
    phase_ = qs(u.message);
    activity_ = qs(texts::tuneActivity(u));
    if (u.best_push > 0 || u.baseline_push) {
        const auto [line, gains] = texts::tuneBest(u);
        best_ = qs(line);
        gains_ = qs(gains);
    }
    history_.clear();
    for (double v : u.history) history_.push_back(v);
    candidates_.clear();
    for (double v : u.candidate_pushes) candidates_.push_back(v > 0 ? v : std::nan(""));
}

void AutotuneBridge::tick() {
    const double since = monotonic() - last_update_;
    if (last_ && last_->total) fraction_ = texts::tuneFraction(*last_, since);
    times_ = qs(texts::tuneTimes(monotonic() - started_, last_ ? &*last_ : nullptr, since));
    Q_EMIT progressChanged();
}

void AutotuneBridge::workerDone() {
    auto w = std::move(worker_);
    if (!w) return;
    if (auto u = w->takeProgress()) onProgress(*u);
    old_ = qs(texts::gainsText(config_ ? &config_->controller : nullptr));
    if (w->outcome) {
        result_ = w->outcome->first;
        comparison_ = w->outcome->second;
        headline_ = qs(texts::tuneHeadline(*result_));
        notes_ = resultNotes(*result_);
        new_ = qs(texts::gainsText(&result_->controller));
    } else {
        result_.reset();
        comparison_.reset();
        headline_ = QStringLiteral("Auto-tune stopped");
        notes_ = w->error ? qs(*w->error) : QStringLiteral("Auto-tune stopped.");
        new_ = QStringLiteral("–");
    }
    metrics_.clear();
    if (comparison_)
        for (const auto& [name, before, after] : texts::tuneMetricRows(*comparison_))
            metrics_.push_back(QVariantMap{{"name", qs(name)}, {"before", qs(before)}, {"after", qs(after)}});
    preview_ = previewSeries();
    page_ = QStringLiteral("result");
    Q_EMIT busyChanged();
    Q_EMIT changed();
    Q_EMIT progressChanged();
}

QVariantList AutotuneBridge::previewSeries() const {
    // Both responses to the same push: θ1 in degrees over time (for a small plot).
    QVariantList out;
    if (!comparison_) return out;
    const std::pair<const char*, const pendulum::ExperimentResult*> runs[] = {{"before", &comparison_->first},
                                                                              {"after", &comparison_->second}};
    for (const auto& [name, r] : runs) {
        const auto t = r->data.find("t"), y = r->data.find("theta1");
        if (t == r->data.end() || y == r->data.end()) continue;
        const std::size_t n = std::min(t->second.size(), y->second.size());
        const std::size_t step = std::max<std::size_t>(1, n / 400);
        QVariantList ts, ys;
        for (std::size_t i = 0; i < n; i += step) {
            ts.push_back(t->second[i]);
            ys.push_back(y->second[i] * 180.0 / std::numbers::pi);
        }
        out.push_back(QVariantMap{{"name", name}, {"t", ts}, {"y", ys}});
    }
    return out;
}

void AutotuneBridge::shutdown() {
    for (auto* w : {worker_.get(), calibration_.get()})
        if (w) {
            w->cancel();
            w->wait();
        }
}

}  // namespace app
