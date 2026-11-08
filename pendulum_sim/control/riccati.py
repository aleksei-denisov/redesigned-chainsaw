"""Continuous algebraic Riccati equation (CARE) solver.

Solves ``Aᵀ P + P A − P B R⁻¹ Bᵀ P + Q = 0`` for the stabilising solution ``P``.

SciPy's ``solve_continuous_are`` (ordered Schur method) is used whenever SciPy
is installed – that is the case on the desktop, so desktop results are
unchanged. On platforms where SciPy is not bundled (the Android build) a
NumPy-only solver is used instead:

1. **Hamiltonian eigenvector method.** The stable invariant subspace of

   .. math::  H = \\begin{bmatrix} A & -B R^{-1} B^T \\\\ -Q & -A^T \\end{bmatrix}

   is spanned by the eigenvectors ``[U₁; U₂]`` whose eigenvalues have negative
   real part; then ``P = U₂ U₁⁻¹``.
2. **Newton–Kleinman refinement.** With ``K = R⁻¹ Bᵀ P`` and ``A_c = A − B K``
   the Lyapunov equation ``A_cᵀ P + P A_c + Q + Kᵀ R K = 0`` is solved
   (Kronecker form – the systems here have at most 8 states) until the CARE
   residual stops shrinking. This recovers full double precision even when the
   eigenvector basis is mildly ill-conditioned.

Only NumPy is imported at module level, so this file is safe on every platform.
"""

from __future__ import annotations

import numpy as np

try:  # SciPy is optional: present on the desktop, absent in the Android build.
    from scipy.linalg import solve_continuous_are as _scipy_care
except ImportError:  # pragma: no cover - exercised by tests through `backend="numpy"`
    _scipy_care = None

HAVE_SCIPY = _scipy_care is not None


def care_residual(A: np.ndarray, B: np.ndarray, Q: np.ndarray, R: np.ndarray, P: np.ndarray) -> float:
    """Frobenius norm of the CARE residual, relative to ``‖Q‖``."""
    res = A.T @ P + P @ A - P @ B @ np.linalg.solve(R, B.T @ P) + Q
    return float(np.linalg.norm(res) / max(np.linalg.norm(Q), 1e-300))


def _solve_lyapunov(Ac: np.ndarray, C: np.ndarray) -> np.ndarray:
    """Solve ``Acᵀ X + X Ac + C = 0`` via the Kronecker (vec) form."""
    n = Ac.shape[0]
    eye = np.eye(n)
    # vec(Acᵀ X) = (I ⊗ Acᵀ) vec X,  vec(X Ac) = (Acᵀ ⊗ I) vec X   (column-major vec)
    M = np.kron(eye, Ac.T) + np.kron(Ac.T, eye)
    x = np.linalg.solve(M, -C.reshape(-1, order="F"))
    X = x.reshape(n, n, order="F")
    return 0.5 * (X + X.T)


def solve_care_numpy(A: np.ndarray, B: np.ndarray, Q: np.ndarray, R: np.ndarray,
                     max_refinements: int = 20) -> np.ndarray:
    """NumPy-only stabilising CARE solution (see module docstring)."""
    A = np.asarray(A, dtype=float)
    B = np.asarray(B, dtype=float)
    Q = np.asarray(Q, dtype=float)
    R = np.asarray(R, dtype=float)
    n = A.shape[0]
    G = B @ np.linalg.solve(R, B.T)
    H = np.block([[A, -G], [-Q, -A.T]])
    eigvals, eigvecs = np.linalg.eig(H)
    stable = np.argsort(eigvals.real)[:n]
    if not np.all(eigvals.real[stable] < 0):
        raise np.linalg.LinAlgError("the Hamiltonian has eigenvalues on the imaginary axis "
                                    "(system not stabilisable/detectable)")
    U = eigvecs[:, stable]
    U1, U2 = U[:n], U[n:]
    P = np.linalg.solve(U1.T, U2.T).T  # U2 @ inv(U1)
    P = np.real(0.5 * (P + P.conj().T))

    best, best_res = P, care_residual(A, B, Q, R, P)
    for _ in range(max_refinements):
        K = np.linalg.solve(R, B.T @ P)
        Ac = A - B @ K
        if np.max(np.linalg.eigvals(Ac).real) >= 0:
            break  # refinement only makes sense from a stabilising gain
        P = _solve_lyapunov(Ac, Q + K.T @ R @ K)
        res = care_residual(A, B, Q, R, P)
        if not res < best_res * 0.5:  # converged (or stopped improving)
            if res < best_res:
                best, best_res = P, res
            break
        best, best_res = P, res
    if not np.all(np.isfinite(best)):
        raise np.linalg.LinAlgError("Riccati solution is not finite")
    return best


def solve_care(A: np.ndarray, B: np.ndarray, Q: np.ndarray, R: np.ndarray,
               backend: str = "auto") -> np.ndarray:
    """Stabilising CARE solution.

    ``backend``: ``"auto"`` (SciPy if installed, else NumPy), ``"scipy"`` or ``"numpy"``.
    """
    if backend == "scipy" or (backend == "auto" and HAVE_SCIPY):
        if _scipy_care is None:
            raise ImportError("SciPy is not installed")
        return _scipy_care(A, B, Q, R)
    if backend not in ("auto", "numpy"):
        raise ValueError(f"unknown backend {backend!r}")
    return solve_care_numpy(A, B, Q, R)
