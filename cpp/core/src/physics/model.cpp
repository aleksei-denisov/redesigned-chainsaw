#include "pendulum/physics/model.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace pendulum {

MultiLinkPendulum::MultiLinkPendulum(const PlantParams& params) { setParams(params); }

void MultiLinkPendulum::setParams(const PlantParams& params) {
    params.validate();
    params_ = params;
    n_ = params.nSegments();
    L_ = m_ = c_ = I_ = visc_ = coul_ = h_ = {};
    for (int i = 0; i < n_; ++i) {
        const auto& s = params.segments[i];
        L_[i] = s.length;
        m_[i] = s.mass;
        c_[i] = s.comDistance();
        I_[i] = s.inertia;
        visc_[i] = s.joint_viscous_friction;
        coul_[i] = s.joint_coulomb_friction;
    }
    // Lever coefficients a_ij = L_j (j < i), c_i (j = i), 0 (j > i).
    double a[kMaxSegments][kMaxSegments] = {};
    for (int i = 0; i < n_; ++i) {
        for (int j = 0; j < i; ++j) a[i][j] = L_[j];
        a[i][i] = c_[i];
    }
    // W = Aᵀ diag(m) A, h = Aᵀ m (summed over i in ascending order, as NumPy does here).
    for (int j = 0; j < n_; ++j) {
        for (int k = 0; k < n_; ++k) {
            double acc = 0.0;
            for (int i = 0; i < n_; ++i) acc += (a[i][j] * m_[i]) * a[i][k];
            W_[j][k] = acc;
        }
        double acc = 0.0;
        for (int i = 0; i < n_; ++i) acc += a[i][j] * m_[i];
        h_[j] = acc;
    }
    double sum_m = 0.0;
    for (int i = 0; i < n_; ++i) sum_m += m_[i];
    total_mass_ = params.cart_mass + sum_m;
    // Track end stops: stiff spring–damper contact (penalty method).
    stop_stiffness_ = 2000.0 * total_mass_;
    stop_damping_ = 2.0 * 0.7 * std::sqrt(stop_stiffness_ * total_mass_);
}

Vec MultiLinkPendulum::initialState() const {
    Vec s = Vec::Zero(stateSize());
    for (int i = 0; i < n_; ++i) {
        s[1 + i] = params_.segments[i].initial_angle;
        s[n_ + 2 + i] = params_.segments[i].initial_velocity;
    }
    return s;
}

// ------------------------------------------------------------- reference equations
MatQ MultiLinkPendulum::massMatrix(const Vec& q) const {
    const int n1 = n_ + 1;
    MatQ M(n1, n1);
    M(0, 0) = total_mass_;
    for (int j = 0; j < n_; ++j) {
        M(0, 1 + j) = h_[j] * std::cos(q[1 + j]);
        M(1 + j, 0) = M(0, 1 + j);
        for (int k = 0; k < n_; ++k)
            M(1 + j, 1 + k) = W_[j][k] * std::cos(q[1 + j] - q[1 + k]) + (j == k ? I_[j] : 0.0);
    }
    return M;
}

Vec MultiLinkPendulum::biasForces(const Vec& q, const Vec& qd) const {
    Vec b(n_ + 1);
    double acc = 0.0;
    for (int k = 0; k < n_; ++k) acc += (h_[k] * std::sin(q[1 + k])) * (qd[1 + k] * qd[1 + k]);
    b[0] = -acc;
    for (int j = 0; j < n_; ++j) {
        double r = 0.0;
        for (int k = 0; k < n_; ++k) r += W_[j][k] * std::sin(q[1 + j] - q[1 + k]) * (qd[1 + k] * qd[1 + k]);
        b[1 + j] = r - params_.gravity * h_[j] * std::sin(q[1 + j]);
    }
    return b;
}

Vec MultiLinkPendulum::jointFrictionTorques(const Vec& qd) const {
    Vec tau(n_), gen(n_);
    for (int k = 0; k < n_; ++k) {
        const double rel = qd[1 + k] - (k > 0 ? qd[k] : 0.0);
        tau[k] = -visc_[k] * rel - coul_[k] * std::tanh(rel / kCoulombSmoothing);
    }
    gen = tau;
    for (int k = 0; k + 1 < n_; ++k) gen[k] -= tau[k + 1];  // reaction on the segment below
    return gen;
}

std::array<double, kMaxSegments> MultiLinkPendulum::pushArms(int segment, double ratio) const {
    std::array<double, kMaxSegments> a{};
    if (segment < 0 || segment >= n_) {
        for (int j = 0; j < n_; ++j) a[j] = L_[j];
        return a;
    }
    for (int j = 0; j < segment; ++j) a[j] = L_[j];
    a[segment] = std::clamp(ratio, 0.0, 1.0) * L_[segment];
    return a;
}

