#pragma once
// QML context object `experiments` (quick/backend/experiments.py): run the current set-up and
// compare runs (the Experiments screen), on the core's pieces: the settings form, the run
// configuration (with "Same start" every run begins from the first run's plant, initial state
// and noise, so only the controller differs), the ExperimentWorker thread and the texts. Each
// run gets a colour index; the comparison plots (angle, control error with the settling band,
// actuator output, cart position) are thinned to a few hundred points per run.
//
// New in C++: a run that spent ≥ 10 % of the time at the actuator limit gets a warning
// (improvement 2).
#include <QObject>
#include <QTimer>
#include <QVariantList>
#include <memory>
#include <vector>

#include "formmodel.h"
#include "pendulum/session/forms.hpp"
#include "pendulum/session/jobs.hpp"

namespace app {

class SimulationBridge;
class ControllerBridge;

class ExperimentsBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(QObject* model READ model CONSTANT)
    Q_PROPERTY(bool running READ running NOTIFY runningChanged)
    Q_PROPERTY(double progress READ progress NOTIFY progressChanged)
    Q_PROPERTY(QString name READ name WRITE setName NOTIFY nameChanged)
    Q_PROPERTY(QString placeholder READ placeholder NOTIFY runsChanged)
    Q_PROPERTY(int segment READ segment WRITE setSegment NOTIFY segmentChanged)
    Q_PROPERTY(int segmentCount READ segmentCount NOTIFY runsChanged)
    Q_PROPERTY(QVariantList plots READ plots NOTIFY runsChanged)
    Q_PROPERTY(QVariantList runs READ runs NOTIFY runsChanged)

public:
    ExperimentsBridge(SimulationBridge* sim, ControllerBridge* controller, QObject* parent = nullptr);
    ~ExperimentsBridge() override;

    QObject* model() { return model_; }
    FormModel* form() { return model_; }
    bool running() const { return worker_ != nullptr; }
    double progress() const { return worker_ ? worker_->progress() : 0.0; }
    QString name() const { return name_; }
    void setName(const QString& n);
    QString placeholder() const { return QStringLiteral("Run %1").arg(counter_); }
    int segment() const { return segment_; }
    void setSegment(int k);
    int segmentCount() const;
    QVariantList plots() const { return plots_; }
    QVariantList runs() const;
    const std::vector<pendulum::ExperimentResult>& results() const { return results_; }

    Q_INVOKABLE void run();
    Q_INVOKABLE void cancel();
    Q_INVOKABLE void toggleVisible(int i);
    Q_INVOKABLE void remove(int i);
    Q_INVOKABLE void clear();
    Q_INVOKABLE void useGains(int i);
    void shutdown();
    void poll();  ///< public for tests

Q_SIGNALS:
    void runsChanged();
    void progressChanged();
    void runningChanged();
    void nameChanged();
    void segmentChanged();

private:
    void edit(const std::string& key, const pendulum::session::Value& value);
    void changed();
    QVariantList makePlots() const;

    SimulationBridge* sim_;
    ControllerBridge* controller_;
    pendulum::session::ExperimentForm form_;
    FormModel* model_;
    std::vector<pendulum::ExperimentResult> results_;
    std::vector<int> colors_;  ///< colour index per run (kept when runs are removed)
    std::unique_ptr<pendulum::session::ExperimentWorker> worker_;
    int counter_ = 1, next_color_ = 0, segment_ = 0;
    QString name_;
    QVariantList plots_;
    QTimer timer_;
};

/// At most `points` samples: min and max of each bucket, so peaks survive.
void thin(const std::vector<double>& t, const std::vector<double>& y, QVariantList& ts, QVariantList& ys,
          std::size_t points = 600);

}  // namespace app
