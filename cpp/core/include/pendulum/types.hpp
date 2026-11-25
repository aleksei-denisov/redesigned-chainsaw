#pragma once
// Small fixed-capacity linear-algebra types: no heap allocation in the real-time loop.
#include <Eigen/Dense>

namespace pendulum {

inline constexpr int kMaxSegments = 3;                       ///< MAX_SEGMENTS in the Python code
inline constexpr int kMaxCoords = kMaxSegments + 1;          ///< q = [x, θ1..θn]
inline constexpr int kMaxState = 2 * kMaxCoords;             ///< s = [q, q̇]

/// State-sized vector (at most 8 entries), stack storage.
using Vec = Eigen::Matrix<double, Eigen::Dynamic, 1, 0, kMaxState, 1>;
/// Coordinate-sized square matrix (mass matrix, at most 4×4).
using MatQ = Eigen::Matrix<double, Eigen::Dynamic, Eigen::Dynamic, 0, kMaxCoords, kMaxCoords>;
/// State-sized square matrix (linearisation, at most 8×8).
using MatS = Eigen::Matrix<double, Eigen::Dynamic, Eigen::Dynamic, 0, kMaxState, kMaxState>;

}  // namespace pendulum
