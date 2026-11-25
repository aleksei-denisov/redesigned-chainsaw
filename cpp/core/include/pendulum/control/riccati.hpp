#pragma once
// Continuous algebraic Riccati equation  AᵀP + PA − PBR⁻¹BᵀP + Q = 0  (control/riccati.py).
// Hamiltonian eigenvector method followed by Newton–Kleinman refinement – the same algorithm
// as the Python NumPy backend; checked against both of its backends (NumPy and SciPy).
#include <Eigen/Dense>

namespace pendulum {

/// ‖AᵀP + PA − PBR⁻¹BᵀP + Q‖_F / ‖Q‖_F
double careResidual(const Eigen::MatrixXd& A, const Eigen::MatrixXd& B, const Eigen::MatrixXd& Q,
                    const Eigen::MatrixXd& R, const Eigen::MatrixXd& P);

/// Stabilising solution P. Throws std::runtime_error if the system is not
/// stabilisable/detectable (Hamiltonian eigenvalues on the imaginary axis) or P is not finite.
Eigen::MatrixXd solveCare(const Eigen::MatrixXd& A, const Eigen::MatrixXd& B, const Eigen::MatrixXd& Q,
                          const Eigen::MatrixXd& R, int max_refinements = 20);

}  // namespace pendulum
