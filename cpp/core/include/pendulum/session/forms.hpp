#pragma once
// Editor models of the parameter screens (session/forms.py): fields with label, range,
// decimals, unit and slider mapping, and the editing rules – three segment editors whatever
// the number of segments, the "uniform rod" switch, degrees and milliseconds on screen,
// loop sections rebuilt when the structure changes. Values per field key ("gravity",
// "seg1.mass", "loop0.kp", …), filled from a configuration and turned into a new one.
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <variant>
#include <vector>

#include "pendulum/control/lqr.hpp"
#include "pendulum/simulation/config.hpp"

namespace pendulum::session {

inline constexpr int kSeedMax = 9999;
inline constexpr double kGainRange = 1e6;

struct Field {
    std::string key, label;
    std::string kind = "number";  ///< number | choice | switch | stepper
    double minimum = 0.0, maximum = 1.0;
    int decimals = 3;
    std::string unit;
    std::string mapping = "linear";  ///< linear | log | symlog
    std::optional<double> step;
    std::string hint;
    std::vector<std::pair<std::string, std::string>> options;  ///< choice: (value, text)
};

struct Section {
    std::string key, title;
    std::vector<Field> fields;
    bool expanded = true;
    std::string note = {};
};

using Value = std::variant<double, bool, std::string>;

class Form {
public:
    virtual ~Form() = default;
    const std::vector<Section>& sections() const { return sections_; }
    const Field& field(const std::string& key) const { return fields_.at(key); }
    bool has(const std::string& key) const { return fields_.count(key) > 0; }
    const Value& get(const std::string& key) const { return values_.at(key); }
    double number(const std::string& key) const { return std::get<double>(values_.at(key)); }
    bool flag(const std::string& key) const { return std::get<bool>(values_.at(key)); }
    const std::string& text(const std::string& key) const { return std::get<std::string>(values_.at(key)); }
    const std::map<std::string, Value>& values() const { return values_; }

protected:
    void setSections(std::vector<Section> sections);
    void put(const std::string& key, const Value& value);  ///< clamps / converts as the field says

    std::vector<Section> sections_;
    std::map<std::string, Field> fields_;
    std::map<std::string, Value> values_;
};

/// The System screen's pendulum, cart, actuator and segment editors.
class PlantForm : public Form {
public:
    PlantForm();
    void setPlant(const PlantParams& plant);
    /// Edit one field; returns the new plant and whether the structure changed.
    std::pair<PlantParams, bool> set(const std::string& key, const Value& value);
    bool editable(const std::string& key) const;  ///< inertia is computed while "Uniform rod" is on
    bool visible(const std::string& section_key) const;
    std::string actuatorNote() const;
    PlantParams plant() const;
    SegmentParams segment(int i) const;

private:
    void setSegment(int i, const SegmentParams& s);
    void updateInertia(int i);
    std::pair<int, std::string> structure_{1, "cart"};
};

/// The System screen's simulation settings (time step in milliseconds).
class SimulationForm : public Form {
public:
    SimulationForm();
    void setSimulation(const SimulationSettings& s);
    SimulationSettings set(const std::string& key, const Value& value);
    SimulationSettings simulation() const;
};

/// The Controller screen: global settings, one section per loop, LQR weights.
class ControllerForm : public Form {
public:
    ControllerForm();
    bool setConfig(const ControllerConfig& c);  ///< true when the loop sections were rebuilt
    /// Edit one field; the new controller (nullopt for an LQR weight).
    std::optional<ControllerConfig> set(const std::string& key, const Value& value);
    PIDLoopConfig loopConfig(int k) const;
    ControllerConfig config() const;
    LQRWeights weights() const;
    std::vector<std::string> loopSignals() const;

private:
    void rebuild(const std::vector<PIDLoopConfig>& loops);
    void setLoop(int k, const PIDLoopConfig& c);
    ControllerConfig ctrl_;
    std::vector<PIDLoopConfig> loops_;
};

/// The Experiments screen's settings.
class ExperimentForm : public Form {
public:
    ExperimentForm();
    void setSettings(const ExperimentSettings& e);
    ExperimentSettings set(const std::string& key, const Value& value);
    bool editable(const std::string& key) const;  ///< push values only matter when the push is on
    ExperimentSettings settings() const;
};

std::string loopTitle(const PIDLoopConfig& loop);

}  // namespace pendulum::session