Vec MultiLinkPendulum::generalizedForces(const Vec& q, const Vec& qd, double actuator, double tip_force,
                                         const PushArms& arms) const {
    Vec Q = Vec::Zero(n_ + 1);
    const Vec fr = jointFrictionTorques(qd);
    for (int j = 0; j < n_; ++j) Q[1 + j] += fr[j];
    if (hasCart()) {
        Q[0] += actuator - params_.cart_viscous_friction * qd[0];
        Q[0] += endStopForce(q[0], qd[0]);
    } else {
        Q[1] += actuator;  // motor torque between ground and segment 1
    }
    if (arms) {
        Q[0] += tip_force;
        for (int j = 0; j < n_; ++j) Q[1 + j] += (*arms)[j] * std::cos(q[1 + j]);
    } else if (tip_force != 0.0) {
        Q[0] += tip_force;
        for (int j = 0; j < n_; ++j) Q[1 + j] += tip_force * L_[j] * std::cos(q[1 + j]);
    }
    return Q;
}

double MultiLinkPendulum::endStopForce(double x, double xd) const {
    const double limit = 0.5 * params_.track_length;
    if (x > limit) return -stop_stiffness_ * (x - limit) - stop_damping_ * std::max(xd, 0.0);
    if (x < -limit) return -stop_stiffness_ * (x + limit) - stop_damping_ * std::min(xd, 0.0);
    return 0.0;
}

Vec MultiLinkPendulum::accelerations(const Vec& state, double actuator, double tip_force,
                                     const PushArms& arms) const {
    const int n1 = n_ + 1;
    const Vec q = state.head(n1), qd = state.segment(n1, n1);
    const Vec rhs = generalizedForces(q, qd, actuator, tip_force, arms) - biasForces(q, qd);
    const MatQ M = massMatrix(q);
    if (hasCart()) return M.partialPivLu().solve(rhs);
    Vec qdd = Vec::Zero(n1);
    qdd.tail(n_) = M.bottomRightCorner(n_, n_).partialPivLu().solve(rhs.tail(n_));
    return qdd;
}

Vec MultiLinkPendulum::derivative(const Vec& state, double actuator, double tip_force, const PushArms& arms) const {
    const int n1 = n_ + 1;
    Vec ds(2 * n1);
    ds.head(n1) = state.segment(n1, n1);
    ds.tail(n1) = accelerations(state, actuator, tip_force, arms);
    if (!hasCart()) ds[0] = 0.0;
    return ds;
}

// ---------------------------------------------------------------------- fast path
namespace {

/// Solves M x = b for SPD M given by its lower triangle (row-major, size ≤ 4).
void choleskySolve(int n, const double M[kMaxCoords][kMaxCoords], const double* b, double* x) {
    double L[kMaxCoords][kMaxCoords] = {};
    for (int i = 0; i < n; ++i) {
        for (int j = 0; j <= i; ++j) {
            double acc = M[i][j];
            for (int k = 0; k < j; ++k) acc -= L[i][k] * L[j][k];
            if (i == j) {
                if (acc <= 0.0) throw std::domain_error("mass matrix is not positive definite");
                L[i][i] = std::sqrt(acc);
            } else {
                L[i][j] = acc / L[j][j];
            }
        }
    }
    double y[kMaxCoords];
    for (int i = 0; i < n; ++i) {
        double acc = b[i];
        for (int k = 0; k < i; ++k) acc -= L[i][k] * y[k];
        y[i] = acc / L[i][i];
    }
    for (int i = n - 1; i >= 0; --i) {
        double acc = y[i];
        for (int k = i + 1; k < n; ++k) acc -= L[k][i] * x[k];
        x[i] = acc / L[i][i];
    }
}

}  // namespace

