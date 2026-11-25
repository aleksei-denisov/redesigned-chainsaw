#pragma once
// QML context object `autotune` (quick/backend/autotune.py): the foreground robustness search
// (set-up → running → result) with the quick-core-v2 options (all loops by LQR design plus
// integral gains, or selected loops and gains by direct search; gain limits; methods; hold
// test), on the core's TuneWorker (thread pool, all cores but one) and the core's texts.
// While a search runs the simulation is paused and `busy` is true – the shell locks
// navigation; leaving the app cancels it.
//
// Differences from Python: the core keeps the current controller as a candidate, so the result
// is never worse than before (the notes say when it stayed best).
#include <QObject>
#include <QTimer>
#include <QVariantList>
#include <QVariantMap>
#include <memory>
#include <optional>

#include "pendulum/session/jobs.hpp"

namespace app {

class SimulationBridge;
class ControllerBridge;

class AutotuneBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString page READ page NOTIFY changed)
    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString preset READ preset NOTIFY changed)
    Q_PROPERTY(QVariantList presets READ presets NOTIFY changed)
    Q_PROPERTY(QVariantMap options READ options NOTIFY optionsChanged)
    Q_PROPERTY(bool hasCart READ hasCart NOTIFY optionsChanged)
    Q_PROPERTY(QVariantList loopList READ loopList NOTIFY optionsChanged)
    Q_PROPERTY(QString coresText READ coresText CONSTANT)
    Q_PROPERTY(QString phaseText READ phaseText NOTIFY progressChanged)
    Q_PROPERTY(QString activityText READ activityText NOTIFY progressChanged)
    Q_PROPERTY(QString bestText READ bestText NOTIFY progressChanged)
    Q_PROPERTY(QString bestGains READ bestGains NOTIFY progressChanged)
    Q_PROPERTY(QString timesText READ timesText NOTIFY progressChanged)
    Q_PROPERTY(double fraction READ fraction NOTIFY progressChanged)
    Q_PROPERTY(QVariantList history READ history NOTIFY progressChanged)
    Q_PROPERTY(QVariantList candidates READ candidates NOTIFY progressChanged)
    Q_PROPERTY(QString headline READ headline NOTIFY changed)
    Q_PROPERTY(QString notes READ notes NOTIFY changed)
    Q_PROPERTY(QString oldGains READ oldGains NOTIFY changed)
    Q_PROPERTY(QString newGains READ newGains NOTIFY changed)
    Q_PROPERTY(QVariantList metrics READ metrics NOTIFY changed)
    Q_PROPERTY(QVariantList preview READ preview NOTIFY changed)
    Q_PROPERTY(bool canApply READ canApply NOTIFY changed)

public:
    AutotuneBridge(SimulationBridge* sim, ControllerBridge* controller, QObject* parent = nullptr);
    ~AutotuneBridge() override;

    QString page() const { return page_; }
    bool busy() const { return worker_ != nullptr; }
    QString preset() const { return preset_; }
    QVariantList presets() const;
    QVariantMap options() const { return options_; }
    bool hasCart() const;
    QVariantList loopList() const;
    QString coresText() const;
    QString phaseText() const { return phase_; }
    QString activityText() const { return activity_; }
    QString bestText() const { return best_; }
    QString bestGains() const { return gains_; }
    QString timesText() const { return times_; }
    double fraction() const { return fraction_; }
    QVariantList history() const { return history_; }
    QVariantList candidates() const { return candidates_; }
    QString headline() const { return headline_; }
    QString notes() const { return notes_; }
    QString oldGains() const { return old_; }
    QString newGains() const { return new_; }
    QVariantList metrics() const { return metrics_; }
    QVariantList preview() const { return preview_; }
    bool canApply() const { return result_.has_value(); }
    std::optional<double> pushTestSeconds() const { return push_test_s_; }
    int workers() const { return workers_; }
    const std::optional<pendulum::TuningResult>& result() const { return result_; }

    Q_INVOKABLE void setOptions(const QVariantMap& options);
    Q_INVOKABLE void open();
    Q_INVOKABLE void choose(const QString& key);
    Q_INVOKABLE void start();
    Q_INVOKABLE void cancel();
    Q_INVOKABLE void apply();
    Q_INVOKABLE void close();
    Q_INVOKABLE void rerun();
    void shutdown();
    /// Tests: a short search (fewer candidates than the quick preset).
    void setSettingsOverride(std::optional<pendulum::TuningSettings> s) { override_ = std::move(s); }

Q_SIGNALS:
    void changed();
    void optionsChanged();
    void progressChanged();
    void busyChanged();
    void applied();

private:
    std::vector<std::string> signalList() const;
    pendulum::TuneOptions tuneOptions() const;
    QString estimate(const std::string& preset) const;
    void poll();
    void onProgress(const pendulum::TuneProgress& u);
    void tick();
    void workerDone();
    QString resultNotes(const pendulum::TuningResult& r) const;
    QVariantList previewSeries() const;

    SimulationBridge* sim_;
    ControllerBridge* controller_;
    int workers_;
    std::optional<pendulum::AppConfig> config_;
    std::unique_ptr<pendulum::session::TuneWorker> calibration_, worker_;
    std::optional<double> push_test_s_;
    std::optional<pendulum::TuningResult> result_;
    std::optional<std::pair<pendulum::ExperimentResult, pendulum::ExperimentResult>> comparison_;
    std::optional<pendulum::TuneProgress> last_;
    std::optional<pendulum::TuningSettings> override_;
    QString page_ = QStringLiteral("setup"), preset_ = QStringLiteral("normal");
    double started_ = 0.0, last_update_ = 0.0;
    bool was_running_ = false;
    QString phase_, activity_, best_ = QStringLiteral("–"), gains_, times_, headline_, notes_, old_, new_;
    double fraction_ = 0.0;
    QVariantList metrics_, history_, candidates_, preview_;
    QVariantMap options_;
    QTimer timer_;
};

}  // namespace app
