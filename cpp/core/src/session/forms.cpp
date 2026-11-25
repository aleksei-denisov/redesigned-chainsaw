#include "pendulum/session/forms.hpp"

#include <cmath>
#include <numbers>
#include <stdexcept>

namespace pendulum::session {

namespace {

double degrees(double r) { return r * (180.0 / std::numbers::pi); }
double radians(double d) { return d * (std::numbers::pi / 180.0); }

Field numField(std::string key, std::string label, double minimum, double maximum, int decimals = 3,
             std::string unit = "", std::string mapping = "linear", std::optional<double> step = std::nullopt,
             std::string hint = "") {
    Field f;
    f.key = std::move(key);
    f.label = std::move(label);
    f.minimum = minimum;
    f.maximum = maximum;
    f.decimals = decimals;
    f.unit = std::move(unit);
    f.mapping = std::move(mapping);
    f.step = step;
    f.hint = std::move(hint);
    return f;
}

Field other(std::string key, std::string label, std::string kind, std::string hint = "",
            std::vector<std::pair<std::string, std::string>> options = {}) {
    Field f;
    f.key = std::move(key);
    f.label = std::move(label);
    f.kind = std::move(kind);
    f.hint = std::move(hint);
    f.options = std::move(options);
    return f;
}

Field stepper(std::string key, std::string label, double minimum, double maximum) {
    Field f = other(std::move(key), std::move(label), "stepper");
    f.minimum = minimum;
    f.maximum = maximum;
    f.decimals = 0;
    return f;
}

std::vector<Field> segmentFields(int i) {
    const std::string p = "seg" + std::to_string(i) + ".";
    return {numField(p + "length", "Length", 0.02, 5.0, 3, "m", "log"),
            numField(p + "mass", "Mass", 0.001, 50.0, 3, "kg", "log"),
            numField(p + "com", "Centre of mass", 0.0, 1.0, 3, "× length"),
            other(p + "rod", "Uniform rod", "switch", "inertia I = m·L²/12"),
            numField(p + "inertia", "Inertia about the COM", 0.0, 10.0, 6, "kg·m²", "symlog"),
            numField(p + "viscous", "Joint viscous friction", 0.0, 5.0, 5, "N·m·s/rad", "symlog"),
            numField(p + "coulomb", "Joint dry friction", 0.0, 5.0, 4, "N·m", "symlog"),
            numField(p + "angle", "Initial angle θ₀", -180.0, 180.0, 2, "°"),
            numField(p + "velocity", "Initial angular velocity ω₀", -2000.0, 2000.0, 1, "°/s", "symlog")};
}

std::vector<Section> plantSections() {
    std::vector<Section> s = {
        {"pendulum", "Pendulum",
         {other("base", "Base", "choice", "", {{"cart", "Cart on a track"}, {"fixed", "Fixed pivot"}}),
          stepper("segments", "Segments", 1, kMaxSegments), numField("gravity", "Gravity", 0.0, 30.0, 3, "m/s²")}},
        {"cart", "Cart",
         {numField("cart_mass", "Cart mass", 0.01, 100.0, 3, "kg", "log"),
          numField("cart_friction", "Cart friction", 0.0, 50.0, 3, "N·s/m", "symlog"),
          numField("track", "Track length", 0.2, 50.0, 2, "m", "log")}},
        {"actuator", "Actuator",
         {numField("max_out", "Max output", 0.001, 10000.0, 3, "", "log"),
          numField("rate", "Rate limit", 0.0, 1e6, 1, "/s", "symlog", std::nullopt, "0 = unlimited"),
          numField("tau", "Time constant", 0.0, 1.0, 4, "s", "symlog", std::nullopt, "0 = ideal"),
          numField("deadband", "Deadband", 0.0, 1000.0, 4, "", "symlog")},
         false},
    };
    for (int i = 0; i < kMaxSegments; ++i)
        s.push_back({"seg" + std::to_string(i), "Segment " + std::to_string(i + 1), segmentFields(i), i == 0,
                     "Initial conditions (used on reset)"});
    return s;
}

const std::vector<std::pair<std::string, std::string>> kActionOptions = {{"direct", "Direct  e = y − r"},
                                                                          {"reverse", "Reverse  e = r − y"}};
const std::vector<std::pair<std::string, std::string>> kAntiWindupOptions = {
    {"none", "None"}, {"clamp", "Clamping"}, {"back_calculation", "Back-calculation"}};
const std::vector<std::pair<std::string, std::string>> kDerivativeOptions = {
    {"measured_rate", "Measured rate"}, {"filtered_difference", "Filtered difference"}};
const char* kWeightNames[] = {"cart_position", "angle", "cart_velocity", "angular_velocity", "effort"};

Section lqrSection() {
    return {"lqr", "LQR weights for seeding",
            {numField("lqr.cart_position", "Cart position weight", 1e-2, 1e3, 3, "", "log"),
             numField("lqr.angle", "Angle weight", 1e-1, 1e5, 2, "", "log"),
             numField("lqr.cart_velocity", "Cart velocity weight", 1e-3, 1e3, 3, "", "log"),
             numField("lqr.angular_velocity", "Angular velocity weight", 1e-3, 1e3, 3, "", "log"),
             numField("lqr.effort", "Effort weight R", 1e-4, 1e1, 4, "", "log", std::nullopt, "larger = gentler control")},
            false};
}

Section controllerSection() {
    return {"controller", "Controller",
            {other("enabled", "Controller enabled", "switch"), other("action", "Action", "choice", "", kActionOptions),
             numField("freq", "Update frequency", 1.0, 10000.0, 1, "Hz", "log"),
             numField("out_min", "Output minimum", -1e6, 1e6, 3, "", "symlog"),
             numField("out_max", "Output maximum", -1e6, 1e6, 3, "", "symlog")}};
}

std::vector<Field> loopFields(int k, const std::string& signal) {
    const bool angle = signal.rfind("theta", 0) == 0;
    const std::string unit = angle ? "rad" : "m";
    const std::string p = "loop" + std::to_string(k) + ".";
    return {other(p + "enabled", "Loop enabled", "switch"),
            numField(p + "kp", "Kp – proportional", -kGainRange, kGainRange, 3, "", "symlog", std::nullopt, "output per " + unit),
            numField(p + "ki", "Ki – integral", -kGainRange, kGainRange, 3, "", "symlog", std::nullopt, "output per " + unit + "·s"),
            numField(p + "kd", "Kd – derivative", -kGainRange, kGainRange, 3, "", "symlog", std::nullopt, "output per " + unit + "/s"),
            other(p + "p", "P term", "switch"),
            other(p + "i", "I term", "switch"),
            other(p + "d", "D term", "switch"),
            numField(p + "setpoint", "Setpoint", angle ? -180.0 : -10.0, angle ? 180.0 : 10.0, angle ? 2 : 3, angle ? "°" : "m"),
            other(p + "anti", "Anti-windup", "choice", "", kAntiWindupOptions),
            numField(p + "kt", "Tracking gain Kt", 0.0, 1e4, 2, "1/s", "symlog"),
            numField(p + "ilimit", "Integral limit", 0.0, 1e6, 3, "", "symlog", std::nullopt,
                   "|integral term| in output units, 0 = none"),
            other(p + "dmode", "Derivative source", "choice", "", kDerivativeOptions),
            numField(p + "dfilter", "Derivative filter", 0.1, 5000.0, 1, "Hz", "log")};
}

}  // namespace

// ------------------------------------------------------------------------- Form
void Form::setSections(std::vector<Section> sections) {
    sections_ = std::move(sections);
    fields_.clear();
    for (const auto& s : sections_)
        for (const auto& f : s.fields) fields_[f.key] = f;
}

void Form::put(const std::string& key, const Value& value) {
    const Field& f = fields_.at(key);
    auto asNumber = [&](const Value& v) {
        if (const auto* d = std::get_if<double>(&v)) return *d;
        if (const auto* b = std::get_if<bool>(&v)) return *b ? 1.0 : 0.0;
        return std::stod(std::get<std::string>(v));
    };
    if (f.kind == "switch") {
        const auto* b = std::get_if<bool>(&value);
        values_[key] = b ? *b : asNumber(value) != 0.0;
    } else if (f.kind == "stepper") {
        values_[key] = std::nearbyint(std::min(std::max(asNumber(value), f.minimum), f.maximum));
    } else if (f.kind == "number") {
        values_[key] = std::min(std::max(asNumber(value), f.minimum), f.maximum);
    } else {
        values_[key] = value;
    }
}

// -------------------------------------------------------------------- PlantForm
PlantForm::PlantForm() { setSections(plantSections()); }

void PlantForm::setPlant(const PlantParams& plant) {
    put("base", toString(plant.base_type));
    put("segments", static_cast<double>(plant.nSegments()));
    put("gravity", plant.gravity);
    put("cart_mass", plant.cart_mass);
    put("cart_friction", plant.cart_viscous_friction);
    put("track", plant.track_length);
    put("max_out", plant.actuator.max_output);
    put("rate", plant.actuator.rate_limit);
    put("tau", plant.actuator.time_constant);
    put("deadband", plant.actuator.deadband);
    for (int i = 0; i < plant.nSegments(); ++i) setSegment(i, plant.segments[i]);
    for (int i = plant.nSegments(); i < kMaxSegments; ++i) {  // unused editors: the last segment, at rest
        SegmentParams t = plant.segments.back();
        t.initial_angle = t.initial_velocity = 0.0;
        setSegment(i, t);
    }
    structure_ = {plant.nSegments(), toString(plant.base_type)};
}

void PlantForm::setSegment(int i, const SegmentParams& s) {
    const std::string p = "seg" + std::to_string(i) + ".";
    put(p + "length", s.length);
    put(p + "mass", s.mass);
    put(p + "com", s.com_ratio);
    put(p + "inertia", s.inertia);
    const double rod = SegmentParams::rodInertia(s.mass, s.length);
    put(p + "rod", std::abs(s.inertia - rod) <= std::max(1e-6 * std::max(std::abs(s.inertia), std::abs(rod)), 1e-12));
    put(p + "viscous", s.joint_viscous_friction);
    put(p + "coulomb", s.joint_coulomb_friction);
    put(p + "angle", degrees(s.initial_angle));
    put(p + "velocity", degrees(s.initial_velocity));
    updateInertia(i);
}

void PlantForm::updateInertia(int i) {
    const std::string p = "seg" + std::to_string(i) + ".";
    if (flag(p + "rod")) put(p + "inertia", SegmentParams::rodInertia(number(p + "mass"), number(p + "length")));
}

std::pair<PlantParams, bool> PlantForm::set(const std::string& key, const Value& value) {
    put(key, value);
    if (key.size() > 3 && key.compare(0, 3, "seg") == 0 && std::isdigit(static_cast<unsigned char>(key[3])))
        updateInertia(key[3] - '0');
    PlantParams p = plant();
    const std::pair<int, std::string> s{p.nSegments(), toString(p.base_type)};
    const bool structural = s != structure_;
    structure_ = s;
    return {p, structural};
}

bool PlantForm::editable(const std::string& key) const {
    const std::string suffix = ".inertia";
    if (key.size() > suffix.size() && key.compare(key.size() - suffix.size(), suffix.size(), suffix) == 0)
        return !flag(key.substr(0, 5) + "rod");
    return true;
}

bool PlantForm::visible(const std::string& section) const {
    if (section == "cart") return text("base") == "cart";
    if (section.rfind("seg", 0) == 0) return std::stoi(section.substr(3)) < number("segments");
    return true;
}

std::string PlantForm::actuatorNote() const {
    return text("base") == "cart" ? "Force on the cart, N" : "Torque at the pivot, N·m";
}

PlantParams PlantForm::plant() const {
    PlantParams p;
    p.base_type = baseTypeFromString(text("base"));
    p.gravity = number("gravity");
    p.cart_mass = number("cart_mass");
    p.cart_viscous_friction = number("cart_friction");
    p.track_length = number("track");
    p.segments.clear();
    for (int i = 0; i < static_cast<int>(number("segments")); ++i) p.segments.push_back(segment(i));
    p.actuator = {number("max_out"), number("rate"), number("tau"), number("deadband")};
    return p;
}

SegmentParams PlantForm::segment(int i) const {
    const std::string p = "seg" + std::to_string(i) + ".";
    SegmentParams s;
    s.length = number(p + "length");
    s.mass = number(p + "mass");
    s.com_ratio = number(p + "com");
    s.inertia = number(p + "inertia");
    s.joint_viscous_friction = number(p + "viscous");
    s.joint_coulomb_friction = number(p + "coulomb");
    s.initial_angle = radians(number(p + "angle"));
    s.initial_velocity = radians(number(p + "velocity"));
    return s;
}

// --------------------------------------------------------------- SimulationForm
SimulationForm::SimulationForm() {
    setSections({{"simulation", "Simulation",
                  {numField("dt", "Physics time step", 0.01, 10.0, 3, "ms", "log", std::nullopt,
                          "smaller = more accurate, slower"),
                   numField("telemetry", "Telemetry rate", 1.0, 2000.0, 0, "Hz", "log"),
                   numField("noise_angle", "Angle noise", 0.0, 10.0, 3, "°", "symlog"),
                   numField("noise_rate", "Angular-rate noise", 0.0, 100.0, 2, "°/s", "symlog"),
                   numField("noise_pos", "Cart-position noise", 0.0, 0.1, 4, "m", "symlog"),
                   numField("seed", "Noise seed", 0, kSeedMax, 0, "", "linear", 1.0,
                          "any whole number; the same seed gives the same noise")},
                  false,
                  "Sensor noise (Gaussian, seeded – runs stay repeatable)"}});
}

void SimulationForm::setSimulation(const SimulationSettings& s) {
    put("dt", s.dt * 1000.0);
    put("telemetry", s.telemetry_rate_hz);
    put("noise_angle", s.sensor_noise_angle_deg);
    put("noise_rate", s.sensor_noise_rate_dps);
    put("noise_pos", s.sensor_noise_position);
    put("seed", static_cast<double>(s.noise_seed));
}

SimulationSettings SimulationForm::set(const std::string& key, const Value& value) {
    put(key, value);
    return simulation();
}

SimulationSettings SimulationForm::simulation() const {
    SimulationSettings s;
    s.dt = number("dt") / 1000.0;
    s.sensor_noise_angle_deg = number("noise_angle");
    s.sensor_noise_rate_dps = number("noise_rate");
    s.sensor_noise_position = number("noise_pos");
    s.noise_seed = static_cast<std::int64_t>(number("seed"));
    s.telemetry_rate_hz = number("telemetry");
    return s;
}

// --------------------------------------------------------------- ControllerForm
std::string loopTitle(const PIDLoopConfig& loop) {
    if (loop.signal == "cart") return "Cart position loop";
    const std::string n = loop.signal.substr(5);
    return "Segment " + n + " angle loop (θ" + n + ")";
}

ControllerForm::ControllerForm() {
    setSections({lqrSection(), controllerSection()});
    ctrl_ = ControllerConfig{};
    const LQRWeights w;
    const double v[] = {w.cart_position, w.angle, w.cart_velocity, w.angular_velocity, w.effort};
    for (int i = 0; i < 5; ++i) put(std::string("lqr.") + kWeightNames[i], v[i]);
}

void ControllerForm::rebuild(const std::vector<PIDLoopConfig>& loops) {
    std::vector<Section> s = {lqrSection(), controllerSection()};
    for (std::size_t k = 0; k < loops.size(); ++k)
        s.push_back({"loop" + std::to_string(k), loopTitle(loops[k]), loopFields(static_cast<int>(k), loops[k].signal),
                     k == 0 || loops[k].signal == "theta1"});
    setSections(std::move(s));
    for (auto it = values_.begin(); it != values_.end();)
        it = it->first.rfind("loop", 0) == 0 ? values_.erase(it) : std::next(it);
}

bool ControllerForm::setConfig(const ControllerConfig& c) {
    ctrl_ = c;
    put("enabled", c.enabled);
    put("action", toString(c.action));
    put("freq", c.frequency_hz);
    put("out_min", c.output_min);
    put("out_max", c.output_max);
    bool rebuilt = c.loops.size() != loops_.size();
    for (std::size_t k = 0; !rebuilt && k < c.loops.size(); ++k) rebuilt = c.loops[k].signal != loops_[k].signal;
    if (rebuilt) rebuild(c.loops);
    loops_ = c.loops;
    for (std::size_t k = 0; k < c.loops.size(); ++k) setLoop(static_cast<int>(k), c.loops[k]);
    return rebuilt;
}

void ControllerForm::setLoop(int k, const PIDLoopConfig& c) {
    const std::string p = "loop" + std::to_string(k) + ".";
    loops_[k] = c;
    put(p + "enabled", c.enabled);
    put(p + "kp", c.kp);
    put(p + "ki", c.ki);
    put(p + "kd", c.kd);
    put(p + "p", c.p_enabled);
    put(p + "i", c.i_enabled);
    put(p + "d", c.d_enabled);
    put(p + "setpoint", c.isAngle() ? degrees(c.setpoint) : c.setpoint);
    put(p + "anti", toString(c.anti_windup));
    put(p + "kt", c.back_calculation_gain);
    put(p + "ilimit", c.integral_limit);
    put(p + "dmode", toString(c.derivative_mode));
    put(p + "dfilter", c.derivative_filter_hz);
}

std::optional<ControllerConfig> ControllerForm::set(const std::string& key, const Value& value) {
    put(key, value);
    if (key.rfind("lqr.", 0) == 0) return std::nullopt;
    ctrl_ = config();
    return ctrl_;
}

PIDLoopConfig ControllerForm::loopConfig(int k) const {
    const std::string p = "loop" + std::to_string(k) + ".";
    PIDLoopConfig c = loops_[k];
    c.enabled = flag(p + "enabled");
    c.kp = number(p + "kp");
    c.ki = number(p + "ki");
    c.kd = number(p + "kd");
    c.p_enabled = flag(p + "p");
    c.i_enabled = flag(p + "i");
    c.d_enabled = flag(p + "d");
    const double sp = number(p + "setpoint");
    c.setpoint = c.isAngle() ? radians(sp) : sp;
    c.anti_windup = antiWindupFromString(text(p + "anti"));
    c.back_calculation_gain = number(p + "kt");
    c.integral_limit = number(p + "ilimit");
    c.derivative_mode = derivativeModeFromString(text(p + "dmode"));
    c.derivative_filter_hz = number(p + "dfilter");
    return c;
}

ControllerConfig ControllerForm::config() const {
    ControllerConfig c = ctrl_;
    c.enabled = flag("enabled");
    c.action = controlActionFromString(text("action"));
    c.frequency_hz = number("freq");
    c.output_min = number("out_min");
    c.output_max = number("out_max");
    c.loops.clear();
    for (std::size_t k = 0; k < loops_.size(); ++k) c.loops.push_back(loopConfig(static_cast<int>(k)));
    return c;
}

LQRWeights ControllerForm::weights() const {
    return {number("lqr.cart_position"), number("lqr.angle"), number("lqr.cart_velocity"),
            number("lqr.angular_velocity"), number("lqr.effort")};
}

std::vector<std::string> ControllerForm::loopSignals() const {
    std::vector<std::string> s;
    for (const auto& l : loops_) s.push_back(l.signal);
    return s;
}

// --------------------------------------------------------------- ExperimentForm
ExperimentForm::ExperimentForm() {
    setSections({{"experiment", "Experiment",
                  {numField("duration", "Duration", 0.1, 600.0, 1, "s", "log"),
                   numField("tolerance", "Settling band ±", 0.01, 45.0, 2, "°", "log", std::nullopt,
                          "settling time = first time after which every segment stays inside the band"),
                   other("reuse", "Same start as the first run", "switch",
                         "plant, initial state and noise of the first run")}},
                 {"disturbance", "Scheduled disturbance",
                  {other("dist_on", "Push the tip during the run", "switch",
                         "off: the run starts from the initial state with no push – the values below are not used"),
                   numField("dist_time", "Start time", 0.0, 600.0, 2, "s"),
                   numField("dist_force", "Force", -1000.0, 1000.0, 3, "N", "symlog"),
                   numField("dist_dur", "Duration", 0.001, 10.0, 3, "s", "log")},
                  false}});
    setSettings(ExperimentSettings{});
    put("reuse", true);
}

void ExperimentForm::setSettings(const ExperimentSettings& e) {
    put("duration", e.duration);
    put("tolerance", e.settle_tolerance_deg);
    put("dist_on", e.disturbance.enabled);
    put("dist_time", e.disturbance.time);
    put("dist_force", e.disturbance.force);
    put("dist_dur", e.disturbance.duration);
}

ExperimentSettings ExperimentForm::set(const std::string& key, const Value& value) {
    put(key, value);
    return settings();
}

bool ExperimentForm::editable(const std::string& key) const {
    if (key == "dist_time" || key == "dist_force" || key == "dist_dur") return flag("dist_on");
    return true;
}

ExperimentSettings ExperimentForm::settings() const {
    ExperimentSettings e;
    e.duration = number("duration");
    e.settle_tolerance_deg = number("tolerance");
    e.disturbance = {flag("dist_on"), number("dist_time"), number("dist_force"), number("dist_dur")};
    return e;
}

}  // namespace pendulum::session
