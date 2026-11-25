#include "pendulum/simulation/scenarios.hpp"

#include <functional>
#include <map>
#include <numbers>
#include <stdexcept>

namespace pendulum {

namespace {

double radians(double d) { return d * (std::numbers::pi / 180.0); }

const double kLengths[] = {0.5, 0.4, 0.3};
const double kMasses[] = {0.2, 0.15, 0.1};

std::vector<SegmentParams> makeSegments(int n, const std::vector<double>& angles_deg, double friction = 0.0005) {
    std::vector<SegmentParams> segs;
    for (int i = 0; i < n; ++i) {
        SegmentParams s;
        s.length = kLengths[i];
        s.mass = kMasses[i];
        s.com_ratio = 0.5;
        s.inertia = SegmentParams::rodInertia(kMasses[i], kLengths[i]);
        s.joint_viscous_friction = friction;
        s.initial_angle = radians(i < static_cast<int>(angles_deg.size()) ? angles_deg[i] : 0.0);
        segs.push_back(s);
    }
    return segs;
}

PlantParams cartPlant(int n, const std::vector<double>& angles, double max_force = 40.0) {
    PlantParams p;
    p.base_type = BaseType::Cart;
    p.cart_mass = 1.0;
    p.cart_viscous_friction = 0.1;
    p.track_length = 4.0;
    p.segments = makeSegments(n, angles);
    p.actuator.max_output = max_force;
    p.actuator.time_constant = 0.002;
    return p;
}

PlantParams fixedPlant(int n, const std::vector<double>& angles, double max_torque = 2.0, double friction = 0.0005) {
    PlantParams p;
    p.base_type = BaseType::Fixed;
    p.segments = makeSegments(n, angles, friction);
    p.actuator.max_output = max_torque;
    p.actuator.time_constant = 0.002;
    return p;
}

PIDLoopConfig loop(const std::string& name, const std::string& signal, double kp, double ki, double kd,
                   bool enabled = true) {
    PIDLoopConfig l;
    l.name = name;
    l.signal = signal;
    l.kp = kp;
    l.ki = ki;
    l.kd = kd;
    l.enabled = enabled;
    return l;
}

ControllerConfig manual(double limit, ControlAction action, std::vector<PIDLoopConfig> loops, bool enabled = true) {
    ControllerConfig c;
    c.enabled = enabled;
    c.output_min = -limit;
    c.output_max = limit;
    c.action = action;
    c.loops = std::move(loops);
    return c;
}

AppConfig make(const char* name, const char* description, PlantParams plant, ControllerConfig controller,
               double push_force, double duration, std::optional<DisturbanceSettings> dist = std::nullopt) {
    AppConfig c;
    c.name = name;
    c.description = description;
    c.plant = std::move(plant);
    c.controller = std::move(controller);
    c.push_force = push_force;
    c.experiment.duration = duration;
    if (dist) c.experiment.disturbance = *dist;
    return c;
}

DisturbanceSettings pulse(double t, double f) { return {true, t, f, 0.1}; }

const std::map<std::string, std::function<AppConfig()>>& registry() {
    static const std::map<std::string, std::function<AppConfig()>> r = {
        {"single_cart_pid", [] {
             return make("Single pendulum on cart – PID",
                         "Classic cart-pole balanced by one PID loop on the angle. The cart is not regulated, so "
                         "it slowly drifts – enable the cart loop (or use the LQR-seeded scenario) to hold it in place.",
                         cartPlant(1, {8.0}),
                         manual(40.0, ControlAction::Direct,
                                {loop("Segment 1 angle", "theta1", 40.0, 5.0, 5.0),
                                 loop("Cart position", "cart", 0.0, 0.0, 0.0, false)}),
                         0.2, 6.0);
         }},
        {"single_cart_full", [] {
             auto p = cartPlant(1, {10.0});
             return make("Single pendulum on cart – angle + cart loops",
                         "Angle loop plus a cart-position loop, robust gains from the auto-tuner (recovers from "
                         "≈5 N tip pushes). Note the negative-looking behaviour of the cart loop: to return to the "
                         "centre the cart first moves away (non-minimum-phase).",
                         p, lqrController(p, tunedWeights("single_cart")), 2.5, 12.0, pulse(4.0, 2.5));
         }},
        {"single_fixed_pid", [] {
             return make("Single pendulum, base motor – PID",
                         "Fixed pivot with a torque motor (reverse-acting PID). Try lowering D to see oscillations, "
                         "or raising P to see faster, more aggressive recovery.",
                         fixedPlant(1, {15.0}),
                         manual(2.0, ControlAction::Reverse, {loop("Segment 1 angle", "theta1", 6.0, 3.0, 0.35)}),
                         3.0, 5.0);
         }},
        {"windup_demo", [] {
             auto l = loop("Segment 1 angle", "theta1", 6.0, 20.0, 0.35);
             l.anti_windup = AntiWindup::None;
             return make("Integral windup demonstration",
                         "Large integral gain with a weak motor and anti-windup OFF: the integrator winds up while "
                         "the output is saturated and causes a large overshoot. Run an experiment, switch anti-windup "
                         "to 'clamp' or 'back_calculation', run again and compare.",
                         fixedPlant(1, {25.0}, 0.8), manual(0.8, ControlAction::Reverse, {l}), 1.0, 6.0);
         }},
        {"double_cart_lqr", [] {
             auto p = cartPlant(2, {4.0, -2.0});
             return make("Double pendulum on cart – multi-loop (LQR seeded)",
                         "Two segments balanced by three parallel loops (cart, θ1, θ2). Robust auto-tuned gains: "
                         "recovers from tip pushes up to ≈0.5 N (0.1 s).",
                         p, lqrController(p, tunedWeights("double_cart")), 0.25, 8.0, pulse(4.0, 0.25));
         }},
        {"double_fixed_lqr", [] {
             auto p = fixedPlant(2, {5.0, -3.0});
             return make("Double pendulum, base motor (pendubot) – LQR seeded",
                         "Underactuated: only the lower joint has a motor, yet both segments are balanced through "
                         "dynamic coupling.",
                         p, lqrController(p, tunedWeights("double_fixed")), 0.4, 6.0);
         }},
        {"triple_cart_lqr", [] {
             auto p = cartPlant(3, {2.0, -1.0, 1.0}, 60.0);
             return make("Triple pendulum on cart – multi-loop (LQR seeded)",
                         "Three segments on a cart. Needs fast, coordinated feedback from all loops; small gain "
                         "changes quickly destabilise it. Robust auto-tuned gains recover from tip pushes up to "
                         "≈0.12 N (0.1 s) – this system is inherently fragile.",
                         p, lqrController(p, tunedWeights("triple_cart")), 0.06, 8.0);
         }},
        {"double_free_swing", [] {
             auto p = fixedPlant(2, {120.0, -30.0}, 2.0, 0.0);
             p.segments[0].joint_viscous_friction = 0.0;
             auto l = PIDLoopConfig{};
             l.enabled = false;
             return make("Double pendulum free swing (chaos)",
                         "Frictionless double pendulum without control. Motion is chaotic; the total energy plot "
                         "stays flat, which validates the integrator.",
                         p, manual(2.0, ControlAction::Reverse, {l}, false), 0.5, 10.0);
         }},
        {"hanging_damped", [] {
             auto l = PIDLoopConfig{};
             l.enabled = false;
             return make("Hanging pendulum with joint friction",
                         "Released from 150°, the pendulum swings down to the stable hanging equilibrium (180°) and "
                         "the oscillation decays due to joint friction.",
                         fixedPlant(1, {150.0}, 2.0, 0.01), manual(2.0, ControlAction::Reverse, {l}, false), 0.5,
                         10.0);
         }},
    };
    return r;
}

}  // namespace

const std::vector<std::string>& scenarioKeys() {
    static const std::vector<std::string> keys = {"single_cart_pid",  "single_cart_full", "single_fixed_pid",
                                                  "windup_demo",      "double_cart_lqr",  "double_fixed_lqr",
                                                  "triple_cart_lqr",  "double_free_swing", "hanging_damped"};
    return keys;
}

LQRWeights tunedWeights(const std::string& system) {
    static const std::map<std::string, LQRWeights> w = {
        {"single_cart", {15.5, 2766.0, 5.30, 0.0377, 0.00845}},
        {"double_cart", {0.378, 0.200, 21.5, 185.0, 0.000606}},
        {"double_fixed", {7.49, 222.3, 0.0094, 62.3, 0.0193}},
        {"triple_cart", {1.22, 1.95, 25.0, 83.7, 0.000115}},
    };
    return w.at(system);
}

AppConfig scenario(const std::string& key) {
    const auto it = registry().find(key);
    if (it == registry().end()) throw std::out_of_range("unknown scenario '" + key + "'");
    AppConfig c = it->second();
    c.validate();
    return c;
}

}  // namespace pendulum
