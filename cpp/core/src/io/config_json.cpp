#include "pendulum/io/config_json.hpp"

#include <cmath>
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

}  // namespace pendulum::io
