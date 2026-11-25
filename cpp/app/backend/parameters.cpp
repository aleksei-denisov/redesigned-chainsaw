#include "parameters.h"

#include <cmath>
#include <exception>
#include <numbers>

#include "pendulum/session/texts.hpp"
#include "pendulum/simulation/telemetry.hpp"

namespace app {

namespace ps = pendulum::session;

namespace {
constexpr double kOutputHz = 5.0;
QString qs(const std::string& s) { return QString::fromStdString(s); }
QString warning(const std::exception& e) { return QStringLiteral("⚠ ") + QString::fromUtf8(e.what()); }
}  // namespace

// =========================================================================== Model
ModelBridge::ModelBridge(SimulationBridge* sim, QObject* parent) : QObject(parent), sim_(sim) {
    fill();
    plant_model_ = new FormModel(plant_form_, [this](const auto& k, const auto& v) { editPlant(k, v); }, this);
    plant_model_->setVisible([this](const std::string& s) { return plant_form_.visible(s); });
    plant_model_->setEditable([this](const std::string& k) { return plant_form_.editable(k); });
    plant_model_->setNote([this](const std::string& s) { return note(s); });
    plant_model_->refresh(true);
    sim_model_ = new FormModel(sim_form_, [this](const auto& k, const auto& v) { editSimulation(k, v); }, this);
    sim_model_->setExpandedDefault("simulation", true);  // its own card on the Model screen
    sim_model_->refresh(true);
    connect(sim_, &SimulationBridge::configChanged, this, [this] {
        reload();
        history_.clear();
        Q_EMIT historyChanged();
    });
}

ModelBridge::Snapshot ModelBridge::snapshot() const {
    const auto& c = sim_->session.config;
    return {c.plant, c.simulation, c.controller};
}

void ModelBridge::undo() { restore(history_.undo(snapshot())); }
void ModelBridge::redo() { restore(history_.redo(snapshot())); }

void ModelBridge::reset() {
    const auto& s = sim_->session;
    history_.record(snapshot());
    restore(Snapshot{s.original_plant, s.original_simulation, s.original_controller});
}

void ModelBridge::restore(const std::optional<Snapshot>& snap) {
    Q_EMIT historyChanged();
    if (!snap) return;
    const auto& [plant, simulation, controller] = *snap;
    ps::PlantChange change;
    try {
        change = sim_->session.restoreModel(plant, simulation, controller);
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("warning", warning(e));
        return;
    }
    fill();
    sim_model_->updateValues();
    applyChange(change);
}

void ModelBridge::applyChange(const ps::PlantChange& change) {
    sim_->applyOperations(change.operations);
    if (change.reset_if_idle) sim_->host().send(cmd::ResetIfIdle{});
    if (change.structural) {
        sim_->structureEdited();
        Q_EMIT sim_->controllerChanged();
        plant_model_->refresh();  // sections shown change (cart, segments)
    } else {
        plant_model_->updateValues();  // e.g. the computed rod inertia
    }
    Q_EMIT sim_->plantChanged(change.structural);
}

std::string ModelBridge::note(const std::string& section) const {
    if (section == "actuator") return plant_form_.actuatorNote();
    for (const auto& s : plant_form_.sections())
        if (s.key == section) return s.note;
    return {};
}

void ModelBridge::fill() {
    plant_form_.setPlant(sim_->session.config.plant);
    sim_form_.setSimulation(sim_->session.config.simulation);
}

void ModelBridge::reload() {
    fill();
    plant_model_->refresh();
    sim_model_->refresh();
}

void ModelBridge::editPlant(const std::string& key, const ps::Value& value) {
    const Snapshot before = snapshot();
    const auto [plant, structural] = plant_form_.set(key, value);
    ps::PlantChange change;
    try {
        change = sim_->session.changePlant(plant, structural);
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("warning", warning(e));
        fill();  // back to the values in force
        plant_model_->updateValues();
        return;
    }
    history_.record(before);
    Q_EMIT historyChanged();
    if (structural) Q_EMIT sim_->message("info", QString::fromUtf8(ps::texts::kStructureChanged));
    applyChange(change);
}

void ModelBridge::editSimulation(const std::string& key, const ps::Value& value) {
    const Snapshot before = snapshot();
    const auto settings = sim_form_.set(key, value);
    try {
        const auto ops = sim_->session.changeSimulation(settings);
        history_.record(before);
        Q_EMIT historyChanged();
        sim_->applyOperations(ops);
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("warning", warning(e));
        sim_form_.setSimulation(sim_->session.config.simulation);
    }
    sim_model_->updateValues();
}

// ====================================================================== Controller
ControllerBridge::ControllerBridge(SimulationBridge* sim, QObject* parent) : QObject(parent), sim_(sim) {
    form_.setConfig(sim_->session.config.controller);
    model_ = new FormModel(form_, [this](const auto& k, const auto& v) { edit(k, v); }, this);
    model_->setNote([this](const std::string& s) { return note(s); });
    model_->refresh(true);
    connect(sim_, &SimulationBridge::controllerChanged, this, &ControllerBridge::reload);
    connect(sim_, &SimulationBridge::configChanged, this, &ControllerBridge::forget);
    timer_.setInterval(static_cast<int>(1000.0 / kOutputHz));
    connect(&timer_, &QTimer::timeout, this, &ControllerBridge::pollOutput);
}

void ControllerBridge::record(const pendulum::ControllerConfig& before) {
    history_.record(before);
    Q_EMIT historyChanged();
}

void ControllerBridge::forget() {
    history_.clear();
    Q_EMIT historyChanged();
}

void ControllerBridge::undo() { restore(history_.undo(sim_->session.config.controller)); }
void ControllerBridge::redo() { restore(history_.redo(sim_->session.config.controller)); }

void ControllerBridge::restore(const std::optional<pendulum::ControllerConfig>& c) {
    Q_EMIT historyChanged();
    if (!c) return;
    try {
        sim_->applyOperations(sim_->session.restoreController(*c));
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("warning", warning(e));
        return;
    }
    Q_EMIT sim_->controllerChanged();
}

std::string ControllerBridge::note(const std::string& section) const {
    if (section.rfind("loop", 0) == 0) {
        auto it = loop_outputs_.find(section);
        return it == loop_outputs_.end() ? std::string() : it->second;
    }
    if (section == "controller") return output_.toStdString();
    return {};
}

void ControllerBridge::reload() {
    if (form_.setConfig(sim_->session.config.controller))
        model_->refresh();
    else
        model_->updateValues();
}

void ControllerBridge::edit(const std::string& key, const ps::Value& value) {
    const auto ctrl = form_.set(key, value);
    if (!ctrl) return;  // an LQR weight: used by the next seeding
    const auto before = sim_->session.config.controller;
    try {
        const auto ops = sim_->session.changeController(*ctrl);
        record(before);
        sim_->applyOperations(ops);
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("warning", warning(e));
        form_.setConfig(sim_->session.config.controller);
    }
    model_->updateValues();
}

void ControllerBridge::seedLqr() {
    const auto before = sim_->session.config.controller;
    const auto base = form_.config();
    std::vector<ps::Operation> ops;
    try {
        ops = sim_->session.seedLqr(form_.weights(), &base).second;
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("error", QStringLiteral("The LQR design could not be computed for this plant:\n") +
                                          QString::fromUtf8(e.what()));
        return;
    }
    record(before);
    sim_->applyOperations(ops);
    reload();
    Q_EMIT sim_->message("info", QString::fromUtf8(ps::texts::kLqrSeeded));
}

void ControllerBridge::restoreGains() {
    const auto before = sim_->session.config.controller;
    std::vector<ps::Operation> ops;
    try {
        ops = sim_->session.restoreGains().second;
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("warning", warning(e));
        return;
    }
    record(before);
    sim_->applyOperations(ops);
    reload();
    Q_EMIT sim_->message("info", QString::fromUtf8(ps::texts::kGainsRestored));
}

void ControllerBridge::resetIntegrators() {
    sim_->applyOperations({ps::op::ResetIntegrators{}});
    Q_EMIT sim_->message("info", QStringLiteral("Integrators reset"));
}

void ControllerBridge::applyTuning(const pendulum::TuningResult& result) {
    record(sim_->session.config.controller);
    sim_->applyOperations(sim_->session.applyTuning(result));
    reload();
    Q_EMIT sim_->pushForceChanged();
    Q_EMIT sim_->message("info", qs(ps::texts::tuningApplied(result)));
}

bool ControllerBridge::useRunGains(const pendulum::AppConfig& run) {
    const auto before = sim_->session.config.controller;
    std::vector<ps::Operation> ops;
    try {
        ops = sim_->session.useRunGains(run).second;
    } catch (const std::exception& e) {
        Q_EMIT sim_->message("warning", QString::fromUtf8(e.what()));
        return false;
    }
    record(before);
    sim_->applyOperations(ops);
    reload();
    return true;
}

void ControllerBridge::setActive(bool on) {
    active_ = on;
    if (on) {
        timer_.start();
        pollOutput();
    } else {
        timer_.stop();
    }
}

void ControllerBridge::pollOutput() {
    const SimHost& host = sim_->host();
    const double command = host.latest(pendulum::kCommand), unsat = host.latest(pendulum::kUnsaturated);
    if (std::isnan(command)) return;
    output_ = QString::asprintf("Output: command %+.3f · unsaturated %+.3f", command, unsat);
    const auto signals_ = form_.loopSignals();
    for (int k = 0; k < static_cast<int>(signals_.size()) && k < pendulum::kMaxLoops; ++k) {
        const double error = host.latest(pendulum::loopColumn(k, 0)), p = host.latest(pendulum::loopColumn(k, 1)),
                     i = host.latest(pendulum::loopColumn(k, 2)), d = host.latest(pendulum::loopColumn(k, 3));
        const QString e = signals_[static_cast<std::size_t>(k)] != "cart"
                              ? QString::asprintf("%+.2f°", error * 180.0 / std::numbers::pi)
                              : QString::asprintf("%+.4f m", error);
        loop_outputs_["loop" + std::to_string(k)] =
            (QStringLiteral("live: error ") + e + QString::asprintf(" · P %+.3g · I %+.3g · D %+.3g", p, i, d))
                .toStdString();
    }
    Q_EMIT outputChanged();
    model_->updateValues();
}

}  // namespace app