void MultiLinkPendulum::accelerationsFast(const double* s, double u, double f, const PushArms& arms,
                                          double* qdd) const {
    const int n = n_, n1 = n + 1;
    const double* th = s + 1;
    const double* w = s + n1 + 1;
    double sin_t[kMaxSegments], cos_t[kMaxSegments], w2[kMaxSegments];
    for (int j = 0; j < n; ++j) {
        sin_t[j] = std::sin(th[j]);
        cos_t[j] = std::cos(th[j]);
        w2[j] = w[j] * w[j];
    }
    const double g = params_.gravity;

    double rhs[kMaxCoords] = {};
    for (int j = 0; j < n; ++j) {
        double acc = g * h_[j] * sin_t[j];
        const double sj = sin_t[j], cj = cos_t[j];
        for (int k = 0; k < n; ++k)
            if (k != j) acc -= W_[j][k] * (sj * cos_t[k] - cj * sin_t[k]) * w2[k];  // sin(θj − θk)
        rhs[j + 1] = acc;
    }
    double prev = 0.0;
    for (int k = 0; k < n; ++k) {
        const double rel = w[k] - prev;
        prev = w[k];
        double tau = -visc_[k] * rel;
        if (coul_[k] != 0.0) tau -= coul_[k] * std::tanh(rel / kCoulombSmoothing);
        rhs[k + 1] += tau;
        if (k > 0) rhs[k] -= tau;
    }
    if (hasCart()) {
        const double x = s[0], xd = s[n1];
        const double limit = 0.5 * params_.track_length;
        double q0 = u - params_.cart_viscous_friction * xd;
        if (x > limit)
            q0 += -stop_stiffness_ * (x - limit) - stop_damping_ * (xd > 0.0 ? xd : 0.0);
        else if (x < -limit)
            q0 += -stop_stiffness_ * (x + limit) - stop_damping_ * (xd < 0.0 ? xd : 0.0);
        double c0 = 0.0;
        for (int k = 0; k < n; ++k) c0 -= h_[k] * sin_t[k] * w2[k];
        rhs[0] = q0 - c0;
    } else {
        rhs[1] += u;
    }
    if (f != 0.0 || arms) {
        rhs[0] += f;
        if (!arms)
            for (int j = 0; j < n; ++j) rhs[j + 1] += f * L_[j] * cos_t[j];
        else
            for (int j = 0; j < n; ++j) rhs[j + 1] += (*arms)[j] * cos_t[j];
    }

    const int size = hasCart() ? n1 : n;
    const int off = hasCart() ? 0 : 1;
    double M[kMaxCoords][kMaxCoords] = {};
    for (int a = 0; a < size; ++a) {
        const int ia = a + off;
        for (int b = 0; b <= a; ++b) {
            const int ib = b + off;
            double v;
            if (ia == 0 && ib == 0) {
                v = total_mass_;
            } else if (ib == 0) {
                v = h_[ia - 1] * cos_t[ia - 1];
            } else {
                const int j = ia - 1, k = ib - 1;
                v = W_[j][k] * (cos_t[j] * cos_t[k] + sin_t[j] * sin_t[k]);  // cos(θj − θk)
                if (j == k) v += I_[j];
            }
            M[a][b] = v;
        }
    }
    if (hasCart()) {
        choleskySolve(size, M, rhs, qdd);
    } else {
        qdd[0] = 0.0;
        choleskySolve(size, M, rhs + 1, qdd + 1);
    }
}

Vec MultiLinkPendulum::rk4Step(const Vec& state, double actuator, double tip_force, double dt,
                               const PushArms& arms) const {
    const int n1 = n_ + 1, size = 2 * n1;
    double s[kMaxState] = {}, k1[kMaxState] = {}, k2[kMaxState] = {}, k3[kMaxState] = {}, k4[kMaxState] = {},
           tmp[kMaxState] = {};
    for (int i = 0; i < size; ++i) s[i] = state[i];
    auto deriv = [&](const double* x, double* out) {
        for (int i = 0; i < n1; ++i) out[i] = x[n1 + i];
        accelerationsFast(x, actuator, tip_force, arms, out + n1);
    };
    deriv(s, k1);
    for (int i = 0; i < size; ++i) tmp[i] = s[i] + 0.5 * dt * k1[i];
    deriv(tmp, k2);
    for (int i = 0; i < size; ++i) tmp[i] = s[i] + 0.5 * dt * k2[i];
    deriv(tmp, k3);
    for (int i = 0; i < size; ++i) tmp[i] = s[i] + dt * k3[i];
    deriv(tmp, k4);
    const double sixth = dt / 6.0;
    Vec out(size);
    for (int i = 0; i < size; ++i) out[i] = s[i] + sixth * (k1[i] + 2.0 * k2[i] + 2.0 * k3[i] + k4[i]);
    if (!hasCart()) {
        out[0] = 0.0;
        out[n1] = 0.0;
    }
    return out;
}

// ------------------------------------------------------------ energetics, kinematics
double MultiLinkPendulum::kineticEnergy(const Vec& state) const {
    const int n1 = n_ + 1;
    const Vec qd = state.segment(n1, n1);
    return 0.5 * qd.dot(massMatrix(state.head(n1)) * qd);
}

double MultiLinkPendulum::potentialEnergy(const Vec& state) const {
    double acc = 0.0;
    for (int j = 0; j < n_; ++j) acc += h_[j] * std::cos(state[1 + j]);
    return params_.gravity * acc;
}

double MultiLinkPendulum::horizontalMomentum(const Vec& state) const {
    const int n1 = n_ + 1;
    const MatQ M = massMatrix(state.head(n1));
    return M.row(0).dot(state.segment(n1, n1));
}

Kinematics MultiLinkPendulum::kinematics(const Vec& state) const {
    Kinematics k;
    k.n = n_;
    const double x = hasCart() ? state[0] : 0.0;
    k.cart = {x, 0.0};
    k.joints[0] = {x, 0.0};
    double px = x, py = 0.0;
    for (int j = 0; j < n_; ++j) {
        const double s = std::sin(state[1 + j]), c = std::cos(state[1 + j]);
        k.coms[j] = {px + c_[j] * s, py + c_[j] * c};
        px += L_[j] * s;
        py += L_[j] * c;
        k.joints[j + 1] = {px, py};
    }
    return k;
}

}  // namespace pendulum
