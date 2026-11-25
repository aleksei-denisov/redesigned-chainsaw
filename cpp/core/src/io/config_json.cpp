#include "pendulum/io/config_json.hpp"

#include <cmath>
#include <fstream>
#include <sstream>
#include <numbers>
#include <stdexcept>

namespace pendulum::io {

using nlohmann::json;

namespace {

double deg2rad(double d) { return d * (std::numbers::pi / 180.0); }  // math.radians
double rad2deg(double r) { return r * (180.0 / std::numbers::pi); }  // math.degrees

template <typename T>
T get(const json& d, const char* key, T fallback) {
    if (!d.is_object() || !d.contains(key)) return fallback;
    const json& v = d.at(key);
    try {
        if constexpr (std::is_same_v<T, bool>) {
            if (!v.is_boolean()) throw std::invalid_argument("not a boolean");
            return v.get<bool>();
        } else if constexpr (std::is_same_v<T, double>) {
            if (v.is_string()) return std::stod(v.get<std::string>());  // float("1.5") in Python
            if (!v.is_number()) throw std::invalid_argument("not a number");
            return v.get<double>();
        } else {
            return v.get<T>();
        }
    } catch (const std::exception&) {
        throw std::invalid_argument(std::string("invalid value for '") + key + "': " + v.dump());
    }
}

SegmentParams segmentFromJson(const json& d) {
    const SegmentParams base;
    SegmentParams s;
    s.length = get(d, "length", base.length);
    s.mass = get(d, "mass", base.mass);
    s.com_ratio = get(d, "com_ratio", base.com_ratio);
    s.inertia = get(d, "inertia", SegmentParams::rodInertia(s.mass, s.length));
    s.joint_viscous_friction = get(d, "joint_viscous_friction", base.joint_viscous_friction);
    s.joint_coulomb_friction = get(d, "joint_coulomb_friction", base.joint_coulomb_friction);
    s.initial_angle = deg2rad(get(d, "initial_angle_deg", 0.0));
    s.initial_velocity = deg2rad(get(d, "initial_velocity_dps", 0.0));
    return s;
}

}  // namespace

PlantParams plantFromJson(const json& pd) {
    PlantParams p;
    p.base_type = baseTypeFromString(get<std::string>(pd, "base_type", "cart"));
    p.gravity = get(pd, "gravity", 9.81);
    p.cart_mass = get(pd, "cart_mass", 1.0);
    p.cart_viscous_friction = get(pd, "cart_viscous_friction", 0.1);
    p.track_length = get(pd, "track_length", 4.0);
    p.segments.clear();
    if (pd.is_object() && pd.contains("segments")) {
        for (const auto& s : pd.at("segments")) p.segments.push_back(segmentFromJson(s));
    } else {
        p.segments.push_back(segmentFromJson(json::object()));
    }
    const json ad = pd.is_object() && pd.contains("actuator") ? pd.at("actuator") : json::object();
    p.actuator.max_output = get(ad, "max_output", 30.0);
    p.actuator.rate_limit = get(ad, "rate_limit", 0.0);
    p.actuator.time_constant = get(ad, "time_constant", 0.005);
    p.actuator.deadband = get(ad, "deadband", 0.0);
    return p;
}

PIDLoopConfig loopFromJson(const json& d) {
    const PIDLoopConfig base;
    PIDLoopConfig c;
    c.signal = get<std::string>(d, "signal", base.signal);
    const double setpoint = get(d, "setpoint", 0.0);
    try {
        c.anti_windup = antiWindupFromString(get<std::string>(d, "anti_windup", toString(base.anti_windup)));
        c.derivative_mode = derivativeModeFromString(get<std::string>(d, "derivative_mode", toString(base.derivative_mode)));
    } catch (const std::invalid_argument& e) {
        throw std::invalid_argument("loop '" + c.signal + "': " + e.what());
    }
    c.name = get<std::string>(d, "name", c.signal);
    c.enabled = get(d, "enabled", true);
    c.setpoint = c.isAngle() ? deg2rad(setpoint) : setpoint;
    c.kp = get(d, "kp", 0.0);
    c.ki = get(d, "ki", 0.0);
    c.kd = get(d, "kd", 0.0);
    c.p_enabled = get(d, "p_enabled", true);
    c.i_enabled = get(d, "i_enabled", true);
    c.d_enabled = get(d, "d_enabled", true);
    c.integral_limit = get(d, "integral_limit", 0.0);
    c.back_calculation_gain = get(d, "back_calculation_gain", base.back_calculation_gain);
    c.derivative_filter_hz = get(d, "derivative_filter_hz", base.derivative_filter_hz);
    return c;
}

ControllerConfig controllerFromJson(const json& cd, const PlantParams& plant) {
    ControllerConfig c;
    c.action = controlActionFromString(get<std::string>(cd, "action", plant.hasCart() ? "direct" : "reverse"));
    c.enabled = get(cd, "enabled", true);
    c.frequency_hz = get(cd, "frequency_hz", 500.0);
    c.output_min = get(cd, "output_min", -plant.actuator.max_output);
    c.output_max = get(cd, "output_max", plant.actuator.max_output);
    c.loops.clear();
    if (cd.is_object() && cd.contains("loops"))
        for (const auto& l : cd.at("loops")) c.loops.push_back(loopFromJson(l));
    return c;
}

json toJson(const PIDLoopConfig& c) {
    return {{"name", c.name},
            {"signal", c.signal},
            {"enabled", c.enabled},
            {"setpoint", c.isAngle() ? rad2deg(c.setpoint) : c.setpoint},
            {"kp", c.kp},
            {"ki", c.ki},
            {"kd", c.kd},
            {"p_enabled", c.p_enabled},
            {"i_enabled", c.i_enabled},
            {"d_enabled", c.d_enabled},
            {"integral_limit", c.integral_limit},
            {"anti_windup", toString(c.anti_windup)},
            {"back_calculation_gain", c.back_calculation_gain},
            {"derivative_mode", toString(c.derivative_mode)},
            {"derivative_filter_hz", c.derivative_filter_hz}};
}

json toJson(const PlantParams& p) {
    json segs = json::array();
    for (const auto& s : p.segments)
        segs.push_back({{"length", s.length},
                        {"mass", s.mass},
                        {"com_ratio", s.com_ratio},
                        {"inertia", s.inertia},
                        {"joint_viscous_friction", s.joint_viscous_friction},
                        {"joint_coulomb_friction", s.joint_coulomb_friction},
                        {"initial_angle_deg", rad2deg(s.initial_angle)},
                        {"initial_velocity_dps", rad2deg(s.initial_velocity)}});
    return {{"base_type", toString(p.base_type)},
            {"gravity", p.gravity},
            {"cart_mass", p.cart_mass},
            {"cart_viscous_friction", p.cart_viscous_friction},
            {"track_length", p.track_length},
            {"actuator",
             {{"max_output", p.actuator.max_output},
              {"rate_limit", p.actuator.rate_limit},
              {"time_constant", p.actuator.time_constant},
              {"deadband", p.actuator.deadband}}},
            {"segments", segs}};
}

json toJson(const ControllerConfig& c) {
    json loops = json::array();
    for (const auto& l : c.loops) loops.push_back(toJson(l));
    return {{"enabled", c.enabled},
            {"frequency_hz", c.frequency_hz},
            {"output_min", c.output_min},
            {"output_max", c.output_max},
            {"action", toString(c.action)},
            {"loops", loops}};
}


AppConfig appConfigFromJson(const json& data) {
    if (!data.is_object()) throw std::invalid_argument("configuration must be a JSON object");
    const int version = data.value("version", kConfigVersion);
    if (version != kConfigVersion)
        throw std::invalid_argument("unsupported configuration version " + std::to_string(version));
    AppConfig c;
    const json empty = json::object();
    auto sub = [&](const json& d, const char* k) -> const json& { return d.contains(k) ? d.at(k) : empty; };
    try {
        c.plant = plantFromJson(sub(data, "plant"));
    } catch (const std::invalid_argument& e) {
        const std::string m = e.what();
        throw std::invalid_argument(m.find("BaseType") != std::string::npos ? "plant: " + m : m);
    }
    try {
        c.controller = controllerFromJson(sub(data, "controller"), c.plant);
    } catch (const std::invalid_argument& e) {
        const std::string m = e.what();
        throw std::invalid_argument(m.find("ControlAction") != std::string::npos ? "controller: " + m : m);
    }
    const json& sd = sub(data, "simulation");
    c.simulation.dt = get(sd, "dt", 0.001);
    c.simulation.sensor_noise_angle_deg = get(sd, "sensor_noise_angle_deg", 0.0);
    c.simulation.sensor_noise_rate_dps = get(sd, "sensor_noise_rate_dps", 0.0);
    c.simulation.sensor_noise_position = get(sd, "sensor_noise_position", 0.0);
    c.simulation.noise_seed = static_cast<std::int64_t>(get(sd, "noise_seed", 1.0));
    c.simulation.telemetry_rate_hz = get(sd, "telemetry_rate_hz", 200.0);
    const json& ed = sub(data, "experiment");
    const json& dd = sub(ed, "disturbance");
    c.experiment.duration = get(ed, "duration", 8.0);
    c.experiment.settle_tolerance_deg = get(ed, "settle_tolerance_deg", 1.0);
    c.experiment.disturbance.enabled = get(dd, "enabled", false);
    c.experiment.disturbance.time = get(dd, "time", 2.0);
    c.experiment.disturbance.force = get(dd, "force", 2.0);
    c.experiment.disturbance.duration = get(dd, "duration", 0.1);
    c.name = get<std::string>(data, "name", "Custom configuration");
    c.description = get<std::string>(data, "description", "");
    c.push_force = get(sub(data, "interaction"), "push_force", 1.0);
    c.validate();
    return c;
}

json toJson(const AppConfig& c) {
    const auto& s = c.simulation;
    const auto& e = c.experiment;
    return {{"version", kConfigVersion},
            {"name", c.name},
            {"description", c.description},
            {"plant", toJson(c.plant)},
            {"controller", toJson(c.controller)},
            {"simulation",
             {{"dt", s.dt},
              {"sensor_noise_angle_deg", s.sensor_noise_angle_deg},
              {"sensor_noise_rate_dps", s.sensor_noise_rate_dps},
              {"sensor_noise_position", s.sensor_noise_position},
              {"noise_seed", s.noise_seed},
              {"telemetry_rate_hz", s.telemetry_rate_hz}}},
            {"interaction", {{"push_force", c.push_force}}},
            {"experiment",
             {{"duration", e.duration},
              {"settle_tolerance_deg", e.settle_tolerance_deg},
              {"disturbance",
               {{"enabled", e.disturbance.enabled},
                {"time", e.disturbance.time},
                {"force", e.disturbance.force},
                {"duration", e.disturbance.duration}}}}}};
}

AppConfig loadConfig(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::invalid_argument("cannot open " + path);
    json data;
    try {
        data = json::parse(in);
    } catch (const json::parse_error& e) {
        const auto slash = path.find_last_of("/\\");
        throw std::invalid_argument((slash == std::string::npos ? path : path.substr(slash + 1)) +
                                    " is not valid JSON: " + e.what());
    }
    return appConfigFromJson(data);
}

void saveConfig(const AppConfig& config, const std::string& path) {
    std::ofstream out(path);
    if (!out) throw std::invalid_argument("cannot write " + path);
    out << toJson(config).dump(2) << "\n";
}

namespace {
json optionalNumber(const std::optional<double>& v) { return v ? json(*v) : json(nullptr); }
std::optional<double> optionalFrom(const json& v) {
    return v.is_null() ? std::nullopt : std::optional<double>(v.get<double>());
}
}  // namespace

json toJson(const RunMetrics& m) {
    json segs = json::array();
    for (const auto& s : m.segments)
        segs.push_back({{"max_deviation", s.max_deviation},
                        {"rmse", s.rmse},
                        {"settling_time", optionalNumber(s.settling_time)},
                        {"overshoot_pct", s.overshoot_pct}});
    return {{"segments", segs},
            {"max_deviation", m.max_deviation},
            {"rmse", m.rmse},
            {"settling_time", optionalNumber(m.settling_time)},
            {"overshoot_pct", m.overshoot_pct},
            {"control_rms", m.control_rms},
            {"control_peak", m.control_peak},
            {"control_energy", m.control_energy},
            {"max_cart_displacement", m.max_cart_displacement},
            {"fell", m.fell},
            {"saturation_pct", m.saturation_pct}};
}

RunMetrics runMetricsFromJson(const json& d) {
    RunMetrics m;
    for (const auto& s : d.at("segments"))
        m.segments.push_back({s.at("max_deviation").get<double>(), s.at("rmse").get<double>(),
                              optionalFrom(s.at("settling_time")), s.at("overshoot_pct").get<double>()});
    m.max_deviation = d.at("max_deviation");
    m.rmse = d.at("rmse");
    m.settling_time = optionalFrom(d.at("settling_time"));
    m.overshoot_pct = d.at("overshoot_pct");
    m.control_rms = d.at("control_rms");
    m.control_peak = d.at("control_peak");
    m.control_energy = d.at("control_energy");
    m.max_cart_displacement = d.at("max_cart_displacement");
    m.fell = d.at("fell");
    m.saturation_pct = d.value("saturation_pct", 0.0);  // absent in files from the Python app
    return m;
}

}  // namespace pendulum::io
