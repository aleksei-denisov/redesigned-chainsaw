#pragma once
// QML context objects `model` and `controller` (quick/backend/parameters.py): the parameter
// screens' editors. Both wrap the core's editor forms in list models and turn each edit into a
// session edit whose operations go to the physics thread. Decisions – what a structural change
// does to the controller, restoring gains, LQR seeding – are the core's session rules; texts
// are the core's texts.
#include <QObject>
#include <QTimer>
#include <map>
#include <string>
#include <tuple>

#include "formmodel.h"
#include "pendulum/session/forms.hpp"
#include "pendulum/session/history.hpp"
#include "simulation.h"

namespace app {

/// Pendulum, cart, actuator, segments and simulation settings (the Model screen), with undo /
/// redo / reset of its edits.
class ModelBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(QObject* plantModel READ plantModel CONSTANT)
    Q_PROPERTY(QObject* simulationModel READ simulationModel CONSTANT)
    Q_PROPERTY(bool canUndo READ canUndo NOTIFY historyChanged)
    Q_PROPERTY(bool canRedo READ canRedo NOTIFY historyChanged)

public:
    explicit ModelBridge(SimulationBridge* sim, QObject* parent = nullptr);

    QObject* plantModel() { return plant_model_; }
    QObject* simulationModel() { return sim_model_; }
    bool canUndo() const { return history_.canUndo(); }
    bool canRedo() const { return history_.canRedo(); }
    FormModel* plantForm() { return plant_model_; }
    FormModel* simulationForm() { return sim_model_; }

    Q_INVOKABLE void undo();
    Q_INVOKABLE void redo();
    Q_INVOKABLE void reset();  ///< back to the scenario's plant and simulation settings (undoable)

Q_SIGNALS:
    void historyChanged();

private:
    using Snapshot = std::tuple<pendulum::PlantParams, pendulum::SimulationSettings, pendulum::ControllerConfig>;
    Snapshot snapshot() const;
    void restore(const std::optional<Snapshot>& s);
    void applyChange(const pendulum::session::PlantChange& change);
    void fill();
    void reload();
    void editPlant(const std::string& key, const pendulum::session::Value& value);
    void editSimulation(const std::string& key, const pendulum::session::Value& value);
    std::string note(const std::string& section) const;

    SimulationBridge* sim_;
    pendulum::session::EditHistory<Snapshot> history_;
    pendulum::session::PlantForm plant_form_;
    pendulum::session::SimulationForm sim_form_;
    FormModel* plant_model_;
    FormModel* sim_model_;
};

/// Controller settings, PID loops, LQR weights and the tuning actions (the Controller
/// screen), with undo / redo / reset of the controller.
class ControllerBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(QObject* model READ model CONSTANT)
    Q_PROPERTY(QString outputText READ outputText NOTIFY outputChanged)
    Q_PROPERTY(bool canUndo READ canUndo NOTIFY historyChanged)
    Q_PROPERTY(bool canRedo READ canRedo NOTIFY historyChanged)

public:
    explicit ControllerBridge(SimulationBridge* sim, QObject* parent = nullptr);

    QObject* model() { return model_; }
    FormModel* form() { return model_; }
    QString outputText() const { return output_; }
    bool canUndo() const { return history_.canUndo(); }
    bool canRedo() const { return history_.canRedo(); }
    bool active() const { return active_; }

    Q_INVOKABLE void undo();
    Q_INVOKABLE void redo();
    Q_INVOKABLE void seedLqr();
    Q_INVOKABLE void restoreGains();  ///< Reset: the controller the scenario was loaded with (undoable)
    Q_INVOKABLE void resetIntegrators();
    Q_INVOKABLE void setActive(bool on);  ///< the screen is shown: read live outputs

    /// Auto-tune's Apply (undoable): gains and the recommended push.
    void applyTuning(const pendulum::TuningResult& result);
    /// An experiment run's gains (undoable); false (with a message) for another structure.
    bool useRunGains(const pendulum::AppConfig& run);
    void forget();

Q_SIGNALS:
    void outputChanged();
    void historyChanged();

private:
    void record(const pendulum::ControllerConfig& before);
    void restore(const std::optional<pendulum::ControllerConfig>& c);
    void reload();
    void edit(const std::string& key, const pendulum::session::Value& value);
    std::string note(const std::string& section) const;
    void pollOutput();

    SimulationBridge* sim_;
    pendulum::session::EditHistory<pendulum::ControllerConfig> history_;
    pendulum::session::ControllerForm form_;
    FormModel* model_;
    QString output_;
    std::map<std::string, std::string> loop_outputs_;
    QTimer timer_;
    bool active_ = false;
};

}  // namespace app
