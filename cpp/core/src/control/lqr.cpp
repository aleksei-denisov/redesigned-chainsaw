#include "pendulum/control/lqr.hpp"

#include <map>
#include <string>

#include "pendulum/control/riccati.hpp"
#include "pendulum/physics/model.hpp"

namespace pendulum {

Linearization linearize(const PlantParams& plant, double eps) {
    const MultiLinkPendulum model(plant);
    const int n1 = model.n() + 1, size = 2 * n1;
    const Vec s0 = Vec::Zero(size);
    Eigen::MatrixXd A = Eigen::MatrixXd::Zero(size, size);
    for (int k = 0; k < size; ++k) {
        Vec d = Vec::Zero(size);
        d[k] = eps;
        A.col(k) = (model.derivative(s0 + d) - model.derivative(s0 - d)) / (2 * eps);
    }
    const Eigen::VectorXd Bfull = (model.derivative(s0, eps) - model.derivative(s0, -eps)) / (2 * eps);
    Linearization lin;
    for (int i = 0; i < size; ++i)
        if (model.hasCart() || (i != 0 && i != n1)) lin.kept.push_back(i);
    const auto m = static_cast<Eigen::Index>(lin.kept.size());
    lin.A.resize(m, m);
    lin.B.resize(m, 1);
    for (Eigen::Index r = 0; r < m; ++r) {
        for (Eigen::Index c = 0; c < m; ++c) lin.A(r, c) = A(lin.kept[r], lin.kept[c]);
        lin.B(r, 0) = Bfull[lin.kept[r]];
    }
    return lin;
}

LQRGain lqrGain(const PlantParams& plant, const LQRWeights& w) {
    const Linearization lin = linearize(plant);
    const int n1 = plant.nSegments() + 1;
    Eigen::VectorXd diag(lin.kept.size());
    for (std::size_t i = 0; i < lin.kept.size(); ++i) {
        const int idx = lin.kept[i];
        diag[i] = idx == 0 ? w.cart_position : idx < n1 ? w.angle : idx == n1 ? w.cart_velocity : w.angular_velocity;
    }
    const Eigen::MatrixXd Q = diag.asDiagonal();
    const Eigen::MatrixXd R = Eigen::MatrixXd::Constant(1, 1, w.effort);
    const Eigen::MatrixXd P = solveCare(lin.A, lin.B, Q, R);
    const Eigen::MatrixXd K = R.partialPivLu().solve(lin.B.transpose() * P);
    return {K.transpose(), lin.kept};
}

ControlAction defaultAction(const PlantParams& plant) {
    return plant.hasCart() ? ControlAction::Direct : ControlAction::Reverse;
}

ControllerConfig lqrController(const PlantParams& plant, const LQRWeights& weights, const ControllerConfig* base) {
    const LQRGain g = lqrGain(plant, weights);
    const ControlAction action = base ? base->action : defaultAction(plant);
    const double sigma = action == ControlAction::Direct ? 1.0 : -1.0;
    const int n1 = plant.nSegments() + 1;
    std::map<int, double> gains;
    for (std::size_t i = 0; i < g.kept.size(); ++i) gains[g.kept[i]] = g.K[static_cast<Eigen::Index>(i)];
    std::map<std::string, const PIDLoopConfig*> old;
    if (base)
        for (const auto& l : base->loops) old[l.signal] = &l;

    std::vector<std::string> signals;
    if (plant.hasCart()) signals.emplace_back("cart");
    for (int i = 0; i < plant.nSegments(); ++i) signals.push_back("theta" + std::to_string(i + 1));

    ControllerConfig out;
    out.loops.clear();
    for (const auto& sig : signals) {
        const int pos = sig == "cart" ? 0 : std::stoi(sig.substr(5));
        const auto it = old.find(sig);
        const PIDLoopConfig* prev = it == old.end() ? nullptr : it->second;
        PIDLoopConfig loop;
        loop.name = sig == "cart" ? "Cart position" : "Segment " + std::to_string(pos) + " angle";
        loop.signal = sig;
        loop.kp = -sigma * gains[pos];
        loop.ki = prev ? prev->ki : 0.0;
        loop.kd = -sigma * gains[pos + n1];
        loop.anti_windup = prev ? prev->anti_windup : AntiWindup::Clamp;
        loop.derivative_mode = prev ? prev->derivative_mode : DerivativeMode::MeasuredRate;
        loop.integral_limit = prev ? prev->integral_limit : 0.0;
        loop.setpoint = prev ? prev->setpoint : 0.0;
        out.loops.push_back(loop);
    }
    out.action = action;
    if (base) {
        out.enabled = base->enabled;
        out.frequency_hz = base->frequency_hz;
        out.output_min = base->output_min;
        out.output_max = base->output_max;
    } else {
        out.output_min = -plant.actuator.max_output;
        out.output_max = plant.actuator.max_output;
    }
    return out;
}

}  // namespace pendulum
