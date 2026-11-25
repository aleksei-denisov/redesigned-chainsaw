#include "pendulum/control/riccati.hpp"

#include <algorithm>
#include <numeric>
#include <stdexcept>
#include <vector>

#include <Eigen/Eigenvalues>

namespace pendulum {

double careResidual(const Eigen::MatrixXd& A, const Eigen::MatrixXd& B, const Eigen::MatrixXd& Q,
                    const Eigen::MatrixXd& R, const Eigen::MatrixXd& P) {
    const Eigen::MatrixXd res =
        A.transpose() * P + P * A - P * B * R.partialPivLu().solve(B.transpose() * P) + Q;
    return res.norm() / std::max(Q.norm(), 1e-300);
}

namespace {

/// Solves Acᵀ X + X Ac + C = 0 via the Kronecker (column-major vec) form; n ≤ 8 here.
Eigen::MatrixXd solveLyapunov(const Eigen::MatrixXd& Ac, const Eigen::MatrixXd& C) {
    const Eigen::Index n = Ac.rows();
    const Eigen::MatrixXd At = Ac.transpose();
    Eigen::MatrixXd M = Eigen::MatrixXd::Zero(n * n, n * n);
    // kron(I, Acᵀ) + kron(Acᵀ, I)
    for (Eigen::Index i = 0; i < n; ++i)
        for (Eigen::Index j = 0; j < n; ++j) {
            M.block(i * n, i * n, n, n)(j, Eigen::all) += At.row(j);
            M.block(i * n, j * n, n, n).diagonal().array() += At(i, j);
        }
    const Eigen::VectorXd c = Eigen::Map<const Eigen::VectorXd>(C.data(), n * n);
    const Eigen::VectorXd x = M.partialPivLu().solve(-c);
    const Eigen::MatrixXd X = Eigen::Map<const Eigen::MatrixXd>(x.data(), n, n);
    return 0.5 * (X + X.transpose());
}

}  // namespace

Eigen::MatrixXd solveCare(const Eigen::MatrixXd& A, const Eigen::MatrixXd& B, const Eigen::MatrixXd& Q,
                          const Eigen::MatrixXd& R, int max_refinements) {
    const Eigen::Index n = A.rows();
    const Eigen::MatrixXd G = B * R.partialPivLu().solve(B.transpose());
    Eigen::MatrixXd H(2 * n, 2 * n);
    H << A, -G, -Q, -A.transpose();

    Eigen::EigenSolver<Eigen::MatrixXd> es(H);
    if (es.info() != Eigen::Success) throw std::runtime_error("eigenvalue decomposition failed");
    const Eigen::VectorXcd ev = es.eigenvalues();
    std::vector<Eigen::Index> order(2 * n);
    std::iota(order.begin(), order.end(), 0);
    std::stable_sort(order.begin(), order.end(),
                     [&](Eigen::Index a, Eigen::Index b) { return ev[a].real() < ev[b].real(); });
    for (Eigen::Index k = 0; k < n; ++k)
        if (!(ev[order[k]].real() < 0.0))
            throw std::runtime_error("the Hamiltonian has eigenvalues on the imaginary axis "
                                     "(system not stabilisable/detectable)");
    const Eigen::MatrixXcd V = es.eigenvectors();
    Eigen::MatrixXcd U(2 * n, n);
    for (Eigen::Index k = 0; k < n; ++k) U.col(k) = V.col(order[k]);
    const Eigen::MatrixXcd U1 = U.topRows(n), U2 = U.bottomRows(n);
    // P = U2 U1⁻¹ = (U1ᵀ \ U2ᵀ)ᵀ
    const Eigen::MatrixXcd Pc = U1.transpose().partialPivLu().solve(U2.transpose()).transpose();
    Eigen::MatrixXd P = (0.5 * (Pc + Pc.adjoint())).real();

    Eigen::MatrixXd best = P;
    double best_res = careResidual(A, B, Q, R, P);
    for (int it = 0; it < max_refinements; ++it) {
        const Eigen::MatrixXd K = R.partialPivLu().solve(B.transpose() * P);
        const Eigen::MatrixXd Ac = A - B * K;
        if (Ac.eigenvalues().real().maxCoeff() >= 0.0) break;  // refine only from a stabilising gain
        P = solveLyapunov(Ac, Q + K.transpose() * R * K);
        const double res = careResidual(A, B, Q, R, P);
        if (!(res < best_res * 0.5)) {  // converged or stopped improving
            if (res < best_res) {
                best = P;
                best_res = res;
            }
            break;
        }
        best = P;
        best_res = res;
    }
    if (!best.allFinite()) throw std::runtime_error("Riccati solution is not finite");
    return best;
}

}  // namespace pendulum
